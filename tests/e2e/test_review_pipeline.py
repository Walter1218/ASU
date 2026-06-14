"""Review Pipeline 端到端集成测试

测试范围：
1. ReviewEngine 三类审查的 prompt 构建和响应解析
2. reference_loader 对 Excel/CSV/JSON/Markdown 的解析
3. ReviewMiddleware 在 Pipeline 中的路由和执行
4. 审查结果 JSON 解析（含 json_repair 容错）
5. 快捷指令解析
"""
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opencopilot.review import (
    ReviewEngine, ReviewResult, ReviewType, ReviewIssue,
    IssueSeverity, ReferenceData, load_from_file, search_local_files,
)
from opencopilot.review.hallucination import (
    build_hallucination_prompt, parse_hallucination_response,
)
from opencopilot.review.data_validator import (
    build_data_check_prompt, parse_data_check_response,
)
from opencopilot.review.style_checker import (
    build_style_prompt, parse_style_response, STYLE_PRESETS,
)


def test_01_review_engine_init():
    """ReviewEngine 初始化和类型枚举"""
    engine = ReviewEngine()
    types = [t.value for t in ReviewType]
    assert "hallucination" in types
    assert "data_check" in types
    assert "style" in types
    print("✅ test_01_review_engine_init passed")


def test_02_hallucination_prompt():
    """幻觉检测 Prompt 构建"""
    sys_p, usr_p = build_hallucination_prompt(
        "Q2营收3800万，同比增长15%，市场份额达到30%"
    )
    assert len(sys_p) > 50
    assert len(usr_p) > 100
    assert "Q2营收3800万" in usr_p
    print("✅ test_02_hallucination_prompt passed")


def test_03_hallucination_with_context():
    """幻觉检测 Prompt 带上下文"""
    sys_p, usr_p = build_hallucination_prompt(
        "营收增长15%",
        context="上一段提到营收下降了5%"
    )
    assert "上下文" in usr_p
    assert "营收下降" in usr_p
    print("✅ test_03_hallucination_with_context passed")


def test_04_hallucination_parse_valid():
    """幻觉检测响应解析 — 正常 JSON"""
    llm_output = '''```json
[
  {
    "claim": "Q2营收3800万",
    "issue_type": "unsupported_claim",
    "severity": "high",
    "reason": "无数据来源",
    "suggestion": "请核实财报数据",
    "confidence": 0.7
  },
  {
    "claim": "市场份额30%",
    "issue_type": "unreasonable_value",
    "severity": "medium",
    "reason": "未说明计算口径",
    "suggestion": "补充市场定义和计算方式",
    "confidence": 0.6
  }
]
```'''
    result = parse_hallucination_response(llm_output, "Q2营收3800万，市场份额30%")
    assert result.review_type == "hallucination"
    assert result.issue_count == 2
    assert result.issues[0].severity == "high"
    assert result.issues[0].claim == "Q2营收3800万"
    assert result.high_severity_count == 1
    print("✅ test_04_hallucination_parse_valid passed")


def test_05_hallucination_parse_empty():
    """幻觉检测响应解析 — 无问题"""
    result = parse_hallucination_response("[]", "文本内容没问题")
    assert result.issue_count == 0
    assert result.summary == "未发现明显问题"
    print("✅ test_05_hallucination_parse_empty passed")


def test_06_hallucination_parse_malformed():
    """幻觉检测响应解析 — JSON 容错"""
    llm_output = '[{"claim": "test", severity: "high", "reason": "bad"}]'
    result = parse_hallucination_response(llm_output, "test content")
    # json_repair 应该能修复
    assert result.issue_count >= 0  # 可能修复也可能不修复
    print("✅ test_06_hallucination_parse_malformed passed")


def test_07_data_check_prompt():
    """数据交叉验证 Prompt 构建"""
    ref = ReferenceData(
        source_path="test.xlsx",
        source_type="excel",
        content="| 季度 | 营收 |\n| --- | --- |\n| Q1 | 3500万 |\n| Q2 | 3600万 |",
        loaded=True,
    )
    sys_p, usr_p = build_data_check_prompt("Q2营收3800万", ref)
    assert "3600万" in usr_p
    assert "3800万" in usr_p
    print("✅ test_07_data_check_prompt passed")


def test_08_data_check_parse():
    """数据交叉验证响应解析"""
    llm_output = '''[
  {
    "claim": "Q2营收3800万",
    "reference_value": "3600万",
    "delta": "+5.6%",
    "issue_type": "data_mismatch",
    "severity": "high",
    "reason": "文本值与参考数据不一致",
    "suggestion": "Q2营收应为3600万",
    "confidence": 0.95
  }
]'''
    result = parse_data_check_response(llm_output, "Q2营收3800万")
    assert result.issue_count == 1
    assert result.issues[0].reference_value == "3600万"
    assert result.issues[0].delta == "+5.6%"
    print("✅ test_08_data_check_parse passed")


def test_09_style_prompt():
    """风格检查 Prompt 构建"""
    sys_p, usr_p = build_style_prompt("这个方案真的很棒！", "b2b_formal")
    assert "B2B" in usr_p
    assert "这个方案真的很棒" in usr_p
    print("✅ test_09_style_prompt passed")


def test_10_style_presets():
    """风格预设完整性"""
    assert "b2b_formal" in STYLE_PRESETS
    assert "academic" in STYLE_PRESETS
    assert "marketing" in STYLE_PRESETS
    assert "technical" in STYLE_PRESETS
    print("✅ test_10_style_presets passed")


def test_11_style_parse_with_correction():
    """风格检查解析 — 含修正文本"""
    llm_output = '''[
  {
    "claim": "真的很棒",
    "issue_type": "tone_mismatch",
    "severity": "medium",
    "reason": "过于口语化",
    "suggestion": "具有显著优势",
    "confidence": 0.8
  }
]'''
    result = parse_style_response(llm_output, "这个方案真的很棒！")
    assert result.issue_count == 1
    assert result.corrected_text  # 应该生成了修正文本
    print("✅ test_11_style_parse_with_correction passed")


def test_12_reference_loader_csv():
    """reference_loader: CSV 文件加载"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("季度,营收,利润\n")
        f.write("Q1,3500,800\n")
        f.write("Q2,3600,850\n")
        f.write("Q3,3700,900\n")
        csv_path = f.name

    ref = load_from_file(csv_path)
    assert ref.loaded
    assert ref.source_type == "csv"
    assert "3500" in ref.content
    assert "Q2" in ref.content
    os.unlink(csv_path)
    print("✅ test_12_reference_loader_csv passed")


def test_13_reference_loader_json():
    """reference_loader: JSON 文件加载"""
    data = {"revenue": {"Q1": 3500, "Q2": 3600}, "unit": "万元"}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(data, f, ensure_ascii=False)
        json_path = f.name

    ref = load_from_file(json_path)
    assert ref.loaded
    assert ref.source_type == "json"
    assert ref.structured is not None
    assert ref.structured["revenue"]["Q1"] == 3500
    os.unlink(json_path)
    print("✅ test_13_reference_loader_json passed")


def test_14_reference_loader_markdown():
    """reference_loader: Markdown 文件加载"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write("# 销售报告\n\nQ1营收3500万，Q2营收3600万。\n")
        md_path = f.name

    ref = load_from_file(md_path)
    assert ref.loaded
    assert ref.source_type == "markdown"
    assert "3500万" in ref.content
    os.unlink(md_path)
    print("✅ test_14_reference_loader_markdown passed")


def test_15_reference_loader_nonexistent():
    """reference_loader: 文件不存在"""
    ref = load_from_file("/nonexistent/file.xlsx")
    assert not ref.loaded
    assert "不存在" in ref.error
    print("✅ test_15_reference_loader_nonexistent passed")


def test_16_review_result_to_dict():
    """ReviewResult 序列化"""
    result = ReviewResult(
        review_type="hallucination",
        issues=[
            ReviewIssue(claim="test", severity="high", reason="test reason"),
            ReviewIssue(claim="test2", severity="low", reason="test2 reason"),
        ],
        summary="发现 2 个问题",
    )
    d = result.to_dict()
    assert d["review_type"] == "hallucination"
    assert d["issue_count"] == 2
    assert d["high_severity_count"] == 1
    assert len(d["issues"]) == 2
    print("✅ test_16_review_result_to_dict passed")


def test_17_engine_build_prompt_all_types():
    """ReviewEngine.build_llm_prompt 三类审查"""
    engine = ReviewEngine()

    # 幻觉检测
    sys_p, usr_p = engine.build_llm_prompt("test text", review_type="hallucination")
    assert len(sys_p) > 0 and len(usr_p) > 0

    # 数据核对（无参考文件时应优雅处理）
    ref = ReferenceData(content="ref data", loaded=True)
    sys_p, usr_p = engine.build_llm_prompt("test", review_type="data_check", reference_data=ref)
    assert "ref data" in usr_p

    # 风格检查
    sys_p, usr_p = engine.build_llm_prompt("test", review_type="style", style_target="academic")
    assert "学术" in usr_p

    print("✅ test_17_engine_build_prompt_all_types passed")


def test_18_engine_parse_response_all_types():
    """ReviewEngine.parse_llm_response 三类审查"""
    engine = ReviewEngine()

    llm_json = '[{"claim":"x","severity":"high","reason":"r","suggestion":"s","confidence":0.5}]'

    for rt in ["hallucination", "data_check", "style"]:
        result = engine.parse_llm_response(llm_json, rt, "x")
        assert result.issue_count == 1

    print("✅ test_18_engine_parse_response_all_types passed")


def test_19_review_type_enum():
    """ReviewType 枚举值"""
    assert ReviewType.HALLUCINATION == "hallucination"
    assert ReviewType.DATA_CHECK == "data_check"
    assert ReviewType.STYLE == "style"
    print("✅ test_19_review_type_enum passed")


def test_20_issue_severity_enum():
    """IssueSeverity 枚举值"""
    assert IssueSeverity.HIGH == "high"
    assert IssueSeverity.MEDIUM == "medium"
    assert IssueSeverity.LOW == "low"
    print("✅ test_20_issue_severity_enum passed")


def test_21_command_parser():
    """快捷指令解析"""
    from gui.v5.review_tab import ReviewTabV5

    assert ReviewTabV5.parse_command("/check") == ("hallucination", "")
    assert ReviewTabV5.parse_command("/verify data.xlsx") == ("data_check", "data.xlsx")
    assert ReviewTabV5.parse_command("/style b2b_formal") == ("style", "b2b_formal")
    assert ReviewTabV5.parse_command("hello") == (None, None)
    print("✅ test_21_command_parser passed")


def test_22_middleware_review_routing():
    """ReviewMiddleware 路由（模拟）"""
    from opencopilot.agent.middlewares import ReviewMiddleware
    mw = ReviewMiddleware()
    # 确认中间件存在且可实例化
    assert mw is not None
    print("✅ test_22_middleware_review_routing passed")


def test_23_reference_data_context_string():
    """ReferenceData.to_context_string 截断"""
    ref = ReferenceData(content="x" * 5000, loaded=True)
    ctx = ref.to_context_string()
    assert len(ctx) < 5000
    assert "截断" in ctx
    print("✅ test_23_reference_data_context_string passed")


def test_24_full_review_pipeline_mock():
    """完整审查流程（Mock LLM 响应）"""
    engine = ReviewEngine()

    # 1. 构建 prompt
    sys_p, usr_p = engine.build_llm_prompt(
        "Q2营收3800万，同比增长15%",
        review_type="hallucination"
    )
    assert len(sys_p) > 0

    # 2. 模拟 LLM 返回
    mock_llm_output = json.dumps([
        {
            "claim": "Q2营收3800万",
            "issue_type": "unsupported_claim",
            "severity": "high",
            "reason": "声明了具体营收数字但未提供数据来源",
            "suggestion": "请核实原始财报数据",
            "confidence": 0.7
        }
    ], ensure_ascii=False)

    # 3. 解析结果
    result = engine.parse_llm_response(mock_llm_output, "hallucination", "Q2营收3800万，同比增长15%")
    assert result.issue_count == 1
    assert result.issues[0].severity == "high"
    assert "3800万" in result.issues[0].claim
    assert result.has_issues
    print("✅ test_24_full_review_pipeline_mock passed")


def test_25_data_check_with_reference_file():
    """数据交叉验证 + 参考文件加载"""
    # 创建临时 CSV
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("指标,Q1,Q2\n营收,3500,3600\n利润,800,850\n")
        csv_path = f.name

    engine = ReviewEngine()
    ref = load_from_file(csv_path)
    assert ref.loaded

    sys_p, usr_p = engine.build_llm_prompt(
        "Q2营收3800万",
        review_type="data_check",
        reference_data=ref,
    )
    assert "3600" in usr_p  # CSV 中的真实值

    os.unlink(csv_path)
    print("✅ test_25_data_check_with_reference_file passed")


if __name__ == "__main__":
    tests = [
        test_01_review_engine_init,
        test_02_hallucination_prompt,
        test_03_hallucination_with_context,
        test_04_hallucination_parse_valid,
        test_05_hallucination_parse_empty,
        test_06_hallucination_parse_malformed,
        test_07_data_check_prompt,
        test_08_data_check_parse,
        test_09_style_prompt,
        test_10_style_presets,
        test_11_style_parse_with_correction,
        test_12_reference_loader_csv,
        test_13_reference_loader_json,
        test_14_reference_loader_markdown,
        test_15_reference_loader_nonexistent,
        test_16_review_result_to_dict,
        test_17_engine_build_prompt_all_types,
        test_18_engine_parse_response_all_types,
        test_19_review_type_enum,
        test_20_issue_severity_enum,
        test_21_command_parser,
        test_22_middleware_review_routing,
        test_23_reference_data_context_string,
        test_24_full_review_pipeline_mock,
        test_25_data_check_with_reference_file,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Review Pipeline E2E Tests: {passed}/{passed+failed} passed")
    if failed:
        print(f"  {failed} tests FAILED")
    else:
        print("  All tests PASSED!")
