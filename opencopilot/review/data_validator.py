"""数据交叉验证引擎 — 将选中文本与参考数据比对"""
import json
import re
import logging

from .models import ReviewResult, ReviewIssue, ReferenceData

logger = logging.getLogger(__name__)

_DATA_CHECK_SYSTEM_PROMPT = """你是一个数据审查员。你的任务是将「待审查文本」中的数值声明与「参考数据」进行逐条比对，找出不一致之处。

审查重点：
1. 数字不匹配：文本中的数字与参考数据不一致
2. 单位/口径错误：如"万元"vs"元"、"含税"vs"不含税"
3. 时间范围错误：如文本说"2025年"但参考数据是"2024年"
4. 遗漏关键数据：文本遗漏了参考数据中的重要数字
5. 百分比计算错误：增长率、占比等衍生数据计算有误

【重要】只有当文本中的数字与参考数据**实际不一致**时才标记为问题。如果数字一致，输出空数组[]。但如果存在不一致，**必须**标记出来。

---
示例1（数据错误 → 应输出问题）：
文本：Q2营收达到4000万元。
参考数据：Q2营收3600万元。
输出：
```json
[{{"claim": "Q2营收达到4000万元", "reference_value": "3600万元", "delta": "+400万元", "issue_type": "data_mismatch", "severity": "high", "reason": "文本Q2营收4000万元与参考数据3600万元不一致", "suggestion": "将Q2营收修正为3600万元", "confidence": 0.95}}]
```

示例2（数据正确 → 应输出空数组）：
文本：Q1营收3500万元，Q2营收3600万元，Q3营收3800万元。
参考数据：Q1营收3500万元，Q2营收3600万元，Q3营收3800万元，Q4营收4100万元。
输出：[]

示例3（单位错误 → 应输出问题）：
文本：公司全年营收15000元。
参考数据：全年营收15000万元。
输出：
```json
[{{"claim": "营收15000元", "reference_value": "15000万元", "delta": "单位不一致：元vs万元", "issue_type": "unit_error", "severity": "high", "reason": "单位错误，应为万元而非元", "suggestion": "修正为15000万元", "confidence": 0.95}}]
```

请严格按照以上示例的判断标准输出。"""

_DATA_CHECK_USER_TEMPLATE = """请比对以下文本与参考数据：

--- 待审查文本 ---
{text}

--- 参考数据 ---
{reference_content}

--- 输出要求 ---
输出 JSON 数组，每个元素包含：
- "claim": 文本中的数据声明（直接引用原文）
- "reference_value": 参考数据中的真实值
- "delta": 差异描述（如"+8.6%"或"单位不一致"）
- "issue_type": 问题类型（data_mismatch / unit_error / time_range_error / missing_data / calculation_error）
- "severity": 严重程度（high / medium / low）
- "reason": 差异原因说明
- "suggestion": 修正建议（给出正确的表述）
- "confidence": 判断置信度（0-1）

如果数据完全一致，输出空数组 []。"""


def build_data_check_prompt(text: str, reference: ReferenceData) -> tuple:
    """构建数据交叉验证的 system + user prompt"""
    ref_content = reference.to_context_string()
    if not ref_content:
        ref_content = "（参考数据加载失败）"

    user_prompt = _DATA_CHECK_USER_TEMPLATE.format(
        text=text,
        reference_content=ref_content
    )
    return _DATA_CHECK_SYSTEM_PROMPT, user_prompt


def parse_data_check_response(llm_output: str, source_text: str) -> ReviewResult:
    """解析 LLM 数据验证响应为 ReviewResult"""
    result = ReviewResult(
        review_type="data_check",
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
            issue_type=item.get("issue_type", "data_mismatch"),
            severity=item.get("severity", "medium"),
            reason=item.get("reason", ""),
            suggestion=item.get("suggestion", ""),
            confidence=float(item.get("confidence", 0.5)),
            reference_value=str(item.get("reference_value", "")),
            delta=str(item.get("delta", "")),
        )
        if issue.claim and source_text:
            pos = source_text.find(issue.claim)
            if pos >= 0:
                issue.claim_position = pos
        result.issues.append(issue)

    high = sum(1 for i in result.issues if i.severity == "high")
    if not result.issues:
        result.summary = "数据与参考文档一致"
    else:
        result.summary = f"发现 {len(result.issues)} 处数据不一致"
        if high:
            result.summary += f"（其中 {high} 处高风险）"

    return result
