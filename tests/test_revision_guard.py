"""2026-11-23 に追加した改訂の採否ロジックのテスト（外部依存なし・LLM を呼ばない）。

実行: .venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import run_pipeline as rp  # noqa: E402
import gen_outline_part2 as op  # noqa: E402


GATE_SAMPLE = """== ch_01.md ==
  [OK] 文字数: 9581 (3000-99999)
  [OK] セリフ比(文字ベース): 70.0% (>=50)
  [OK] 最小窓500字(文字ベース): 54% (>=30)
  [FAIL] 重複(12字以上x2): 35 (=0)
  [FAIL] ……/——/英語: 39/0/0 (<=2/<=1/0)
  [OK] 空の台詞「」: 0 (=0)
  [FAIL] 同じ行が2回以上(12字以上): 44 (=0)
  [OK] 文頭の読点・連続句読点: 0 (=0)
  [FAIL] 比喩密度: 6.1/1000字 (<=3.0)・まるで+みたい=15 (<=4) — 内訳 まるで8/よう系43/みたい7
  [OK] 括弧の対応(「」『』（）の過不足): 対応OK (=0)
"""


class ParseGateMetricsTest(unittest.TestCase):
    def test_parses_every_gate_item(self):
        m = rp.parse_gate_metrics(GATE_SAMPLE)
        self.assertEqual(m["chars"], 9581)
        self.assertEqual(m["dup"], 35)
        self.assertEqual((m["dots"], m["dash"], m["eng"]), (39, 0, 0))
        self.assertEqual(m["dup_line"], 44)
        self.assertAlmostEqual(m["metaphor"], 6.1)

    def test_empty_output_is_empty_dict(self):
        self.assertEqual(rp.parse_gate_metrics(""), {})
        self.assertEqual(rp.parse_gate_metrics(None), {})


class ImprovementVerdictTest(unittest.TestCase):
    OLD = {"chars": 9581, "dup": 35, "dup_line": 44, "dots": 39, "metaphor": 6.1}

    def test_adopts_when_no_metric_worse_and_some_better(self):
        new = {"chars": 8390, "dup": 34, "dup_line": 42, "dots": 35, "metaphor": 5.5}
        adopt, why = rp.improvement_verdict(self.OLD, new)
        self.assertTrue(adopt, why)

    def test_rejects_when_chapter_shrank_below_ratio(self):
        new = {"chars": 5054, "dup": 5, "dup_line": 10, "dots": 19, "metaphor": 5.7}
        adopt, why = rp.improvement_verdict(self.OLD, new)
        self.assertFalse(adopt)
        self.assertIn("80%", why)

    def test_rejects_when_any_metric_worse(self):
        new = {"chars": 9581, "dup": 30, "dup_line": 44, "dots": 50, "metaphor": 6.1}
        adopt, why = rp.improvement_verdict(self.OLD, new)
        self.assertFalse(adopt)
        self.assertIn("悪化", why)

    def test_rejects_when_nothing_changed(self):
        adopt, why = rp.improvement_verdict(self.OLD, dict(self.OLD))
        self.assertFalse(adopt)
        self.assertIn("改善なし", why)

    def test_rejects_when_metrics_missing(self):
        adopt, _ = rp.improvement_verdict({}, dict(self.OLD))
        self.assertFalse(adopt)


class ClampChaptersTest(unittest.TestCase):
    SAMPLE = (
        "## 章ごとのアウトライン（第2章〜第20章）\n\n"
        "### 第2章：A\n本文2\n"
        "### 第3章：B\n本文3\n"
        "### 第20章：C\n本文20\n\n"
        "## 伏線台帳\n| 糸 | 植え込み |\n"
    )

    def test_drops_chapters_beyond_limit_but_keeps_ledger(self):
        out = op.clamp_chapters(self.SAMPLE, 2)
        self.assertIn("### 第2章", out)
        self.assertNotIn("### 第3章", out)
        self.assertNotIn("### 第20章", out)
        self.assertIn("## 伏線台帳", out)

    def test_keeps_everything_when_within_limit(self):
        out = op.clamp_chapters(self.SAMPLE, 20)
        self.assertIn("### 第20章", out)
        self.assertIn("## 伏線台帳", out)


if __name__ == "__main__":
    unittest.main()
