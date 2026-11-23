"""gen_characters が章数に合わせて配役数を変えることの確認（LLM も書き込みもしない）。"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import gen_characters as gc  # noqa: E402


def build_prompt(chapters: str) -> str:
    """main() を走らせてプロンプトだけ取り出す（LLM と保存は差し替え）。"""
    captured = {}

    def fake_writer(prompt, **kwargs):
        captured["prompt"] = prompt
        return "# キャラクター登録簿\n\n## ユウト\n- 17歳・主人公\n"

    env = {k: v for k, v in os.environ.items() if k != "AUTONOVEL_CHAPTERS"}
    env["AUTONOVEL_CHAPTERS"] = chapters
    with mock.patch.dict(os.environ, env, clear=True), \
         mock.patch.object(gc.llm, "call_writer", fake_writer), \
         mock.patch.object(gc.safeio, "safe_write", lambda *a, **k: None):
        gc.main()
    return captured.get("prompt", "")


class CastSizeTest(unittest.TestCase):
    def test_short_work_limits_cast_to_three(self):
        prompt = build_prompt("2")
        self.assertIn("3人まで", prompt)
        self.assertIn("登録しない", prompt)
        self.assertNotIn("敵対者／対立する人物", prompt)
        self.assertNotIn("少なくとも1〜2人の追加キャラクター", prompt)

    def test_full_length_keeps_six_person_registry(self):
        prompt = build_prompt("24")
        self.assertIn("敵対者／対立する人物", prompt)
        self.assertIn("少なくとも1〜2人の追加キャラクター", prompt)
        self.assertNotIn("3人まで", prompt)

    def test_invalid_chapter_count_falls_back_to_full_cast(self):
        prompt = build_prompt("abc")
        self.assertIn("敵対者／対立する人物", prompt)


if __name__ == "__main__":
    unittest.main()
