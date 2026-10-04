#!/usr/bin/env python3
"""
Unified OpenAI-compatible LLM client for autonovel.

Replaces the original Anthropic (x-api-key / v1/messages) calls with
OpenAI-compatible /v1/chat/completions calls, so the pipeline can run
against ANY OpenAI-compatible endpoint:

  - Local Ollama     : http://host.docker.internal:11434/v1
  - Cloud DeepSeek   : https://api.deepseek.com/v1
  - OpenRouter / others : https://openrouter.ai/api/v1  (etc.)

Roles (writer / judge / review) map to their own model env var, so you
can mix local + cloud models per stage. Configure in `.env`.
"""
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# 3 roles -> (env var for model, default model)
ROLES_MODEL_ENV = {
    "writer": ("AUTONOVEL_WRITER_MODEL", "qwen3.6:latest"),
    "revision": ("AUTONOVEL_REVISION_MODEL", "qwen3.6:latest"),
    "judge": ("AUTONOVEL_JUDGE_MODEL", "qwen3.6:latest"),
    "review": ("AUTONOVEL_REVIEW_MODEL", "qwen3.6:latest"),
}

# Per-role default temperature (writer creative, judge/review analytic)
ROLE_TEMPERATURE = {
    "writer": 0.8,
    "revision": 0.8,   # revision rewrites prose, so keep creative temperature
    "judge": 0.3,
    "review": 0.3,
}

# Per-role base URL env var. writer/revision default to the shared
# AUTONOVEL_API_BASE_URL; judge/review can point at a different endpoint
# (e.g. local Ollama) via their own env var, so evaluation can run on a
# local model while drafting uses a cloud model.
ROLES_BASE_URL_ENV = {
    "writer": "AUTONOVEL_API_BASE_URL",
    "revision": "AUTONOVEL_API_BASE_URL",
    "judge": "AUTONOVEL_JUDGE_BASE_URL",
    "review": "AUTONOVEL_REVIEW_BASE_URL",
}

# Fallback default base URL (local Ollama)
# Default: local Ollama on the same machine. Inside Docker/containers,
# point at the host via: AUTONOVEL_API_BASE_URL=http://host.docker.internal:11434/v1
DEFAULT_BASE_URL = "http://localhost:11434/v1"

# --- 長丁場の運転を守る（一時的な失敗で捨てない）-----------------------------
# 上限（429）・混雑（500/502/503/504/529）・通信断・タイムアウトは「一時的な失敗」と
# みなし、指数バックオフで待ってやり直す。数時間の放置運転が4時間目で死ぬのを防ぐ。
#   AUTONOVEL_LLM_RETRIES : 1回の呼び出しの最大試行回数（既定 5）
#   AUTONOVEL_LLM_BACKOFF : 最初に待つ秒数（既定 15 → 15/30/60/120… 最大600秒）
# Retry-After ヘッダがあればそちらを優先する。
RETRY_STATUS = (429, 500, 502, 503, 504, 529)
LLM_RETRIES = max(1, int(os.environ.get("AUTONOVEL_LLM_RETRIES", "5")))
LLM_BACKOFF = float(os.environ.get("AUTONOVEL_LLM_BACKOFF", "15"))

# 消費量の記録: 1呼び出し1行を TSV に追記（role/model/prompt/completion/total）。
# 試走の前に「24章でどれだけ使うか」を見積もるための実測用。AUTONOVEL_USAGE_LOG=0 で無効。
USAGE_LOG = os.environ.get("AUTONOVEL_USAGE_LOG",
                           str(BASE_DIR / "edit_logs" / "llm_usage.tsv"))


class _Transient(Exception):
    """再試行してよい失敗。"""

    def __init__(self, msg: str, wait: float = 0.0):
        super().__init__(msg)
        self.wait = wait


def _wait_seconds(attempt: int, resp=None) -> float:
    """待ち時間。Retry-After があれば優先、なければ指数バックオフ（最大600秒）。"""
    if resp is not None:
        ra = resp.headers.get("retry-after")
        if ra:
            try:
                return max(1.0, float(ra))
            except ValueError:
                pass
    return min(LLM_BACKOFF * (2 ** (attempt - 1)), 600.0)


def _post(url: str, headers: dict, payload: dict):
    """一時的な失敗を待ってやり直す POST。上限・混雑・通信断で即死させない。"""
    last = None
    for attempt in range(1, LLM_RETRIES + 1):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=900)
            if resp.status_code in RETRY_STATUS:
                raise _Transient(f"HTTP {resp.status_code}",
                                 _wait_seconds(attempt, resp))
            resp.raise_for_status()
            return resp
        except _Transient as e:
            last, wait = e, e.wait
        except httpx.HTTPError as e:
            last, wait = e, _wait_seconds(attempt)
        if attempt >= LLM_RETRIES:
            break
        sys.stderr.write(f"[llm] 一時的な失敗（{last}）→ {wait:.0f}秒待って再試行 "
                         f"{attempt}/{LLM_RETRIES - 1}\n")
        sys.stderr.flush()
        time.sleep(wait)
    raise last if last is not None else RuntimeError(f"POST failed: {url}")


def _log_usage(role: str, model: str, usage: dict) -> None:
    """消費量を1行追記する（失敗しても本筋を止めない）。"""
    if not USAGE_LOG or USAGE_LOG == "0" or not usage:
        return
    try:
        p = Path(USAGE_LOG)
        p.parent.mkdir(parents=True, exist_ok=True)
        import datetime as _dt
        new = not p.exists()
        with p.open("a", encoding="utf-8") as f:
            if new:
                f.write("time\trole\tmodel\tprompt\tcompletion\ttotal\n")
            f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')}\t{role}\t{model}\t"
                    f"{usage.get('prompt_tokens') or 0}\t{usage.get('completion_tokens') or 0}\t"
                    f"{usage.get('total_tokens') or 0}\n")
    except Exception as e:      # noqa: BLE001 — 記録の失敗で本文生成を止めない
        sys.stderr.write(f"[llm] usage 記録に失敗: {e}\n")


def _resolve(role: str):
    """Return (model, base_url, api_key) for the given role."""
    if role not in ROLES_MODEL_ENV:
        raise ValueError(f"Unknown role: {role!r}. Must be writer/revision/judge/review.")
    env_model, default_model = ROLES_MODEL_ENV[role]
    model = os.environ.get(env_model, default_model)
    env_base = ROLES_BASE_URL_ENV.get(role, "AUTONOVEL_API_BASE_URL")
    base = os.environ.get(env_base, DEFAULT_BASE_URL)
    key = os.environ.get("AUTONOVEL_API_KEY", "")
    return model, base.rstrip("/"), key


# 評価役（judge/review）の回答言語: 作品は日本語なので、指摘・理由も日本語で書かせる。
# 生成側（gen_*.py / draft_*.py）は既に「出力は必ず日本語で書いてください」を持っているが、
# 評価側は英語のままだった（2026-10-03 Hisa指摘: 「日本語指定しながら英語で返って来たなら切り捨ててよい」）。
# JSONのキーだけ英語のままにする。
JUDGE_LANGUAGE_NOTE = (
    "The novel under review is written in JAPANESE. Write **every value** in the JSON "
    "(gap / fix / note / reasons / weakest_dimension / top_3_improvements / free text) "
    "in JAPANESE. Keep only the JSON keys in English. Never answer in English."
)


def call(
    role: str,
    prompt: str,
    system: str = "",
    max_tokens: int = 16000,
    temperature: float | None = None,
) -> str:
    """
    Send a single prompt to the configured model for `role` and return text.

    Args:
        role: "writer" | "judge" | "review" — selects model env + default temp.
        prompt: the user message.
        system: optional system prompt (ignored by some local models).
        max_tokens: output cap.
        temperature: override the role default.
    """
    model, base, key = _resolve(role)
    if temperature is None:
        temperature = ROLE_TEMPERATURE.get(role, 0.7)

    if role in ("judge", "review"):
        system = f"{system}\n{JUDGE_LANGUAGE_NOTE}".strip() if system else JUDGE_LANGUAGE_NOTE

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["messages"].insert(0, {"role": "system", "content": system})
    # Hybrid "thinking" models (e.g. glm-5.3) burn the completion budget on
    # reasoning and return empty prose. Disable thinking when requested.
    # NOTE (2026-09-17): deepseek-v4.1-flash IGNORES think:False — for it use
    # AUTONOVEL_REASONING_EFFORT=none (measured: reasoning 0字, prose returned).
    if os.environ.get("AUTONOVEL_DISABLE_THINKING", "") == "1":
        payload["think"] = False
    reasoning_effort = os.environ.get("AUTONOVEL_REASONING_EFFORT", "").strip()
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort

    url = f"{base}/chat/completions"
    resp = _post(url, headers, payload)
    data = resp.json()
    usage = data.get("usage") or {}
    if usage:
        sys.stderr.write(
            f"[llm] role={role} model={model} prompt_tokens={usage.get('prompt_tokens')} "
            f"completion_tokens={usage.get('completion_tokens')} total={usage.get('total_tokens')}\n")
        _log_usage(role, model, usage)
    return data["choices"][0]["message"]["content"]


def call_writer(prompt, system="", max_tokens=16000, temperature=None):
    return call("writer", prompt, system=system, max_tokens=max_tokens, temperature=temperature)


def call_revision(prompt, system="", max_tokens=16000, temperature=None):
    return call("revision", prompt, system=system, max_tokens=max_tokens, temperature=temperature)


def call_judge(prompt, system="", max_tokens=8000, temperature=None):
    return call("judge", prompt, system=system, max_tokens=max_tokens, temperature=temperature)


def call_review(prompt, system="", max_tokens=8000, temperature=None):
    return call("review", prompt, system=system, max_tokens=max_tokens, temperature=temperature)
