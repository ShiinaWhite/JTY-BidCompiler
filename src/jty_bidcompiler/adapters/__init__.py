"""素材来源适配层。

上层匹配逻辑只依赖 :class:`MaterialRepository` 抽象——**不允许**假设"素材一定
是本地文件"。当前可用的是本地 adapter；将来接 Paperless REST 只需新增一个
adapter，业务规则（rules/ 与 matchers/）一行都不用改。
"""

from .repository import (  # noqa: F401
    InMemoryMaterialRepository,
    LocalMaterialRepository,
    MaterialRepository,
    PaperlessMaterialRepository,
    open_repository,
)
