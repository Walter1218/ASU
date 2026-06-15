"""Review Few-Shot AB 实验 — A(基线) vs B(Few-Shot增强)

实验设计：
- A 组：原始 prompt（无 few-shot 示例）
- B 组：在 system prompt 中添加正反例 few-shot 教学

核心思路：
- 正例：文本确实有问题 → 输出非空 JSON 数组
- 反例：文本没有问题 → 输出空数组 []
- 通过反例教会 LLM "没有问题也是一种答案"

运行：python tests/e2e/test_review_fewshot_ab.py
"""
import sys, os, json, time, copy, re, logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opencopilot.review import load_from_file, ReferenceData
from opencopilot.review.hallucination import parse_hallucination_response
from opencopilot.review.data_validator import parse_data_check_response
from opencopilot.review.style_checker import parse_style_response

# ============================================================
# A 组 Prompt（基线，不修改）
# ============================================================

A_HALLUCINATION_SYS = """你是一个严格的事实审查员。你的任务是审查给定文本中的事实声明，检查是否存在以下问题：

1. **无源数据**：声明了具体数字/日期/比例，但没有给出数据来源
2. **逻辑矛盾**：文本内部存在前后矛盾的声明
3. **不合理数值**：数值明显不合理（如"市场份额 120%"）
4. **虚构引用**：引用了不存在的研究、报告、机构名称
5. **时间线错误**：事件发生时间与已知事实不符

请严格输出 JSON 格式，不要输出其他内容。"""

A_DATA_CHECK_SYS = """你是一个数据审查员。你的任务是将「待审查文本」中的数值声明与「参考数据」进行逐条比对，找出不一致之处。

审查重点：
1. 数字不匹配：文本中的数字与参考数据不一致
2. 单位/口径错误：如"万元"vs"元"、"含税"vs"不含税"
3. 时间范围错误：如文本说"2025年"但参考数据是"2024年"
4. 遗漏关键数据：文本遗漏了参考数据中的重要数字
5. 百分比计算错误：增长率、占比等衍生数据计算有误

请严格输出 JSON 格式。"""

A_STYLE_SYS = """你是一个专业的文本风格审查员。你的任务是检查文本的风格问题，包括：

1. **语气不当**：正式文档中出现口语化表达
2. **用词不专业**：使用了非行业术语或模糊表述
3. **句式问题**：过长的句子、重复句式、逻辑连接不当
4. **一致性**：同一概念使用不同术语、格式不统一
5. **冗余表达**：可以精简的啰嗦表述

请严格输出 JSON 格式。"""

# ============================================================
# B 组 Few-Shot Prompt
# ============================================================

B_HALLUCINATION_SYS = """你是一个严格的事实审查员。你的任务是审查给定文本中的事实声明，检查是否存在以下问题：

1. **无源数据**：声明了具体数字/日期/比例，但没有给出数据来源
2. **逻辑矛盾**：文本内部存在前后矛盾的声明
3. **不合理数值**：数值明显不合理（如"市场份额 120%"）
4. **虚构引用**：引用了不存在的研究、报告、机构名称
5. **时间线错误**：事件发生时间与已知事实不符

【重要】以下情况不应标记为问题：
- 文本中引用了真实存在的知名机构/报告/法律（如世界银行、IMF、国家统计局、《民法典》等），不算虚构引用
- 常识性陈述（如"Python是一种编程语言"、"公司成立于2015年"）不需要数据来源
- 不含具体数值声明的描述性文本，不应标记为"无源数据"

---
示例1（有问题的文本 → 应输出问题）：
文本：根据最新的行业报告，我们的产品日活用户突破8000万，市场份额达到67%，是第二名的3倍。
输出：
```json
[
  {{"claim": "日活用户突破8000万", "issue_type": "unsupported_claim", "severity": "high", "reason": "声明了具体用户数但未指明数据来源", "suggestion": "请核实数据来源", "confidence": 0.85}},
  {{"claim": "市场份额达到67%", "issue_type": "unsupported_claim", "severity": "high", "reason": "声明了具体市场份额但无来源", "suggestion": "请补充市场份额数据出处", "confidence": 0.85}}
]
```

示例2（无问题的文本 → 应输出空数组）：
文本：据世界银行2023年报告，中国GDP总量约为17.96万亿美元。国际货币基金组织预测2024年全球经济增长率为3.1%。
输出：[]

示例3（无问题的常识文本 → 应输出空数组）：
文本：公司成立于2015年，总部位于上海。目前在北京、深圳、杭州设有分公司，员工总数超过500人。
输出：[]

请严格按照以上示例的判断标准输出。"""

B_DATA_CHECK_SYS = """你是一个数据审查员。你的任务是将「待审查文本」中的数值声明与「参考数据」进行逐条比对，找出不一致之处。

审查重点：
1. 数字不匹配：文本中的数字与参考数据不一致
2. 单位/口径错误：如"万元"vs"元"、"含税"vs"不含税"
3. 时间范围错误：如文本说"2025年"但参考数据是"2024年"
4. 遗漏关键数据：文本遗漏了参考数据中的重要数字
5. 百分比计算错误：增长率、占比等衍生数据计算有误

【重要】只有当文本中的数字与参考数据**实际不一致**时才标记为问题。如果文本中的数字与参考数据一致，输出空数组[]。如果文本中没有与参考数据相关的数值声明，也输出空数组[]。

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

示例3（无关内容 → 应输出空数组）：
文本：今天天气很好，适合出去郊游。
参考数据：同上财务数据。
输出：[]

请严格按照以上示例的判断标准输出。"""

B_STYLE_SYS = """你是一个专业的文本风格审查员。你的任务是检查文本的风格问题，包括：

1. **语气不当**：正式文档中出现口语化表达
2. **用词不专业**：使用了非行业术语或模糊表述
3. **句式问题**：过长的句子、重复句式、逻辑连接不当
4. **一致性**：同一概念使用不同术语、格式不统一
5. **冗余表达**：可以精简的啰嗦表述

【重要】以下情况不应标记为问题：
- 目标风格是 technical 时，技术术语（如 API、SQL、Kubernetes）完全正常
- 目标风格是 b2b_formal 时，KPI、ROI、ARR、GMV 等常见商业缩写不算不专业
- 目标风格是 academic 时，研究方法描述（如"采用随机对照试验"）是规范表述
- 目标风格是 casual 时，轻松自然的表达（如"咱们"、"挺好的"）是风格要求
- 目标风格是 marketing 时，行动号召（如"立即试用"）是必要元素
- 法律条款中的"甲方"、"乙方"、"违约责任"等是专业用语，不算风格问题

---
示例1（有风格问题 → 应输出问题）：
目标风格：b2b_formal
文本：这个方案真的超赞的，客户用了都说好，基本上没啥毛病，性价比超高！
输出：
```json
[
  {{"claim": "超赞的", "issue_type": "tone_mismatch", "severity": "medium", "reason": "口语化表达不适合B2B正式文档", "suggestion": "表现优异", "confidence": 0.9}},
  {{"claim": "没啥毛病", "issue_type": "tone_mismatch", "severity": "medium", "reason": "口语化表达不适合B2B正式文档", "suggestion": "运行稳定", "confidence": 0.9}}
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

# ============================================================
# User prompt 模板（A/B 共用）
# ============================================================

HALLUCINATION_USER_TPL = """请审查以下文本中的事实声明：

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

如果文本没有明显问题，输出空数组 []。"""

DATA_CHECK_USER_TPL = """请比对以下文本与参考数据：

--- 待审查文本 ---
{text}

--- 参考数据 ---
{reference_content}

--- 输出要求 ---
输出 JSON 数组，每个元素包含：
- "claim": 文本中的数据声明（直接引用原文）
- "reference_value": 参考数据中的真实值
- "delta": 差异描述
- "issue_type": 问题类型（data_mismatch / unit_error / time_range_error / missing_data / calculation_error）
- "severity": 严重程度（high / medium / low）
- "reason": 差异原因说明
- "suggestion": 修正建议
- "confidence": 判断置信度（0-1）

如果数据完全一致，输出空数组 []。"""

STYLE_USER_TPL = """请审查以下文本的风格问题：

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

# ============================================================
# LLM
# ============================================================

_provider = None
def get_llm():
    global _provider
    if _provider is None:
        from llm_provider import ProviderFactory
        _provider = ProviderFactory.create_provider()
    return _provider

def call_llm(sys_p, usr_p, timeout=90):
    full = ""
    t0 = time.time()
    for chunk in get_llm().stream_chat(prompt=usr_p, system_prompt=sys_p):
        full += chunk
        if time.time() - t0 > timeout:
            break
    return full

# ============================================================
# 评估
# ============================================================

def evaluate(case, result):
    gt = case["ground_truth"]
    if result.summary in ("未能解析审查结果", "审查结果 JSON 解析失败", "审查结果格式异常"):
        return {"json_ok": False, "recall": 0, "fpr": 1, "verdict": "FAIL", "hits": 0, "fp": result.issue_count}

    hits = 0
    for g in gt:
        kw = g["keyword"]
        if any(kw in i.claim or kw in i.reason or kw in i.suggestion for i in result.issues):
            hits += 1

    recall = hits / len(gt) if gt else 1.0
    if gt:
        fp = max(0, result.issue_count - hits)
        fpr = fp / max(result.issue_count, 1)
    else:
        fp = result.issue_count
        fpr = 1.0 if result.issue_count > 0 else 0.0

    if not gt:
        verdict = "PASS" if result.issue_count == 0 else "FAIL"
    elif recall >= 0.5 and fpr <= 0.5:
        verdict = "PASS"
    elif recall >= 0.3:
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"

    return {"json_ok": True, "recall": recall, "fpr": fpr, "verdict": verdict, "hits": hits, "fp": fp}

# ============================================================
# 数据加载
# ============================================================

def load_cases():
    d = os.path.dirname(os.path.abspath(__file__))
    merged = {"hallucination": [], "data_check": [], "style": []}
    for f in ["review_test_cases.json", "review_test_cases_v2.json"]:
        p = os.path.join(d, f)
        if not os.path.exists(p): continue
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for k in merged:
            merged[k].extend(data.get(k, []))
    return merged

_REF_CSV = """指标,Q1,Q2,Q3,Q4
营收(万元),3500,3600,3800,4100
净利润(万元),420,460,510,580
员工数,280,295,310,325
客户数,1200,1350,1480,1600
市场份额,12.3%,12.8%,13.5%,14.1%
"""
_ref = None
def get_ref():
    global _ref
    if _ref is None:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(_REF_CSV); _ref = load_from_file(f.name)
    return _ref

# ============================================================
# AB 执行器
# ============================================================

def run_ab(cases, review_type, ref=None):
    """对一组 case 运行 A/B 评估 — 同一次 LLM 调用对比 A/B prompt"""
    results = []
    total = len(cases)

    for i, c in enumerate(cases):
        kwargs = {"text": c["text"]}
        if review_type == "hallucination":
            ctx = c.get("context", "")
            kwargs["context_section"] = f"--- 上下文 ---\n{ctx}\n" if ctx else ""
        elif review_type == "data_check" and ref:
            kwargs["reference_content"] = ref.to_context_string()
        else:
            kwargs["style_target"] = c.get("style_target", "b2b_formal")

        # 构建 user prompt（A/B 共用）
        user_prompt = HALLUCINATION_USER_TPL.format(**kwargs) if review_type == "hallucination" else \
                      DATA_CHECK_USER_TPL.format(**kwargs) if review_type == "data_check" else \
                      STYLE_USER_TPL.format(**kwargs)

        # A 组：原始 prompt
        a_sys = A_HALLUCINATION_SYS if review_type == "hallucination" else \
                A_DATA_CHECK_SYS if review_type == "data_check" else \
                A_STYLE_SYS
        t0 = time.time()
        a_raw = call_llm(a_sys, user_prompt)
        a_ms = (time.time() - t0) * 1000

        # B 组：Few-Shot prompt
        b_sys = B_HALLUCINATION_SYS if review_type == "hallucination" else \
                B_DATA_CHECK_SYS if review_type == "data_check" else \
                B_STYLE_SYS
        t0 = time.time()
        b_raw = call_llm(b_sys, user_prompt)
        b_ms = (time.time() - t0) * 1000

        # 解析
        parser = parse_hallucination_response if review_type == "hallucination" else \
                 parse_data_check_response if review_type == "data_check" else \
                 parse_style_response
        a_res = parser(a_raw, c["text"])
        b_res = parser(b_raw, c["text"])

        # 评估
        a_ev = evaluate(c, a_res)
        b_ev = evaluate(c, b_res)

        changed = a_ev["verdict"] != b_ev["verdict"]
        improved = (a_ev["verdict"] != "PASS" and b_ev["verdict"] == "PASS")
        degraded = (a_ev["verdict"] == "PASS" and b_ev["verdict"] != "PASS")

        result = {
            "id": c["id"], "desc": c["desc"][:25], "gt_count": len(c["ground_truth"]),
            "a": {**a_ev, "ms": round(a_ms), "issues": a_res.issue_count},
            "b": {**b_ev, "ms": round(b_ms), "issues": b_res.issue_count},
            "changed": changed, "improved": improved, "degraded": degraded,
        }
        results.append(result)

        marker = ""
        if improved: marker = " IMPROVED"
        elif degraded: marker = " DEGRADED"
        elif changed: marker = " CHANGED"

        a_sym = {"PASS":"PASS","PARTIAL":"PARTIAL","FAIL":"FAIL"}[a_ev["verdict"]]
        b_sym = {"PASS":"PASS","PARTIAL":"PARTIAL","FAIL":"FAIL"}[b_ev["verdict"]]
        print(f"  {c['id']} A:{a_sym} B:{b_sym}{marker} "
              f"A:rec={a_ev['recall']:.0%}/fpr={a_ev['fpr']:.0%}({a_res.issue_count}i) "
              f"B:rec={b_ev['recall']:.0%}/fpr={b_ev['fpr']:.0%}({b_res.issue_count}i)")

        if (i + 1) % 10 == 0:
            print(f"  ... 已完成 {i+1}/{total}")

    return results

# ============================================================
# 报告
# ============================================================

def print_report(all_results):
    total = len(all_results)
    a_pass = sum(1 for r in all_results if r["a"]["verdict"] == "PASS")
    b_pass = sum(1 for r in all_results if r["b"]["verdict"] == "PASS")
    improved = sum(1 for r in all_results if r["improved"])
    degraded = sum(1 for r in all_results if r["degraded"])

    a_recalls = [r["a"]["recall"] for r in all_results if r["gt_count"] > 0]
    b_recalls = [r["b"]["recall"] for r in all_results if r["gt_count"] > 0]
    a_avg_r = sum(a_recalls) / len(a_recalls) if a_recalls else 0
    b_avg_r = sum(b_recalls) / len(b_recalls) if b_recalls else 0

    neg_cases = [r for r in all_results if r["gt_count"] == 0]
    a_neg_pass = sum(1 for r in neg_cases if r["a"]["verdict"] == "PASS")
    b_neg_pass = sum(1 for r in neg_cases if r["b"]["verdict"] == "PASS")

    a_ms = sum(r["a"]["ms"] for r in all_results) / total
    b_ms = sum(r["b"]["ms"] for r in all_results) / total

    # issue 数量统计
    a_total_issues = sum(r["a"]["issues"] for r in all_results)
    b_total_issues = sum(r["b"]["issues"] for r in all_results)
    a_neg_issues = sum(r["a"]["issues"] for r in neg_cases)
    b_neg_issues = sum(r["b"]["issues"] for r in neg_cases)

    print(f"\n{'='*70}")
    print("  Few-Shot AB 实验报告")
    print(f"{'='*70}")
    print(f"  总 case: {total}")
    print(f"  B 优化策略: System Prompt 添加 few-shot 正反例示例")
    print(f"{'─'*70}")
    print(f"  {'指标':<20} {'A (基线)':>12} {'B (FewShot)':>12} {'变化':>10}")
    print(f"{'─'*70}")
    print(f"  {'PASS 数':<20} {a_pass:>12} {b_pass:>12} {b_pass-a_pass:>+10}")
    print(f"  {'PASS 率':<20} {a_pass/total:>11.0%} {b_pass/total:>11.0%} {(b_pass-a_pass)/total:>+9.0%}")
    print(f"  {'平均 Recall':<20} {a_avg_r:>11.0%} {b_avg_r:>11.0%} {b_avg_r-a_avg_r:>+9.0%}")
    print(f"  {'总 Issue 数':<20} {a_total_issues:>12} {b_total_issues:>12} {b_total_issues-a_total_issues:>+10}")
    print(f"  {'平均耗时(ms)':<20} {a_ms:>12.0f} {b_ms:>12.0f} {b_ms-a_ms:>+10.0f}")
    print(f"{'─'*70}")
    print(f"  {'负向 case PASS':<20} {a_neg_pass:>12} {b_neg_pass:>12} {b_neg_pass-a_neg_pass:>+10}")
    if neg_cases:
        print(f"  {'负向 PASS 率':<20} {a_neg_pass/len(neg_cases):>11.0%} {b_neg_pass/len(neg_cases):>11.0%} {(b_neg_pass-a_neg_pass)/len(neg_cases):>+9.0%}")
        print(f"  {'负向 Issue 数':<20} {a_neg_issues:>12} {b_neg_issues:>12} {b_neg_issues-a_neg_issues:>+10}")
    print(f"{'─'*70}")
    print(f"  改善 (→PASS): {improved}")
    print(f"  退化 (→非PASS): {degraded}")
    print(f"  无变化: {total - improved - degraded}")
    print(f"{'='*70}")

    for rtype, label, prefix in [("hallucination","幻觉检测","H"),("data_check","数据核对","D"),("style","风格检查","S")]:
        sub = [r for r in all_results if r["id"][0] == prefix]
        if not sub: continue
        ap = sum(1 for r in sub if r["a"]["verdict"]=="PASS")
        bp = sum(1 for r in sub if r["b"]["verdict"]=="PASS")
        imp = sum(1 for r in sub if r["improved"])
        deg = sum(1 for r in sub if r["degraded"])
        ai = sum(r["a"]["issues"] for r in sub)
        bi = sum(r["b"]["issues"] for r in sub)
        sub_neg = [r for r in sub if r["gt_count"] == 0]
        anp = sum(1 for r in sub_neg if r["a"]["verdict"]=="PASS")
        bnp = sum(1 for r in sub_neg if r["b"]["verdict"]=="PASS")
        print(f"\n  [{label}] A:{ap}/{len(sub)} → B:{bp}/{len(sub)}  改善:{imp} 退化:{deg}  Issue:{ai}→{bi}({bi-ai:+d})")
        if sub_neg:
            print(f"    负向case: A:{anp}/{len(sub_neg)} → B:{bnp}/{len(sub_neg)} PASS")

    degraded_cases = [r for r in all_results if r["degraded"]]
    if degraded_cases:
        print(f"\n  DEGRADED 详情:")
        for r in degraded_cases:
            print(f"    {r['id']}: A:{r['a']['verdict']} → B:{r['b']['verdict']} "
                  f"(A:{r['a']['issues']}i B:{r['b']['issues']}i) {r['desc']}")

    improved_cases = [r for r in all_results if r["improved"]]
    if improved_cases:
        print(f"\n  IMPROVED 详情:")
        for r in improved_cases:
            print(f"    {r['id']}: A:{r['a']['verdict']} → B:{r['b']['verdict']} "
                  f"(A:{r['a']['issues']}i→B:{r['b']['issues']}i) {r['desc']}")

# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    try:
        get_llm()
        print("LLM Provider OK")
    except Exception as e:
        print(f"LLM init failed: {e}"); sys.exit(1)

    cases = load_cases()
    total = sum(len(v) for v in cases.values())
    print(f"已加载 {total} 个 case\n")
    print(f"B 组优化策略: Few-Shot 正反例教学")
    print(f"  幻觉: 2正例(无源数据/虚构引用) + 2反例(真实引用/常识)")
    print(f"  数据: 2正例(数据错误) + 2反例(数据正确/无关内容)")
    print(f"  风格: 2正例(口语化) + 2反例(正式文本/合同条款)")
    print()

    ref = get_ref()
    all_results = []

    # 幻觉检测
    h = cases["hallucination"]
    print(f"{'='*60}\n  幻觉检测 ({len(h)} cases)\n{'='*60}")
    hr = run_ab(h, "hallucination")
    all_results.extend(hr)

    # 数据核对
    d = cases["data_check"]
    print(f"\n{'='*60}\n  数据核对 ({len(d)} cases)\n{'='*60}")
    dr = run_ab(d, "data_check", ref=ref)
    all_results.extend(dr)

    # 风格检查
    s = cases["style"]
    print(f"\n{'='*60}\n  风格检查 ({len(s)} cases)\n{'='*60}")
    sr = run_ab(s, "style")
    all_results.extend(sr)

    print_report(all_results)
