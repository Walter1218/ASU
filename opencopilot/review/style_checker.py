"""风格检查引擎 — 检查文本风格一致性、专业性、语气"""
import json
import re
import logging

from .models import ReviewResult, ReviewIssue

logger = logging.getLogger(__name__)

_STYLE_SYSTEM_PROMPT = """你是一个专业的文本风格审查员。你的任务是检查文本的风格问题，包括：

1. **语气不当**：正式文档中出现口语化表达
2. **用词不专业**：使用了非行业术语或模糊表述
3. **句式问题**：过长的句子、重复句式、逻辑连接不当
4. **一致性**：同一概念使用不同术语、格式不统一
5. **冗余表达**：可以精简的啰嗦表述

请严格输出 JSON 格式。"""

_STYLE_USER_TEMPLATE = """请审查以下文本的风格问题：

--- 待审查文本 ---
{text}

--- 目标风格 ---
{style_target}

--- 输出要求 ---
输出 JSON 数组，每个元素包含：
- "claim": 有问题的原文片段
- "issue_type": 问题类型（tone_mismatch / unprofessional / sentence_issue / inconsistency / redundancy）
- "severity": 严重程度（high / medium / low）
- "reason": 问题说明
- "suggestion": 修改后的文本建议
- "confidence": 判断置信度（0-1）

如果风格没有明显问题，输出空数组 []。"""


# 预设风格目标
STYLE_PRESETS = {
    "b2b_formal": "B2B 商务正式风格：专业术语、数据驱动、客观语气、避免口语化",
    "academic": "学术论文风格：严谨引用、客观表述、避免主观判断、使用被动语态",
    "casual": "轻松口语风格：简洁自然、避免生硬术语、适当使用短句",
    "marketing": "营销推广风格：有感染力、突出价值、使用行动号召",
    "technical": "技术文档风格：精确术语、步骤清晰、无歧义表述",
}


def build_style_prompt(text: str, style_target: str = "") -> tuple:
    """构建风格检查的 system + user prompt"""
    if not style_target:
        style_target = STYLE_PRESETS.get("b2b_formal", "B2B 商务正式风格")
    elif style_target in STYLE_PRESETS:
        style_target = STYLE_PRESETS[style_target]

    user_prompt = _STYLE_USER_TEMPLATE.format(
        text=text,
        style_target=style_target
    )
    return _STYLE_SYSTEM_PROMPT, user_prompt


def parse_style_response(llm_output: str, source_text: str) -> ReviewResult:
    """解析 LLM 风格检查响应为 ReviewResult"""
    result = ReviewResult(
        review_type="style",
        source_text=source_text,
    )

    json_match = re.search(r'\[[\s\S]*\]', llm_output)
    if not json_match:
        result.summary = "未能解析审查结果"
        return result

    try:
        issues_data = json.loads(json_match.group())
    except json.JSONDecodeError:
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
            issue_type=item.get("issue_type", "style"),
            severity=item.get("severity", "low"),
            reason=item.get("reason", ""),
            suggestion=item.get("suggestion", ""),
            confidence=float(item.get("confidence", 0.5)),
        )
        if issue.claim and source_text:
            pos = source_text.find(issue.claim)
            if pos >= 0:
                issue.claim_position = pos
        result.issues.append(issue)

    if not result.issues:
        result.summary = "风格符合目标要求"
    else:
        result.summary = f"发现 {len(result.issues)} 处风格问题"

    # 尝试生成完整的修正文本
    if result.issues and source_text:
        corrected = source_text
        for issue in sorted(result.issues, key=lambda i: -(i.claim_position or 0)):
            if issue.claim and issue.suggestion and issue.claim_position is not None:
                start = issue.claim_position
                end = start + len(issue.claim)
                corrected = corrected[:start] + issue.suggestion + corrected[end:]
        result.corrected_text = corrected

    return result
