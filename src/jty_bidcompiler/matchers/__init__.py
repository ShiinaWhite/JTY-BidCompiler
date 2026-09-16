"""匹配层：要求 × 素材 → 判定。

两条铁律（来自人工核验口径，写死在代码里而不是只写在文档里）：

1. **判定词封闭**：只允许 明确满足 / 明确不满足 / 证据不足（不能认定）。
   出现"可能满足""大概率满足"这类词直接判为不合格输出。
2. **没有引用的判定无效**：每条结论都必须挂上原文引用（evidence_chain），
   禁止"AI认为满足"这种无证据结论。
"""

from .performance import FiveElementEvaluator, PerformanceVerdict, ElementResult  # noqa: F401
from .engine import RequirementMatcher  # noqa: F401
