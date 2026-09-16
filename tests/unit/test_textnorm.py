"""文本规整层测试：扁平检索必须能还原出原文。"""

from __future__ import annotations

import unittest

from tests.support import ROOT  # noqa: F401  （触发 sys.path 注入）
from jty_bidcompiler.io.textnorm import PageText, fold


class TestFold(unittest.TestCase):
    def test_strips_whitespace_and_folds_punctuation(self):
        self.assertEqual(fold("2026 年09 月18 日11:00"), "2026年09月18日11:00")
        self.assertEqual(fold("投标人须具有（承修、承试）"), "投标人须具有(承修,承试)")
        self.assertEqual(fold("ＡＢＣ１２３"), "ABC123")

    def test_empty(self):
        self.assertEqual(fold("  \n\t "), "")


class TestPageText(unittest.TestCase):
    def setUp(self):
        self.page = PageText(page=7, raw="投标保证金的金额：人民币2 万元\n下一行内容")

    def test_find_returns_raw_text(self):
        hit = self.page.find("人民币2万元")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.text, "人民币2 万元")  # 取证回原文，带原始空格

    def test_line_of_gives_readable_quote(self):
        hit = self.page.find("人民币2万元")
        self.assertEqual(self.page.line_of(hit), "投标保证金的金额：人民币2 万元")

    def test_search_all_finds_every_occurrence(self):
        page = PageText(page=1, raw="开关柜12台与开关柜16台")
        self.assertEqual([h.text for h in page.find_all("开关柜")], ["开关柜", "开关柜"])

    def test_missing_needle(self):
        self.assertIsNone(self.page.find("不存在的词"))


if __name__ == "__main__":
    unittest.main()
