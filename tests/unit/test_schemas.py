"""Schema 校验器与产物契约测试。

这些测试保护的是"证据不许丢"这条红线：如果有人图省事把 source_page 删掉，
这里必须红。
"""

from __future__ import annotations

import copy
import unittest

from tests.support import ROOT, load_json
from jty_bidcompiler.schemas import load_schema, validate, SchemaError

SCHEMAS = ROOT / "schemas"


class TestValidator(unittest.TestCase):
    def test_required_field_missing_raises(self):
        schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
        with self.assertRaises(SchemaError) as ctx:
            validate({"b": 1}, schema)
        self.assertIn("a", str(ctx.exception))

    def test_wrong_type_raises(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        with self.assertRaises(SchemaError):
            validate({"n": "12"}, schema)

    def test_bool_is_not_integer(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        with self.assertRaises(SchemaError):
            validate({"n": True}, schema)

    def test_nullable_type(self):
        schema = {"type": "object", "properties": {"n": {"type": ["string", "null"]}}}
        validate({"n": None}, schema)
        validate({"n": "x"}, schema)

    def test_additional_properties_false(self):
        schema = {"type": "object", "properties": {"a": {}}, "additionalProperties": False}
        with self.assertRaises(SchemaError):
            validate({"a": 1, "b": 2}, schema)

    def test_ref_resolution_and_nested_path(self):
        schema = load_schema(SCHEMAS / "project.schema.json")
        good = {
            "schema_version": "1.0",
            "generated_by": {"tool": "t", "tool_version": "1", "generated_at": "now"},
            "source_document": {"path": "p", "sha256": "0" * 64},
            "fields": {
                name: {"value": None, "source": None, "source_page": None,
                       "confidence": 0.0, "reason": "未找到"}
                for name in (
                    "project_name", "project_number", "tenderer", "agency", "location",
                    "deadline", "service_period", "warranty", "bid_security",
                    "consortium_allowed", "platform", "submission_format",
                )
            },
        }
        validate(good, schema)

        bad = copy.deepcopy(good)
        bad["fields"]["deadline"] = {"value": "x", "source_page": 3, "confidence": 0.9}
        with self.assertRaises(SchemaError) as ctx:
            validate(bad, schema)
        self.assertIn("deadline", str(ctx.exception))

    def test_project_schema_rejects_bare_value_without_evidence(self):
        """字段只存最终值、不带证据位置 → 必须被拒。"""
        schema = load_schema(SCHEMAS / "project.schema.json")
        doc = {
            "schema_version": "1.0",
            "generated_by": {"tool": "t", "tool_version": "1", "generated_at": "now"},
            "source_document": {"path": "p", "sha256": "0" * 64},
            "fields": {"deadline": "2026-09-18"},
        }
        with self.assertRaises(SchemaError):
            validate(doc, schema)

    def test_all_shipped_schemas_are_valid_json(self):
        for path in SCHEMAS.glob("*.schema.json"):
            with self.subTest(schema=path.name):
                self.assertIsInstance(load_json(path), dict)


if __name__ == "__main__":
    unittest.main()
