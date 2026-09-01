#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quota_guard.py — ollama.com クラウド使用率による汎用オートストップ/オートリジュース。

使い方:
  quota_guard.py [options] -- <command> [args...]

- 起動前: session/weekly が各上限未満になるまで待機 (pre-flight gate)
- 実行中: poll ごとに使用率を確認し、上限超過で子プロセスへ SIGSTOP (自動停止)
- 使用率が resume 閾値未満まで回復したら SIGCONT (自動再開)
- usage API: {base}/api/usage (Bearer トークン). base 末尾の /v1 は自動で外す

主オプション:
  --session-limit 0.90   セッション上限 (これ以上で開始待ち/自動停止)
  --weekly-limit  0.95   週次上限
  --resume-below  0.40   自動再開しきい値 (session がここ未満まで回復したら再開)
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
from pathlib import Path

try:
    from dotenv import load_dotenv  # venv の python-dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass


def _api_base(raw: str) -> str:
    return raw.replace("/v1", "").rstrip("/")


def fetch_usage(base_url: str, api_key: str, timeout: int = 15):
    """{'session': float, 'weekly': float} を返す。取得失敗は None。"""
    url = _api_base(base_url) + "/api/usage"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        lim = d["limits"]
        return {
            "session": float(lim["session"]["usage"]),
            "weekly": float(lim["weekly"]["usage"]),
        }
    except Exception as e:
        print(f"[quota_guard] usage取得失敗 ({e.__class__.__name__}: {e}) — 判定スキップ", flush=True)
        return None


def log(msg: str) -> None:
    ts = time.strftime("%m-%d %H:%M:%S")
    print(f"[quota_guard {ts}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-limit", type=float, default=0.90)
    ap.add_argument("--weekly-limit", type=float, default=0.95)
    ap.add_argument("--resume-below", type=float, default=0.40)
    ap.add_argument("--poll", type=int, default=120, help="監視間隔(秒)")
    ap.add_argument("--base-url", default=os.environ.get("AUTONOVEL_API_BASE_URL", "https://ollama.com/v1"))
    ap.add_argument("--api-key", default=os.environ.get("AUTONOVEL_API_KEY", ""))
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        ap.error("実行コマンドが空です。 -- <command> 形式で指定してください")

    base = _api_base(args.base_url)
    log(f"guard開始 session上限{args.session_limit:.0%} weekly上限{args.weekly_limit:.0%} 再開<{args.resume_below:.0%} poll={args.poll}s")

    # --- 起動前ゲート ---
    while True:
        u = fetch_usage(base, args.api_key)
        if u is None:
            break  # API障害時は止めない(フェイルセーフ: 先へ進む)
        if u["session"] < args.session_limit and u["weekly"] < args.weekly_limit:
            log(f"起動許可 session={u['session']:.1%} weekly={u['weekly']:.1%}")
            break
        log(f"上限到達のため起動待機 session={u['session']:.1%} weekly={u['weekly']:.1%} (poll={args.poll}s)")
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
        over_s = u["session"] >= args.session_limit
        over_w = u["weekly"] >= args.weekly_limit
        if not stopped and (over_s or over_w):
            why = "session" if over_s else "weekly"
            log(f"AUTO-STOP ({why}={u[why]:.1%}): pid={proc.pid} をSIGSTOP")
            try:
                os.kill(proc.pid, signal.SIGSTOP)
                stopped = True
            except ProcessLookupError:
                pass
        elif stopped and u["session"] < args.resume_below and u["weekly"] < args.weekly_limit:
            log(f"AUTO-RESUME (session={u['session']:.1%} weekly={u['weekly']:.1%})")
            try:
                os.kill(proc.pid, signal.SIGCONT)
                stopped = False
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    sys.exit(main())