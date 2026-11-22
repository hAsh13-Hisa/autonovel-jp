#!/usr/bin/env python3
"""作品ファイルへの書き込みを安全にする小さなヘルパ。

なぜ必要か（2026-11-19 のコードレビュー指摘）:
`gen_world.py` / `gen_characters.py` / `gen_canon.py` / `gen_outline.py` は
LLM の出力をそのまま `world.md` などへ上書きしていた。空応答や短すぎる出力、
書き込み途中の例外で、作りためた設計文書が消える（バックアップも無い）。
→ 既存ファイルは `.bak` に退避し、下限未満の出力は書かない。
"""
from __future__ import annotations

import shutil
from pathlib import Path


def safe_write(path, text, *, min_chars: int = 500, backup: bool = True) -> Path:
    """`path` へ書く。既存があれば `.bak` に退避し、短すぎる出力は書かない。

    下限未満なら ValueError を投げて**既存ファイルをそのまま残す**（無言で
    壊さない）。改行は末尾に1つだけ付ける（従来の `result.strip() + "\\n"` と同じ）。
    """
    path = Path(path)
    body = (text or "").strip()
    if len(body) < min_chars:
        raise ValueError(
            f"{path.name}: 出力が {len(body)} 字しかない（下限 {min_chars} 字）。"
            "既存ファイルを守るため書き込みません。")
    if backup and path.exists() and path.stat().st_size > 0:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n", encoding="utf-8")
    return path
