"""JTY-BidCompiler —— 投标编译器 PoC。

把一份招标文件编译成"可审的第一稿"所需的结构化中间产物。
设计红线见 README.md：一切结论带证据位置、规则优先、不许猜值。
"""

__version__ = "0.1.0"

ROOT_MARKERS = ("schemas", "rules", "config")


def project_root():
    """返回仓库根目录（含 schemas/ rules/ config/ 的那一层）。"""
    import pathlib

    here = pathlib.Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if all((parent / m).is_dir() for m in ROOT_MARKERS):
            return parent
    raise RuntimeError("找不到项目根目录（应包含 schemas/ rules/ config/）")
