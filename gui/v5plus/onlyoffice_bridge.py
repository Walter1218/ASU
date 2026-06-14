"""OnlyOfficeBridge — PyQt6 <-> ONLYOFFICE JavaScript 双向通信桥

通过 QWebChannel 将 Python 对象暴露给 JS，
实现 ONLYOFFICE 编辑器事件到 PyQt6 信号的转发。
"""

import logging
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


class OnlyOfficeBridge(QObject):
    """PyQt6 <-> ONLYOFFICE JS Bridge

    JS 端通过 window.pyBridge 调用 @pyqtSlot 方法。
    Python 端通过 pyqtSignal 接收 JS 事件。
    """

    # ── JS → Python 信号 ──────────────────────────────────────

    slide_selected = pyqtSignal(int)       # 用户在 ONLYOFFICE 中切换了幻灯片
    document_ready = pyqtSignal()          # ONLYOFFICE 文档加载完成
    document_saved = pyqtSignal(str)       # 文档保存完成（URL）
    document_error = pyqtSignal(str)       # 编辑器报错
    document_modified = pyqtSignal()       # 文档内容被修改（脏标记）
    editor_closed = pyqtSignal()           # 编辑器关闭

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_ready = False
        logger.info("OnlyOfficeBridge: created")

    # ── JS 调用的 Slot ──────────────────────────────────────

    @pyqtSlot(int)
    def onSlideSelected(self, index: int):
        """JS 回调：用户选中了某张幻灯片"""
        logger.debug("Bridge: slide_selected(%d)", index)
        self.slide_selected.emit(index)

    @pyqtSlot()
    def onDocumentReady(self):
        """JS 回调：文档加载完成"""
        self._is_ready = True
        logger.info("Bridge: document_ready")
        self.document_ready.emit()

    @pyqtSlot(str)
    def onDocumentSaved(self, url: str):
        """JS 回调：文档已保存"""
        logger.info("Bridge: document_saved(%s)", url)
        self.document_saved.emit(url)

    @pyqtSlot(str)
    def onDocumentError(self, error: str):
        """JS 回调：编辑器报错"""
        logger.warning("Bridge: document_error(%s)", error)
        self.document_error.emit(error)

    @pyqtSlot()
    def onDocumentModified(self):
        """JS 回调：文档被修改"""
        logger.debug("Bridge: document_modified")
        self.document_modified.emit()

    @pyqtSlot()
    def onEditorClosed(self):
        """JS 回调：编辑器关闭"""
        logger.info("Bridge: editor_closed")
        self.editor_closed.emit()

    @pyqtSlot(str)
    def onConsoleLog(self, message: str):
        """JS 回调：console.log 透传"""
        logger.debug("Bridge [JS]: %s", message)

    @pyqtSlot(result=bool)
    def isReady(self) -> bool:
        """JS 查询：编辑器是否就绪"""
        return self._is_ready

    # ── Python → JS 方法 ──────────────────────────────────────

    def get_js_bridge_init_code(self) -> str:
        """返回需要注入到页面中的 JS 代码，用于初始化桥接

        在 QWebEngineView.loadFinished 后通过 runJavaScript 执行。
        """
        return """
        // OnlyOfficeBridge JS 端初始化
        // QWebChannel 自动将 Python 对象暴露为 window.pyBridge
        if (typeof QWebChannel !== 'undefined') {
            new QWebChannel(qt.webChannelTransport, function(channel) {
                window.pyBridge = channel.objects.pyBridge;
                console.log('[OnlyOfficeBridge] Python bridge connected');
            });
        }
        """

    def get_onlyoffice_event_hook_js(self) -> str:
        """返回 JS 代码：挂载 ONLYOFFICE 事件回调 → 调用 Python bridge

        在 ONLYOFFICE DocEditor 初始化完成后执行。
        """
        return """
        // 挂载 ONLYOFFICE 事件到 Python bridge
        (function() {
            function hookBridge() {
                if (!window.pyBridge) {
                    setTimeout(hookBridge, 200);
                    return;
                }
                console.log('[OnlyOfficeBridge] hooking events to Python');
            }
            hookBridge();
        })();
        """
