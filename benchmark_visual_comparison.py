"""
PPT 双渲染器可视化对比测试

对比维度：
1. 自研 SlideRenderer（PyQt 自绘）截图
2. 生成 .pptx 后通过 OnlyOffice QWebEngineView 截图（真实渲染）
3. 使用 SSIM 计算视觉差异

运行方式：
    python benchmark_visual_comparison.py

依赖：
    - OnlyOffice Document Server 运行在 http://localhost:9090
    - PyQt6 + PyQt6-WebEngine
    - scipy (用于 SSIM 计算)
"""

import json
import time
import copy
import sys
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Tuple
import math

# 添加项目路径
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from opencopilot.capabilities.ppt.render_command import RenderCommand, RenderCommandParser
from opencopilot.capabilities.ppt.render_executor import RenderDispatcher

# Qt 截图
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QImage, QColor

# 自研渲染器
from opencopilot.capabilities.ppt.preview_panel import SlideRenderer

# PPT 生成器
from ppt_generator import generate_ppt_from_json

# OnlyOffice 截图器
from benchmark_onlyoffice_capture import OnlyOfficeScreenshotter

# 尝试导入 Pillow 用于图像处理
from PIL import Image
import numpy as np


# ============================================================
# 测试数据（复用 benchmark 的标准数据）
# ============================================================

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

# AI 指令测试集
TEST_CASES = [
    {
        "name": "chart_rendering",
        "description": "图表渲染对比",
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
    {
        "name": "table_rendering",
        "description": "表格渲染对比",
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
    {
        "name": "flowchart_rendering",
        "description": "流程图渲染对比",
        "instruction": "把产品路线图用流程图展示",
        "ai_response": json.dumps({
            "render_commands": [{
                "source_text": "Q1：完成核心架构升级。Q2：推出AI助手功能",
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
    {
        "name": "text_layout",
        "description": "文本布局对比",
        "instruction": "精简这段内容",
        "ai_response": json.dumps({
            "render_commands": [{
                "source_text": "年度营收达到12.8亿元，同比增长35%",
                "render_type": "text",
                "render_params": {"text": "营收12.8亿，增长35%"},
                "slide_index": 1,
                "slot": "body"
            }]
        }, ensure_ascii=False)
    },
]


# ============================================================
# SSIM 图像对比算法
# ============================================================

def resize_to_standard(img: np.ndarray, target_size: tuple = (750, 1333)) -> np.ndarray:
    """将图片缩放到标准尺寸"""
    from PIL import Image
    pil_img = Image.fromarray(img)
    pil_img = pil_img.resize((target_size[1], target_size[0]), Image.Resampling.LANCZOS)
    return np.array(pil_img)


def calculate_ssim(img1: np.ndarray, img2: np.ndarray) -> float:
    """
    计算两张图片的 SSIM (Structural Similarity Index)
    
    Args:
        img1: 图片1 (H, W, C) 或 (H, W)，值范围 [0, 255]
        img2: 图片2 (H, W, C) 或 (H, W)，值范围 [0, 255]
    
    Returns:
        SSIM 值，范围 [0, 1]，1 表示完全相同
    """
    # 统一缩放到标准尺寸 (750, 1333) 以便公平对比
    img1 = resize_to_standard(img1)
    img2 = resize_to_standard(img2)
    
    # 确保尺寸相同
    if img1.shape != img2.shape:
        h, w = min(img1.shape[0], img2.shape[0]), min(img1.shape[1], img2.shape[1])
        img1 = img1[:h, :w]
        img2 = img2[:h, :w]
    
    # 转换为灰度图（如果是彩色）
    if len(img1.shape) == 3:
        img1 = np.mean(img1, axis=2)
    if len(img2.shape) == 3:
        img2 = np.mean(img2, axis=2)
    
    # 常量
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    
    # 使用高斯窗口
    from scipy.ndimage import gaussian_filter
    
    mu1 = gaussian_filter(img1.astype(np.float64), sigma=1.5)
    mu2 = gaussian_filter(img2.astype(np.float64), sigma=1.5)
    
    mu1_sq = mu1 ** 2
    mu2_sq = mu2 ** 2
    mu1_mu2 = mu1 * mu2
    
    sigma1_sq = gaussian_filter(img1.astype(np.float64) ** 2, sigma=1.5) - mu1_sq
    sigma2_sq = gaussian_filter(img2.astype(np.float64) ** 2, sigma=1.5) - mu2_sq
    sigma12 = gaussian_filter(img1.astype(np.float64) * img2.astype(np.float64), sigma=1.5) - mu1_mu2
    
    ssim_map = ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / ((mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2))
    
    return float(np.mean(ssim_map))


def calculate_mse(img1: np.ndarray, img2: np.ndarray) -> float:
    """计算均方误差"""
    img1 = resize_to_standard(img1)
    img2 = resize_to_standard(img2)
    if img1.shape != img2.shape:
        h, w = min(img1.shape[0], img2.shape[0]), min(img1.shape[1], img2.shape[1])
        img1 = img1[:h, :w]
        img2 = img2[:h, :w]
    return float(np.mean((img1.astype(np.float64) - img2.astype(np.float64)) ** 2))


def calculate_psnr(img1: np.ndarray, img2: np.ndarray) -> float:
    """计算 PSNR"""
    mse = calculate_mse(img1, img2)
    if mse == 0:
        return float('inf')
    return 20 * math.log10(255.0 / math.sqrt(mse))


# ============================================================
# 截图工具
# ============================================================

class SlideRendererCapture:
    """自研 SlideRenderer 截图器"""
    
    def __init__(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.renderer = SlideRenderer()
        self.renderer.setFixedSize(1333, 750)  # 标准 16:9 尺寸
    
    def capture(self, slide_data: dict, output_path: str) -> str:
        """截图并保存"""
        self.renderer.set_slide(slide_data)
        self.renderer.repaint()
        
        # 等待渲染完成
        self.app.processEvents()
        time.sleep(0.1)
        
        # 截图
        pixmap = self.renderer.grab()
        pixmap.save(output_path, "PNG")
        
        return output_path
    
    def capture_to_numpy(self, slide_data: dict) -> np.ndarray:
        """截图并返回 numpy 数组"""
        self.renderer.set_slide(slide_data)
        self.renderer.repaint()
        self.app.processEvents()
        time.sleep(0.1)
        
        pixmap = self.renderer.grab()
        image = pixmap.toImage()
        
        # 转换为 numpy
        width = image.width()
        height = image.height()
        ptr = image.bits()
        ptr.setsize(height * width * 4)
        arr = np.frombuffer(ptr, np.uint8).reshape((height, width, 4))
        return arr[:, :, :3]  # 去掉 alpha


class OnlyOfficeRendererCapture:
    """OnlyOffice 真实渲染截图器（通过 QWebEngineView 加载 OnlyOffice 编辑器）"""
    
    def __init__(self):
        self.screenshotter = OnlyOfficeScreenshotter()
        self.screenshotter.show()
    
    def capture(self, slides_data: List[Dict], output_path: str, slide_index: int = 1) -> str:
        """使用 OnlyOffice 渲染 slides_data 并截图"""
        from PyQt6.QtCore import QTimer, QEventLoop
        
        success = False
        
        def on_capture_finished(result):
            nonlocal success
            success = result
            loop.quit()
        
        # 使用 QTimer 在事件循环中执行截图
        loop = QEventLoop()
        
        def do_capture():
            result = self.screenshotter.capture(slides_data, output_path, timeout_ms=60000, slide_index=slide_index)
            on_capture_finished(result)
        
        QTimer.singleShot(500, do_capture)
        loop.exec()
        
        if success and os.path.exists(output_path):
            return output_path
        else:
            self._create_placeholder(output_path, "OnlyOffice 截图失败")
            return output_path
    
    def capture_to_numpy(self, slides_data: List[Dict], slide_index: int = 1) -> np.ndarray:
        """截图并返回 numpy 数组"""
        output_path = os.path.join(tempfile.gettempdir(), f"onlyoffice_capture_{slide_index}.png")
        self.capture(slides_data, output_path, slide_index=slide_index)
        
        if not os.path.exists(output_path):
            return np.zeros((750, 1333, 3), dtype=np.uint8)
        
        img = Image.open(output_path)
        img = img.convert("RGB")
        return np.array(img)
    
    def _create_placeholder(self, output_path: str, text: str):
        """创建占位图"""
        img = Image.new("RGB", (1333, 750), color=(240, 240, 240))
        img.save(output_path)
    
    def close(self):
        """关闭截图器"""
        self.screenshotter.close()


# ============================================================
# 可视化对比主流程
# ============================================================

def run_visual_benchmark():
    """运行可视化对比测试"""
    print("=" * 70)
    print("PPT 双渲染器可视化对比测试")
    print("=" * 70)
    
    # 检查依赖
    try:
        from scipy.ndimage import gaussian_filter
    except ImportError:
        print("❌ 需要 scipy: pip install scipy")
        return
    
    # 创建输出目录
    output_dir = PROJECT_ROOT / "benchmark_visual_output"
    output_dir.mkdir(exist_ok=True)
    
    # 初始化截图器
    print("\n初始化截图器...")
    native_capture = SlideRendererCapture()
    
    oo_capture = None
    try:
        oo_capture = OnlyOfficeRendererCapture()
        print("✅ OnlyOffice 截图器初始化成功")
    except Exception as e:
        print(f"⚠️ OnlyOffice 截图器初始化失败（将跳过 OnlyOffice 对比）: {e}")
    
    results = []
    
    for i, test in enumerate(TEST_CASES, 1):
        print(f"\n[{i}/{len(TEST_CASES)}] {test['name']}: {test['description']}")
        print(f"  指令: {test['instruction']}")
        
        # 准备数据副本
        slides_data = copy.deepcopy(STANDARD_SLIDES_DATA)
        
        # 执行 AI 指令
        dispatcher = RenderDispatcher(slides_data, STANDARD_ORIGINAL_TEXT)
        commands = RenderCommandParser.parse(test["ai_response"], STANDARD_ORIGINAL_TEXT)
        
        if commands:
            for cmd in commands:
                if cmd.slide_index < 0:
                    cmd.slide_index = 1
            results_dispatch = dispatcher.dispatch_from_render_commands(commands, 1)
            success_count = sum(1 for r in results_dispatch if r.success)
            print(f"  渲染指令: {success_count}/{len(results_dispatch)} 成功")
        
        # 获取目标幻灯片
        target_slide = slides_data[1]  # 第1页（索引1）是内容页
        
        # 自研截图
        native_path = output_dir / f"{test['name']}_native.png"
        native_capture.capture(target_slide, str(native_path))
        native_img = native_capture.capture_to_numpy(target_slide)
        print(f"  自研截图: {native_path} ({native_img.shape})")
        
        # OnlyOffice 截图
        if oo_capture:
            oo_path = output_dir / f"{test['name']}_onlyoffice.png"
            try:
                # 确定目标 slide_index（从测试用例中获取，默认第1页）
                target_slide_index = 1
                if 'ai_response' in test:
                    try:
                        resp = json.loads(test['ai_response'])
                        if 'render_commands' in resp and resp['render_commands']:
                            cmd = resp['render_commands'][0]
                            if 'slide_index' in cmd:
                                target_slide_index = cmd['slide_index']
                    except:
                        pass
                
                oo_capture.capture(slides_data, str(oo_path), slide_index=target_slide_index)
                oo_img = oo_capture.capture_to_numpy(slides_data, slide_index=target_slide_index)
                print(f"  OnlyOffice截图: {oo_path} (slide {target_slide_index}, {oo_img.shape})")
                
                # 计算对比指标
                ssim = calculate_ssim(native_img, oo_img)
                mse = calculate_mse(native_img, oo_img)
                psnr = calculate_psnr(native_img, oo_img)
                
                print(f"  对比指标: SSIM={ssim:.4f} | MSE={mse:.1f} | PSNR={psnr:.2f}dB")
                
                results.append({
                    "name": test["name"],
                    "description": test["description"],
                    "ssim": ssim,
                    "mse": mse,
                    "psnr": psnr,
                    "native_path": str(native_path),
                    "onlyoffice_path": str(oo_path),
                })
            except Exception as e:
                print(f"  ❌ OnlyOffice 截图失败: {e}")
                results.append({
                    "name": test["name"],
                    "description": test["description"],
                    "error": str(e),
                })
        else:
            print(f"  ⚠️ 跳过 OnlyOffice 对比（截图器不可用）")
            results.append({
                "name": test["name"],
                "description": test["description"],
                "error": "OnlyOffice 截图器不可用",
            })
    
    # 关闭 OnlyOffice 截图器
    if oo_capture:
        oo_capture.close()
    
    # 生成报告
    print("\n" + "=" * 70)
    print("可视化对比报告")
    print("=" * 70)
    
    valid_results = [r for r in results if "ssim" in r]
    if valid_results:
        avg_ssim = sum(r["ssim"] for r in valid_results) / len(valid_results)
        avg_psnr = sum(r["psnr"] for r in valid_results) / len(valid_results)
        
        print(f"\n有效对比: {len(valid_results)}/{len(results)}")
        print(f"平均 SSIM: {avg_ssim:.4f}")
        print(f"平均 PSNR: {avg_psnr:.2f} dB")
        
        print(f"\n详细结果:")
        for r in valid_results:
            quality = "✅ 优秀" if r["ssim"] > 0.9 else "⚠️ 一般" if r["ssim"] > 0.7 else "❌ 差异大"
            print(f"  {r['name']}: SSIM={r['ssim']:.4f} PSNR={r['psnr']:.1f}dB {quality}")
    
    # 保存报告
    report = {
        "summary": {
            "total_tests": len(results),
            "valid_comparisons": len(valid_results),
            "avg_ssim": round(avg_ssim, 4) if valid_results else None,
            "avg_psnr": round(avg_psnr, 2) if valid_results else None,
        },
        "details": results
    }
    
    report_path = output_dir / "visual_comparison_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n📄 报告已保存: {report_path}")
    print(f"📁 截图目录: {output_dir}")


if __name__ == "__main__":
    run_visual_benchmark()
