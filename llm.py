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
    resp = httpx.post(url, headers=headers, json=payload, timeout=900)
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage") or {}
    import sys as _sys
    if usage:
        _sys.stderr.write(
            f"[llm] role={role} model={model} prompt_tokens={usage.get('prompt_tokens')} "
            f"completion_tokens={usage.get('completion_tokens')} total={usage.get('total_tokens')}\n")
    return data["choices"][0]["message"]["content"]


def call_writer(prompt, system="", max_tokens=16000, temperature=None):
    return call("writer", prompt, system=system, max_tokens=max_tokens, temperature=temperature)


def call_revision(prompt, system="", max_tokens=16000, temperature=None):
    return call("revision", prompt, system=system, max_tokens=max_tokens, temperature=temperature)


def call_judge(prompt, system="", max_tokens=8000, temperature=None):
    return call("judge", prompt, system=system, max_tokens=max_tokens, temperature=temperature)


def call_review(prompt, system="", max_tokens=8000, temperature=None):
    return call("review", prompt, system=system, max_tokens=max_tokens, temperature=temperature)
