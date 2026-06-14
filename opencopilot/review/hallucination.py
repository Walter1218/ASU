"""幻觉检测引擎 — 纯 LLM 驱动，不依赖外部数据源"""
import json
import time
import logging
from typing import Optional

from .models import ReviewResult, ReviewIssue

logger = logging.getLogger(__name__)

# 幻觉检测 Prompt
_HALLUCINATION_SYSTEM_PROMPT = """你是一个严格的事实审查员。你的任务是审查给定文本中的事实声明，检查是否存在以下问题：

1. **无源数据**：声明了具体数字/日期/比例，但没有给出数据来源
2. **逻辑矛盾**：文本内部存在前后矛盾的声明
3. **不合理数值**：数值明显不合理（如"市场份额 120%"）
4. **虚构引用**：引用了不存在的研究、报告、机构名称
5. **时间线错误**：事件发生时间与已知事实不符

请严格输出 JSON 格式，不要输出其他内容。"""

_HALLUCINATION_USER_TEMPLATE = """请审查以下文本中的事实声明：

--- 待审查文本 ---
{text}

{context_section}

--- 输出要求 ---
输出 JSON 数组，每个元素包含：
- "claim": 被质疑的原文片段（直接引用）
- "issue_type": 问题类型（unsupported_claim / logical_contradiction / unreasonable_value / fabricated_reference / timeline_error）
- "severity": 严重程度（high / medium / low）
- "reason": 简要说明为什么质疑
- "suggestion": 修正建议（如果不确定正确值，写"请核实原始数据"）
- "confidence": 你的判断置信度（0-1 之间的小数）

如果文本没有明显问题，输出空数组 []。

示例输出：
```json
[
  {{
    "claim": "Q2营收3800万",
    "issue_type": "unsupported_claim",
    "severity": "high",
    "reason": "声明了具体营收数字但未提供数据来源",
    "suggestion": "请核实原始财报数据，补充准确数字",
    "confidence": 0.7
  }}
]
```"""


def build_hallucination_prompt(text: str, context: str = "") -> tuple:
    """构建幻觉检测的 system + user prompt"""
    context_section = ""
    if context:
        context_section = f"--- 上下文（同文档相邻内容）---\n{context}\n"

    user_prompt = _HALLUCINATION_USER_TEMPLATE.format(
        text=text,
        context_section=context_section
    )
    return _HALLUCINATION_SYSTEM_PROMPT, user_prompt


def parse_hallucination_response(llm_output: str, source_text: str) -> ReviewResult:
    """解析 LLM 幻觉检测响应为 ReviewResult"""
    import re

    result = ReviewResult(
        review_type="hallucination",
        source_text=source_text,
    )

    # 提取 JSON 数组
    json_match = re.search(r'\[[\s\S]*\]', llm_output)
    if not json_match:
        result.summary = "未能解析审查结果"
        return result

    try:
        issues_data = json.loads(json_match.group())
    except json.JSONDecodeError:
        # 尝试用 json_repair
        try:
            from json_repair import repair_json
            repaired = repair_json(json_match.group())
            issues_data = json.loads(repaired)
        except Exception:
            result.summary = "审查结果 JSON 解析失败"
            return result

    if not isinstance(issues_data, list):
        result.summary = "审查结果格式异常"
        return result

    for item in issues_data:
        if not isinstance(item, dict):
            continue
        issue = ReviewIssue(
            claim=item.get("claim", ""),
            issue_type=item.get("issue_type", "unsupported_claim"),
            severity=item.get("severity", "medium"),
            reason=item.get("reason", ""),
            suggestion=item.get("suggestion", ""),
            confidence=float(item.get("confidence", 0.5)),
        )
        # 尝试定位 claim 在原文中的位置
        if issue.claim and source_text:
            pos = source_text.find(issue.claim)
            if pos >= 0:
                issue.claim_position = pos
        result.issues.append(issue)

    # 生成摘要
    high = sum(1 for i in result.issues if i.severity == "high")
    medium = sum(1 for i in result.issues if i.severity == "medium")
    low = sum(1 for i in result.issues if i.severity == "low")

    if not result.issues:
        result.summary = "未发现明显问题"
    else:
        parts = []
        if high:
            parts.append(f"{high} 个高风险")
        if medium:
            parts.append(f"{medium} 个中风险")
        if low:
            parts.append(f"{low} 个低风险")
        result.summary = f"发现 {len(result.issues)} 个问题：" + "、".join(parts)

    return result
