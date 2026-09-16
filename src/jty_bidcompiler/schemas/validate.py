"""极简 JSON Schema 校验器（零依赖）。

支持的关键字：$ref（仅本地 #/$defs/...）、type、required、properties、
additionalProperties（bool 或 schema）、items、enum、const、pattern、
minimum、maximum、minItems、anyOf、not。

不支持的关键字会被静默忽略——这是刻意的：本校验器用于守住"证据字段不许丢"
这类结构性契约，不打算复刻完整 JSON Schema 语义。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class SchemaError(AssertionError):
    """校验失败。message 里带字段路径，便于直接定位产物问题。"""

    def __init__(self, path: str, message: str):
        self.path = path
        self.message = message
        super().__init__(f"{path or '<root>'}: {message}")


_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def load_schema(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(instance: Any, schema: dict, *, path: str = "", root: dict | None = None) -> None:
    """校验 instance；不合规抛 :class:`SchemaError`。"""
    root = root if root is not None else schema

    if "$ref" in schema:
        target = _resolve(schema["$ref"], root)
        return validate(instance, target, path=path, root=root)

    if "not" in schema:
        try:
            validate(instance, schema["not"], path=path, root=root)
        except SchemaError:
            pass
        else:
            raise SchemaError(path, "命中了 not 分支（本应不匹配）")

    if "anyOf" in schema:
        errors = []
        for sub in schema["anyOf"]:
            try:
                validate(instance, sub, path=path, root=root)
                break
            except SchemaError as exc:  # 记录最像样的那个错误
                errors.append(str(exc))
        else:
            raise SchemaError(path, f"不满足 anyOf 任一分支: {errors}")

    if "const" in schema and instance != schema["const"]:
        raise SchemaError(path, f"必须等于常量 {schema['const']!r}，实际 {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaError(path, f"取值必须在 {schema['enum']!r} 内，实际 {instance!r}")

    if "type" in schema:
        expected = schema["type"]
        names = expected if isinstance(expected, list) else [expected]
        ok = False
        for name in names:
            py = _TYPES.get(name)
            if py is None:
                continue
            if py is int and isinstance(instance, bool):
                continue  # bool 是 int 的子类，语义上不算 integer
            if isinstance(instance, py):
                ok = True
                break
        if not ok:
            raise SchemaError(path, f"类型应为 {names}，实际 {type(instance).__name__}")

    if isinstance(instance, str):
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            raise SchemaError(path, f"不匹配 pattern {schema['pattern']!r}: {instance!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaError(path, f"小于最小值 {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaError(path, f"大于最大值 {schema['maximum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                raise SchemaError(path, f"缺少必填字段 {key!r}")
        props = schema.get("properties", {})
        ap = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child = f"{path}.{key}" if path else key
            if key in props:
                validate(value, props[key], path=child, root=root)
            elif ap is False:
                raise SchemaError(path, f"不允许出现额外字段 {key!r}")
            elif isinstance(ap, dict):
                validate(value, ap, path=child, root=root)

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaError(path, f"元素数少于 {schema['minItems']}")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, value in enumerate(instance):
                validate(value, items, path=f"{path}[{i}]", root=root)


def _resolve(ref: str, root: dict) -> dict:
    if not ref.startswith("#/"):
        raise SchemaError("", f"不支持的 $ref: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def check_file(instance_path: str | Path, schema_path: str | Path) -> list[str]:
    """校验磁盘上的 JSON 文件，返回错误字符串列表（空列表 = 通过）。"""
    data = json.loads(Path(instance_path).read_text(encoding="utf-8"))
    schema = load_schema(schema_path)
    try:
        validate(data, schema)
    except SchemaError as exc:
        return [str(exc)]
    return []
