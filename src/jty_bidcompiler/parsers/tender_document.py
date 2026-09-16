"""招标文件加载：PDF 或已提取的文本，统一成按页可检索的 :class:`TenderText`。

优先直接读 PDF（pymupdf 文本层），因为那是**原件**；只有当 PDF 没有文本层
（纯扫描件）或离线复现需要时，才回落到带页标记的文本文件。

带页标记的文本格式（本仓库历史产物即此格式）::

    ===== PDF第3页 =====
    ...正文...
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..io.textnorm import PageText, TenderText

_PAGE_MARK = re.compile(r"^=====\s*PDF第(\d+)页\s*=====\s*$", re.M)


def sha256_file(path: str | Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def load_pdf(path: str | Path) -> TenderText:
    import pymupdf  # 延迟导入：只有真的要解析 PDF 时才需要

    doc = pymupdf.open(str(path))
    pages = [PageText(page=i + 1, raw=page.get_text()) for i, page in enumerate(doc)]
    doc.close()
    return TenderText(pages)


def load_marked_text(path: str | Path) -> TenderText:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    marks = list(_PAGE_MARK.finditer(raw))
    if not marks:
        # 没有页标记：整文件当一页，仍可用，只是溯源粒度变粗
        return TenderText([PageText(page=1, raw=raw)])

    pages: list[PageText] = []
    for i, mark in enumerate(marks):
        start = mark.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(raw)
        pages.append(PageText(page=int(mark.group(1)), raw=raw[start:end]))
    return TenderText(pages)


def load_tender(path: str | Path) -> tuple[TenderText, dict]:
    """加载招标文件，返回 (文本视图, 元信息)。元信息含 sha256 与真实页数。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"招标文件不存在: {p}")
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        text = load_pdf(p)
        source_kind = "pdf_text_layer"
    elif suffix in (".txt", ".md"):
        text = load_marked_text(p)
        source_kind = "derived_text"
    else:
        raise ValueError(f"暂不支持的招标文件格式: {suffix}")

    meta = {
        "path": str(p),
        "sha256": sha256_file(p),
        "pages": len(text),
        "source_kind": source_kind,
        "title": _guess_title(text),
    }
    return text, meta


def _guess_title(text: TenderText) -> str:
    if not len(text):
        return ""
    for line in text.page_text(1).splitlines():
        line = line.strip()
        if len(line) >= 8 and line.endswith(("项目", "服务", "工程", "标段")):
            return line
    return text.page_text(1).strip().splitlines()[0] if text.page_text(1).strip() else ""
