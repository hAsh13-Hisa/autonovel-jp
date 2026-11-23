#!/usr/bin/env python3
"""shellcmd — コマンド文字列を shell を介さずに実行する。

2026-11-23: 11/19 コードレビューの「高」指摘（`shell=True` の全面廃止）に対応。
呼び出し側は今までどおり文字列でコマンドを渡せるが、実行時に
`shlex.split` で引数リストへ分けて `shell=False` で走らせる。

シェル機能（パイプ・リダイレクト・;・&&・$()・バッククォート）は**実行しない**。
黙って別の意味で走ると事故になるので、検出したら `ValueError` で止める。
引用符の中の記号（`grep 'a\\|b'` の `|` など）は対象外。

使い方:
    from shellcmd import run
    run('git status --short')
    run(["git", "show", "HEAD:outline.md"])
    run('AUTONOVEL_X=1 prog --flag "a b"')   # 前置の VAR=値 は env として渡す
"""
import os
import re
import shlex
import subprocess

# シェル機能として扱う記号（引用符の外にあるものだけ拒否する）
_META_CHARS = set("|<>;&`")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def _find_unquoted_meta(cmd: str) -> str:
    """引用符の外にあるシェル記号を返す（無ければ空文字）。"""
    quote = ""
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "\\":
            i += 2
            continue
        elif ch in _META_CHARS:
            return ch
        elif ch == "$" and i + 1 < len(cmd) and cmd[i + 1] in "({":
            return "$"
        i += 1
    return ""


def split_command(cmd) -> tuple:
    """コマンド文字列（または引数リスト）→ (argv, 追加の環境変数)。

    シェル機能を含む文字列は ValueError。空のコマンドも ValueError。
    """
    if isinstance(cmd, (list, tuple)):
        argv = [str(a) for a in cmd]
        if not argv:
            raise ValueError("空のコマンド")
        return argv, {}

    meta = _find_unquoted_meta(cmd)
    if meta:
        raise ValueError(
            "シェル機能（" + meta + "）を含むコマンドは実行できません。"
            "引数リストで渡すか、Python 側で処理してください: " + cmd)

    argv = shlex.split(cmd)
    env = {}
    while argv and _ASSIGN.match(argv[0]):
        key, _, value = argv[0].partition("=")
        env[key] = value
        argv = argv[1:]
    if not argv:
        raise ValueError("空のコマンド: " + cmd)
    return argv, env


def run(cmd, timeout: int = 600, cwd=None, check: bool = False):
    """shell を介さずに実行する（戻り値は subprocess.CompletedProcess）。"""
    argv, extra_env = split_command(cmd)
    env = None
    if extra_env:
        env = dict(os.environ)
        env.update(extra_env)
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout,
        cwd=cwd, env=env, check=check,
    )
