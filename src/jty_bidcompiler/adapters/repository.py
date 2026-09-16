"""MaterialRepository 抽象与两种实现。

契约（上层只允许依赖这里的能力）：
* ``describe()``  —— 来源描述（写进产物，便于复现）
* ``list_materials()`` / ``by_category()`` / ``get()``
* ``evidence_of(material_id)`` —— 该素材已核验的原文片段（五要素判定的唯一输入）
* ``asset_handle(material_id)`` —— 取素材本体（本地=路径；Paperless=下载 URL/字节流）

刻意**不做**的事：不解析 PDF/OCR、不猜字段。素材本体的读取（例如扫描件 OCR）
属于 adapter 的后续职责，见 docs/架构设计.md 的"素材获取"一节。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional


class MaterialRepository(ABC):
    """素材仓库抽象。"""

    #: 供产物记录来源
    kind: str = "abstract"

    @abstractmethod
    def describe(self) -> dict:
        """返回来源描述（kind/path/note/provenance）。"""

    @abstractmethod
    def list_materials(self) -> list[dict]:
        """返回全部素材记录（schema: materials.schema.json#/$defs/Material）。"""

    def by_category(self, category: str) -> list[dict]:
        return [m for m in self.list_materials() if m.get("category") == category]

    def by_categories(self, categories: list[str]) -> list[dict]:
        wanted = set(categories)
        return [m for m in self.list_materials() if m.get("category") in wanted]

    def get(self, material_id: str) -> Optional[dict]:
        for m in self.list_materials():
            if m.get("material_id") == material_id:
                return m
        return None

    def evidence_of(self, material_id: str) -> list[dict]:
        """素材的已核验原文片段；没有则返回空列表（上层据此判 EVIDENCE_INSUFFICIENT）。"""
        m = self.get(material_id)
        return list((m or {}).get("evidence_spans") or [])

    def asset_handle(self, material_id: str) -> Optional[str]:
        """素材本体句柄。本地 adapter 返回绝对路径；Paperless 返回文档 URL。"""
        m = self.get(material_id)
        if not m:
            return None
        return m.get("source_file")


class LocalMaterialRepository(MaterialRepository):
    """本地素材：一个 manifest（JSON）+ 可选的本体目录。

    manifest 是**唯一事实来源**；本体目录只用于取文件字节（MVP-6～8 需要）。
    这样即便素材本体不在本机（例如只拿到了核验后的转录），匹配与判定照样能跑。
    """

    kind = "local_fixture"

    def __init__(self, manifest_path: str | Path, *, asset_root: str | Path | None = None):
        self.manifest_path = Path(manifest_path)
        self.asset_root = Path(asset_root) if asset_root else None
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._data = data
        self._materials: list[dict] = list(data.get("materials") or [])

    def describe(self) -> dict:
        src = dict(self._data.get("source") or {})
        src.setdefault("kind", self.kind)
        src["path"] = str(self.manifest_path)
        src.setdefault("note", "本地 manifest（PoC 阶段素材来源；后续可替换为 Paperless adapter）")
        return src

    def list_materials(self) -> list[dict]:
        return self._materials

    def asset_handle(self, material_id: str) -> Optional[str]:
        m = self.get(material_id)
        if not m:
            return None
        raw = m.get("source_file") or ""
        if self.asset_root and raw and not Path(raw).is_absolute():
            candidate = self.asset_root / raw
            if candidate.exists():
                return str(candidate)
        return raw or None


class InMemoryMaterialRepository(MaterialRepository):
    """手工给一批素材记录（测试与"判别性验证"用）。

    用途举例：把某份证书从素材库里拿掉，看判定会不会翻——
    如果拿掉了它还判"满足"，说明判定的依据根本不是那份证书（假 PASS）。
    """

    kind = "in_memory"

    def __init__(self, materials: list[dict], *, note: str = "内存素材库（测试用）"):
        self._materials = list(materials)
        self._note = note

    def describe(self) -> dict:
        return {"kind": self.kind, "note": self._note, "count": len(self._materials)}

    def list_materials(self) -> list[dict]:
        return self._materials

    def without(self, material_ids: list[str]) -> "InMemoryMaterialRepository":
        drop = set(material_ids)
        return InMemoryMaterialRepository(
            [m for m in self._materials if m["material_id"] not in drop],
            note=f"{self._note}（已移除 {sorted(drop)}）",
        )


class PaperlessMaterialRepository(MaterialRepository):
    """Paperless-ngx REST adapter（占位，PoC 未启用）。

    预留映射（不改动上层）::

        GET /api/documents/?tags=<category_tag>   → 素材清单
        document.id                              → material_id 前缀 P-<id>
        document.title                           → title
        custom_fields[]                          → certificate_number / issue_date /
                                                   expiry_date / contract_* / voltage_level
        document.content                         → evidence_spans[].text（文本层）
        /api/documents/<id>/download/            → asset_handle

    只在真正接入时实现；现在调用会明确报错，避免"看起来能跑"。
    """

    kind = "paperless_rest"

    def __init__(self, base_url: str, token: str, *, verify_tls: bool = True):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.verify_tls = verify_tls

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "path": self.base_url,
            "note": "Paperless-ngx REST adapter 尚未实现（PoC 阶段预留接口）",
        }

    def list_materials(self) -> list[dict]:  # pragma: no cover - 预留
        raise NotImplementedError(
            "PaperlessMaterialRepository 尚未实现。PoC 阶段请使用 LocalMaterialRepository；"
            "接口契约见 docs/架构设计.md「Paperless 适配器预留」。"
        )


def open_repository(spec: str | Path, *, asset_root: str | Path | None = None) -> MaterialRepository:
    """按路径/URL 打开素材仓库。

    * ``*.json``     → :class:`LocalMaterialRepository`
    * ``http(s)://`` → :class:`PaperlessMaterialRepository`（未实现，会报错）
    """
    text = str(spec)
    if text.startswith(("http://", "https://")):
        raise NotImplementedError(
            "Paperless adapter 尚未实现；PoC 请传入本地 manifest 的 .json 路径。"
        )
    return LocalMaterialRepository(text, asset_root=asset_root)


def dump_materials(repo: MaterialRepository, out_path: str | Path) -> dict:
    """把仓库内容固化为 materials.json（MVP-3 的产物形态）。"""
    out = {
        "schema_version": "1.0",
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "source": repo.describe(),
        "materials": repo.list_materials(),
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
