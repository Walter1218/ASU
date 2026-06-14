"""参考文档加载器 — 支持 Excel/CSV/JSON/Markdown/Word"""
import os
import json
import logging
from typing import Optional

from .models import ReferenceData

logger = logging.getLogger(__name__)


def load_from_file(file_path: str) -> ReferenceData:
    """从文件路径加载参考文档"""
    if not os.path.exists(file_path):
        return ReferenceData(source_path=file_path, error=f"文件不存在: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext in ('.xlsx', '.xls'):
            return _load_excel(file_path)
        elif ext == '.csv':
            return _load_csv(file_path)
        elif ext == '.json':
            return _load_json(file_path)
        elif ext == '.md':
            return _load_markdown(file_path)
        elif ext in ('.docx', '.doc'):
            return _load_word(file_path)
        elif ext in ('.txt', '.text'):
            return _load_text(file_path)
        else:
            return _load_text(file_path)
    except Exception as e:
        logger.error(f"Failed to load reference file {file_path}: {e}")
        return ReferenceData(source_path=file_path, error=str(e))


def _load_excel(path: str) -> ReferenceData:
    """加载 Excel 文件"""
    try:
        import openpyxl
    except ImportError:
        # 降级到 python-pptx 中的依赖或返回错误
        return ReferenceData(source_path=path, error="openpyxl 未安装，无法读取 Excel")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []

    for sheet_name in wb.sheetnames[:5]:  # 最多5个sheet
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        # 转为 markdown 表格
        headers = [str(c) if c is not None else "" for c in rows[0]]
        parts.append(f"## Sheet: {sheet_name}\n")
        parts.append("| " + " | ".join(headers) + " |")
        parts.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows[1:50]:  # 最多50行
            cells = [str(c) if c is not None else "" for c in row]
            parts.append("| " + " | ".join(cells) + " |")
        if len(rows) > 51:
            parts.append(f"\n...(共 {len(rows)} 行，已截断)")

    wb.close()
    content = "\n".join(parts)
    return ReferenceData(
        source_path=path, source_type="excel",
        content=content, loaded=True
    )


def _load_csv(path: str) -> ReferenceData:
    """加载 CSV 文件"""
    import csv
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return ReferenceData(source_path=path, source_type="csv", error="CSV 文件为空")

    headers = rows[0]
    parts = ["| " + " | ".join(headers) + " |"]
    parts.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows[1:50]:
        parts.append("| " + " | ".join(row) + " |")

    content = "\n".join(parts)
    return ReferenceData(
        source_path=path, source_type="csv",
        content=content, loaded=True
    )


def _load_json(path: str) -> ReferenceData:
    """加载 JSON 文件"""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    content = json.dumps(data, ensure_ascii=False, indent=2)
    return ReferenceData(
        source_path=path, source_type="json",
        content=content, structured=data, loaded=True
    )


def _load_markdown(path: str) -> ReferenceData:
    """加载 Markdown 文件"""
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    return ReferenceData(
        source_path=path, source_type="markdown",
        content=content, loaded=True
    )


def _load_word(path: str) -> ReferenceData:
    """加载 Word 文件"""
    try:
        from docx import Document
    except ImportError:
        return ReferenceData(source_path=path, error="python-docx 未安装")

    doc = Document(path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    content = "\n\n".join(paragraphs)
    return ReferenceData(
        source_path=path, source_type="word",
        content=content, loaded=True
    )


def _load_text(path: str) -> ReferenceData:
    """加载纯文本文件"""
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    return ReferenceData(
        source_path=path, source_type="text",
        content=content, loaded=True
    )


def search_local_files(description: str, search_dirs: list = None) -> list:
    """
    根据描述在本地搜索可能的参考文件。

    Args:
        description: 用户描述（如"上季度的销售数据"）
        search_dirs: 搜索目录列表，默认为 ~/Desktop, ~/Documents, ~/Downloads

    Returns:
        候选文件路径列表
    """
    if search_dirs is None:
        search_dirs = [
            os.path.expanduser("~/Desktop"),
            os.path.expanduser("~/Documents"),
            os.path.expanduser("~/Downloads"),
        ]

    # 从描述中提取关键词
    keywords = []
    for word in description.replace("，", " ").replace(",", " ").split():
        if len(word) >= 2:
            keywords.append(word.lower())

    candidates = []
    valid_exts = {'.xlsx', '.xls', '.csv', '.json', '.md', '.docx', '.txt'}

    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            continue
        try:
            for entry in os.listdir(search_dir):
                entry_lower = entry.lower()
                ext = os.path.splitext(entry_lower)[1]
                if ext not in valid_exts:
                    continue
                # 关键词匹配
                if any(kw in entry_lower for kw in keywords):
                    full_path = os.path.join(search_dir, entry)
                    candidates.append(full_path)
        except PermissionError:
            continue

    return candidates[:10]  # 最多返回10个候选
