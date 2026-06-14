"""
OnlyOffice PPT 编辑器封装组件

彻底替换自研 SlideRenderer，将 PPT 预览和编辑全部交给 OnlyOffice Document Server。

职责：
- 封装 QWebEngineView 加载 OnlyOffice 编辑器
- 提供 slides_data → .pptx → OnlyOffice 加载的完整链路
- 暴露常用 API：跳转幻灯片、获取内容、监听变更、截图缩略图

参考实现：复用 benchmark_onlyoffice_capture.py 中的 OnlyOfficeScreenshotter 逻辑

注意：本模块采用延迟导入策略，避免在模块加载时触发 PyQt6-WebEngine 的
动态库依赖检查。这样即使 QtWebEngine 二进制文件缺失，也不会阻塞整个应用启动。
"""

import json
import time
import sys
import os
import tempfile
import uuid
import threading
from pathlib import Path
from typing import List, Dict, Optional, Callable

# Qt (基础组件)
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QTimer, QEventLoop, QObject, pyqtSlot
from PyQt6.QtGui import QPixmap

# 项目路径
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ppt_generator import generate_ppt_from_json

# OnlyOffice 配置
ONLYOFFICE_SERVER_URL = os.environ.get("ONLYOFFICE_SERVER_URL", "http://localhost:9090")
ONLYOFFICE_JWT_SECRET = os.environ.get("ONLYOFFICE_JWT_SECRET", "opencopilot-local-dev")

# PPTX 文件服务
from opencopilot.services.pptx_file_server import ensure_server_running, get_pptx_server

# ── 延迟导入 WebEngine 组件 ──────────────────────────────────
# 避免在模块加载时触发 QtWebEngine 动态库加载，只在实例化 OnlyOfficeWidget 时导入

_webengine_imported = False
QWebEngineView = None
QWebChannel = None


def _ensure_webengine_imported():
    """延迟导入 QtWebEngine 组件，失败时抛出明确错误"""
    global _webengine_imported, QWebEngineView, QWebChannel
    if _webengine_imported:
        return
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView as _QWebEngineView
        from PyQt6.QtWebChannel import QWebChannel as _QWebChannel
        QWebEngineView = _QWebEngineView
        QWebChannel = _QWebChannel
        _webengine_imported = True
    except ImportError as e:
        raise ImportError(
            "PyQt6 QtWebEngine 组件加载失败。"
            "macOS 上请确保已安装完整版 PyQt6-WebEngine:\n"
            "  pip install --force-reinstall PyQt6-WebEngine PyQt6-WebEngine-Qt6\n"
            f"原始错误: {e}"
        ) from e


class OnlyOfficeBridge(QObject):
    """Bridge for two-way communication between Python and OnlyOffice JS"""
    document_ready = pyqtSignal()
    document_error = pyqtSignal(str)
    slide_changed = pyqtSignal(int)  # 当前幻灯片索引变更
    content_modified = pyqtSignal()   # 内容被用户编辑

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_ready = False
        self._current_slide = 0

    @pyqtSlot()
    def onDocumentReady(self):
        self._is_ready = True
        self.document_ready.emit()

    @pyqtSlot(str)
    def onDocumentError(self, error_msg: str):
        self.document_error.emit(error_msg)

    @pyqtSlot(int)
    def onSlideChanged(self, slide_index: int):
        self._current_slide = slide_index
        self.slide_changed.emit(slide_index)

    @pyqtSlot()
    def onContentModified(self):
        self.content_modified.emit()

    def is_ready(self) -> bool:
        return self._is_ready

    def get_current_slide(self) -> int:
        return self._current_slide


class OnlyOfficeWidget(QWidget):
    """OnlyOffice PPT 编辑器封装 — 唯一渲染+编辑入口"""

    # 信号
    slide_changed = pyqtSignal(int)       # 当前幻灯片索引变更
    content_modified = pyqtSignal()       # 内容被用户编辑（OnlyOffice内）
    document_ready = pyqtSignal()         # 文档加载完成
    document_error = pyqtSignal(str)     # 文档加载错误

    def __init__(self, parent=None):
        super().__init__(parent)

        # 延迟导入 WebEngine 组件
        _ensure_webengine_imported()

        self._slides_data: List[Dict] = []
        self._pptx_path: Optional[str] = None
        self._pptx_url: Optional[str] = None
        self._current_slide_index = 0
        self._is_loading = False
        self._onlyoffice_available: Optional[bool] = None  # 缓存可用性检查结果

        # WebView
        self._web_view = QWebEngineView()
        self._web_view.setVisible(True)

        # Bridge
        self._bridge = OnlyOfficeBridge(self)
        self._channel = QWebChannel(self._web_view.page())
        self._channel.registerObject("pyBridge", self._bridge)
        self._web_view.page().setWebChannel(self._channel)

        # 连接桥信号到本组件信号
        self._bridge.document_ready.connect(self.document_ready.emit)
        self._bridge.document_error.connect(self.document_error.emit)
        self._bridge.slide_changed.connect(self.slide_changed.emit)
        self._bridge.content_modified.connect(self.content_modified.emit)

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._web_view)

        # 确保文件服务在运行
        self._file_server = ensure_server_running()

        # 异步检查 OnlyOffice 可用性（不阻塞 UI）
        QTimer.singleShot(500, self._check_onlyoffice_async)

    # ============================================================
    # 核心 API
    # ============================================================

    def load_slides(self, slides_data: List[Dict], current_index: int = 0) -> bool:
        """加载 slides_data 并渲染到 OnlyOffice

        Args:
            slides_data: 幻灯片数据列表
            current_index: 加载后显示的幻灯片索引

        Returns:
            是否成功启动加载
        """
        if not slides_data:
            self._slides_data = []
            self._web_view.setHtml(self._build_empty_html())
            return True

        self._slides_data = slides_data
        self._current_slide_index = current_index
        self._is_loading = True

        try:
            # 1. 生成 .pptx
            self._pptx_path = self._generate_pptx(slides_data)
            if not self._pptx_path:
                self.document_error.emit("PPTX 生成失败")
                return False

            # 2. 放入文件服务目录
            filename = f"{uuid.uuid4().hex[:8]}.pptx"
            self._file_server.put_file(self._pptx_path, filename)
            self._pptx_url = self._file_server.get_file_url(filename)

            # 3. 生成 OnlyOffice HTML 并加载
            doc_key = f"oc_{uuid.uuid4().hex[:8]}_{int(time.time())}"
            html = self._generate_editor_html(self._pptx_url, doc_key, current_index)
            self._bridge._is_ready = False
            self._web_view.setHtml(html, QUrl(ONLYOFFICE_SERVER_URL))

            return True

        except Exception as e:
            self.document_error.emit(f"加载失败: {e}")
            self._is_loading = False
            return False

    def go_to_slide(self, index: int):
        """跳转到指定幻灯片"""
        if not self._bridge.is_ready():
            return
        self._current_slide_index = index
        # 通过 JS 调用 OnlyOffice API 或发送键盘事件
        js = f"""
        (function() {{
            if (window.docEditor && window.docEditor.goToSlide) {{
                window.docEditor.goToSlide({index});
            }} else {{
                // Fallback: send keyboard event to navigate
                var event = new KeyboardEvent('keydown', {{
                    key: 'PageDown',
                    code: 'PageDown',
                    keyCode: 34,
                    which: 34,
                    bubbles: true
                }});
                document.dispatchEvent(event);
            }}
        }})();
        """
        self._web_view.page().runJavaScript(js)

    def get_current_slide_index(self) -> int:
        """获取当前幻灯片索引"""
        return self._bridge.get_current_slide()

    def capture_thumbnail(self, slide_index: int, output_path: str,
                          timeout_ms: int = 10000) -> bool:
        """截图指定幻灯片作为缩略图

        Args:
            slide_index: 要截图的幻灯片索引
            output_path: 截图保存路径
            timeout_ms: 最大等待时间

        Returns:
            是否成功
        """
        if not self._bridge.is_ready():
            return False

        # 跳转到目标幻灯片
        self.go_to_slide(slide_index)

        # 等待渲染稳定
        start_time = time.time()
        while (time.time() - start_time) * 1000 < 2000:
            QApplication.processEvents()
            time.sleep(0.1)

        # 截图
        pixmap = self._web_view.grab()

        # 裁剪内容区域（去掉 OnlyOffice 工具栏和面板）
        # 根据实际布局调整裁剪参数
        w = pixmap.width()
        h = pixmap.height()

        # 估算内容区域（左侧缩略图面板约 200px，顶部工具栏约 90px）
        left_panel = 200
        top_margin = 90
        right_margin = 20
        bottom_margin = 30

        content_rect = pixmap.rect().adjusted(
            left_panel, top_margin, -right_margin, -bottom_margin
        )
        content_pixmap = pixmap.copy(content_rect)
        content_pixmap.save(output_path, "PNG")

        return True

    def get_pixmap(self) -> Optional[QPixmap]:
        """获取当前视图的截图"""
        if not self._bridge.is_ready():
            return None
        return self._web_view.grab()

    def reload(self):
        """重新加载当前 slides_data"""
        self.load_slides(self._slides_data, self._current_slide_index)

    def is_ready(self) -> bool:
        """OnlyOffice 是否已就绪"""
        return self._bridge.is_ready()

    def is_loading(self) -> bool:
        """是否正在加载中"""
        return self._is_loading

    # ============================================================
    # 内部方法
    # ============================================================

    def _generate_pptx(self, slides_data: List[Dict]) -> Optional[str]:
        """生成 .pptx 文件"""
        try:
            temp_dir = tempfile.mkdtemp(prefix="oc_pptx_")
            pptx_path = os.path.join(temp_dir, "presentation.pptx")
            generate_ppt_from_json(slides_data, pptx_path)
            return pptx_path
        except Exception as e:
            print(f"[OnlyOfficeWidget] PPTX 生成失败: {e}")
            return None

    def _generate_jwt_token(self, payload: dict = None) -> str:
        """生成 OnlyOffice JWT token"""
        import jwt
        from datetime import datetime, timezone, timedelta

        if not ONLYOFFICE_JWT_SECRET:
            return ""

        if payload is None:
            payload = {}

        now = datetime.now(timezone.utc)
        default_payload = {
            "iss": "OpenCopilot",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        }
        default_payload.update(payload)

        token = jwt.encode(default_payload, ONLYOFFICE_JWT_SECRET, algorithm="HS256")
        return token

    def _generate_editor_html(self, pptx_url: str, doc_key: str,
                              initial_slide: int = 0) -> str:
        """生成 OnlyOffice 编辑器 HTML"""
        # 生成 JWT token（如果配置了密钥）
        token = ""
        if ONLYOFFICE_JWT_SECRET:
            token_payload = {
                "document": {
                    "key": doc_key,
                    "url": pptx_url,
                    "fileType": "pptx",
                    "title": "OpenCopilot_Presentation.pptx"
                },
                "editorConfig": {
                    "mode": "edit",
                    "lang": "zh-CN",
                    "callbackUrl": "",
                    "user": {"id": "user-001", "name": "OpenCopilot"}
                }
            }
            token = self._generate_jwt_token(token_payload)

        token_field = f'"token": "{token}",' if token else ""

        return f"""<!DOCTYPE html>
<html style="height: 100%; width: 100%;">
<head>
    <meta charset="UTF-8">
    <title>OpenCopilot PPT Editor</title>
    <script type="text/javascript" src="{ONLYOFFICE_SERVER_URL}/web-apps/apps/api/documents/api.js"></script>
    <script type="text/javascript" src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{ height: 100%; width: 100%; overflow: hidden; }}
        #editor {{ height: 100%; width: 100%; }}
    </style>
</head>
<body>
    <div id="editor"></div>
    <script>
        var pyBridge = null;
        new QWebChannel(qt.webChannelTransport, function(channel) {{
            pyBridge = channel.objects.pyBridge;
        }});

        var docEditor = new DocsAPI.DocEditor("editor", {{
            "document": {{
                "fileType": "pptx",
                "key": "{doc_key}",
                "title": "OpenCopilot_Presentation.pptx",
                "url": "{pptx_url}"
            }},
            "documentType": "slide",
            {token_field}
            "editorConfig": {{
                "mode": "edit",
                "lang": "zh-CN",
                "callbackUrl": "",
                "user": {{"id": "user-001", "name": "OpenCopilot"}},
                "customization": {{
                    "autosave": false,
                    "chat": false,
                    "comments": false,
                    "feedback": false,
                    "goback": false,
                    "help": false,
                    "compactHeader": true,
                    "compactToolbar": true,
                    "toolbarNoTabs": true
                }}
            }},
            "events": {{
                "onAppReady": function() {{
                    console.log('[OnlyOffice] App ready');
                }},
                "onDocumentReady": function() {{
                    console.log('[OnlyOffice] Document ready');
                    setTimeout(function() {{
                        if (pyBridge) pyBridge.onDocumentReady();
                        // Navigate to initial slide
                        if ({initial_slide} > 0) {{
                            // Try to navigate using internal API
                            var api = window.docEditor;
                            if (api && api.asc_moveToSlide) {{
                                api.asc_moveToSlide({initial_slide});
                            }}
                        }}
                    }}, 2000);
                }},
                "onError": function(event) {{
                    console.error('[OnlyOffice] Error:', event.data);
                    if (pyBridge) pyBridge.onDocumentError(JSON.stringify(event.data));
                }}
            }}
        }});
    </script>
</body>
</html>"""

    def _build_empty_html(self) -> str:
        """空状态 HTML"""
        return """<!DOCTYPE html>
<html style="height: 100%; width: 100%; background: #1e1e1e;">
<head><meta charset="UTF-8"><title>Empty</title></head>
<body style="display: flex; justify-content: center; align-items: center; height: 100%; margin: 0;">
    <div style="color: #666; font-family: sans-serif; text-align: center;">
        <div style="font-size: 48px; margin-bottom: 16px;">📄</div>
        <div style="font-size: 16px;">尚未加载 PPT 内容</div>
        <div style="font-size: 12px; color: #888; margin-top: 8px;">请从左侧导入或粘贴内容</div>
    </div>
</body>
</html>"""

    def _check_onlyoffice_async(self):
        """异步检查 OnlyOffice 可用性并缓存结果"""
        import threading
        def _check():
            self._onlyoffice_available = check_onlyoffice_available()
        threading.Thread(target=_check, daemon=True).start()

    def _closeEvent(self, event):
        """清理资源"""
        # 可选：清理临时文件
        if self._pptx_path and os.path.exists(self._pptx_path):
            try:
                os.remove(self._pptx_path)
            except:
                pass
        super().closeEvent(event)


# ============================================================
# 便捷函数
# ============================================================

def check_onlyoffice_available() -> bool:
    """检查 OnlyOffice Document Server 是否可用"""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{ONLYOFFICE_SERVER_URL}/web-apps/apps/api/documents/api.js",
            method="HEAD"
        )
        req.add_header("User-Agent", "OpenCopilot/1.0")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
