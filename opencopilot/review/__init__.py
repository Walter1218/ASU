"""OpenCopilot Review Engine — 文本审查与修正引擎

三类审查能力：
- hallucination: 幻觉检测（无源数据、逻辑矛盾、虚构引用）
- data_check: 数据交叉验证（与 Excel/CSV/文档比对）
- style: 风格检查（正式度、一致性、专业性）
"""
from .models import ReviewResult, ReviewIssue, ReviewType, IssueSeverity, ReferenceData
from .engine import ReviewEngine
from .reference_loader import load_from_file, search_local_files

__all__ = [
    "ReviewEngine",
    "ReviewResult",
    "ReviewIssue",
    "ReviewType",
    "IssueSeverity",
    "ReferenceData",
    "load_from_file",
    "search_local_files",
]
