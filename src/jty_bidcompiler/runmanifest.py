"""run_manifest：为每次真实运行留一份可复现的"运行指纹"。

目的只有一个：将来有人问"为什么这一次的结果和上一次不一样"，
能直接回答——是代码变了、规则变了、输入变了，还是素材变了。

因此 manifest 里记的是**哈希**而不是描述：config/rules/schemas/输入/素材
各自的 sha256 指纹，加上 git commit（如仓库存在）。
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import __version__


def sha256_file(path: str | Path) -> Optional[str]:
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def dir_fingerprint(path: str | Path, pattern: str = "*.json") -> dict:
    """目录指纹：逐文件哈希 + 组合哈希（用于"规则是否动过"的一眼判断）。"""
    p = Path(path)
    files: dict[str, str] = {}
    if p.is_dir():
        for f in sorted(p.rglob(pattern)):
            if "__pycache__" in str(f):
                continue
            digest = sha256_file(f)
            if digest:
                files[str(f.relative_to(p)).replace("\\", "/")] = digest
    combined = hashlib.sha256()
    for name, digest in sorted(files.items()):
        combined.update(name.encode("utf-8"))
        combined.update(digest.encode("utf-8"))
    return {"files": files, "combined": combined.hexdigest()}


def git_commit(root: str | Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # pragma: no cover - git 不存在或超时
        pass
    return None


class RunManifest:
    """上下文管理器：``with RunManifest(root, run_id, ...) as mf: ...`` 。

    退出时写入 ``run_manifest.json``（含耗时与 warnings/extraction issues）。
    """

    def __init__(self, root: str | Path, run_id: str, *, profile: str,
                 config_path: str | Path, inputs: dict, outputs_dir: str | Path):
        self.root = Path(root)
        self.run_id = run_id
        self.profile = profile
        self.config_path = Path(config_path)
        self.inputs = {k: str(v) for k, v in inputs.items()}
        self.outputs_dir = Path(outputs_dir)
        self.warnings: list[str] = []
        self.extraction_issues: list[str] = []
        self._t0 = time.time()

    def __enter__(self) -> "RunManifest":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.write(status="ok" if exc_type is None else "error",
                   error=None if exc_type is None else f"{exc_type.__name__}: {exc}")
        return False  # 不吞异常

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def issues(self, issues: list[str]) -> None:
        self.extraction_issues.extend(issues)

    def to_dict(self, *, status: str, error: Optional[str]) -> dict:
        started = datetime.fromtimestamp(self._t0).isoformat(timespec="seconds")
        ended = datetime.now()
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "profile": self.profile,
            "status": status,
            "error": error,
            "compiler": {
                "tool": "jty-bidcompiler",
                "version": __version__,
                "python": sys.version.split()[0],
                "platform": platform.platform(),
            },
            "git_commit": git_commit(self.root),
            "fingerprints": {
                "config": {"path": str(self.config_path),
                           "sha256": sha256_file(self.config_path)},
                "rules": dir_fingerprint(self.root / "rules"),
                "schemas": dir_fingerprint(self.root / "schemas"),
                "src": dir_fingerprint(self.root / "src", "*.py"),
            },
            "inputs": {
                name: {"path": path, "sha256": sha256_file(self.root / path)
                       if not Path(path).is_absolute() else sha256_file(path)}
                for name, path in self.inputs.items()
            },
            "outputs_dir": str(self.outputs_dir),
            "started_at": started,
            "ended_at": ended.isoformat(timespec="seconds"),
            "duration_seconds": round(time.time() - self._t0, 2),
            "warnings": self.warnings,
            "extraction_issues": self.extraction_issues,
        }

    def write(self, *, status: str, error: Optional[str] = None) -> Path:
        data = self.to_dict(status=status, error=error)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        out = self.outputs_dir / "run_manifest.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
