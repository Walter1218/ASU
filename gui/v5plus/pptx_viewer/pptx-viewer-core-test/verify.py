#!/usr/bin/env python3
"""pptx-viewer-core 验证 - 测试解析、渲染、编辑、保存"""
import base64, os, sys, threading, time
from http.server import HTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt, QUrl, pyqtSignal, pyqtSlot, QObject, QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

VIEWER_DIR = os.path.dirname(os.path.abspath(__file__))
HTTP_PORT = 18925

class _H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw): super().__init__(*a, directory=VIEWER_DIR, **kw)
    def log_message(self, *a): pass

class Bridge(QObject):
    @pyqtSlot(str, result=str)
    def echo(self, s): return s

def make_test_pptx():
    """3 页测试 PPT"""
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # 封面
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    tb = s1.shapes.add_textbox(Inches(1), Inches(2.5), Inches(11.333), Inches(1.5))
    tf = tb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]; p.text = "pptx-viewer-core 验证"; p.font.size = Pt(48); p.font.bold = True
    p.font.color.rgb = RGBColor(255,255,255); p.alignment = PP_ALIGN.CENTER
    bg = s1.background.fill; bg.solid(); bg.fore_color.rgb = RGBColor(0,82,204)

    # 内容页
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    tb = s2.shapes.add_textbox(Inches(1), Inches(0.5), Inches(11), Inches(1))
    tf = tb.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]; p.text = "多元素测试"; p.font.size = Pt(36); p.font.bold = True; p.font.color.rgb = RGBColor(0,82,204)

    tb1 = s2.shapes.add_textbox(Inches(1), Inches(2), Inches(5), Inches(1))
    tf1 = tb1.text_frame; tf1.word_wrap = True
    p1 = tf1.paragraphs[0]; p1.text = "文本框 A"; p1.font.size = Pt(20)

    tb2 = s2.shapes.add_textbox(Inches(7), Inches(2), Inches(5), Inches(1))
    tf2 = tb2.text_frame; tf2.word_wrap = True
    p2 = tf2.paragraphs[0]; p2.text = "文本框 B"; p2.font.size = Pt(20)

    import io
    buf = io.BytesIO(); prs.save(buf); return buf.getvalue()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("pptx-viewer-core 验证")
        self.resize(1400, 800)
        self.bridge = Bridge()
        central = QWidget(); self.setCentralWidget(central)
        layout = QVBoxLayout(central); layout.setContentsMargins(0,0,0,0)
        self.web = QWebEngineView(); layout.addWidget(self.web)
        page = self.web.page()
        ch = QWebChannel(page); ch.registerObject("pyBridge", self.bridge)
        page.setWebChannel(ch)
        page.setUrl(QUrl(f"http://127.0.0.1:{HTTP_PORT}/verify.html"))
        QTimer.singleShot(2000, self._load)

    def _load(self):
        pptx_bytes = make_test_pptx()
        b64 = base64.b64encode(pptx_bytes).decode("ascii")
        print(f"[DEBUG] loading {len(pptx_bytes)} bytes, b64 len={len(b64)}")
        js = f"""
        (function() {{
            window.__pptxBase64 = "{b64}";
            console.log('[py] base64 loaded, length:', window.__pptxBase64.length);
            return 'ok';
        }})();
        """
        self.web.page().runJavaScript(js, 0, lambda r: print(f"[DEBUG] JS init: {r}"))
        # 8秒后读取日志
        QTimer.singleShot(8000, self._read_log)

    def _read_log(self):
        js = "document.getElementById('log')?.textContent || 'no log'"
        self.web.page().runJavaScript(js, 0, lambda r: print(f"[LOG DUMP]\n{r}"))

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", HTTP_PORT), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[HTTP] port {HTTP_PORT}")
    app = QApplication(sys.argv)
    win = MainWindow(); win.show()
    print("窗口已打开，请点击 🧪 运行验证")
    sys.exit(app.exec())
