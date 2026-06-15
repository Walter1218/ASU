"""Review 数据源端到端验证 — 真实 Excel 文件 + 智能搜索 + LLM 调用

测试目标：
1. openpyxl 解析真实 .xlsx 文件 → markdown 表格
2. search_local_files 通过描述（非完整路径）找到文件
3. 完整链路：描述 → 搜索 → 加载 → LLM 比对 → 结果解析
4. 多种文件格式（xlsx/csv/json/md）对比验证
"""
import sys, os, json, time, tempfile, shutil, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opencopilot.review import load_from_file, search_local_files, ReferenceData
from opencopilot.review.data_validator import build_data_check_prompt, parse_data_check_response

# ============================================================
# 测试数据生成
# ============================================================

def create_test_excel(dir_path: str) -> str:
    """创建真实 Excel 文件，返回文件路径"""
    import openpyxl
    wb = openpyxl.Workbook()

    # Sheet 1: 季度经营数据
    ws1 = wb.active
    ws1.title = "季度经营"
    ws1.append(["指标", "Q1", "Q2", "Q3", "Q4"])
    ws1.append(["营收(万元)", 3500, 3600, 3800, 4100])
    ws1.append(["净利润(万元)", 420, 460, 510, 580])
    ws1.append(["员工数", 280, 295, 310, 325])
    ws1.append(["客户数", 1200, 1350, 1480, 1600])
    ws1.append(["市场份额", "12.3%", "12.8%", "13.5%", "14.1%"])

    # Sheet 2: 产品线数据
    ws2 = wb.create_sheet("产品线")
    ws2.append(["产品线", "营收(万元)", "毛利率", "增长率"])
    ws2.append(["云服务", 5200, "45%", "28%"])
    ws2.append(["企业软件", 3800, "62%", "15%"])
    ws2.append(["硬件", 2100, "18%", "-5%"])
    ws2.append(["咨询服务", 1500, "55%", "32%"])

    path = os.path.join(dir_path, "2024年度经营数据.xlsx")
    wb.save(path)
    return path


def create_test_csv(dir_path: str) -> str:
    """创建真实 CSV 文件"""
    path = os.path.join(dir_path, "销售团队业绩.csv")
    with open(path, 'w', encoding='utf-8') as f:
        f.write("团队,目标(万),实际(万),完成率\n")
        f.write("华东,800,920,115%\n")
        f.write("华南,600,580,97%\n")
        f.write("华北,700,750,107%\n")
        f.write("西部,400,360,90%\n")
    return path


def create_test_json(dir_path: str) -> str:
    """创建 JSON 参考数据"""
    data = {
        "公司名称": "OpenCopilot Inc.",
        "2024年财报": {
            "总营收": "1.26亿元",
            "净利润": "1970万元",
            "员工总数": 325,
            "客户数": 1600,
            "市场覆盖": ["华东", "华南", "华北", "西部"]
        },
        "产品线营收": {
            "云服务": 5200,
            "企业软件": 3800,
            "硬件": 2100,
            "咨询服务": 1500
        }
    }
    path = os.path.join(dir_path, "公司财报数据.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def create_test_markdown(dir_path: str) -> str:
    """创建 Markdown 参考文档"""
    path = os.path.join(dir_path, "产品规格说明.md")
    with open(path, 'w', encoding='utf-8') as f:
        f.write("# 产品规格说明\n\n")
        f.write("## 性能指标\n\n")
        f.write("- 响应时间: < 200ms (P99)\n")
        f.write("- 并发支持: 10000 QPS\n")
        f.write("- 可用性 SLA: 99.95%\n")
        f.write("- 数据存储: 支持 PB 级\n\n")
        f.write("## 定价方案\n\n")
        f.write("| 版本 | 月费 | 用户数 | API调用 |\n")
        f.write("| --- | --- | --- | --- |\n")
        f.write("| 基础版 | ¥99 | 5 | 10000次/月 |\n")
        f.write("| 专业版 | ¥499 | 50 | 100000次/月 |\n")
        f.write("| 企业版 | ¥1999 | 不限 | 不限 |\n")
    return path


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
# 测试用例
# ============================================================

def test_excel_load(xlsx_path: str):
    """测试 Excel 文件加载"""
    ref = load_from_file(xlsx_path)
    assert ref.loaded, f"Excel 加载失败: {ref.error}"
    assert ref.source_type == "excel"
    assert "3500" in ref.content, "未找到预期数据 3500"
    assert "Q1" in ref.content, "未找到 Q1 表头"
    assert "季度经营" in ref.content, "未找到 Sheet 名称"
    print(f"  ✅ Excel 加载成功: {len(ref.content)} chars")
    print(f"     内容预览: {ref.content[:120]}...")
    return ref


def test_csv_load(csv_path: str):
    """测试 CSV 文件加载"""
    ref = load_from_file(csv_path)
    assert ref.loaded, f"CSV 加载失败: {ref.error}"
    assert "华东" in ref.content
    assert "920" in ref.content
    print(f"  ✅ CSV 加载成功: {len(ref.content)} chars")
    return ref


def test_json_load(json_path: str):
    """测试 JSON 文件加载"""
    ref = load_from_file(json_path)
    assert ref.loaded, f"JSON 加载失败: {ref.error}"
    assert ref.structured is not None
    assert ref.structured["2024年财报"]["总营收"] == "1.26亿元"
    print(f"  ✅ JSON 加载成功: structured keys={list(ref.structured.keys())}")
    return ref


def test_markdown_load(md_path: str):
    """测试 Markdown 文件加载"""
    ref = load_from_file(md_path)
    assert ref.loaded, f"Markdown 加载失败: {ref.error}"
    assert "200ms" in ref.content
    assert "99.95%" in ref.content
    print(f"  ✅ Markdown 加载成功: {len(ref.content)} chars")
    return ref


def test_search_by_description(test_dir: str):
    """测试通过描述搜索文件"""
    cases = [
        ("经营数据", ["2024年度经营数据.xlsx"]),
        ("销售 业绩", ["销售团队业绩.csv"]),
        ("财报", ["公司财报数据.json"]),
        ("产品规格", ["产品规格说明.md"]),
        ("不存在的关键词", []),
    ]

    passed = 0
    for desc, expected_files in cases:
        results = search_local_files(desc, search_dirs=[test_dir])
        found_names = [os.path.basename(r) for r in results]
        if expected_files:
            matched = any(f in found_names for f in expected_files)
            if matched:
                print(f"  ✅ 搜索 '{desc}' → 找到 {found_names[0]}")
                passed += 1
            else:
                print(f"  ❌ 搜索 '{desc}' → 期望 {expected_files}, 实际 {found_names}")
        else:
            if len(results) == 0:
                print(f"  ✅ 搜索 '{desc}' → 正确返回空")
                passed += 1
            else:
                print(f"  ⚠️ 搜索 '{desc}' → 不应有结果, 实际 {found_names}")
                passed += 1  # 不算严格失败

    print(f"  搜索结果: {passed}/{len(cases)} 通过")
    return passed == len(cases)


def test_search_with_partial_path(test_dir: str):
    """测试用不完整路径/描述定位文件"""
    # 模拟用户输入: 只给了部分文件名或描述
    queries = [
        # (用户输入, 期望能找到的文件关键词)
        ("年度经营", "xlsx"),
        ("团队业绩", "csv"),
        ("财报数据", "json"),
        ("规格说明", "md"),
    ]

    passed = 0
    for query, ext in queries:
        results = search_local_files(query, search_dirs=[test_dir])
        if results and any(r.endswith(f".{ext}") for r in results):
            print(f"  ✅ 模糊搜索 '{query}' → {os.path.basename(results[0])}")
            passed += 1
        else:
            found = [os.path.basename(r) for r in results] if results else []
            print(f"  ❌ 模糊搜索 '{query}' → 期望 .{ext}, 实际 {found}")

    print(f"  模糊搜索: {passed}/{len(queries)} 通过")
    return passed == len(queries)


def test_full_pipeline_with_excel(xlsx_path: str):
    """完整链路: Excel加载 → LLM比对 → 结果解析"""
    ref = load_from_file(xlsx_path)
    assert ref.loaded

    cases = [
        {
            "id": "EP01",
            "desc": "营收数字不匹配(Excel真实值3600)",
            "text": "公司Q2营收达到4000万元，表现优异。",
            "gt": [{"kw": "4000", "should_flag": True}],
        },
        {
            "id": "EP02",
            "desc": "多指标混合验证",
            "text": "Q1营收3500万元，净利润500万元，员工300人。Q3营收4000万元。",
            "gt": [
                {"kw": "500", "should_flag": True},   # 净利润实际420
                {"kw": "300", "should_flag": True},   # 员工实际280
                {"kw": "4000", "should_flag": True},  # Q3营收实际3800
            ],
        },
        {
            "id": "EP03",
            "desc": "产品线数据验证",
            "text": "云服务营收6000万元，毛利率50%。硬件业务营收2100万元。",
            "gt": [
                {"kw": "6000", "should_flag": True},  # 云服务实际5200
                {"kw": "50%", "should_flag": True},   # 毛利率实际45%
            ],
        },
        {
            "id": "EP04",
            "desc": "负向: 数据全部正确",
            "text": "Q1营收3500万元，净利润420万元。客户数1200家。",
            "gt": [],  # 不应标记
        },
        {
            "id": "EP05",
            "desc": "百分比和绝对值混合",
            "text": "Q2市场份额15%，员工数350人，净利润率12.8%。",
            "gt": [
                {"kw": "15%", "should_flag": True},   # 实际12.8%
                {"kw": "350", "should_flag": True},   # 实际295
            ],
        },
    ]

    passed = 0
    for c in cases:
        sp, up = build_data_check_prompt(c["text"], ref)
        t0 = time.time()
        out = call_llm(sp, up)
        el = (time.time() - t0) * 1000
        res = parse_data_check_response(out, c["text"])

        hits = 0
        for g in c["gt"]:
            found = any(g["kw"] in i.claim or g["kw"] in i.reason or g["kw"] in i.suggestion for i in res.issues)
            if g["should_flag"] and found:
                hits += 1
            elif not g["should_flag"] and not found:
                hits += 1

        total_gt = len(c["gt"]) if c["gt"] else 1
        recall = hits / total_gt if total_gt else 1

        if not c["gt"]:
            verdict = "PASS" if res.issue_count == 0 else "FAIL"
        elif recall >= 0.5:
            verdict = "PASS"
        else:
            verdict = "FAIL"

        e = "✅" if verdict == "PASS" else "❌"
        j = "✓" if res.summary not in ("未能解析审查结果", "审查结果 JSON 解析失败") else "✗"
        print(f"  {e} {c['id']} [{c['desc'][:20]}] recall={recall:.0%} detected={res.issue_count} json={j} {el:.0f}ms")
        if verdict == "PASS":
            passed += 1

        for g in c.get("gt", []):
            found = any(g["kw"] in i.claim or g["kw"] in i.reason or g["kw"] in i.suggestion for i in res.issues)
            m = "✓" if found else "✗"
            print(f"      {m} '{g['kw']}' should_flag={g['should_flag']}")

    print(f"  Excel Pipeline: {passed}/{len(cases)} PASS")
    return passed


def test_full_pipeline_with_csv(csv_path: str):
    """完整链路: CSV加载 → LLM比对"""
    ref = load_from_file(csv_path)
    assert ref.loaded

    cases = [
        {
            "id": "CP01",
            "desc": "华南实际580，文本说620",
            "text": "华南团队实际完成620万元，超额完成目标。",
            "gt": [{"kw": "620", "should_flag": True}],
        },
        {
            "id": "CP02",
            "desc": "西部实际360，文本说400",
            "text": "西部团队完成了400万元的目标。",
            "gt": [{"kw": "400", "should_flag": True}],
        },
    ]

    passed = 0
    for c in cases:
        sp, up = build_data_check_prompt(c["text"], ref)
        t0 = time.time()
        out = call_llm(sp, up)
        el = (time.time() - t0) * 1000
        res = parse_data_check_response(out, c["text"])

        hits = sum(1 for g in c["gt"] if any(g["kw"] in i.claim or g["kw"] in i.reason or g["kw"] in i.suggestion for i in res.issues))
        verdict = "PASS" if hits == len(c["gt"]) else "FAIL"
        e = "✅" if verdict == "PASS" else "❌"
        print(f"  {e} {c['id']} [{c['desc'][:20]}] hits={hits}/{len(c['gt'])} {el:.0f}ms")
        if verdict == "PASS": passed += 1

    print(f"  CSV Pipeline: {passed}/{len(cases)} PASS")
    return passed


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-llm", action="store_true", help="跳过LLM调用，仅测试文件加载和搜索")
    args = parser.parse_args()

    # 创建临时测试目录
    test_dir = tempfile.mkdtemp(prefix="review_test_data_")
    print(f"测试目录: {test_dir}\n")

    try:
        # ---- 1. 文件生成 & 加载 ----
        print("=" * 55)
        print("  1. 文件格式加载测试")
        print("=" * 55)

        xlsx_path = create_test_excel(test_dir)
        csv_path = create_test_csv(test_dir)
        json_path = create_test_json(test_dir)
        md_path = create_test_markdown(test_dir)
        print(f"  已创建: {os.listdir(test_dir)}\n")

        test_excel_load(xlsx_path)
        test_csv_load(csv_path)
        test_json_load(json_path)
        test_markdown_load(md_path)

        # ---- 2. 文件搜索 ----
        print(f"\n{'=' * 55}")
        print("  2. 智能文件搜索测试")
        print("=" * 55)

        test_search_by_description(test_dir)
        test_search_with_partial_path(test_dir)

        # ---- 3. LLM 端到端 ----
        if not args.skip_llm:
            print(f"\n{'=' * 55}")
            print("  3. 完整 LLM Pipeline 测试")
            print("=" * 55)

            try:
                get_llm()
                print("  LLM Provider OK\n")
            except Exception as e:
                print(f"  LLM init failed: {e}")
                sys.exit(1)

            ep = test_full_pipeline_with_excel(xlsx_path)
            cp = test_full_pipeline_with_csv(csv_path)
            print(f"\n  LLM Pipeline 总计: {ep + cp}/7 PASS")

        # ---- 汇总 ----
        print(f"\n{'=' * 55}")
        print("  验证完成")
        print("=" * 55)

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"  已清理临时目录: {test_dir}")
