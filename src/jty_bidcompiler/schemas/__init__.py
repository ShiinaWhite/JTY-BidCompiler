"""Schema 层：JSON Schema 契约 + 极简校验器 + 数据模型辅助。

为什么不用 `jsonschema` 库：PoC 要求零新增依赖、可离线复现。这里实现的校验器
只覆盖本项目 schema 用到的关键字子集（type/required/properties/additionalProperties
/items/enum/const/pattern/minimum/maximum/minItems/$ref），足够守住契约，
并在产物不合规时给出**字段路径**级的报错。
"""

from .validate import SchemaError, load_schema, validate  # noqa: F401
