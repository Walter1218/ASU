"""OnlyOfficeWidget — 嵌入 ONLYOFFICE 编辑器的 PyQt6 Widget

封装 QWebEngineView + OnlyOfficeBridge + PptxAdapter，
提供与原 SlideRenderer 兼容的高层接口。
"""

import os
import time
import logging
from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from gui.v5plus.onlyoffice_bridge import OnlyOfficeBridge

logger = logging.getLogger(__name__)

# ONLYOFFICE Document Server 地址
ONLYOFFICE_SERVER_URL = os.environ.get("ONLYOFFICE_SERVER_URL", "http://localhost:9090")
ONLYOFFICE_JWT_SECRET = os.environ.get("ONLYOFFICE_JWT_SECRET", "opencopilot-local-dev")


class OnlyOfficeWidget(QWidget):
    """嵌入 ONLYOFFICE 编辑器的 Widget，可替换 SlideRenderer"""

    # 信号（与 StageEditor 对接）
    document_ready = pyqtSignal()
    slide_changed = pyqtSignal(int)
    document_modified = pyqtSignal()
    export_ready = pyqtSignal(str)  # 导出文件路径

    # 类级缓存：预加载的 JS 代码和编辑器实例
    _preloaded_js = None
    _editor_pool = None  # 可选：编辑器实例池

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session_id = ""
        self._slides_data = []
        self._pptx_path = None
        self._pptx_url = None
        self._adapter = None
        self._file_server = None
        self._is_loaded = False
        self._load_start_time = 0
        self._init_ui()
        # 启动预加载（异步）
        self._preload_editor_resources()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 状态标签（加载中 / 错误时显示）──
        self._status_label = QLabel("正在加载 ONLYOFFICE 编辑器...")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet(
            "background: #fafafa; font-size: 14px; color: #666; padding: 40px;"
        )
        layout.addWidget(self._status_label)

        # ── QWebEngineView ──
        self._web_view = QWebEngineView()
        self._web_view.setVisible(False)  # 加载完成后显示
        layout.addWidget(self._web_view, stretch=1)

        # ── Bridge + QWebChannel ──
        self._bridge = OnlyOfficeBridge(parent=self)
        self._channel = QWebChannel(self._web_view.page())
        self._channel.registerObject("pyBridge", self._bridge)
        self._web_view.page().setWebChannel(self._channel)

        # 连接信号
        self._bridge.document_ready.connect(self._on_document_ready)
        self._bridge.document_saved.connect(self._on_document_saved)
        self._bridge.document_error.connect(self._on_document_error)
        self._bridge.slide_selected.connect(self._on_slide_selected)
        self._bridge.document_modified.connect(self._on_document_modified)

        self._web_view.loadFinished.connect(self._on_page_loaded)

    # ── 公共 API ──────────────────────────────────────────────

    def load_slides_data(self, slides_data: list, session_id: str = "default"):
        """加载幻灯片数据（转为 .pptx 后在 ONLYOFFICE 中打开）

        这是替换 SlideRenderer 的核心入口。
        """
        self._slides_data = slides_data
        self._session_id = session_id

        from opencopilot.capabilities.ppt.pptx_adapter import PptxAdapter
        from opencopilot.services.pptx_file_server import ensure_server_running

        # 确保文件服务已启动
        self._file_server = ensure_server_running()
        self._adapter = PptxAdapter(file_server=self._file_server)

        try:
            # slides_data → .pptx → HTTP URL
            # for_docker=False 因为 QWebEngineView 在宿主机运行，需要 127.0.0.1
            self._pptx_path, self._pptx_url = self._adapter.slides_data_to_pptx(
                slides_data, session_id, for_docker=False
            )
            logger.info("OnlyOfficeWidget: pptx ready at %s", self._pptx_url)
        except Exception as e:
            logger.error("OnlyOfficeWidget: pptx generation failed: %s", e)
            self._show_error(f"PPT 生成失败: {e}")
            return

        # 加载 ONLYOFFICE 编辑器
        self._load_editor(self._pptx_url, session_id)

    def reload_pptx(self, slides_data: list = None, session_id: str = None):
        """重新加载 .pptx（AI 修改后调用）

        如果传了新的 slides_data，会重新生成 .pptx。
        """
        if slides_data is not None:
            self._slides_data = slides_data
        sid = session_id or self._session_id

        if self._slides_data and self._adapter:
            try:
                self._pptx_path, self._pptx_url = self._adapter.slides_data_to_pptx(
                    self._slides_data, sid, for_docker=False
                )
                logger.info("OnlyOfficeWidget: reloaded pptx at %s", self._pptx_url)
                # 重新加载页面
                self._load_editor(self._pptx_url, sid)
            except Exception as e:
                logger.error("OnlyOfficeWidget: reload failed: %s", e)
                self._show_error(f"重载失败: {e}")

    def get_current_pptx_path(self) -> str:
        """获取当前 .pptx 文件路径（导出用）"""
        return self._pptx_path

    def get_current_pptx_url(self) -> str:
        """获取当前 .pptx 的 HTTP URL"""
        return self._pptx_url

    def request_force_save(self):
        """请求 ONLYOFFICE 强制保存"""
        # 通过 JS 调用 ONLYOFFICE forcesave API
        js = """
        if (typeof docEditor !== 'undefined' && docEditor) {
            try {
                docEditor.processSaveResult();
                console.log('[OnlyOfficeWidget] force save requested');
            } catch(e) {
                console.error('[OnlyOfficeWidget] force save error:', e);
            }
        }
        """
        self._web_view.page().runJavaScript(js)

    def is_available(self) -> bool:
        """检查 ONLYOFFICE Document Server 是否可达"""
        import urllib.request
        try:
            url = f"{ONLYOFFICE_SERVER_URL}/healthcheck"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    # ── 内部方法 ──────────────────────────────────────────────

    def _load_editor(self, pptx_url: str, session_id: str):
        """生成编辑器 HTML 并加载到 QWebEngineView"""
        html = self._generate_editor_html(pptx_url, session_id)
        self._show_status("正在加载 ONLYOFFICE 编辑器...")
        logger.info("OnlyOfficeWidget: loading editor with pptx_url=%s", pptx_url)
        self._web_view.setHtml(html, QUrl(ONLYOFFICE_SERVER_URL))

    def _generate_jwt_token(self, payload: dict = None) -> str:
        """生成 OnlyOffice JWT token"""
        import jwt
        from datetime import datetime, timezone, timedelta
        
        if not ONLYOFFICE_JWT_SECRET:
            return ""
        
        if payload is None:
            payload = {}
        
        # OnlyOffice 要求的 payload 字段
        now = datetime.now(timezone.utc)
        default_payload = {
            "iss": "OpenCopilot",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        }
        default_payload.update(payload)
        
        token = jwt.encode(default_payload, ONLYOFFICE_JWT_SECRET, algorithm="HS256")
        return token

    def _generate_editor_html(self, pptx_url: str, session_id: str) -> str:
        """生成包含 ONLYOFFICE 编辑器的 HTML 页面（优化版：支持预加载和进度显示）"""
        # 生成唯一的 document key（同一 key 支持协作）
        doc_key = f"opencopilot_{session_id}_{int(time.time())}"
        
        # 生成 JWT token - OnlyOffice 要求完整的配置
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
                "user": {
                    "id": "user-001",
                    "name": "OpenCopilot"
                }
            }
        }
        token = self._generate_jwt_token(token_payload)
        
        # 如果有 token，添加到文档 URL
        if token:
            token_field = f'"token": "{token}",'
        else:
            token_field = ""

        # 使用预加载的 JS（如果可用）
        preloaded_js = OnlyOfficeWidget._preloaded_js
        if preloaded_js:
            # 内联 JS 避免二次请求
            api_js_tag = f'<script type="text/javascript">{preloaded_js}</script>'
        else:
            api_js_tag = f'<script type="text/javascript" src="{ONLYOFFICE_SERVER_URL}/web-apps/apps/api/documents/api.js"></script>'

        return f"""<!DOCTYPE html>
<html style="height: 100%; width: 100%;">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OpenCopilot PPT Editor</title>
    {api_js_tag}
    <script type="text/javascript" src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{ height: 100%; width: 100%; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }}
        #editor {{ height: 100%; width: 100%; }}
        #loading-overlay {{ 
            position: fixed; top: 0; left: 0; width: 100%; height: 100%; 
            background: #fafafa; display: flex; flex-direction: column;
            align-items: center; justify-content: center; z-index: 1000;
            transition: opacity 0.3s;
        }}
        #loading-overlay.hidden {{ opacity: 0; pointer-events: none; }}
        .spinner {{ width: 40px; height: 40px; border: 3px solid #e0e0e0; border-top-color: #007bff; border-radius: 50%; animation: spin 1s linear infinite; }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        .loading-text {{ margin-top: 16px; color: #666; font-size: 14px; }}
    </style>
</head>
<body>
    <div id="loading-overlay">
        <div class="spinner"></div>
        <div class="loading-text" id="loading-text">正在初始化编辑器...</div>
    </div>
    <div id="editor"></div>
    <script>
        // 全局错误捕获
        window.onerror = function(msg, url, line, col, error) {{
            console.error('[OnlyOfficeWidget] JS Error:', msg, 'at', url, line, col);
            if (pyBridge) pyBridge.onDocumentError('JS Error: ' + msg + ' at line ' + line);
            return false;
        }};
        
        // 进度更新函数
        function setProgress(step, percent) {{
            const el = document.getElementById('loading-text');
            if (el) el.textContent = '正在加载: ' + step + ' (' + percent + '%)';
        }}
        
        // 初始化 Python Bridge
        var pyBridge = null;
        new QWebChannel(qt.webChannelTransport, function(channel) {{
            pyBridge = channel.objects.pyBridge;
            console.log('[OnlyOfficeWidget] Python bridge connected');
        }});

        // 分阶段初始化
        setProgress('加载核心', 20);
        
        // 检查 DocsAPI 是否可用
        if (typeof DocsAPI === 'undefined') {{
            console.error('[OnlyOfficeWidget] DocsAPI not loaded!');
            setProgress('加载失败: DocsAPI 未加载', 0);
            if (pyBridge) pyBridge.onDocumentError('DocsAPI not loaded - check OnlyOffice server');
            document.getElementById('loading-text').textContent = '加载失败: ONLYOFFICE API 未加载，请检查 Document Server';
        }} else {{
            console.log('[OnlyOfficeWidget] DocsAPI available, initializing editor...');
        }}
        
        // 初始化 ONLYOFFICE 编辑器
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
                "user": {{
                    "id": "user-001",
                    "name": "OpenCopilot"
                }},
                "customization": {{
                    "autosave": false,
                    "forcesave": true,
                    "chat": false,
                    "comments": false,
                    "compactToolbar": false,
                    "feedback": false,
                    "goback": false,
                    "help": false,
                    "hideRightMenu": true
                }}
            }},
            "events": {{
                "onAppReady": function() {{
                    setProgress('渲染文档', 80);
                    console.log('[OnlyOffice] App ready');
                    if (pyBridge) pyBridge.onDocumentReady();
                    // 隐藏加载遮罩
                    setTimeout(function() {{
                        var overlay = document.getElementById('loading-overlay');
                        if (overlay) overlay.classList.add('hidden');
                    }}, 500);
                }},
                "onDocumentReady": function() {{
                    setProgress('文档就绪', 100);
                    console.log('[OnlyOffice] Document ready');
                }},
                "onDocumentStateChange": function(event) {{
                    console.log('[OnlyOffice] Document state:', event.data);
                    if (pyBridge) pyBridge.onDocumentModified();
                }},
                "onError": function(event) {{
                    console.error('[OnlyOffice] Error:', event.data);
                    var errorInfo = event.data;
                    var errorMsg = '';
                    if (typeof errorInfo === 'object' && errorInfo !== null) {{
                        errorMsg = JSON.stringify(errorInfo);
                        if (errorInfo.message) errorMsg = errorInfo.message;
                        else if (errorInfo.code) errorMsg = 'Error code: ' + errorInfo.code;
                    }} else {{
                        errorMsg = String(errorInfo);
                    }}
                    if (pyBridge) pyBridge.onDocumentError(errorMsg);
                }},
                "onWarning": function(event) {{
                    console.warn('[OnlyOffice] Warning:', event.data);
                }}
            }}
        }});

        setProgress('初始化编辑器', 50);
        console.log('[OnlyOfficeWidget] Editor initialized');
    </script>
</body>
</html>"""

    def _on_page_loaded(self, ok: bool):
        """QWebEngineView 页面加载完成"""
        if ok:
            logger.info("OnlyOfficeWidget: page loaded successfully")
        else:
            logger.error("OnlyOfficeWidget: page load failed")
            self._show_error("ONLYOFFICE 页面加载失败，请检查 Document Server 是否运行。")

    def _on_document_ready(self):
        """ONLYOFFICE 文档加载完成"""
        self._is_loaded = True
        self._status_label.setVisible(False)
        self._web_view.setVisible(True)
        self.document_ready.emit()
        logger.info("OnlyOfficeWidget: document ready")

    def _on_document_saved(self, url: str):
        """文档保存完成"""
        self.export_ready.emit(url)

    def _on_document_error(self, error: str):
        """编辑器报错"""
        self._show_error(f"ONLYOFFICE 错误: {error}")

    def _on_slide_selected(self, index: int):
        """幻灯片切换"""
        self.slide_changed.emit(index)

    def _on_document_modified(self):
        """文档被修改"""
        self.document_modified.emit()

    def _show_status(self, message: str):
        """显示状态提示（带加载进度）"""
        self._status_label.setText(message)
        self._status_label.setStyleSheet(
            "background: #fafafa; font-size: 14px; color: #666; padding: 40px;"
        )
        self._status_label.setVisible(True)
        self._web_view.setVisible(False)

    def _show_loading_progress(self, step: str, percent: int):
        """显示加载进度"""
        self._status_label.setText(f"正在加载 ONLYOFFICE...\n{step} ({percent}%)")
        self._status_label.setStyleSheet(
            f"background: #fafafa; font-size: 14px; color: #666; padding: 40px;"
        )
        self._status_label.setVisible(True)

    def _preload_editor_resources(self):
        """预加载 OnlyOffice JS API（异步，不阻塞 UI）"""
        if OnlyOfficeWidget._preloaded_js is not None:
            return  # 已预加载
        
        def fetch_js():
            try:
                import urllib.request
                url = f"{ONLYOFFICE_SERVER_URL}/web-apps/apps/api/documents/api.js"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        OnlyOfficeWidget._preloaded_js = resp.read().decode('utf-8')
                        logger.info("OnlyOfficeWidget: JS API preloaded (%d bytes)", len(OnlyOfficeWidget._preloaded_js))
            except Exception as e:
                logger.warning("OnlyOfficeWidget: preload failed: %s", e)
        
        # 在后台线程预加载
        import threading
        threading.Thread(target=fetch_js, daemon=True).start()

    def _show_error(self, message: str):
        """显示错误提示"""
        self._status_label.setText(f"❌ {message}")
        self._status_label.setStyleSheet(
            "background: #fff5f5; font-size: 14px; color: #c53030; padding: 40px;"
        )
        self._status_label.setVisible(True)
        self._web_view.setVisible(False)
        logger.error("OnlyOfficeWidget: %s", message)
