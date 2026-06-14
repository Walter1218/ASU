"""
OnlyOffice 截图器（用于 Benchmark 对比测试）

功能：
1. 生成 .pptx 文件
2. 启动本地文件服务
3. 加载 OnlyOffice 编辑器到 QWebEngineView
4. 等待渲染完成后截图
5. 保存截图供 SSIM 对比

运行方式：
    python benchmark_onlyoffice_capture.py
"""

import json
import time
import sys
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Optional
import threading

# Qt
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QTimer, QEventLoop, QObject, pyqtSlot
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtGui import QPixmap

# 项目路径
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from ppt_generator import generate_ppt_from_json

# OnlyOffice 配置
ONLYOFFICE_SERVER_URL = os.environ.get("ONLYOFFICE_SERVER_URL", "http://localhost:9090")
ONLYOFFICE_JWT_SECRET = os.environ.get("ONLYOFFICE_JWT_SECRET", "opencopilot-local-dev")


class OnlyOfficeCaptureBridge(QObject):
    """Bridge for capturing OnlyOffice rendering completion"""
    document_ready = pyqtSignal()
    document_error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_ready = False

    @pyqtSlot()
    def onDocumentReady(self):
        self._is_ready = True
        self.document_ready.emit()

    @pyqtSlot(str)
    def onDocumentError(self, error_msg: str):
        self.document_error.emit(error_msg)

    def is_ready(self) -> bool:
        return self._is_ready


class OnlyOfficeScreenshotter(QWidget):
    """OnlyOffice 截图器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OnlyOffice Benchmark Capture")
        self.resize(1400, 900)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # WebView 缓存设置（禁用缓存）
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        profile = QWebEngineProfile.defaultProfile()
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        
        # WebView
        self._web_view = QWebEngineView()
        self._web_view.setVisible(True)
        layout.addWidget(self._web_view)
        
        # Bridge
        self._bridge = OnlyOfficeCaptureBridge(self)
        self._channel = QWebChannel(self._web_view.page())
        self._channel.registerObject("pyBridge", self._bridge)
        self._web_view.page().setWebChannel(self._channel)
        
        # 文件服务
        self._file_server = None
        self._temp_dir = tempfile.mkdtemp(prefix="oo_capture_")
        
        # 截图结果
        self._last_pixmap = None

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

    def _generate_editor_html(self, pptx_url: str, doc_key: str) -> str:
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
        
        print(f"  [OnlyOffice] doc_key={doc_key}, token={'有' if token else '无'}")

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
                    "autosave": false, "chat": false, "comments": false,
                    "feedback": false, "goback": false, "help": false
                }}
            }},
            "events": {{
                "onAppReady": function() {{
                    console.log('[OnlyOffice] App ready');
                }},
                "onDocumentReady": function() {{
                    console.log('[OnlyOffice] Document ready - content loaded');
                    // 文档内容已加载，但可能还在渲染中，延迟通知
                    setTimeout(function() {{
                        if (pyBridge) pyBridge.onDocumentReady();
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

    def capture(self, slides_data: List[Dict], output_path: str, timeout_ms: int = 30000, slide_index: int = 0) -> bool:
        """
        截图 OnlyOffice 渲染结果
        
        Args:
            slides_data: 幻灯片数据
            output_path: 截图保存路径
            timeout_ms: 最大等待时间
            slide_index: 要截图的幻灯片索引（从0开始）
            
        Returns:
            是否成功
        """
        # 1. 生成 .pptx
        pptx_path = os.path.join(self._temp_dir, "test.pptx")
        try:
            generate_ppt_from_json(slides_data, pptx_path)
        except Exception as e:
            print(f"  [OnlyOffice] PPTX 生成失败: {e}")
            return False
        
        # 2. 启动文件服务（使用动态端口）
        try:
            import socket
            # 找可用端口
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('localhost', 0))
            port = sock.getsockname()[1]
            sock.close()
            
            # 启动简单 HTTP 服务
            import http.server
            import threading
            
            server_dir = self._temp_dir
            os.makedirs(server_dir, exist_ok=True)
            
            class Handler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=server_dir, **kwargs)
                
                def log_message(self, format, *args):
                    pass  # 静默日志
            
            self._file_server = http.server.HTTPServer(('0.0.0.0', port), Handler)
            server_thread = threading.Thread(target=self._file_server.serve_forever, daemon=True)
            server_thread.start()
            
            # 获取 URL
            pptx_filename = os.path.basename(pptx_path)
            # 如果 pptx 已经在 server_dir 中，不需要复制
            target_path = os.path.join(server_dir, pptx_filename)
            if os.path.abspath(pptx_path) != os.path.abspath(target_path):
                import shutil
                shutil.copy(pptx_path, target_path)
            
            # 使用 host.docker.internal 让 Docker 容器访问宿主机
            # 服务绑定到 0.0.0.0，Docker 通过 host.docker.internal 访问
            pptx_url = f"http://host.docker.internal:{port}/{pptx_filename}"
            
            print(f"  [OnlyOffice] 文件服务: http://0.0.0.0:{port}/{pptx_filename} (Docker 内部访问: {pptx_url})")
            
        except Exception as e:
            print(f"  [OnlyOffice] 文件服务启动失败: {e}")
            return False
        
        # 3. 加载 OnlyOffice（使用随机 doc key 避免缓存）
        import uuid
        doc_key = f"benchmark_{uuid.uuid4().hex[:8]}_{int(time.time())}"
        html = self._generate_editor_html(pptx_url, doc_key)
        
        self._bridge._is_ready = False
        self._web_view.setHtml(html, QUrl(ONLYOFFICE_SERVER_URL))
        
        # 4. 等待渲染完成
        start_time = time.time()
        while not self._bridge.is_ready():
            QApplication.processEvents()
            time.sleep(0.1)
            if (time.time() - start_time) * 1000 > timeout_ms:
                print(f"  [OnlyOffice] 等待超时 ({timeout_ms}ms)")
                return False
        
        # 额外等待渲染稳定（OnlyOffice 需要更长时间初始化）
        print(f"  [OnlyOffice] 等待渲染稳定...")
        # 分阶段等待，确保内容完全渲染
        for i in range(5):
            time.sleep(1.0)
            QApplication.processEvents()
            print(f"  [OnlyOffice] 等待中... {i+1}/5")
        
        # 5. 关闭可能的弹窗（通过 JS 模拟点击）
        self._web_view.page().runJavaScript("""
            // 尝试关闭弹窗
            var closeButtons = document.querySelectorAll('.asc-window-close, .btn-close, .modal-close, button[id*="close"]');
            closeButtons.forEach(function(btn) { btn.click(); });
            // 点击 "知道了" 按钮
            var knowButtons = document.querySelectorAll('button');
            knowButtons.forEach(function(btn) { 
                if (btn.textContent.includes('知道了') || btn.textContent.includes('OK')) {
                    btn.click();
                }
            });
            console.log('[OnlyOffice] Attempted to close popups');
        """)
        time.sleep(0.5)
        QApplication.processEvents()
        
        # 6. 切换到指定幻灯片（如果 slide_index > 0）
        if slide_index > 0:
            print(f"  [OnlyOffice] 切换到第 {slide_index + 1} 页...")
            # 方法1: 使用键盘快捷键 Ctrl+PageDown 或方向键切换
            from PyQt6.QtCore import Qt
            from PyQt6.QtGui import QKeyEvent
            
            # 先点击 webview 获取焦点
            self._web_view.setFocus()
            QApplication.processEvents()
            
            # 发送 PageDown 或方向键来切换幻灯片
            for _ in range(slide_index):
                key_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_PageDown, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(self._web_view, key_event)
                QApplication.processEvents()
                time.sleep(0.5)
            
            print(f"  [OnlyOffice] 已发送 {slide_index} 次 PageDown")
            time.sleep(1.0)
            QApplication.processEvents()
        
        # 7. 截图（截取内容区域，去掉工具栏、左侧面板和边框）
        pixmap = self._web_view.grab()
        self._last_pixmap = pixmap
        
        # 获取图片尺寸
        w = pixmap.width()
        h = pixmap.height()
        
        # 根据实际截图分析：
        # - 左侧缩略图面板约 200px
        # - 顶部工具栏约 90px
        # - 右侧边框约 20px
        # - 底部状态栏约 30px
        left_panel = 200               # 左侧面板宽度
        top_margin = 90                # 顶部工具栏
        right_margin = 20              # 右边框
        bottom_margin = 30             # 底部状态栏
        
        content_rect = pixmap.rect().adjusted(
            left_panel, top_margin, -right_margin, -bottom_margin
        )
        content_pixmap = pixmap.copy(content_rect)
        
        content_pixmap.save(output_path, "PNG")
        
        print(f"  [OnlyOffice] 截图成功: {output_path} ({content_pixmap.width()}x{content_pixmap.height()})")
        return True

    def capture_to_numpy(self, slides_data: List[Dict]) -> Optional:
        """截图并返回 numpy 数组"""
        import numpy as np
        
        output_path = os.path.join(self._temp_dir, "capture.png")
        if not self.capture(slides_data, output_path):
            return None
        
        from PIL import Image
        img = Image.open(output_path)
        img = img.convert("RGB")
        return np.array(img)

    def closeEvent(self, event):
        """清理资源"""
        if self._file_server:
            try:
                self._file_server.shutdown()
            except:
                pass
        super().closeEvent(event)


# ============================================================
# 测试入口
# ============================================================

def test_onlyoffice_capture():
    """测试 OnlyOffice 截图功能"""
    app = QApplication(sys.argv)
    
    # 标准测试数据
    slides_data = [
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
            ]
        },
    ]
    
    # 添加 chart 数据
    slides_data[1]["items"].insert(0, {
        "content_type": "chart",
        "chart_type": "bar",
        "chart_data": {
            "labels": ["2023", "2024", "2025"],
            "values": [8.5, 9.5, 12.8]
        },
        "text": "营收数据"
    })
    
    screenshotter = OnlyOfficeScreenshotter()
    screenshotter.show()
    
    output_dir = PROJECT_ROOT / "benchmark_visual_output"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / "onlyoffice_test_capture.png"
    
    def do_capture():
        success = screenshotter.capture(slides_data, str(output_path))
        if success:
            print(f"✅ 截图成功: {output_path}")
        else:
            print(f"❌ 截图失败")
        QTimer.singleShot(1000, app.quit)
    
    QTimer.singleShot(1000, do_capture)
    
    app.exec()
    screenshotter.close()


if __name__ == "__main__":
    test_onlyoffice_capture()
