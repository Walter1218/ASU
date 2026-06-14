"""Review Engine 数据结构定义"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class ReviewType(str, Enum):
    """审查类型"""
    HALLUCINATION = "hallucination"   # 幻觉检测
    DATA_CHECK = "data_check"         # 数据交叉验证
    STYLE = "style"                   # 风格检查


class IssueSeverity(str, Enum):
    """问题严重程度"""
    HIGH = "high"       # 高风险：数据错误、逻辑矛盾
    MEDIUM = "medium"   # 中风险：可能不准确
    LOW = "low"         # 低风险：风格建议


@dataclass
class ReviewIssue:
    """单条审查问题"""
    # 问题定位
    claim: str                           # 被质疑的原文片段
    claim_position: Optional[int] = None # 在原文中的起始位置

    # 问题描述
    issue_type: str = ""                 # 问题类型（如 "data_mismatch", "unsupported_claim", "style"）
    severity: str = "medium"             # 严重程度
    reason: str = ""                     # 问题原因说明

    # 数据验证（仅 DATA_CHECK 类型）
    reference_value: str = ""            # 参考数据中的真实值
    delta: str = ""                      # 差异描述（如 "+8.6%"）

    # 修正建议
    suggestion: str = ""                 # 修正后的文本建议
    confidence: float = 0.5              # 置信度 (0-1)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "claim": self.claim,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "reason": self.reason,
            "suggestion": self.suggestion,
            "confidence": self.confidence,
        }
        if self.reference_value:
            result["reference_value"] = self.reference_value
        if self.delta:
            result["delta"] = self.delta
        return result


@dataclass
class ReviewResult:
    """审查结果"""
    review_type: str = ""                # 审查类型
    issues: List[ReviewIssue] = field(default_factory=list)
    summary: str = ""                    # 整体摘要
    corrected_text: str = ""             # 修正后的完整文本（可选）

    # 元数据
    source_text: str = ""                # 原始选中文本
    reference_source: str = ""           # 参考数据来源描述
    elapsed_ms: float = 0                # 审查耗时

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def high_severity_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "high")

    @property
    def has_issues(self) -> bool:
        return len(self.issues) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "review_type": self.review_type,
            "issues": [i.to_dict() for i in self.issues],
            "summary": self.summary,
            "corrected_text": self.corrected_text,
            "issue_count": self.issue_count,
            "high_severity_count": self.high_severity_count,
        }


@dataclass
class ReferenceData:
    """参考文档加载结果"""
    source_path: str = ""                # 文件路径
    source_type: str = ""                # 类型：excel / csv / json / markdown / text
    content: str = ""                    # 文本化内容
    structured: Optional[Any] = None     # 结构化数据（如 DataFrame.to_dict）
    loaded: bool = False
    error: str = ""

    def to_context_string(self) -> str:
        """转换为可注入 LLM prompt 的上下文字符串"""
        if not self.loaded:
            return ""
        if self.content:
            # 截断过长内容
            max_len = 4000
            if len(self.content) > max_len:
                return self.content[:max_len] + "\n...(内容已截断)"
            return self.content
        return ""
