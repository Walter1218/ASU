"""PPTX 数据适配层 — slides_data 与 .pptx 文件之间的双向转换

职责：
- slides_data → .pptx（复用 ppt_generator）
- .pptx → slides_data（用 python-pptx 解析回来，供 AI 二次处理）
- 为 ONLYOFFICE 提供 HTTP URL
"""

import os
import re
import tempfile
import logging
from typing import Optional

from pptx import Presentation
from pptx.util import Inches, Pt

logger = logging.getLogger(__name__)


class PptxAdapter:
    """PPTX 数据适配器"""

    def __init__(self, file_server=None):
        """
        Args:
            file_server: PPTXFileServer 实例（可选，不传则使用默认单例）
        """
        self._file_server = file_server

    def _get_server(self):
        if self._file_server:
            return self._file_server
        from opencopilot.services.pptx_file_server import ensure_server_running
        return ensure_server_running()

    # ── 核心转换 ──────────────────────────────────────────────

    def slides_data_to_pptx(self, slides_data: list, session_id: str = "default", for_docker: bool = True) -> tuple:
        """slides_data → .pptx 文件，返回 (pptx_path, http_url)

        Args:
            slides_data: 幻灯片数据列表
            session_id: 会话标识（用于生成文件名）
            for_docker: 是否返回 Docker 可访问的 URL（默认 True）

        Returns:
            (local_pptx_path, http_url)
        """
        from ppt_generator import generate_ppt_from_json

        filename = f"cocreation_{session_id}.pptx"
        server = self._get_server()

        # 生成到服务目录
        output_path = os.path.join(server.file_dir, filename)
        try:
            generate_ppt_from_json(slides_data, output_path)
            logger.info("PptxAdapter: generated %s (%d slides)", filename, len(slides_data))
        except Exception as e:
            logger.error("PptxAdapter: generation failed: %s", e)
            raise

        # 获取文件 URL
        http_url = server.get_file_url(filename)
        
        # 如果是 Docker 环境，将 127.0.0.1 替换为 host.docker.internal
        if for_docker and "127.0.0.1" in http_url:
            http_url = http_url.replace("127.0.0.1", "host.docker.internal")
            logger.info("PptxAdapter: Docker URL %s", http_url)
        
        return output_path, http_url

    def pptx_to_slides_data(self, pptx_path: str) -> list:
        """解析 .pptx 文件，提取为 slides_data 格式（供 AI 二次处理）

        Args:
            pptx_path: .pptx 文件路径

        Returns:
            slides_data 列表
        """
        if not os.path.isfile(pptx_path):
            raise FileNotFoundError(f"PPTX not found: {pptx_path}")

        prs = Presentation(pptx_path)
        slides = []

        for i, slide in enumerate(prs.slides):
            slide_data = self._parse_slide(slide, i)
            slides.append(slide_data)

        logger.info("PptxAdapter: parsed %s → %d slides", os.path.basename(pptx_path), len(slides))
        return slides

    def update_single_slide(self, pptx_path: str, slide_index: int, slide_data: dict):
        """只更新指定幻灯片，不重建整个文件

        Args:
            pptx_path: 现有 .pptx 文件路径
            slide_index: 要更新的幻灯片索引
            slide_data: 新的幻灯片数据
        """
        if not os.path.isfile(pptx_path):
            raise FileNotFoundError(f"PPTX not found: {pptx_path}")

        prs = Presentation(pptx_path)

        if slide_index >= len(prs.slides):
            raise IndexError(f"Slide index {slide_index} out of range ({len(prs.slides)} slides)")

        # 删除指定位置的幻灯片，插入新的
        # python-pptx 不直接支持删除幻灯片，需要通过 XML 操作
        slide = prs.slides[slide_index]
        self._clear_slide(slide)
        self._populate_slide(slide, slide_data, prs)

        prs.save(pptx_path)
        logger.info("PptxAdapter: updated slide %d in %s", slide_index, os.path.basename(pptx_path))

    # ── 内部：解析 ──────────────────────────────────────────

    def _parse_slide(self, slide, index: int) -> dict:
        """解析单个幻灯片为 slides_data 格式"""
        title = ""
        subtitle = ""
        items = []

        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if not text:
                    continue

                if shape == slide.shapes.title:
                    title = text
                elif not subtitle and title:
                    # 第二个有文本的 shape 作为 subtitle
                    subtitle = text
                else:
                    # 其余作为 items
                    for para in shape.text_frame.paragraphs:
                        para_text = para.text.strip()
                        if para_text:
                            items.append({
                                "level": para.level,
                                "text": para_text,
                            })

            # 处理表格
            elif shape.has_table:
                table = shape.table
                table_rows = []
                for row in table.rows:
                    row_cells = []
                    for cell in row.cells:
                        row_cells.append(cell.text.strip())
                    table_rows.append("| " + " | ".join(row_cells) + " |")

                if table_rows:
                    items.append({
                        "level": 0,
                        "text": f"[表格 {len(table_rows)} 行]",
                        "content_type": "table",
                        "table_data": table_rows,
                    })

        # 判断 slide 类型
        if index == 0 and not items:
            slide_type = "title"
        else:
            slide_type = "content"

        layout = self._guess_layout(items)

        result = {
            "type": slide_type,
            "layout": layout,
            "title": title,
            "items": items,
        }
        if subtitle:
            result["subtitle"] = subtitle

        return result

    def _guess_layout(self, items: list) -> str:
        """根据 items 内容猜测 layout 类型"""
        if not items:
            return "text_only"

        has_table = any(it.get("content_type") == "table" for it in items)
        if has_table:
            return "table"

        # 如果有 3 组以上的 level-0 开头内容，可能是三栏
        top_level_count = sum(1 for it in items if it.get("level", 0) == 0)
        if top_level_count >= 3:
            return "three_columns"

        return "text_only"

    # ── 内部：写入 ──────────────────────────────────────────

    def _clear_slide(self, slide):
        """清空幻灯片内容"""
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    para.text = ""

    def _populate_slide(self, slide, slide_data: dict, prs):
        """将 slides_data 填充到幻灯片"""
        from ppt_generator import apply_corporate_theme, format_title_slide, format_content_slide

        slide_type = slide_data.get("type", "content")
        if slide_type in ("title", "ending"):
            format_title_slide(slide, slide_data.get("title", ""), slide_data.get("subtitle", ""))
        else:
            layout_type = slide_data.get("layout", "text_only")
            format_content_slide(
                slide,
                slide_data.get("title", ""),
                slide_data.get("items", []),
                layout_type,
                prs,
                slide_data,
            )
