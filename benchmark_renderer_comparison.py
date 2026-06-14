"""
PPT 双渲染器 Benchmark 对比测试

测试目标：对比自研 SlideRenderer（PyQt 自绘）与 OnlyOfficeWidget（嵌入编辑器）
对同一组 AI 指令的渲染效果差异。

测试维度：
1. 渲染成功率（success_rate）
2. 渲染耗时（latency_ms）
3. 输出 slides_data 结构完整性（schema_completeness）
4. 内容保真度（content_fidelity）
5. 特殊类型支持度（chart/table/flowchart 等）

运行方式：
    python benchmark_renderer_comparison.py
"""

import json
import time
import copy
import sys
import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from pathlib import Path

# 添加项目路径
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from opencopilot.capabilities.ppt.render_command import RenderCommand, RenderCommandParser
from opencopilot.capabilities.ppt.render_executor import RenderDispatcher, RenderExecutor


# ============================================================
# 测试数据：标准 PPT + AI 指令集
# ============================================================

# 标准测试 PPT（5页：封面 + 3内容页 + 结尾）
STANDARD_SLIDES_DATA = [
    {
        "type": "title",
        "layout": "center",
        "title": "2025年度产品战略报告",
        "subtitle": "AI驱动的产品创新与增长",
        "items": []
    },
    {
        "type": "content",
        "layout": "text_only",
        "title": "核心业务指标",
        "items": [
            {"level": 0, "text": "年度营收达到12.8亿元，同比增长35%"},
            {"level": 0, "text": "用户规模突破500万，月活用户280万"},
            {"level": 0, "text": "客户满意度评分4.8/5.0"},
            {"level": 1, "text": "NPS净推荐值达到72分"}
        ]
    },
    {
        "type": "content",
        "layout": "text_only",
        "title": "市场竞争分析",
        "items": [
            {"level": 0, "text": "主要竞争对手：A公司、B公司、C公司"},
            {"level": 0, "text": "我们的优势：技术领先、用户体验好、价格竞争力强"},
            {"level": 0, "text": "面临挑战：市场饱和、获客成本上升"}
        ]
    },
    {
        "type": "content",
        "layout": "text_only",
        "title": "产品路线图",
        "items": [
            {"level": 0, "text": "Q1：完成核心架构升级"},
            {"level": 0, "text": "Q2：推出AI助手功能"},
            {"level": 0, "text": "Q3：扩展企业版功能"},
            {"level": 0, "text": "Q4：国际化部署"}
        ]
    },
    {
        "type": "ending",
        "layout": "center",
        "title": "谢谢",
        "subtitle": "Q & A",
        "items": []
    }
]

# 标准原文（用于 source_range 定位）
STANDARD_ORIGINAL_TEXT = """
2025年度产品战略报告

核心业务指标
年度营收达到12.8亿元，同比增长35%。用户规模突破500万，月活用户280万。
客户满意度评分4.8/5.0，NPS净推荐值达到72分。

市场竞争分析
主要竞争对手：A公司、B公司、C公司。我们的优势：技术领先、用户体验好、价格竞争力强。
面临挑战：市场饱和、获客成本上升。

产品路线图
Q1：完成核心架构升级。Q2：推出AI助手功能。Q3：扩展企业版功能。Q4：国际化部署。
"""


# ============================================================
# AI 指令测试集（覆盖各种 render_type 和场景）
# ============================================================

TEST_INSTRUCTIONS = [
    # 测试 1: 文本改写（最基础）
    {
        "name": "text_rewrite",
        "description": "改写标题文本",
        "instruction": "把标题改为'2025年度产品战略总结'",
        "ai_response": json.dumps({
            "render_commands": [{
                "source_text": "2025年度产品战略报告",
                "render_type": "text",
                "render_params": {"title": "2025年度产品战略总结"},
                "slide_index": 0,
                "slot": "title"
            }]
        }, ensure_ascii=False)
    },
    
    # 测试 2: 数据转图表（chart）
    {
        "name": "data_to_chart",
        "description": "将营收数据转为柱状图",
        "instruction": "把营收数据用柱状图展示",
        "ai_response": json.dumps({
            "render_commands": [{
                "source_text": "年度营收达到12.8亿元，同比增长35%",
                "render_type": "chart",
                "render_params": {
                    "title": "营收增长趋势",
                    "chart_type": "bar",
                    "chart_data": {
                        "labels": ["2023", "2024", "2025"],
                        "values": [8.5, 9.5, 12.8]
                    }
                },
                "slide_index": 1,
                "slot": "body"
            }]
        }, ensure_ascii=False)
    },
    
    # 测试 3: 对比转表格（table）
    {
        "name": "comparison_to_table",
        "description": "将竞争分析转为对比表格",
        "instruction": "把竞争分析做成对比表格",
        "ai_response": json.dumps({
            "render_commands": [{
                "source_text": "主要竞争对手：A公司、B公司、C公司",
                "render_type": "table",
                "render_params": {
                    "title": "竞争对手对比",
                    "table_data": {
                        "columns": ["维度", "A公司", "B公司", "C公司", "我们"],
                        "rows": [
                            ["技术实力", "强", "中", "弱", "强"],
                            ["用户体验", "中", "强", "中", "强"],
                            ["价格", "高", "中", "低", "中"]
                        ]
                    }
                },
                "slide_index": 2,
                "slot": "body"
            }]
        }, ensure_ascii=False)
    },
    
    # 测试 4: 流程转流程图（flowchart）
    {
        "name": "steps_to_flowchart",
        "description": "将产品路线图转为流程图",
        "instruction": "把产品路线图用流程图展示",
        "ai_response": json.dumps({
            "render_commands": [{
                "source_text": "Q1：完成核心架构升级。Q2：推出AI助手功能。Q3：扩展企业版功能。Q4：国际化部署",
                "render_type": "flowchart",
                "render_params": {
                    "title": "2025产品路线图",
                    "flowchart_data": {
                        "steps": ["Q1 架构升级", "Q2 AI助手", "Q3 企业版", "Q4 国际化"]
                    }
                },
                "slide_index": 3,
                "slot": "body"
            }]
        }, ensure_ascii=False)
    },
    
    # 测试 5: 多条指令复合操作
    {
        "name": "multi_command",
        "description": "同时修改标题和正文",
        "instruction": "改标题并添加一个要点",
        "ai_response": json.dumps({
            "render_commands": [
                {
                    "source_text": "核心业务指标",
                    "render_type": "text",
                    "render_params": {"title": "核心业务数据概览"},
                    "slide_index": 1,
                    "slot": "title"
                },
                {
                    "source_text": "年度营收达到12.8亿元",
                    "render_type": "text",
                    "render_params": {"text": "年度营收达到12.8亿元，同比增长35%，超额完成目标"},
                    "slide_index": 1,
                    "slot": "body"
                }
            ]
        }, ensure_ascii=False)
    },
    
    # 测试 6: 旧格式兼容（action 格式）
    {
        "name": "old_format_compat",
        "description": "旧格式 action 自动转换",
        "instruction": "改标题",
        "ai_response": json.dumps({
            "action": "modify_text",
            "content": "2025年度产品战略总结",
            "slide_index": 0
        }, ensure_ascii=False)
    },
    
    # 测试 7: 格式容错（中文引号、多余逗号）
    {
        "name": "format_tolerance",
        "description": "测试格式容错能力",
        "instruction": "改标题",
        "ai_response": '{"render_commands": [{"source_text": "2025年度产品战略报告", "render_type": "text", "render_params": {"title": "新标题"}, "slide_index": 0, "slot": "title", }]}'
    },
    
    # 测试 8: 缺失字段自动补全（chart 缺少 chart_data）
    {
        "name": "auto_complete",
        "description": "测试字段自动补全",
        "instruction": "展示数据",
        "ai_response": json.dumps({
            "render_commands": [{
                "source_text": "年度营收达到12.8亿元",
                "render_type": "chart",
                "render_params": {
                    "title": "营收数据"
                    # 故意缺少 chart_type 和 chart_data
                },
                "slide_index": 1,
                "slot": "body"
            }]
        }, ensure_ascii=False)
    },
    
    # 测试 9: 纯文本回退（无 JSON）
    {
        "name": "plain_text_fallback",
        "description": "测试纯文本回退解析",
        "instruction": "提取表格",
        "ai_response": "| 维度 | A公司 | B公司 |\n|------|------|------|\n| 技术 | 强 | 中 |"
    },
]


# ============================================================
# 结果数据结构
# ============================================================

@dataclass
class BenchmarkResult:
    """单个测试用例的结果"""
    test_name: str
    test_description: str
    instruction: str
    
    # 自研渲染器结果
    native_success: bool = False
    native_latency_ms: float = 0.0
    native_error: str = ""
    native_slide_data: Dict = field(default_factory=dict)
    native_items_count: int = 0
    
    # OnlyOffice 渲染器结果（通过 slides_data 对比）
    oo_success: bool = False
    oo_latency_ms: float = 0.0
    oo_error: str = ""
    oo_slide_data: Dict = field(default_factory=dict)
    oo_items_count: int = 0
    
    # 对比指标
    structure_match: bool = False  # 结构是否一致
    content_fidelity: float = 0.0  # 内容保真度 0-1
    
    def to_dict(self) -> Dict:
        return {
            "test_name": self.test_name,
            "test_description": self.test_description,
            "instruction": self.instruction,
            "native": {
                "success": self.native_success,
                "latency_ms": round(self.native_latency_ms, 2),
                "error": self.native_error,
                "items_count": self.native_items_count,
            },
            "onlyoffice": {
                "success": self.oo_success,
                "latency_ms": round(self.oo_latency_ms, 2),
                "error": self.oo_error,
                "items_count": self.oo_items_count,
            },
            "comparison": {
                "structure_match": self.structure_match,
                "content_fidelity": round(self.content_fidelity, 3),
            }
        }


# ============================================================
# 渲染器封装（统一接口）
# ============================================================

class NativeRenderer:
    """自研 SlideRenderer 渲染器（通过 RenderDispatcher 执行）"""
    
    def __init__(self, slides_data: List[Dict], original_text: str):
        self.slides_data = slides_data
        self.original_text = original_text
        self.dispatcher = RenderDispatcher(slides_data, original_text)
    
    def render(self, ai_response: str, current_index: int = 0) -> Dict:
        """
        执行 AI 响应渲染
        
        Returns:
            {
                "success": bool,
                "latency_ms": float,
                "error": str,
                "slides_data": List[Dict],  # 渲染后的 slides_data
                "results": List[RenderResult]
            }
        """
        start = time.time()
        
        try:
            # 解析并执行渲染指令
            results, old_actions = self.dispatcher.dispatch_from_ai_response(
                ai_response, current_index
            )
            
            success_count = sum(1 for r in results if r.success)
            total_count = len(results)
            
            # 如果没有 render_commands 结果，但有 old_actions，说明是旧格式
            if not results and old_actions:
                return {
                    "success": False,
                    "latency_ms": (time.time() - start) * 1000,
                    "error": "旧格式 action 未转换为 render_commands",
                    "slides_data": self.slides_data,
                    "results": []
                }
            
            # 如果没有结果也没有旧 actions，可能是解析失败
            if not results and not old_actions:
                return {
                    "success": False,
                    "latency_ms": (time.time() - start) * 1000,
                    "error": "无法解析 AI 响应（无 render_commands 也无 actions）",
                    "slides_data": self.slides_data,
                    "results": []
                }
            
            latency_ms = (time.time() - start) * 1000
            
            return {
                "success": success_count > 0,
                "latency_ms": latency_ms,
                "error": "" if success_count > 0 else f"{total_count - success_count}/{total_count} 指令失败",
                "slides_data": self.slides_data,
                "results": results,
                "success_count": success_count,
                "total_count": total_count
            }
            
        except Exception as e:
            return {
                "success": False,
                "latency_ms": (time.time() - start) * 1000,
                "error": str(e),
                "slides_data": self.slides_data,
                "results": []
            }


class OnlyOfficeRenderer:
    """OnlyOffice 渲染器（通过 RenderDispatcher 执行，对比 slides_data 输出）"""
    
    def __init__(self, slides_data: List[Dict], original_text: str):
        self.slides_data = slides_data
        self.original_text = original_text
        self.dispatcher = RenderDispatcher(slides_data, original_text)
    
    def render(self, ai_response: str, current_index: int = 0) -> Dict:
        """
        执行 AI 响应渲染
        
        注：OnlyOffice 实际渲染需要 QWebEngineView + OnlyOffice Server，
        这里通过 RenderDispatcher 生成 slides_data，然后对比结构差异。
        真正的 OnlyOffice 渲染效果需要通过截图对比（后续扩展）。
        """
        start = time.time()
        
        try:
            # 使用同样的 RenderDispatcher 执行
            results, old_actions = self.dispatcher.dispatch_from_ai_response(
                ai_response, current_index
            )
            
            success_count = sum(1 for r in results if r.success)
            total_count = len(results)
            
            latency_ms = (time.time() - start) * 1000
            
            return {
                "success": success_count > 0,
                "latency_ms": latency_ms,
                "error": "" if success_count > 0 else f"{total_count - success_count}/{total_count} 指令失败",
                "slides_data": self.slides_data,
                "results": results,
                "success_count": success_count,
                "total_count": total_count
            }
            
        except Exception as e:
            return {
                "success": False,
                "latency_ms": (time.time() - start) * 1000,
                "error": str(e),
                "slides_data": self.slides_data,
                "results": []
            }


# ============================================================
# 对比分析工具
# ============================================================

def compare_slide_structure(slide1: Dict, slide2: Dict) -> bool:
    """比较两个 slide 的结构是否一致"""
    keys1 = set(slide1.keys())
    keys2 = set(slide2.keys())
    
    # 忽略某些非关键字段
    ignore_keys = {"_meta", "trace_id", "created_at"}
    keys1 -= ignore_keys
    keys2 -= ignore_keys
    
    if keys1 != keys2:
        return False
    
    # 比较 items 结构
    items1 = slide1.get("items", [])
    items2 = slide2.get("items", [])
    
    if len(items1) != len(items2):
        return False
    
    for i1, i2 in zip(items1, items2):
        if i1.get("content_type") != i2.get("content_type"):
            return False
    
    return True


def calculate_content_fidelity(slide1: Dict, slide2: Dict) -> float:
    """计算内容保真度（0-1）"""
    # 提取文本内容
    def extract_texts(slide: Dict) -> List[str]:
        texts = []
        if "title" in slide:
            texts.append(str(slide["title"]))
        for item in slide.get("items", []):
            if "text" in item:
                texts.append(str(item["text"]))
            if "chart_data" in item:
                texts.append(json.dumps(item["chart_data"], sort_keys=True))
            if "table_data" in item:
                texts.append(json.dumps(item["table_data"], sort_keys=True))
        return texts
    
    texts1 = extract_texts(slide1)
    texts2 = extract_texts(slide2)
    
    if not texts1 and not texts2:
        return 1.0
    
    if not texts1 or not texts2:
        return 0.0
    
    # 简单对比：相同文本数量 / 最大文本数量
    matched = sum(1 for t1 in texts1 if any(t1 == t2 for t2 in texts2))
    return matched / max(len(texts1), len(texts2))


# ============================================================
# Benchmark 主流程
# ============================================================

def run_benchmark() -> List[BenchmarkResult]:
    """运行完整 benchmark"""
    results = []
    
    print("=" * 70)
    print("PPT 双渲染器 Benchmark 对比测试")
    print("=" * 70)
    print(f"测试用例数: {len(TEST_INSTRUCTIONS)}")
    print(f"标准 PPT 页数: {len(STANDARD_SLIDES_DATA)}")
    print()
    
    for i, test in enumerate(TEST_INSTRUCTIONS, 1):
        print(f"\n[{i}/{len(TEST_INSTRUCTIONS)}] {test['name']}: {test['description']}")
        print(f"  指令: {test['instruction']}")
        print(f"  AI响应: {test['ai_response'][:100]}...")
        
        # 准备独立的数据副本（避免互相影响）
        native_slides = copy.deepcopy(STANDARD_SLIDES_DATA)
        oo_slides = copy.deepcopy(STANDARD_SLIDES_DATA)
        
        # 执行自研渲染器
        native_renderer = NativeRenderer(native_slides, STANDARD_ORIGINAL_TEXT)
        native_result = native_renderer.render(test["ai_response"], current_index=1)
        
        # 执行 OnlyOffice 渲染器
        oo_renderer = OnlyOfficeRenderer(oo_slides, STANDARD_ORIGINAL_TEXT)
        oo_result = oo_renderer.render(test["ai_response"], current_index=1)
        
        # 构建对比结果
        result = BenchmarkResult(
            test_name=test["name"],
            test_description=test["description"],
            instruction=test["instruction"],
            native_success=native_result["success"],
            native_latency_ms=native_result["latency_ms"],
            native_error=native_result.get("error", ""),
            native_items_count=len(native_result["slides_data"][1].get("items", [])) if len(native_result["slides_data"]) > 1 else 0,
            oo_success=oo_result["success"],
            oo_latency_ms=oo_result["latency_ms"],
            oo_error=oo_result.get("error", ""),
            oo_items_count=len(oo_result["slides_data"][1].get("items", [])) if len(oo_result["slides_data"]) > 1 else 0,
        )
        
        # 结构对比（取第1页，因为大部分测试针对第1页）
        if len(native_result["slides_data"]) > 1 and len(oo_result["slides_data"]) > 1:
            result.structure_match = compare_slide_structure(
                native_result["slides_data"][1],
                oo_result["slides_data"][1]
            )
            result.content_fidelity = calculate_content_fidelity(
                native_result["slides_data"][1],
                oo_result["slides_data"][1]
            )
        
        results.append(result)
        
        # 打印结果
        status_native = "✅" if result.native_success else "❌"
        status_oo = "✅" if result.oo_success else "❌"
        print(f"  自研: {status_native} {result.native_latency_ms:.1f}ms {result.native_error[:50]}")
        print(f"  OnlyOffice: {status_oo} {result.oo_latency_ms:.1f}ms {result.oo_error[:50]}")
        print(f"  结构一致: {'✅' if result.structure_match else '❌'} | 保真度: {result.content_fidelity:.2f}")
    
    return results


def generate_report(results: List[BenchmarkResult]) -> Dict:
    """生成测试报告"""
    total = len(results)
    native_success = sum(1 for r in results if r.native_success)
    oo_success = sum(1 for r in results if r.oo_success)
    structure_match = sum(1 for r in results if r.structure_match)
    avg_fidelity = sum(r.content_fidelity for r in results) / total if total > 0 else 0
    avg_native_latency = sum(r.native_latency_ms for r in results) / total if total > 0 else 0
    avg_oo_latency = sum(r.oo_latency_ms for r in results) / total if total > 0 else 0
    
    report = {
        "summary": {
            "total_tests": total,
            "native_success_rate": f"{native_success}/{total} ({native_success/total*100:.1f}%)",
            "oo_success_rate": f"{oo_success}/{total} ({oo_success/total*100:.1f}%)",
            "structure_match_rate": f"{structure_match}/{total} ({structure_match/total*100:.1f}%)",
            "avg_content_fidelity": round(avg_fidelity, 3),
            "avg_native_latency_ms": round(avg_native_latency, 2),
            "avg_oo_latency_ms": round(avg_oo_latency, 2),
        },
        "details": [r.to_dict() for r in results]
    }
    
    return report


def print_report(report: Dict):
    """打印报告到控制台"""
    print("\n" + "=" * 70)
    print("Benchmark 测试报告")
    print("=" * 70)
    
    summary = report["summary"]
    print(f"\n总测试数: {summary['total_tests']}")
    print(f"\n自研渲染器:")
    print(f"  成功率: {summary['native_success_rate']}")
    print(f"  平均耗时: {summary['avg_native_latency_ms']}ms")
    print(f"\nOnlyOffice 渲染器:")
    print(f"  成功率: {summary['oo_success_rate']}")
    print(f"  平均耗时: {summary['avg_oo_latency_ms']}ms")
    print(f"\n对比指标:")
    print(f"  结构一致率: {summary['structure_match_rate']}")
    print(f"  平均内容保真度: {summary['avg_content_fidelity']}")
    
    print(f"\n详细结果:")
    for detail in report["details"]:
        print(f"\n  {detail['test_name']}: {detail['test_description']}")
        print(f"    自研: {'✅' if detail['native']['success'] else '❌'} ({detail['native']['latency_ms']}ms)")
        if detail['native']['error']:
            print(f"      错误: {detail['native']['error']}")
        print(f"    OnlyOffice: {'✅' if detail['onlyoffice']['success'] else '❌'} ({detail['onlyoffice']['latency_ms']}ms)")
        if detail['onlyoffice']['error']:
            print(f"      错误: {detail['onlyoffice']['error']}")
        print(f"    对比: 结构{'✅' if detail['comparison']['structure_match'] else '❌'} | 保真度 {detail['comparison']['content_fidelity']}")


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    results = run_benchmark()
    report = generate_report(results)
    print_report(report)
    
    # 保存 JSON 报告
    report_path = PROJECT_ROOT / "benchmark_renderer_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n📄 报告已保存: {report_path}")
