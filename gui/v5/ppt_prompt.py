"""PPT Prompt 构建 & 解析 — Studio / V5Plus 共享模块

统一 prompt 模板 + 幻灯片解析逻辑，确保两个入口生成一致的 JSON 输出。
"""
import json
import re


def build_ppt_generation_prompt(
    text: str,
    strategy: str = "pyramid",
    audience: str = "",
    duration: str = "10",
) -> str:
    """构建 PPT 生成 prompt（与 Studio Tab 完全一致）

    Args:
        text: 原始输入文本
        strategy: 叙事策略（pyramid / narrative / contrast）
        audience: 目标受众
        duration: 演讲时长（分钟）
    """
    text_len = len(text)
    length_hint = ""
    if text_len > 8000:
        length_hint = (
            f"\n\n注意：原始内容较长（{text_len} 字符），请重点提炼核心信息，"
            f"不要试图覆盖所有细节。优先保证结构清晰和要点精炼。"
        )

    prompt = (
        f"请根据以下内容生成 PPT 大纲。\n\n"
        f"要求：\n"
        f"1. 严格输出纯 JSON 格式，不要输出任何其他文字、代码块标记或解释\n"
        f"2. 输出格式为 {{\"title\": \"演示文稿标题\", \"slides\": [...]}}\n"
        f"3. 每个 slide 包含 type, layout, title, items, source_excerpt 等字段\n"
        f"4. layout 可选值:\n"
        f"   - center: 居中标题页（封面/结尾）\n"
        f"   - text_only: 纯文字列表（默认）\n"
        f"   - image_right / image_left: 图文混排（案例说明/场景描述）\n"
        f"   - three_columns: 三栏并排（多维度对比）\n"
        f"   - table: 表格（结构化数据/参数对比/统计数据）\n"
        f"   - chart: 图表（数值趋势/占比/对比，支持 bar/line/pie）\n"
        f"   - flowchart: 流程图（步骤流程/决策树/工作流）\n"
        f"5. 智能选型规则：\n"
        f"   - 含数值数据、统计、趋势 → 用 chart\n"
        f"   - 含结构化对比、参数表、分类数据 → 用 table\n"
        f"   - 含步骤、流程、阶段、顺序 → 用 flowchart\n"
        f"   - 含案例、场景描述 → 用 image_right\n"
        f"   - 其他普通内容 → 用 text_only\n"
        f"6. 每页 3-5 个要点，每个要点一句话\n"
        f"7. 特殊布局的 items 数据结构：\n"
        f"   table 类型: items[0] 需含 content_type=\"table\" 和 "
        f"table_data={{\"columns\":[\"列1\",\"列2\"],\"rows\":[[\"值1\",\"值2\"],...]}}\n"
        f"   chart 类型: items[0] 需含 content_type=\"chart\", chart_type=\"bar|line|pie\" 和 "
        f"chart_data={{\"title\":\"图表标题\",\"labels\":[\"标签1\",\"标签2\"],\"datasets\":[{{\"label\":\"系列名\",\"data\":[10,20]}}]}}\n"
        f"   flowchart 类型: items[0] 需含 content_type=\"flowchart\" 和 "
        f"flowchart_data={{\"title\":\"流程标题\",\"steps\":[\"步骤1\",\"步骤2\",\"步骤3\"],\"layout\":\"horizontal\"}}\n"
        f"8. source_excerpt 字段：每页 slide 必须包含，值为该页内容对应的原文片段（20-80字），"
        f"从原始内容中直接摘录，用于原文高亮联动\n"
        f"9. 必须包含结尾页：type=ending, layout=center, title='谢谢', subtitle='Q & A'\n"
        f"10. 覆盖原文所有一级章节，不要遗漏任何主题\n"
        f"11. 叙事策略: {strategy}"
    )
    if audience:
        prompt += f"\n12. 目标受众: {audience}"
    if duration:
        prompt += f"\n13. 演讲时长: 约 {duration} 分钟"
    prompt += f"\n\n原始内容：\n{text}{length_hint}"

    return prompt


def build_ppt_modify_prompt(instruction: str, slides_data: list,
                              current_slide_index: int = -1,
                              original_text: str = "") -> str:
    """构建 PPT 修改指令 prompt（与 Studio 一致，使用 render_commands 格式）

    Args:
        instruction: 用户修改指令（如 "把标题改短"、"转成流程图"）
        slides_data: 当前幻灯片数据
        current_slide_index: 当前正在编辑的幻灯片索引（-1 表示未指定）
        original_text: 原始文档文本（用于 source_range 定位）
    """
    total = len(slides_data)
    idx = max(0, current_slide_index) if current_slide_index >= 0 else 0
    current_slide = slides_data[idx] if slides_data and idx < total else {}

    # 上下文信息（与 Studio 一致）
    parts = [f"PPT 共 {total} 页，当前第 {idx + 1} 页。"]

    # 相邻幻灯片摘要
    if idx > 0:
        prev = slides_data[idx - 1]
        parts.append(f"\n前一页（第 {idx} 页）摘要：{prev.get('title', '')}")
    if idx < total - 1:
        nxt = slides_data[idx + 1]
        parts.append(f"\n后一页（第 {idx + 2} 页）摘要：{nxt.get('title', '')}")

    parts.append(f"\n当前幻灯片：\n```json\n{json.dumps(current_slide, ensure_ascii=False, indent=2)}\n```")
    parts.append(f"\n用户指令：{instruction}")

    # 渲染指令格式说明（与 Studio 一致，使用动态 Prompt 生成器）
    try:
        from opencopilot.capabilities.ppt.render_prompt_generator import generate_render_prompt
        prompt_section = generate_render_prompt(
            instruction=instruction,
            current_slide=current_slide,
            original_text=original_text,
            selected_text=""
        )
        parts.append(f"\n{prompt_section}")
    except Exception:
        # 回退到静态 Prompt
        parts.append(_STATIC_RENDER_PROMPT_TEMPLATE)

    return "\n".join(parts)


# 静态回退模板（当 render_prompt_generator 不可用时）
_STATIC_RENDER_PROMPT_TEMPLATE = """
## 输出格式（推荐：渲染指令）

返回 JSON 格式的渲染指令数组：

```json
{
  "render_commands": [
    {
      "source_text": "原文片段（用于定位）",
      "render_type": "chart",
      "render_params": {
        "chart_type": "bar",
        "title": "图表标题",
        "chart_data": {"labels": ["标签1", "标签2"], "values": [10, 20]}
      },
      "slide_index": -1,
      "slot": "body"
    }
  ]
}
```

支持的 render_type：
- text: 纯文本（默认）
- table: 表格（需提供 table_data，含 columns 和 rows）
- chart: 图表（需指定 chart_type: bar/line/pie，提供 chart_data）
- flowchart: 流程图（需提供 flowchart_data，含 steps 为字符串数组）
- image_right/image_left: 图文混排
- delete_slide: 删除幻灯片（指定 slide_index）
- delete_item: 删除元素（指定 slide_index 和 render_params.item_index）
- modify_layout: 修改排版（指定 slide_index 和 render_params.layout，如 three_columns / text_only / image_right）
- move_item: 移动元素到其他页（指定 slide_index, render_params.item_index, render_params.target_slide）
- update_item: 更新元素内容（指定 slide_index, render_params.item_index, render_params.updates）

重要：
- 默认只修改当前正在编辑的页，除非用户明确要求新增页面或删除页面
- 删除操作请确认 slide_index 正确（从 0 开始计数）
"""


def build_ppt_reextract_prompt(instruction: str, text: str) -> str:
    """构建重新提炼 prompt

    Args:
        instruction: 用户重新提炼指令
        text: 原始文本
    """
    return (
        f"请根据以下指令重新提炼 PPT 大纲。\n\n"
        f"用户指令: {instruction}\n\n"
        f"要求:\n"
        f"1. 严格输出纯 JSON 格式\n"
        f"2. 输出格式为 {{\"title\": \"标题\", \"slides\": [...]}}\n"
        f"3. 每个 slide 包含 type, layout, title, items, source_excerpt\n"
        f"4. layout 可选: center / text_only / image_right / image_left / "
        f"three_columns / chart / flowchart / table\n"
        f"5. 特殊布局数据结构与生成时一致\n"
        f"6. 根据内容智能选择 layout，不要全部用 text_only\n"
        f"7. source_excerpt 字段必须包含\n\n"
        f"原始内容:\n{text}"
    )


def parse_slides_from_text(text: str) -> list:
    """从 AI 输出文本中解析 JSON slides 数组

    优先使用 ppt_generator.extract_json_from_text（更健壮，含 Markdown 降级），
    若不可用则回退到本地解析逻辑。与 Studio Tab 完全一致。
    """
    # 优先使用 ppt_generator 的健壮解析
    try:
        from ppt_generator import extract_json_from_text
        result = extract_json_from_text(text)
        if result:
            if isinstance(result, dict) and "slides" in result:
                slides = result["slides"]
                if isinstance(slides, list) and len(slides) > 0:
                    return slides
            elif isinstance(result, list) and len(result) > 0:
                return result
    except ImportError:
        pass
    except Exception as e:
        print(f"[ppt_prompt] ppt_generator 解析异常: {e}")

    # 回退：本地 JSON 解析
    # 尝试提取 ```json ... ``` 代码块
    json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if isinstance(data, dict) and "slides" in data and isinstance(data["slides"], list):
                return data["slides"]
            elif isinstance(data, list) and len(data) > 0:
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    # 尝试提取花括号包裹的 JSON 对象
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if obj_match:
        try:
            data = json.loads(obj_match.group(0))
            if isinstance(data, dict) and "slides" in data and isinstance(data["slides"], list):
                return data["slides"]
        except (json.JSONDecodeError, ValueError):
            pass

    # 尝试直接解析整个文本为 JSON
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict) and "slides" in data and isinstance(data["slides"], list):
            return data["slides"]
        elif isinstance(data, list) and len(data) > 0:
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # 尝试提取方括号包裹的数组
    array_match = re.search(r"\[\s*\{.*?\}\s*\]", text, re.DOTALL)
    if array_match:
        try:
            slides = json.loads(array_match.group(0))
            if isinstance(slides, list) and len(slides) > 0:
                return slides
        except (json.JSONDecodeError, ValueError):
            pass

    print(f"[ppt_prompt] 所有解析方式均失败，文本前 300 字符: {text[:300]}")
    return []
