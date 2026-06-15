"""Review 迭代优化 AB 实验 — A(基线评估) vs B(迭代优化评估)

实验设计：
- A 组：A prompts + 原始评估逻辑
- B 组：三次迭代组合优化
  - 迭代1: 置信度后置过滤（hallucination≥0.6, data_check≥0.5, style≥0.4）
  - 迭代2: 幻觉检测两阶段验证（对 flagged fabricated_reference 二次确认）
  - 迭代3: 风格检查 prompt 微调（缩写豁免 + 负向约束）

运行：python tests/e2e/test_review_iteration_ab.py
"""
import sys, os, json, time, copy, logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opencopilot.review import load_from_file, ReferenceData
from opencopilot.review.hallucination import parse_hallucination_response
from opencopilot.review.data_validator import parse_data_check_response, build_data_check_prompt
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
# B 组迭代3 Prompt（风格检查优化版）
# ============================================================

B_STYLE_SYS = """你是一个专业的文本风格审查员。你的任务是检查文本的风格问题，包括：

1. **语气不当**：正式文档中出现口语化表达
2. **用词不专业**：使用了非行业术语或模糊表述（注意：KPI、ROI、API、ARR、GMV等常见商业/技术缩写不算不专业）
3. **句式问题**：过长的句子、重复句式、逻辑连接不当
4. **一致性**：同一概念使用不同术语、格式不统一
5. **冗余表达**：可以精简的啰嗦表述

请严格输出 JSON 格式。"""

# 幻觉检测二阶段验证 prompt
VERIFY_REF_SYS = """你是一个事实核查助手。请判断以下引用来源是否真实存在或合理。

对每个引用来源，输出 JSON 对象：
- "source": 原始引用文本
- "is_real": true/false（是否真实存在）
- "reason": 判断理由（一句话）

如果引用来源是真实存在的（如真实法律条款、真实机构报告），输出 is_real: true。
如果引用来源是虚构的（如不存在的研究报告、伪造的法律条款），输出 is_real: false。
如果无法确定，输出 is_real: false 并在 reason 中说明"无法确认"。

请严格输出 JSON 数组。"""

# ============================================================
# User prompt 模板
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
# 迭代1：置信度后置过滤
# ============================================================

CONFIDENCE_THRESHOLDS = {
    "hallucination": 0.6,   # 幻觉检测：过滤 <0.6 的低置信度 issue
    "data_check": 0.5,      # 数据核对：过滤 <0.5
    "style": 0.4,           # 风格检查：过滤 <0.4
}

def filter_by_confidence(result, review_type):
    """过滤低置信度 issue，返回新 ReviewResult"""
    threshold = CONFIDENCE_THRESHOLDS.get(review_type, 0.5)
    filtered = copy.deepcopy(result)
    filtered.issues = [i for i in result.issues if i.confidence >= threshold]
    # 更新 summary
    if not filtered.issues:
        if review_type == "hallucination":
            filtered.summary = "未发现明显问题"
        elif review_type == "data_check":
            filtered.summary = "数据与参考文档一致"
        else:
            filtered.summary = "风格符合目标要求"
    return filtered

# ============================================================
# 迭代2：幻觉检测两阶段验证
# ============================================================

def verify_fabricated_references(result):
    """对 flagged 的 fabricated_reference/unsupported_claim 进行二次验证"""
    # 找出需要验证的 issue
    to_verify = [i for i in result.issues
                 if i.issue_type in ("fabricated_reference", "unsupported_claim")
                 and i.confidence >= CONFIDENCE_THRESHOLDS["hallucination"]]
    if not to_verify:
        return result

    # 构建验证 prompt
    sources = [f"{idx+1}. {i.claim}" for idx, i in enumerate(to_verify)]
    user_p = "请验证以下引用来源是否真实存在：\n\n" + "\n".join(sources)

    raw = call_llm(VERIFY_REF_SYS, user_p, timeout=30)

    # 解析验证结果
    import re
    json_match = re.search(r'\[[\s\S]*\]', raw)
    if not json_match:
        return result

    try:
        verifications = json.loads(json_match.group())
    except:
        return result

    if not isinstance(verifications, list):
        return result

    # 根据验证结果过滤
    verified = copy.deepcopy(result)
    real_claims = set()
    for v in verifications:
        if isinstance(v, dict) and v.get("is_real", False):
            src = v.get("source", "")
            # 匹配原始 issue
            for i in to_verify:
                if src and (src in i.claim or i.claim in src):
                    real_claims.add(i.claim)

    verified.issues = [i for i in result.issues if i.claim not in real_claims]
    if not verified.issues:
        verified.summary = "未发现明显问题"
    return verified

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

import tempfile

# ============================================================
# AB 执行器
# ============================================================

def run_ab(cases, review_type, ref=None):
    """对一组 case 运行 A/B 评估"""
    results = []
    total = len(cases)
    two_stage_count = 0  # 二阶段验证触发次数

    for i, c in enumerate(cases):
        # 构建 user prompt
        kwargs = {"text": c["text"]}
        if review_type == "hallucination":
            ctx = c.get("context", "")
            kwargs["context_section"] = f"--- 上下文 ---\n{ctx}\n" if ctx else ""
            style_target = None
        elif review_type == "data_check" and ref:
            kwargs["reference_content"] = ref.to_context_string()
            style_target = None
        else:
            kwargs["style_target"] = c.get("style_target", "b2b_formal")

        # A 组：A prompt + A eval
        a_user = HALLUCINATION_USER_TPL.format(**kwargs) if review_type == "hallucination" else \
                 DATA_CHECK_USER_TPL.format(**kwargs) if review_type == "data_check" else \
                 STYLE_USER_TPL.format(**kwargs)

        t0 = time.time()
        a_raw = call_llm(A_HALLUCINATION_SYS if review_type == "hallucination" else
                        A_DATA_CHECK_SYS if review_type == "data_check" else
                        A_STYLE_SYS, a_user)
        a_ms = (time.time() - t0) * 1000

        a_parser = parse_hallucination_response if review_type == "hallucination" else \
                   parse_data_check_response if review_type == "data_check" else \
                   parse_style_response
        a_res = a_parser(a_raw, c["text"])
        a_ev = evaluate(c, a_res)

        # B 组：迭代优化
        # 迭代1：置信度过滤
        b_res = filter_by_confidence(a_res, review_type)
        b_ms = a_ms  # 同一 LLM 调用，无额外耗时

        # 迭代2：幻觉检测二阶段（仅对有 flagged issue 的触发）
        if review_type == "hallucination" and b_res.issue_count > 0:
            flagged_types = [it for it in b_res.issues if it.issue_type in ("fabricated_reference", "unsupported_claim")]
            if flagged_types:
                two_stage_count += 1
                t0 = time.time()
                b_res = verify_fabricated_references(b_res)
                b_ms += (time.time() - t0) * 1000

        # 迭代3：风格检查用 B prompt 重跑
        if review_type == "style":
            b_user = STYLE_USER_TPL.format(**kwargs)
            t0 = time.time()
            b_raw = call_llm(B_STYLE_SYS, b_user)
            b_ms = (time.time() - t0) * 1000
            b_res = parse_style_response(b_raw, c["text"])
            b_res = filter_by_confidence(b_res, "style")

        b_ev = evaluate(c, b_res)

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

    if review_type == "hallucination":
        print(f"  [二阶段验证触发: {two_stage_count} 次]")

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

    print(f"\n{'='*65}")
    print("  迭代优化 AB 实验报告")
    print(f"{'='*65}")
    print(f"  总 case: {total}")
    print(f"  B 优化策略: 置信度过滤 + 幻觉二阶段验证 + 风格prompt微调")
    print(f"{'─'*65}")
    print(f"  {'指标':<20} {'A (基线)':>12} {'B (优化)':>12} {'变化':>10}")
    print(f"{'─'*65}")
    print(f"  {'PASS 数':<20} {a_pass:>12} {b_pass:>12} {b_pass-a_pass:>+10}")
    print(f"  {'PASS 率':<20} {a_pass/total:>11.0%} {b_pass/total:>11.0%} {(b_pass-a_pass)/total:>+9.0%}")
    print(f"  {'平均 Recall':<20} {a_avg_r:>11.0%} {b_avg_r:>11.0%} {b_avg_r-a_avg_r:>+9.0%}")
    print(f"  {'平均耗时(ms)':<20} {a_ms:>12.0f} {b_ms:>12.0f} {b_ms-a_ms:>+10.0f}")
    print(f"{'─'*65}")
    print(f"  {'负向 case PASS':<20} {a_neg_pass:>12} {b_neg_pass:>12} {b_neg_pass-a_neg_pass:>+10}")
    if neg_cases:
        print(f"  {'负向 PASS 率':<20} {a_neg_pass/len(neg_cases):>11.0%} {b_neg_pass/len(neg_cases):>11.0%} {(b_neg_pass-a_neg_pass)/len(neg_cases):>+9.0%}")
    print(f"{'─'*65}")
    print(f"  改善 (FAIL→PASS): {improved}")
    print(f"  退化 (PASS→FAIL): {degraded}")
    print(f"  无变化: {total - improved - degraded}")
    print(f"{'='*65}")

    for rtype, label, prefix in [("hallucination","幻觉检测","H"),("data_check","数据核对","D"),("style","风格检查","S")]:
        sub = [r for r in all_results if r["id"][0] == prefix]
        if not sub: continue
        ap = sum(1 for r in sub if r["a"]["verdict"]=="PASS")
        bp = sum(1 for r in sub if r["b"]["verdict"]=="PASS")
        imp = sum(1 for r in sub if r["improved"])
        deg = sum(1 for r in sub if r["degraded"])
        print(f"\n  [{label}] A:{ap}/{len(sub)} → B:{bp}/{len(sub)}  改善:{imp} 退化:{deg}")

    degraded_cases = [r for r in all_results if r["degraded"]]
    if degraded_cases:
        print(f"\n  ⚠️ 退化 case 详情:")
        for r in degraded_cases:
            print(f"    {r['id']}: A:PASS → B:{r['b']['verdict']} "
                  f"(A:rec={r['a']['recall']:.0%} B:rec={r['b']['recall']:.0%}) {r['desc']}")

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
    print(f"优化策略:")
    print(f"  迭代1: 置信度过滤 (H≥0.6, D≥0.5, S≥0.4)")
    print(f"  迭代2: 幻觉检测二阶段验证")
    print(f"  迭代3: 风格检查 prompt 微调 (缩写豁免)")
    print()

    ref = get_ref()
    all_results = []

    # 幻觉检测
    h = cases["hallucination"]
    print(f"{'='*55}\n  幻觉检测 ({len(h)} cases)\n{'='*55}")
    hr = run_ab(h, "hallucination")
    all_results.extend(hr)

    # 数据核对
    d = cases["data_check"]
    print(f"\n{'='*55}\n  数据核对 ({len(d)} cases)\n{'='*55}")
    dr = run_ab(d, "data_check", ref=ref)
    all_results.extend(dr)

    # 风格检查
    s = cases["style"]
    print(f"\n{'='*55}\n  风格检查 ({len(s)} cases)\n{'='*55}")
    sr = run_ab(s, "style")
    all_results.extend(sr)

    print_report(all_results)
