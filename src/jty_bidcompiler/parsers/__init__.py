"""解析层：把招标文件（原件）变成结构化中间产物。

原则：
* 解析结果必须能回到**页码 + 原文**，因此所有解析都走 :class:`TenderText`；
* 解析器只产出"没判过的"状态（``NOT_EVALUATED``），判定权归 matchers；
* 解析器不做语义推断——抽不到就写 null 并说明原因（不许猜值）。
"""

from .tender_document import load_tender, sha256_file  # noqa: F401
