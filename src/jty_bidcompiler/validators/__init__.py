"""校验层：投标文件自动 QA（MVP-5）。

每条检查项只允许三种结论：``PASS`` / ``FAIL`` / ``WAIT_COMPANY``。
- FAIL        = 投标文件里确实有毛病，必须改（能定位到行/原文）；
- WAIT_COMPANY= 缺口在真实材料或公司决策，编译器改不了；
- PASS        = 已确认没问题。

这个三分法是刻意的：如果只有"通过/不通过"，公司会把"还没交保证金"和
"文件里写错了旧项目名"当成同一类问题，前者等公司、后者要立刻改。
"""

from .bid_lint import BidLinter, render_markdown  # noqa: F401
