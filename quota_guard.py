#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quota_guard.py — ollama.com クラウド使用量による汎用オートストップ/オートリジュース。

使い方:
  quota_guard.py [options] -- <command> [args...]

- 起動前: 使用量が上限未満になるまで待機 (pre-flight gate)
- 実行中: poll ごとに使用量を確認し、上限超過で子プロセスへ SIGSTOP (自動停止)
- 回復したら SIGCONT (自動再開)
- usage API: {base}/api/usage (Bearer トークン). base 末尾の /v1 は自動で外す

**2026-10-08 実測: 使用率APIの返す形が変わった。**
  旧: {"limits": {"session": {"usage": 0.157}, "weekly": {"usage": 0.425}}}  → 割合で止められた
  新: {"range": "7d", "totals": {"request_count": 12682}, "buckets": [...]}  → **割合が返らない**
       （7日窓の**リクエスト件数**だけ。日ごとの内訳は buckets に入る）
そのため本ガードは両方の形を読む:
  - 割合が返る形 → これまでどおり割合で止める（--session-limit / --weekly-limit）
  - 件数しか返らない形 → **上限がAPIから分からないので、止めるには人が件数を決める**
    （--weekly-requests / --daily-requests。既定0＝止めない＝記録だけ）。
    割合が分からないまま勝手に止めると、正常な走行を人為的に止めることになるため既定は無効。
    レート制限の連続(429)による停止は llm.py 側の `AUTONOVEL_QUOTA_STREAK` が受け持つ。

主オプション:
  --session-limit 0.90   セッション上限（割合が返る形のときだけ効く）
  --weekly-limit  0.95   週次上限（同上）
  --resume-below  0.40   自動再開しきい値
  --weekly-requests 0    7日窓のリクエスト件数の上限（0=無効。件数しか返らない形で使う）
  --daily-requests  0    当日のリクエスト件数の上限（0=無効）
  --poll          120    監視間隔(秒)
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv  # venv の python-dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass


def _api_base(raw: str) -> str:
    return raw.replace("/v1", "").rstrip("/")


def fetch_usage(base_url: str, api_key: str, timeout: int = 15):
    """使用量を返す。取れなければ None。

    返り値の形:
      {"shape": "limits",   "session": float, "weekly": float,
       "raw": <生のJSON>}
      {"shape": "requests", "weekly_requests": int, "today_requests": int|None,
       "raw": <生のJSON>}
      {"shape": "unknown",  "raw": <生のJSON>}   ← 知らない形。止めずに記録だけ
    """
    url = _api_base(base_url) + "/api/usage"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[quota_guard] usage取得失敗 ({e.__class__.__name__}: {e}) — 判定スキップ", flush=True)
        return None

    # --- 形1: 割合（limits.session.usage / limits.weekly.usage） ---
    lim = d.get("limits")
    if isinstance(lim, dict) and "session" in lim and "weekly" in lim:
        try:
            return {
                "shape": "limits",
                "session": float(lim["session"]["usage"]),
                "weekly": float(lim["weekly"]["usage"]),
                "raw": d,
            }
        except (KeyError, TypeError, ValueError):
            pass

    # --- 形2: 件数（totals.request_count ＋ buckets の当日ぶん） ---
    tot = d.get("totals")
    if isinstance(tot, dict) and "request_count" in tot:
        today = None
        buckets = d.get("buckets")
        if isinstance(buckets, list) and buckets:
            last = buckets[-1]
            b_from = str(last.get("from", ""))[:10]
            if b_from == datetime.now(timezone.utc).strftime("%Y-%m-%d"):
                try:
                    today = int(last.get("request_count"))
                except (TypeError, ValueError):
                    today = None
        try:
            return {
                "shape": "requests",
                "weekly_requests": int(tot["request_count"]),
                "today_requests": today,
                "raw": d,
            }
        except (TypeError, ValueError):
            pass

    print("[quota_guard] usageの形が未知です（割合も件数も見つからない）— 判定スキップ", flush=True)
    return None


def describe(u: dict) -> str:
    if u["shape"] == "limits":
        return f"session={u['session']:.1%} weekly={u['weekly']:.1%}"
    if u["shape"] == "requests":
        t = u["today_requests"]
        return f"7日={u['weekly_requests']:,}件" + (f" 今日={t:,}件" if t is not None else "")
    return "形式不明"


def is_over(u: dict, args) -> str:
    """上限超過なら理由の文字列、そうでなければ空文字。"""
    if u["shape"] == "limits":
        if u["session"] >= args.session_limit:
            return f"session={u['session']:.1%}"
        if u["weekly"] >= args.weekly_limit:
            return f"weekly={u['weekly']:.1%}"
        return ""
    if u["shape"] == "requests":
        # 上限がAPIから分からないので、人が決めた件数があるときだけ止める（既定0=無効）
        if args.weekly_requests and u["weekly_requests"] >= args.weekly_requests:
            return f"7日={u['weekly_requests']:,}件"
        if args.daily_requests and u["today_requests"] is not None and u["today_requests"] >= args.daily_requests:
            return f"今日={u['today_requests']:,}件"
        return ""
    return ""


def is_recovered(u: dict, args) -> bool:
    if u["shape"] == "limits":
        return u["session"] < args.resume_below and u["weekly"] < args.weekly_limit
    if u["shape"] == "requests":
        # 件数は時間が経てば窓から外れて減る。上限を決めていない場合は再開条件なし。
        if not (args.weekly_requests or args.daily_requests):
            return False
        ok_w = (not args.weekly_requests) or u["weekly_requests"] < args.weekly_requests
        ok_d = ((not args.daily_requests) or u["today_requests"] is None
                or u["today_requests"] < args.daily_requests)
        return ok_w and ok_d
    return False


def log(msg: str) -> None:
    ts = time.strftime("%m-%d %H:%M:%S")
    print(f"[quota_guard {ts}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-limit", type=float, default=0.90)
    ap.add_argument("--weekly-limit", type=float, default=0.95)
    ap.add_argument("--resume-below", type=float, default=0.40)
    ap.add_argument("--weekly-requests", type=int,
                    default=int(os.environ.get("AUTONOVEL_QUOTA_WEEKLY_REQUESTS", "0") or 0))
    ap.add_argument("--daily-requests", type=int,
                    default=int(os.environ.get("AUTONOVEL_QUOTA_DAILY_REQUESTS", "0") or 0))
    ap.add_argument("--poll", type=int, default=120, help="監視間隔(秒)")
    ap.add_argument("--base-url", default=os.environ.get("AUTONOVEL_API_BASE_URL", "https://ollama.com/v1"))
    ap.add_argument("--api-key", default=os.environ.get("AUTONOVEL_API_KEY", ""))
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        ap.error("実行コマンドが空です。 -- <command> 形式で指定してください")

    base = _api_base(args.base_url)
    log(f"guard開始 session上限{args.session_limit:.0%} weekly上限{args.weekly_limit:.0%} "
        f"再開<{args.resume_below:.0%} 7日上限={args.weekly_requests or '無効'}件 "
        f"当日上限={args.daily_requests or '無効'}件 poll={args.poll}s")

    # --- 起動前ゲート ---
    while True:
        u = fetch_usage(base, args.api_key)
        if u is None:
            break  # API障害時は止めない(フェイルセーフ: 先へ進む)
        why = is_over(u, args)
        if not why:
            log(f"起動許可 {describe(u)}")
            break
        log(f"上限到達のため起動待機 {describe(u)} ({why}) poll={args.poll}s")
        time.sleep(args.poll)

    proc = subprocess.Popen(cmd)
    log(f"起動 pid={proc.pid}: {' '.join(cmd)}")

    stopped = False
    while True:
        rc = proc.poll()
        if rc is not None:
            if stopped:  # 停止中に終了した場合は再開してお掃除
                os.kill(proc.pid, signal.SIGCONT)
            log(f"終了 rc={rc}")
            if rc < 0:  # 子がシグナルで死んだ → ガード自身も同じシグナルで終了 (rc=-N を忠実に伝播)
                sig = -rc
                try:
                    signal.signal(sig, signal.SIG_DFL)
                    os.kill(os.getpid(), sig)
                except Exception:
                    return 128 + sig
            return rc
        time.sleep(max(args.poll, 5))
        u = fetch_usage(base, args.api_key)
        if u is None:
            continue
        why = is_over(u, args)
        if not stopped and why:
            log(f"AUTO-STOP ({why}): pid={proc.pid} をSIGSTOP")
            try:
                os.kill(proc.pid, signal.SIGSTOP)
                stopped = True
            except ProcessLookupError:
                pass
        elif stopped and is_recovered(u, args):
            log(f"AUTO-RESUME ({describe(u)})")
            try:
                os.kill(proc.pid, signal.SIGCONT)
                stopped = False
            except ProcessLookupError:
                pass
        elif not stopped:
            # 止めない形でも、いま何件使ったかは見えるようにしておく（桁が読める計器）
            log(f"稼働中 {describe(u)}")


if __name__ == "__main__":
    sys.exit(main())
