"""测试公共支撑：定位仓库根、加载 fixture、跳过条件。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

#: 招标文件 fixture 路径由 config 决定；没有配置时相关用例自动跳过。
def _tender_path() -> Path:
    cfg = ROOT / "config" / "sample_a.json"
    if cfg.exists():
        rel = json.loads(cfg.read_text(encoding="utf-8"))["inputs"]["tender"]
        return ROOT / rel
    return ROOT / "fixtures" / "sample_a" / "source" / "tender" / "tender.pdf"


TENDER_PDF = _tender_path()
MATERIALS = ROOT / "fixtures" / "sample_a" / "materials" / "materials.sample_a.json"
def _bid_text_path() -> Path:
    cfg = ROOT / "config" / "sample_a.json"
    if cfg.exists():
        rel = json.loads(cfg.read_text(encoding="utf-8"))["inputs"].get("bid_text")
        if rel:
            return ROOT / rel
    return ROOT / "fixtures" / "sample_a" / "source" / "sample_a" / "bid_text.derived.txt"


BID_TEXT = _bid_text_path()
PROFILE = ROOT / "config" / "sample_a.json"
PERF_RULES = ROOT / "rules" / "performance_five_elements.json"
QUAL_RULES = ROOT / "rules" / "qualification_rules.json"
LINT_RULES = ROOT / "rules" / "lint_rules.json"


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_profile(name: str) -> dict:
    """读取 config/<name>.json（真实项目配置，本地存在才可测）。"""
    p = ROOT / "config" / f"{name}.json"
    if not p.exists():
        raise unittest.SkipTest(f"缺少项目配置 config/{name}.json（真实项目配置不入库）")
    return load_json(p)


def bidder_of(name: str) -> str:
    return load_profile(name)["bidder"]


def requires_config(name: str) -> unittest.skipIf:
    return unittest.skipIf(not (ROOT / "config" / f"{name}.json").exists(),
                           f"缺少 config/{name}.json")


def load_materials() -> list[dict]:
    return load_json(MATERIALS)["materials"]


def material(mid: str) -> dict:
    for m in load_materials():
        if m["material_id"] == mid:
            return m
    raise KeyError(mid)


def requires_tender() -> unittest.skipIf:
    return unittest.skipIf(not TENDER_PDF.exists(),
                           f"缺少招标文件 fixture：{TENDER_PDF}")


_tender_cache: dict = {}


def tender_text():
    """加载并缓存招标文件文本（PDF 解析较慢，多个测试共用）。"""
    if "text" not in _tender_cache:
        from jty_bidcompiler.parsers.tender_document import load_tender

        text, meta = load_tender(TENDER_PDF)
        _tender_cache["text"] = text
        _tender_cache["meta"] = meta
    return _tender_cache["text"], _tender_cache["meta"]
