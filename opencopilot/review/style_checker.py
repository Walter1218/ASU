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

【重要】以下情况不应标记为问题：
- 目标风格是 technical 时，技术术语（如 API、SQL、Kubernetes）完全正常
- 目标风格是 b2b_formal 时，KPI、ROI、ARR、GMV 等常见商业缩写不算不专业
- 目标风格是 academic 时，研究方法描述（如“采用随机对照试验”）是规范表述
- 目标风格是 casual 时，轻松自然的表达（如“咱们”、“挺好的”）是风格要求
- 目标风格是 marketing 时，行动号召（如“立即试用”）是必要元素
- 法律条款中的“甲方”、“乙方”、“违约责任”等是专业用语，不算风格问题

---
示例1（有风格问题 → 应输出问题）：
目标风格：b2b_formal
文本：这个方案真的超赞的，客户用了都说好，基本上没啥毛病，性价比超高！
输出：
```json
[
  {{"claim": “超赞的”, “issue_type”: “tone_mismatch”, “severity”: “medium”, “reason”: “口语化表达不适合B2B正式文档”, “suggestion”: “表现优异”, “confidence”: 0.9}},
  {{"claim": “没啥毛病”, “issue_type”: “tone_mismatch”, “severity”: “medium”, “reason”: “口语化表达不适合B2B正式文档”, “suggestion”: “运行稳定”, “confidence”: 0.9}}
]
```

示例2（无风格问题的正式文本 → 应输出空数组）：
目标风格：b2b_formal
文本：本季度营收环比增长5.6%，主要受益于新客户拓展和产品升级。净利润率保持在14.2%，符合年度经营目标。
输出：[]

示例3（无风格问题的合同条款 → 应输出空数组）：
目标风格：b2b_formal
文本：甲方应在合同签订后5个工作日内支付合同总额的30%作为预付款。剩余70%在项目验收合格后10个工作日内支付。
输出：[]

请严格按照以上示例的判断标准输出。"""

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
