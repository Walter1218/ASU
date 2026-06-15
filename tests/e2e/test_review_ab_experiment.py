"""Review Prompt AB 实验 — 同数据集对比 A(基线) vs B(精度优化)

实验设计：
- A 组：当前 prompt（基线）
- B 组：增加精度控制指令（克制原则 + 内容过滤）
- 同一 200-case 数据集，同 LLM，逐 case 对比

聚焦：B 组在负向 case 上是否降低误报，正向 case 上是否保持 recall

运行：python tests/e2e/test_review_ab_experiment.py
"""
import sys, os, json, time, argparse, tempfile, shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opencopilot.review import load_from_file, ReferenceData
from opencopilot.review.hallucination import parse_hallucination_response
from opencopilot.review.data_validator import parse_data_check_response, build_data_check_prompt
from opencopilot.review.style_checker import parse_style_response

# ============================================================
# A 组 Prompt（当前基线）
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
# B 组 Prompt（精度优化版）
# ============================================================

B_HALLUCINATION_SYS = """你是一个严格的事实审查员。你的任务是审查给定文本中的事实声明，检查是否存在以下问题：

1. **无源数据**：声明了具体数字/日期/比例，但没有给出数据来源（注意：如果文本已标注来源如"根据XX报告"则不算无源数据）
2. **逻辑矛盾**：文本内部存在前后矛盾的声明
3. **不合理数值**：数值明显不合理（如"市场份额 120%"）
4. **虚构引用**：引用了不存在的研究、报告、机构名称
5. **时间线错误**：事件发生时间与已知事实不符

请严格输出 JSON 格式，不要输出其他内容。"""

B_DATA_CHECK_SYS = """你是一个数据审查员。你的任务是将「待审查文本」中的数值声明与「参考数据」进行逐条比对，找出不一致之处。

审查重点：
1. 数字不匹配：文本中的数字与参考数据不一致
2. 单位/口径错误：如"万元"vs"元"、"含税"vs"不含税"
3. 时间范围错误：如文本说"2025年"但参考数据是"2024年"
4. 遗漏关键数据：文本遗漏了参考数据中的重要数字
5. 百分比计算错误：增长率、占比等衍生数据计算有误（偏差<2个百分点的合理四舍五入不视为错误）

请严格输出 JSON 格式。"""

B_STYLE_SYS = """你是一个专业的文本风格审查员。你的任务是检查文本的风格问题，包括：

1. **语气不当**：正式文档中出现口语化表达
2. **用词不专业**：使用了非行业术语或模糊表述（注意：KPI、ROI、API、ARR、GMV等常见商业/技术缩写不算不专业）
3. **句式问题**：过长的句子、重复句式、逻辑连接不当
4. **一致性**：同一概念使用不同术语、格式不统一
5. **冗余表达**：可以精简的啰嗦表述

请严格输出 JSON 格式。"""

# ============================================================
# 通用 user prompt 模板
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

def call_llm(sys_p, usr_p, timeout=60):
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
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(_REF_CSV); _ref = load_from_file(f.name)
    return _ref

# ============================================================
# AB 实验执行器
# ============================================================

def run_ab(cases, review_type, a_sys, b_sys, user_tpl, parser_fn, ref=None, extra_kwargs=None):
    """对一组 case 运行 A/B 两组 prompt"""
    results = []
    total = len(cases)
    for i, c in enumerate(cases):
        # 构建 user prompt
        kwargs = {"text": c["text"]}
        if extra_kwargs:
            kwargs.update(extra_kwargs(c))
        if review_type == "hallucination":
            ctx = c.get("context", "")
            kwargs["context_section"] = f"--- 上下文 ---\n{ctx}\n" if ctx else ""
        elif review_type == "data_check" and ref:
            kwargs["reference_content"] = ref.to_context_string()
        user_p = user_tpl.format(**kwargs)

        # A 组
        t0 = time.time()
        a_out = call_llm(a_sys, user_p)
        a_ms = (time.time() - t0) * 1000
        a_res = parser_fn(a_out, c["text"])
        a_ev = evaluate(c, a_res)

        # B 组
        t0 = time.time()
        b_out = call_llm(b_sys, user_p)
        b_ms = (time.time() - t0) * 1000
        b_res = parser_fn(b_out, c["text"])
        b_ev = evaluate(c, b_res)

        # 判断是否有变化
        changed = a_ev["verdict"] != b_ev["verdict"]
        improved = (a_ev["verdict"] != "PASS" and b_ev["verdict"] == "PASS")
        degraded = (a_ev["verdict"] == "PASS" and b_ev["verdict"] != "PASS")

        result = {
            "id": c["id"], "desc": c["desc"][:25], "gt_count": len(c["ground_truth"]),
            "a": {**a_ev, "ms": round(a_ms)},
            "b": {**b_ev, "ms": round(b_ms)},
            "changed": changed, "improved": improved, "degraded": degraded,
        }
        results.append(result)

        # 打印
        marker = ""
        if improved: marker = " 🟢"
        elif degraded: marker = " 🔴"
        elif changed: marker = " 🟡"

        a_sym = {"PASS":"✅","PARTIAL":"⚠️","FAIL":"❌"}[a_ev["verdict"]]
        b_sym = {"PASS":"✅","PARTIAL":"⚠️","FAIL":"❌"}[b_ev["verdict"]]
        print(f"  {c['id']} A:{a_sym} B:{b_sym}{marker} "
              f"A:rec={a_ev['recall']:.0%}/fpr={a_ev['fpr']:.0%} "
              f"B:rec={b_ev['recall']:.0%}/fpr={b_ev['fpr']:.0%}")

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
    unchanged = total - improved - degraded

    a_recalls = [r["a"]["recall"] for r in all_results if r["gt_count"] > 0]
    b_recalls = [r["b"]["recall"] for r in all_results if r["gt_count"] > 0]
    a_avg_r = sum(a_recalls) / len(a_recalls) if a_recalls else 0
    b_avg_r = sum(b_recalls) / len(b_recalls) if b_recalls else 0

    # 负向 case 单独统计
    neg_cases = [r for r in all_results if r["gt_count"] == 0]
    a_neg_pass = sum(1 for r in neg_cases if r["a"]["verdict"] == "PASS")
    b_neg_pass = sum(1 for r in neg_cases if r["b"]["verdict"] == "PASS")

    a_ms = sum(r["a"]["ms"] for r in all_results) / total
    b_ms = sum(r["b"]["ms"] for r in all_results) / total

    print(f"\n{'='*65}")
    print("  AB 实验报告")
    print(f"{'='*65}")
    print(f"  总 case: {total}")
    print(f"{'─'*65}")
    print(f"  {'指标':<20} {'A (基线)':>12} {'B (优化)':>12} {'变化':>10}")
    print(f"{'─'*65}")
    print(f"  {'PASS 数':<20} {a_pass:>12} {b_pass:>12} {b_pass-a_pass:>+10}")
    print(f"  {'PASS 率':<20} {a_pass/total:>11.0%} {b_pass/total:>11.0%} {(b_pass-a_pass)/total:>+9.0%}")
    print(f"  {'平均 Recall':<20} {a_avg_r:>11.0%} {b_avg_r:>11.0%} {b_avg_r-a_avg_r:>+9.0%}")
    print(f"  {'平均耗时(ms)':<20} {a_ms:>12.0f} {b_ms:>12.0f} {b_ms-a_ms:>+10.0f}")
    print(f"{'─'*65}")
    print(f"  {'负向 case PASS':<20} {a_neg_pass:>12} {b_neg_pass:>12} {b_neg_pass-a_neg_pass:>+10}")
    neg_total = len(neg_cases)
    if neg_total:
        print(f"  {'负向 PASS 率':<20} {a_neg_pass/neg_total:>11.0%} {b_neg_pass/neg_total:>11.0%} {(b_neg_pass-a_neg_pass)/neg_total:>+9.0%}")
    print(f"{'─'*65}")
    print(f"  改善 (FAIL→PASS): {improved}")
    print(f"  退化 (PASS→FAIL): {degraded}")
    print(f"  无变化: {unchanged}")
    print(f"{'='*65}")

    # 按类型分组
    for rtype in ["hallucination", "data_check", "style"]:
        sub = [r for r in all_results if r["id"][0] == {"hallucination":"H","data_check":"D","style":"S"}[rtype]]
        if not sub: continue
        ap = sum(1 for r in sub if r["a"]["verdict"]=="PASS")
        bp = sum(1 for r in sub if r["b"]["verdict"]=="PASS")
        imp = sum(1 for r in sub if r["improved"])
        deg = sum(1 for r in sub if r["degraded"])
        label = {"hallucination":"幻觉检测","data_check":"数据核对","style":"风格检查"}[rtype]
        print(f"\n  [{label}] A:{ap}/{len(sub)} → B:{bp}/{len(sub)}  改善:{imp} 退化:{deg}")

    # 列出退化 case（如有）
    degraded_cases = [r for r in all_results if r["degraded"]]
    if degraded_cases:
        print(f"\n  ⚠️ 退化 case 详情:")
        for r in degraded_cases:
            print(f"    {r['id']}: A:PASS → B:{r['b']['verdict']} "
                  f"(A:rec={r['a']['recall']:.0%} B:rec={r['b']['recall']:.0%}) {r['desc']}")

    # 列出改善 case
    improved_cases = [r for r in all_results if r["improved"]]
    if improved_cases:
        print(f"\n  🟢 改善 case 详情:")
        for r in improved_cases:
            print(f"    {r['id']}: A:{r['a']['verdict']} → B:PASS "
                  f"(A:fpr={r['a']['fpr']:.0%} B:fpr={r['b']['fpr']:.0%}) {r['desc']}")

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

    ref = get_ref()
    all_results = []

    # 幻觉检测 AB
    h = cases["hallucination"]
    print(f"{'='*55}\n  幻觉检测 AB ({len(h)} cases)\n{'='*55}")
    hr = run_ab(h, "hallucination", A_HALLUCINATION_SYS, B_HALLUCINATION_SYS,
                HALLUCINATION_USER_TPL, parse_hallucination_response)
    all_results.extend(hr)

    # 数据核对 AB
    d = cases["data_check"]
    print(f"\n{'='*55}\n  数据核对 AB ({len(d)} cases)\n{'='*55}")
    dr = run_ab(d, "data_check", A_DATA_CHECK_SYS, B_DATA_CHECK_SYS,
                DATA_CHECK_USER_TPL, parse_data_check_response, ref=ref)
    all_results.extend(dr)

    # 风格检查 AB
    s = cases["style"]
    print(f"\n{'='*55}\n  风格检查 AB ({len(s)} cases)\n{'='*55}")
    sr = run_ab(s, "style", A_STYLE_SYS, B_STYLE_SYS,
                STYLE_USER_TPL, parse_style_response,
                extra_kwargs=lambda c: {"style_target": c.get("style_target", "b2b_formal")})
    all_results.extend(sr)

    print_report(all_results)
