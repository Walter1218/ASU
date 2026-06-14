"""
PPT 渲染指令系统

声明式渲染指令架构，将 AI 响应从"JSON 操作序列"升级为"渲染指令"。

核心组件：
- RenderCommand: 单条渲染指令（描述如何将原文片段渲染为幻灯片元素）
- RenderGroup: 渲染组（多条指令合并为一页）
- RenderCommandParser: 指令解析器（从 AI 响应提取渲染指令）

设计原则：
- 向后兼容：同时支持旧格式（JSON 操作序列）和新格式（渲染指令）
- 原文定位前置：用户先选中原文，生成指令时携带 source_range
- 渲染类型解耦：新增渲染类型只需注册，不改现有代码
"""

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple


# ============================================================
# 数据结构
# ============================================================

@dataclass
class RenderCommand:
    """
    渲染指令 - 描述如何将原文片段渲染为幻灯片元素
    
    示例：
    ```json
    {
      "source_text": "2025年全年营收12.8亿元...",
      "render_type": "chart",
      "render_params": {
        "chart_type": "bar",
        "title": "营收趋势"
      },
      "slide_index": -1,
      "slot": "body"
    }
    ```
    """
    
    # 唯一标识
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    
    # 目标定位
    slide_index: int = -1                    # 目标幻灯片（-1 = 新建）
    slot: str = "body"                       # 目标位置：title | body | sidebar | full
    
    # 原文来源
    source_range: Optional[Tuple[int, int]] = None  # 原文位置 (start, end)
    source_text: str = ""                    # 原文内容（用于定位）
    
    # 渲染方式
    render_type: str = "text"                # text | table | chart | flowchart | image_right
    render_params: Dict[str, Any] = field(default_factory=dict)  # 渲染参数
    
    # 可选：直接指定 items（兼容旧格式）
    items: Optional[List[Dict]] = None
    
    # 元数据
    instruction: str = ""                    # 用户原始指令
    confidence: float = 1.0                  # 置信度
    trace_id: str = ""                       # 追踪ID
    created_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = {
            "command_id": self.command_id,
            "slide_index": self.slide_index,
            "slot": self.slot,
            "source_text": self.source_text,
            "render_type": self.render_type,
            "render_params": self.render_params,
        }
        if self.source_range:
            result["source_range"] = list(self.source_range)
        if self.items:
            result["items"] = self.items
        if self.instruction:
            result["instruction"] = self.instruction
        if self.trace_id:
            result["trace_id"] = self.trace_id
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RenderCommand':
        """从字典创建"""
        source_range = data.get("source_range")
        if source_range and isinstance(source_range, list):
            source_range = tuple(source_range)
        
        return cls(
            command_id=data.get("command_id", uuid.uuid4().hex[:8]),
            slide_index=data.get("slide_index", -1),
            slot=data.get("slot", "body"),
            source_range=source_range,
            source_text=data.get("source_text", ""),
            render_type=data.get("render_type", "text"),
            render_params=data.get("render_params", {}),
            items=data.get("items"),
            instruction=data.get("instruction", ""),
            confidence=data.get("confidence", 1.0),
            trace_id=data.get("trace_id", ""),
        )


@dataclass
class RenderGroup:
    """
    渲染组 - 多条指令合并为一页
    
    用于将多段原文合并渲染到同一页幻灯片的不同位置。
    """
    target_slide: int = -1                   # 目标幻灯片（-1 = 新建）
    layout: str = "text_only"                # 整体布局
    commands: List[RenderCommand] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_slide": self.target_slide,
            "layout": self.layout,
            "commands": [cmd.to_dict() for cmd in self.commands],
        }


@dataclass
class RenderResult:
    """渲染结果"""
    success: bool
    slide_index: int
    slide_data: Optional[Dict] = None        # 渲染后的幻灯片数据
    source_ranges: List[Tuple[int, int]] = field(default_factory=list)
    message: str = ""
    trace_id: str = ""


# ============================================================
# 指令解析器
# ============================================================

class RenderCommandParser:
    """
    渲染指令解析器
    
    从 AI 响应中提取渲染指令，支持多种格式：
    1. 新格式：render_commands 数组
    2. 旧格式：action JSON 操作序列（向后兼容）
    """
    
    # 支持的渲染类型
    SUPPORTED_RENDER_TYPES = {
        # 内容渲染类型
        "text", "table", "chart", "flowchart",
        "image_right", "image_left", "image_top",
        "quote", "highlight", "code",
        # 操作类型（V5Plus 共创模式）
        "delete_slide",    # 删除幻灯片
        "delete_item",     # 删除元素
        "modify_layout",   # 修改排版
        "move_item",       # 移动元素到其他页
        "reorder_slides",  # 重新排序幻灯片
        "update_item",     # 更新元素内容
    }
    
    @classmethod
    def parse(cls, response: str, original_text: str = "") -> List[RenderCommand]:
        """
        从 AI 响应解析渲染指令
        
        Args:
            response: AI 响应文本
            original_text: 原始文档文本（用于 source_range 定位）
            
        Returns:
            渲染指令列表
        """
        # 1. 尝试提取 JSON 对象
        json_objects = cls._extract_json(response)
        if not json_objects:
            return []
        
        commands = []
        for json_str in json_objects:
            try:
                data = json.loads(json_str)
                
                # 2. 检查是否是新格式（render_commands）
                if "render_commands" in data:
                    for cmd_data in data["render_commands"]:
                        cmd = cls._parse_render_command(cmd_data, original_text)
                        if cmd:
                            commands.append(cmd)
                
                # 3. 检查是否是单条渲染指令
                elif "render_type" in data:
                    cmd = cls._parse_render_command(data, original_text)
                    if cmd:
                        commands.append(cmd)
                
                # 4. 旧格式：跳过（由 CoCreationWidget 处理）
                elif "action" in data:
                    # 旧格式，返回空让 CoCreationWidget 处理
                    return []
                    
            except json.JSONDecodeError:
                continue
        
        return commands
    
    @classmethod
    def _parse_render_command(cls, data: Dict, original_text: str = "") -> Optional[RenderCommand]:
        """解析单条渲染指令"""
        render_type = data.get("render_type", "text")
        
        # 验证渲染类型
        if render_type not in cls.SUPPORTED_RENDER_TYPES:
            return None
        
        # 构建 RenderCommand
        cmd = RenderCommand.from_dict(data)
        
        # 如果有 source_text 但没有 source_range，尝试从原文定位
        if cmd.source_text and not cmd.source_range and original_text:
            cmd.source_range = cls._find_source_range(cmd.source_text, original_text)
        
        return cmd
    
    @classmethod
    def _find_source_range(cls, source_text: str, original_text: str) -> Optional[Tuple[int, int]]:
        """在原文中定位 source_text 的位置"""
        if not source_text or not original_text:
            return None
        
        # 精确匹配
        idx = original_text.find(source_text)
        if idx >= 0:
            return (idx, idx + len(source_text))
        
        # 模糊匹配（去掉空白字符）
        clean_source = re.sub(r'\s+', '', source_text)
        clean_original = re.sub(r'\s+', '', original_text)
        idx = clean_original.find(clean_source)
        if idx >= 0:
            # 映射回原始位置（近似）
            return (idx, idx + len(source_text))
        
        return None
    
    @classmethod
    def _extract_json(cls, text: str) -> List[str]:
        """从文本中提取 JSON 对象"""
        results = []
        
        # 尝试代码块
        blocks = re.findall(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
        for block in blocks:
            block = block.strip()
            if block.startswith("{") or block.startswith("["):
                results.append(block)
        
        if results:
            return results
        
        # 回退：大括号匹配
        i = 0
        while i < len(text):
            start = text.find("{", i)
            if start < 0:
                break
            depth = 0
            in_string = False
            escape = False
            for j in range(start, len(text)):
                c = text[j]
                if escape:
                    escape = False
                    continue
                if c == '\\':
                    escape = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        results.append(text[start:j + 1])
                        i = j + 1
                        break
            else:
                break
        
        return results


# ============================================================
# 快捷指令生成器
# ============================================================

class QuickActionGenerator:
    """
    快捷指令生成器
    
    根据用户选中的原文内容，智能推荐渲染指令。
    """
    
    @classmethod
    def generate_actions(cls, selected_text: str) -> List[Dict[str, str]]:
        """
        根据选中文本生成快捷指令
        
        Returns:
            指令列表，格式：[{"label": "转为图表", "prompt": "..."}]
        """
        actions = []
        
        # 检测是否包含数值数据
        if cls._has_numbers(selected_text):
            actions.extend([
                {"label": "📊 转为柱状图", "prompt": "把这段数据用柱状图展示", "render_type": "chart"},
                {"label": "📈 转为折线图", "prompt": "把这段数据用折线图展示趋势", "render_type": "chart"},
                {"label": "🥧 转为饼图", "prompt": "把这段数据用饼图展示占比", "render_type": "chart"},
                {"label": "📋 转为表格", "prompt": "把这段数据整理成表格", "render_type": "table"},
            ])
        
        # 检测是否包含步骤序列
        if cls._has_steps(selected_text):
            actions.extend([
                {"label": "🔄 转为流程图", "prompt": "用流程图展示这个流程", "render_type": "flowchart"},
            ])
        
        # 检测是否包含对比数据
        if cls._has_comparison(selected_text):
            actions.extend([
                {"label": "📋 转为对比表格", "prompt": "用表格对比这些内容", "render_type": "table"},
            ])
        
        # 通用指令
        actions.extend([
            {"label": "📝 精简提炼", "prompt": "精简这段内容，保留核心要点", "render_type": "text"},
            {"label": "📖 扩写说明", "prompt": "扩写这段内容，增加细节说明", "render_type": "text"},
            {"label": "🔄 改写措辞", "prompt": "换种方式表达这段内容", "render_type": "text"},
        ])
        
        return actions
    
    @classmethod
    def _has_numbers(cls, text: str) -> bool:
        """检测是否包含数值数据"""
        # 匹配数字模式：百分比、金额、数量等
        patterns = [
            r'\d+%',           # 百分比
            r'\d+\.?\d*[亿万千]',  # 中文单位
            r'¥\d+',           # 金额
            r'\$\d+',          # 美元
            r'\d+\.\d+',       # 小数
        ]
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return False
    
    @classmethod
    def _has_steps(cls, text: str) -> bool:
        """检测是否包含步骤序列"""
        # 匹配步骤模式
        patterns = [
            r'第[一二三四五六七八九十\d]+步',
            r'\d+[.、)）]',
            r'[首先然后接着最后]',
            r'[①②③④⑤⑥⑦⑧⑨⑩]',
        ]
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return False
    
    @classmethod
    def _has_comparison(cls, text: str) -> bool:
        """检测是否包含对比数据"""
        # 匹配对比模式
        patterns = [
            r'[对比比较]',
            r'[优势劣势]',
            r'[优点缺点]',
            r'[vs|VS|Vs]',
        ]
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return False


# ============================================================
# 工具函数
# ============================================================

def convert_render_command_to_slide_json(cmd: RenderCommand) -> Dict[str, Any]:
    """
    将渲染指令转换为 slides JSON 格式
    
    用于向后兼容现有的 slides_data 结构。
    """
    if cmd.render_type == "chart":
        return {
            "type": "content",
            "layout": "text_only",
            "title": cmd.render_params.get("title", ""),
            "items": [{
                "content_type": "chart",
                "chart_type": cmd.render_params.get("chart_type", "bar"),
                "chart_data": cmd.render_params.get("chart_data", {}),
                "text": cmd.source_text
            }]
        }
    elif cmd.render_type == "table":
        return {
            "type": "content",
            "layout": "text_only",
            "title": cmd.render_params.get("title", ""),
            "items": [{
                "content_type": "table",
                "table_data": cmd.render_params.get("table_data", {}),
                "text": cmd.source_text
            }]
        }
    elif cmd.render_type == "flowchart":
        return {
            "type": "content",
            "layout": "text_only",
            "title": cmd.render_params.get("title", ""),
            "items": [{
                "content_type": "flowchart",
                "flowchart_data": cmd.render_params.get("flowchart_data", {}),
                "text": cmd.source_text
            }]
        }
    else:
        # 默认文本
        return {
            "type": "content",
            "layout": "text_only",
            "title": cmd.render_params.get("title", ""),
            "items": [{
                "content_type": "text",
                "text": cmd.render_params.get("text", cmd.source_text)
            }]
        }


# ============================================================
# 批量操作解析器
# ============================================================

class BatchOperationParser:
    """
    批量操作解析器
    
    解析用户的批量操作指令，生成多条渲染指令。
    示例：
    - "把所有数据都用表格" → 批量转换为表格
    - "所有图表用蓝色系" → 批量更新图表颜色
    - "把所有要点精简到20字以内" → 批量精简
    """
    
    # 批量操作关键词
    BATCH_KEYWORDS = {
        "所有": "all",
        "全部": "all",
        "每个": "all",
        "每页": "all",
        "批量": "batch",
    }
    
    # 操作类型
    OPERATION_TYPES = {
        "表格": "table",
        "图表": "chart",
        "流程图": "flowchart",
        "精简": "refine",
        "扩写": "expand",
    }
    
    @classmethod
    def is_batch_operation(cls, instruction: str) -> bool:
        """判断是否是批量操作"""
        for keyword in cls.BATCH_KEYWORDS:
            if keyword in instruction:
                return True
        return False
    
    @classmethod
    def parse_batch_operation(
        cls,
        instruction: str,
        slides_data: List[Dict],
        original_text: str = ""
    ) -> List[RenderCommand]:
        """
        解析批量操作指令
        
        Args:
            instruction: 用户指令
            slides_data: 幻灯片数据
            original_text: 原始文档
            
        Returns:
            渲染指令列表
        """
        commands = []
        
        # 检测目标渲染类型
        target_type = None
        for keyword, render_type in cls.OPERATION_TYPES.items():
            if keyword in instruction:
                target_type = render_type
                break
        
        if not target_type:
            return []
        
        # 遍历所有幻灯片
        for slide_idx, slide in enumerate(slides_data):
            items = slide.get("items", [])
            for item_idx, item in enumerate(items):
                # 根据操作类型生成渲染指令
                if target_type == "table" and item.get("content_type") == "text":
                    # 文本转表格
                    cmd = RenderCommand(
                        source_text=item.get("text", ""),
                        render_type="table",
                        render_params={"title": slide.get("title", "")},
                        slide_index=slide_idx,
                        instruction=instruction
                    )
                    commands.append(cmd)
                
                elif target_type == "chart" and item.get("content_type") == "text":
                    # 文本转图表（检测是否包含数值）
                    text = item.get("text", "")
                    if cls._has_numbers(text):
                        cmd = RenderCommand(
                            source_text=text,
                            render_type="chart",
                            render_params={
                                "chart_type": "bar",
                                "title": slide.get("title", "")
                            },
                            slide_index=slide_idx,
                            instruction=instruction
                        )
                        commands.append(cmd)
                
                elif target_type == "refine":
                    # 批量精简
                    cmd = RenderCommand(
                        source_text=item.get("text", ""),
                        render_type="text",
                        render_params={
                            "text": item.get("text", ""),
                            "style": "concise"
                        },
                        slide_index=slide_idx,
                        instruction=instruction
                    )
                    commands.append(cmd)
        
        return commands
    
    @classmethod
    def _has_numbers(cls, text: str) -> bool:
        """检测是否包含数值数据"""
        patterns = [
            r'\d+%',
            r'\d+\.?\d*[亿万千]',
            r'¥\d+',
            r'\$\d+',
            r'\d+\.\d+',
        ]
        for pattern in patterns:
            if re.search(pattern, text):
                return True
        return False


def parse_batch_operation(
    instruction: str,
    slides_data: List[Dict],
    original_text: str = ""
) -> List[RenderCommand]:
    """便捷函数：解析批量操作"""
    return BatchOperationParser.parse_batch_operation(instruction, slides_data, original_text)

