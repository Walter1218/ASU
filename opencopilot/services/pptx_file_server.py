"""PPTX 文件服务 — 让 ONLYOFFICE Document Server 能访问本地生成的 .pptx

职责：
- 监听 localhost:9091
- /healthcheck — 健康检查
- /files/<filename> — 提供 .pptx 文件下载（ONLYOFFICE 通过此 URL 加载文档）
- /upload — ONLYOFFICE force save 回调，接收编辑后的 .pptx 并存回本地
"""

import os
import shutil
import tempfile
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

# 默认端口
DEFAULT_PORT = 9091

# 文件存放目录（临时）
_FILE_DIR = os.path.join(tempfile.gettempdir(), "opencopilot_pptx")


class PPTXRequestHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    # 禁止打印每次请求日志到 stderr（除非 DEBUG）
    def log_message(self, fmt, *args):
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("PPTX-Server: %s", fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/healthcheck":
            self._send_json({"status": "ok", "file_dir": _FILE_DIR})
        elif path.startswith("/files/"):
            filename = path[len("/files/"):]
            self._serve_file(filename)
        else:
            self._send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/upload":
            self._handle_upload()
        else:
            self._send_error(404, "Not Found")

    # ── 内部方法 ──────────────────────────────────────────────

    def _serve_file(self, filename: str):
        """提供 .pptx 文件下载"""
        # 安全检查：禁止路径穿越
        safe_name = os.path.basename(filename)
        file_path = os.path.join(_FILE_DIR, safe_name)

        if not os.path.isfile(file_path):
            self._send_error(404, f"File not found: {safe_name}")
            return

        try:
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.presentationml.presentation")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            logger.debug("PPTX-Server: served %s (%d bytes)", safe_name, len(data))
        except Exception as e:
            logger.error("PPTX-Server: serve error: %s", e)
            self._send_error(500, str(e))

    def _handle_upload(self):
        """接收 ONLYOFFICE force save 回调"""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_error(400, "Empty body")
            return

        # 从 query 参数获取文件名
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        filename = params.get("filename", ["upload.pptx"])[0]
        safe_name = os.path.basename(filename)

        try:
            body = self.rfile.read(content_length)
            dest = os.path.join(_FILE_DIR, safe_name)
            with open(dest, "wb") as f:
                f.write(body)
            logger.info("PPTX-Server: received %s (%d bytes)", safe_name, content_length)
            self._send_json({"status": "ok", "filename": safe_name, "size": content_length})
        except Exception as e:
            logger.error("PPTX-Server: upload error: %s", e)
            self._send_error(500, str(e))

    def _send_json(self, data: dict):
        import json
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, message: str):
        import json
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """CORS preflight"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


class PPTXFileServer:
    """PPTX 文件服务管理器（可启停的后台线程）"""

    def __init__(self, port: int = DEFAULT_PORT, file_dir: str = None):
        self.port = port
        self.file_dir = file_dir or _FILE_DIR
        self._server = None
        self._thread = None

        # 确保目录存在
        os.makedirs(self.file_dir, exist_ok=True)

    def start(self):
        """启动后台 HTTP 服务"""
        if self._server is not None:
            logger.warning("PPTX-Server: already running on port %d", self.port)
            return

        # 更新全局文件目录
        global _FILE_DIR
        _FILE_DIR = self.file_dir

        try:
            self._server = HTTPServer(("127.0.0.1", self.port), PPTXRequestHandler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            logger.info("PPTX-Server: started on http://127.0.0.1:%d (dir=%s)", self.port, self.file_dir)
        except OSError as e:
            if "Address already in use" in str(e):
                logger.warning("PPTX-Server: port %d already in use, assuming another instance is running", self.port)
                # 标记为运行中，因为端口已被占用说明有服务在运行
                self._server = True  # 占位标记
            else:
                raise

    def stop(self):
        """停止服务"""
        if self._server:
            self._server.shutdown()
            self._server = None
            self._thread = None
            logger.info("PPTX-Server: stopped")

    def is_running(self) -> bool:
        return self._server is not None

    def get_file_url(self, filename: str) -> str:
        """获取文件的 HTTP URL（供 ONLYOFFICE 使用）"""
        return f"http://127.0.0.1:{self.port}/files/{os.path.basename(filename)}"

    def put_file(self, src_path: str, target_name: str = None) -> str:
        """将文件复制到服务目录，返回文件名"""
        target_name = target_name or os.path.basename(src_path)
        dest = os.path.join(self.file_dir, target_name)
        shutil.copy2(src_path, dest)
        logger.info("PPTX-Server: stored %s (%d bytes)", target_name, os.path.getsize(dest))
        return target_name


# ── 便捷的单例管理 ────────────────────────────────────────────

_server_instance: PPTXFileServer = None


def get_pptx_server(port: int = DEFAULT_PORT) -> PPTXFileServer:
    """获取或创建全局单例"""
    global _server_instance
    if _server_instance is None:
        _server_instance = PPTXFileServer(port=port)
    return _server_instance


def ensure_server_running(port: int = DEFAULT_PORT) -> PPTXFileServer:
    """确保服务已启动，返回实例"""
    server = get_pptx_server(port)
    if not server.is_running():
        try:
            server.start()
        except OSError as e:
            if "Address already in use" in str(e):
                logger.warning("PPTX-Server: port %d already in use, reusing existing server", port)
                # 标记为运行中
                server._server = True
            else:
                raise
    return server
