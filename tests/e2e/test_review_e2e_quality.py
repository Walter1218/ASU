"""Review Pipeline 100-case 端到端验证 — 真实 LLM 调用 + Ground Truth 评估

从 review_test_cases.json 加载 100 个 case，覆盖：
- 幻觉检测 35 case
- 数据交叉验证 35 case
- 风格检查 30 case

运行：
  python tests/e2e/test_review_e2e_quality.py              # 全量
  python tests/e2e/test_review_e2e_quality.py --type hallucination
"""
import sys, os, json, time, tempfile, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opencopilot.review.hallucination import build_hallucination_prompt, parse_hallucination_response
from opencopilot.review.data_validator import build_data_check_prompt, parse_data_check_response
from opencopilot.review.style_checker import build_style_prompt, parse_style_response
from opencopilot.review import load_from_file, ReferenceData

# ============================================================
# 加载测试集
# ============================================================

_CASES_DIR = os.path.dirname(os.path.abspath(__file__))
_CASES_FILES = ["review_test_cases.json", "review_test_cases_v2.json"]

def load_cases():
    """加载并合并多个 case 文件"""
    merged = {"hallucination": [], "data_check": [], "style": []}
    for fname in _CASES_FILES:
        path = os.path.join(_CASES_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in merged:
            merged[key].extend(data.get(key, []))
    total = sum(len(v) for v in merged.values())
    print(f"  已加载 {total} 个 case (幻觉{len(merged['hallucination'])} + 数据{len(merged['data_check'])} + 风格{len(merged['style'])})")
    return merged

# ============================================================
# 参考数据（数据核对用）
# ============================================================

_REF_CSV = """指标,Q1,Q2,Q3,Q4
营收(万元),3500,3600,3800,4100
净利润(万元),420,460,510,580
员工数,280,295,310,325
客户数,1200,1350,1480,1600
市场份额,12.3%,12.8%,13.5%,14.1%
"""

_ref_path = None
def get_ref():
    global _ref_path
    if _ref_path is None:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(_REF_CSV)
            _ref_path = f.name
    return load_from_file(_ref_path)

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

def evaluate(case, result, elapsed_ms):
    gt = case["ground_truth"]
    r = {"id": case["id"], "desc": case["desc"][:25], "gt_count": len(gt),
         "detected": result.issue_count, "elapsed": round(elapsed_ms), "json_ok": True}

    if result.summary in ("未能解析审查结果", "审查结果 JSON 解析失败", "审查结果格式异常"):
        r["json_ok"] = False
        r["recall"] = 0.0; r["fpr"] = 1.0; r["verdict"] = "FAIL"
        return r

    hits = 0
    details = []
    for g in gt:
        kw = g["keyword"]
        found = any(kw in i.claim or kw in i.reason or kw in i.suggestion for i in result.issues)
        details.append({"kw": kw, "found": found})
        if found: hits += 1

    recall = hits / len(gt) if gt else 1.0
    if gt:
        fp = max(0, result.issue_count - hits)
        fpr = fp / max(result.issue_count, 1)
    else:
        fp = result.issue_count
        fpr = 1.0 if result.issue_count > 0 else 0.0

    r["recall"] = round(recall, 2); r["fpr"] = round(fpr, 2)
    r["hits"] = hits; r["fp"] = fp; r["details"] = details

    if not gt:
        r["verdict"] = "PASS" if result.issue_count == 0 else "FAIL"
    elif recall >= 0.5 and fpr <= 0.5:
        r["verdict"] = "PASS"
    elif recall >= 0.3:
        r["verdict"] = "PARTIAL"
    else:
        r["verdict"] = "FAIL"
    return r

# ============================================================
# 运行
# ============================================================

def run_hallucination(cases):
    results = []
    for c in cases:
        ctx = c.get("context", "")
        sp, up = build_hallucination_prompt(c["text"], ctx)
        t0 = time.time()
        out = call_llm(sp, up)
        el = (time.time() - t0) * 1000
        res = parse_hallucination_response(out, c["text"])
        ev = evaluate(c, res, el)
        results.append(ev)
        _print(ev)
    return results

def run_data_check(cases):
    ref = get_ref()
    results = []
    for c in cases:
        sp, up = build_data_check_prompt(c["text"], ref)
        t0 = time.time()
        out = call_llm(sp, up)
        el = (time.time() - t0) * 1000
        res = parse_data_check_response(out, c["text"])
        ev = evaluate(c, res, el)
        results.append(ev)
        _print(ev)
    return results

def run_style(cases):
    results = []
    for c in cases:
        sp, up = build_style_prompt(c["text"], c.get("style_target", ""))
        t0 = time.time()
        out = call_llm(sp, up)
        el = (time.time() - t0) * 1000
        res = parse_style_response(out, c["text"])
        ev = evaluate(c, res, el)
        results.append(ev)
        _print(ev)
    return results

def _print(ev):
    e = {"PASS":"✅","PARTIAL":"⚠️","FAIL":"❌"}[ev["verdict"]]
    j = "✓" if ev["json_ok"] else "✗"
    print(f"  {e} {ev['id']} [{ev['desc']}] recall={ev['recall']:.0%} fpr={ev['fpr']:.0%} json={j} {ev['elapsed']}ms")
    for d in ev.get("details", []):
        m = "✓" if d["found"] else "✗"
        print(f"      {m} '{d['kw']}'")

# ============================================================
# 报告
# ============================================================

def report(all_results):
    total = len(all_results)
    passed = sum(1 for r in all_results if r["verdict"]=="PASS")
    partial = sum(1 for r in all_results if r["verdict"]=="PARTIAL")
    failed = sum(1 for r in all_results if r["verdict"]=="FAIL")
    jok = sum(1 for r in all_results if r["json_ok"])
    recalls = [r["recall"] for r in all_results if r["gt_count"]>0]
    avg_r = sum(recalls)/len(recalls) if recalls else 0
    avg_t = sum(r["elapsed"] for r in all_results)/total if total else 0

    print("\n"+"="*65)
    print("  Review Pipeline E2E 验证报告 (100-case)")
    print("="*65)
    print(f"  总 case: {total}  |  PASS: {passed}  PARTIAL: {partial}  FAIL: {failed}")
    print(f"  JSON合法: {jok}/{total} ({jok/total:.0%})")
    print(f"  平均Recall: {avg_r:.0%}  |  平均耗时: {avg_t:.0f}ms")
    print("-"*65)

    for label, rtype in [("幻觉检测","hallucination"),("数据核对","data_check"),("风格检查","style")]:
        sub = [r for r in all_results if r.get("_type")==rtype]
        if not sub: continue
        sp = sum(1 for r in sub if r["verdict"]=="PASS")
        sr = [r["recall"] for r in sub if r["gt_count"]>0]
        ar = sum(sr)/len(sr) if sr else 0
        at = sum(r["elapsed"] for r in sub)/len(sub)
        print(f"\n  [{label}] {sp}/{len(sub)} PASS, recall={ar:.0%}, avg={at:.0f}ms")

    print("\n"+"="*65)
    fails = [r for r in all_results if r["verdict"] in ("FAIL","PARTIAL")]
    if fails:
        print("\n  待改进:")
        for r in fails:
            print(f"    {r['id']}: recall={r['recall']:.0%} fpr={r['fpr']:.0%} ({r['desc']})")
    print()

# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["hallucination","data_check","style","all"], default="all")
    args = parser.parse_args()

    try:
        get_llm()
        print("LLM Provider OK\n")
    except Exception as e:
        print(f"LLM init failed: {e}"); sys.exit(1)

    cases = load_cases()
    all_results = []

    if args.type in ("hallucination","all"):
        h = cases["hallucination"]
        print(f"\n{'='*50}\n  幻觉检测 ({len(h)} cases)\n{'='*50}")
        res = run_hallucination(h)
        for r in res: r["_type"]="hallucination"
        all_results.extend(res)

    if args.type in ("data_check","all"):
        d = cases["data_check"]
        print(f"\n{'='*50}\n  数据交叉验证 ({len(d)} cases)\n{'='*50}")
        res = run_data_check(d)
        for r in res: r["_type"]="data_check"
        all_results.extend(res)

    if args.type in ("style","all"):
        s = cases["style"]
        print(f"\n{'='*50}\n  风格检查 ({len(s)} cases)\n{'='*50}")
        res = run_style(s)
        for r in res: r["_type"]="style"
        all_results.extend(res)

    report(all_results)

    if _ref_path and os.path.exists(_ref_path):
        os.unlink(_ref_path)
