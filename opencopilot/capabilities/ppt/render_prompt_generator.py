"""
渲染指令 Prompt 生成器

根据用户指令和上下文动态生成针对性的 Prompt，提高 AI 理解渲染指令的成功率。
"""

import re
from typing import List, Dict, Any, Optional


class RenderPromptGenerator:
    """
    渲染指令 Prompt 生成器
    
    根据用户指令类型，生成针对性的示例和说明。
    """
    
    # 指令类型 → 渲染类型映射
    INSTRUCTION_TYPE_MAP = {
        # 图表相关
        "柱状图": "chart",
        "折线图": "chart",
        "饼图": "chart",
        "图表": "chart",
        "数据可视化": "chart",
        "趋势": "chart",
        "占比": "chart",
        
        # 表格相关
        "表格": "table",
        "对比": "table",
        "列表": "table",
        "整理": "table",
        
        # 流程图相关
        "流程图": "flowchart",
        "步骤": "flowchart",
        "流程": "flowchart",
        "过程": "flowchart",
        
        # 文本相关
        "精简": "text",
        "提炼": "text",
        "改写": "text",
        "扩写": "text",
        "总结": "text",
        # V6 新增：忠实改写关键词
        "润色": "text",
        "专业化": "text",
        "正式": "text",
        "汇报": "text",
        # V7 新增：标题改写关键词
        "标题": "compound",
        "换标题": "compound",
    }
    
    # 渲染类型 → 示例
    RENDER_TYPE_EXAMPLES = {
        "chart": {
            "bar": {
                "source_text": "2025年营收12.8亿元，2024年营收10.5亿元",
                "render_type": "chart",
                "render_params": {
                    "chart_type": "bar",
                    "title": "营收对比",
                    "chart_data": {
                        "labels": ["2024年", "2025年"],
                        "values": [10.5, 12.8]
                    }
                }
            },
            "line": {
                "source_text": "Q1营收2.8亿，Q2营收3.1亿，Q3营收3.5亿，Q4营收3.4亿",
                "render_type": "chart",
                "render_params": {
                    "chart_type": "line",
                    "title": "季度营收趋势",
                    "chart_data": {
                        "labels": ["Q1", "Q2", "Q3", "Q4"],
                        "values": [2.8, 3.1, 3.5, 3.4]
                    }
                }
            },
            "pie": {
                "source_text": "市场份额：A公司35%，B公司25%，C公司20%，其他20%",
                "render_type": "chart",
                "render_params": {
                    "chart_type": "pie",
                    "title": "市场份额分布",
                    "chart_data": {
                        "labels": ["A公司", "B公司", "C公司", "其他"],
                        "values": [35, 25, 20, 20]
                    }
                }
            }
        },
        "table": {
            "comparison": {
                "source_text": "方案A：成本低、周期长；方案B：成本高、周期短",
                "render_type": "table",
                "render_params": {
                    "title": "方案对比",
                    "table_data": {
                        "columns": ["维度", "方案A", "方案B"],
                        "rows": [
                            ["成本", "低", "高"],
                            ["周期", "长", "短"]
                        ]
                    }
                }
            },
            "list": {
                "source_text": "团队成员：张三-前端，李四-后端，王五-设计",
                "render_type": "table",
                "render_params": {
                    "title": "团队成员",
                    "table_data": {
                        "columns": ["姓名", "角色"],
                        "rows": [
                            ["张三", "前端"],
                            ["李四", "后端"],
                            ["王五", "设计"]
                        ]
                    }
                }
            }
        },
        "flowchart": {
            "process": {
                "source_text": "1.需求分析 2.架构设计 3.编码实现 4.测试验证 5.部署上线",
                "render_type": "flowchart",
                "render_params": {
                    "title": "开发流程",
                    "flowchart_data": {
                        "steps": ["需求分析", "架构设计", "编码实现", "测试验证", "部署上线"]
                    }
                }
            }
        },
        "text": {
            "refine": {
                "source_text": "这段文字需要精简",
                "render_type": "text",
                "render_params": {
                    "text": "精简后的文字",
                    "style": "concise"
                }
            },
            # V6 新增：忠实改写正例
            "faithful_rewrite_good": {
                "source_text": "2025年营收达到12.8亿元，同比增长21.9%，其中海外市场贡献3.2亿元",
                "render_type": "text",
                "render_params": {
                    "text": "2025年营收达12.8亿元（同比+21.9%），海外市场贡献3.2亿元",
                    "style": "professional_faithful",
                    "_note": "正例：保留所有数值、百分比和时间信息"
                }
            },
            # V6 新增：忠实改写反例
            "faithful_rewrite_bad": {
                "source_text": "2025年营收达到12.8亿元，同比增长21.9%",
                "render_type": "text",
                "render_params": {
                    "text": "营收实现大幅增长，业绩表现亮眼",
                    "style": "WRONG_factual_drift",
                    "_note": "反例：数值被模糊化，事实锚点丢失"
                }
            }
        }
    }
    
    @classmethod
    def detect_instruction_type(cls, instruction: str) -> Optional[str]:
        """
        检测用户指令类型
        
        Returns:
            渲染类型（chart/table/flowchart/text）或 None
        """
        for keyword, render_type in cls.INSTRUCTION_TYPE_MAP.items():
            if keyword in instruction:
                return render_type
        return None
    
    @classmethod
    def generate_example_for_type(cls, render_type: str) -> Dict[str, Any]:
        """
        为指定渲染类型生成示例
        
        Returns:
            示例数据
        """
        examples = cls.RENDER_TYPE_EXAMPLES.get(render_type, {})
        if examples:
            # 返回第一个示例
            return list(examples.values())[0]
        return {}
    
    @classmethod
    def generate_prompt(
        cls,
        instruction: str,
        current_slide: Dict[str, Any],
        original_text: str = "",
        selected_text: str = ""
    ) -> str:
        """
        生成针对性的 Prompt
        
        Args:
            instruction: 用户指令
            current_slide: 当前幻灯片数据
            original_text: 原始文档文本
            selected_text: 用户选中的文本
            
        Returns:
            生成的 Prompt
        """
        # 检测指令类型
        render_type = cls.detect_instruction_type(instruction)
        
        # 构建 Prompt
        parts = []
        
        # 基础信息
        parts.append(f"用户指令：{instruction}")
        
        if selected_text:
            parts.append(f"\n用户选中的原文：\n{selected_text}")
        
        # V7 新增：复合任务示例（当指令包含标题改写时）
        if render_type == "compound":
            parts.append(cls._get_compound_task_example())
        # 针对性示例
        elif render_type:
            example = cls.generate_example_for_type(render_type)
            if example:
                parts.append(f"\n## 输出示例（{render_type}类型）")
                parts.append(f"```json\n{{\n  \"render_commands\": [{cls._format_example(example)}]\n}}\n```")
        
        # 通用格式说明
        parts.append("""
## 输出格式要求

必须返回 JSON 格式的渲染指令：

```json
{
  "render_commands": [
    {
      "source_text": "原文片段（必填，用于定位）",
      "render_type": "chart|table|flowchart|text|delete_slide|delete_item|modify_layout|move_item|update_item",
      "render_params": {
        "title": "标题",
        // 根据 render_type 提供相应参数
      },
      "slide_index": -1,
      "slot": "body"
    }
  ]
}
```

重要：
1. source_text 必须是原文中的完整片段（delete/modify/move 操作可为空字符串）
2. render_type 支持以下类型：
   - 内容类型：chart, table, flowchart, text
   - 操作类型：delete_slide（删除幻灯片）, delete_item（删除元素，需 render_params.item_index）, modify_layout（修改排版，需 render_params.layout）, move_item（移动元素，需 item_index + target_slide）, update_item（更新元素，需 item_index + updates）
3. chart 类型需要提供 chart_type（bar/line/pie）和 chart_data
4. table 类型需要提供 table_data（含 columns 和 rows）
5. flowchart 类型需要提供 flowchart_data（含 steps，为字符串数组）
6. 默认只修改当前正在编辑的页，除非用户明确要求新增/删除页面；此时 slide_index 必须使用当前页索引或 -1
7. 如果用户要求修改标题、headline 或结论型标题，必须输出 slot=title，并将标题文本放在 render_params.title
8. 改写/润色时，每条输出必须保留原文中的事实锚点（数字、金额、百分比、时间、专有名词）
9. 不得将计划/预计/目标等未来态表述改写为已完成/已实现等过去态
10. 风险、限制、负面信息必须显式保留
11. 条目数量和顺序保持不变，不合并、不拆分
12. 区间数据不可被压缩为单一数值
13. 删除/移动操作请确认 slide_index 正确（从 0 开始计数）
""")
        
        return "\n".join(parts)
    
    @classmethod
    def _format_example(cls, example: Dict[str, Any]) -> str:
        """格式化示例为 JSON 字符串"""
        import json
        return json.dumps(example, ensure_ascii=False, indent=2)
    
    @classmethod
    def _get_compound_task_example(cls) -> str:
        """V7 新增：复合任务示例（标题改写 + 图表转换 + 文案润色）"""
        return """
## 复合任务输出示例

当指令同时包含多种操作时（如改标题+转图表+润色文案），输出多个 render_commands：

```json
{
  "render_commands": [
    {
      "source_text": "Business Overview",
      "render_type": "text",
      "render_params": {
        "title": "2026 H1 营收增长 32%，突破 850M",
        "style": "conclusion_headline"
      },
      "slide_index": -1,
      "slot": "title"
    },
    {
      "source_text": "Q1 revenue was 380M RMB and Q2 revenue was 470M RMB",
      "render_type": "chart",
      "render_params": {
        "chart_type": "bar",
        "title": "季度营收对比",
        "chart_data": {"labels": ["Q1", "Q2"], "values": [380, 470]}
      },
      "slide_index": -1,
      "slot": "body"
    },
    {
      "source_text": "Revenue grew 32 percent year over year",
      "render_type": "text",
      "render_params": {
        "text": "2026 H1 营收同比增长 32%，企业级订阅驱动主要增长",
        "style": "professional_faithful"
      },
      "slide_index": -1,
      "slot": "body"
    }
  ]
}
```

注意：标题改写必须使用 slot=title，并将标题文本放在 render_params.title 中。改写文案时必须保留原文中的事实锚点（数字、百分比、时间）。
"""


# 便捷函数
def generate_render_prompt(
    instruction: str,
    current_slide: Dict[str, Any],
    original_text: str = "",
    selected_text: str = ""
) -> str:
    """生成渲染指令 Prompt"""
    return RenderPromptGenerator.generate_prompt(
        instruction, current_slide, original_text, selected_text
    )
