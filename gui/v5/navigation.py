"""NavigationManager — v5.0 跳转中枢，管理所有窗口生命周期与跨窗口跳转"""
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QCursor

from gui.v5.telemetry import telemetry


class NavigationManager(QObject):
    """单例跳转管理器。

    所有 v5.0 窗口之间的跳转、生命周期控制均通过此类，
    避免窗口之间互相 import 形成循环依赖。

    10 条核心链路:
        A. Tab 切换 (内部，由 SmartCopilot 自行处理)
        B. Smart Copilot → Studio 窗口 (旧 4-Panel)
        B+. Smart Copilot → V5Plus CoCreation (3 阶段 E2E)
        C. Smart Copilot → Settings 弹窗
        D. Workspace → Settings 弹窗
        E. System Tray → 各窗口
        F. Work Tab → Chat Tab (上下文跳转)
        G. Studio → Smart Copilot (结果回传)
        H. CoCreation 内部 Stage 0→1→2 (条件路由)
        I. CoCreation → Smart Copilot Chat (导出回传)

    全局触发姿态:
        双击右键 → Smart Copilot (680×520)
        三击右键 → Agent Workspace (1000×700)
        System Tray 单击/双击 → Smart Copilot
        Studio Tab 按钮 → Studio / V5Plus CoCreation
    """

    # 信号：供外部监听
    studio_opened = pyqtSignal()
    cocreation_opened = pyqtSignal()
    settings_opened = pyqtSignal()
    workspace_opened = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._smart_copilot = None      # SmartCopilotV5 实例
        self._workspace = None           # WorkspaceV5 实例
        self._studio_window = None       # StudioWindowV5 实例
        self._cocreation_window = None   # CoCreationWindow (V5Plus) 实例
        self._settings_dialog = None     # SettingsDialogV5 实例

    # =========================================================================
    # Smart Copilot
    # =========================================================================

    def show_smart_copilot(self, x: int, y: int, selected_text: str = ""):
        """链路 E: 双击右键 → 弹出 Smart Copilot"""
        t = telemetry()
        t.nav_event("V5_NAV_SC_SHOW", x=x, y=y,
                    has_selected_text=bool(selected_text),
                    text_len=len(selected_text))
        if self._smart_copilot is None:
            from gui.v5.smart_copilot import SmartCopilotV5
            self._smart_copilot = SmartCopilotV5(self)

        sc = self._smart_copilot
        sc.set_selected_text(selected_text)

        # 计算屏幕安全位置
        pos = QCursor.pos()
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        sr = screen.geometry()
        w, h = sc.width(), sc.height()

        target_x = x + 15
        target_y = y + 15
        if target_x + w > sr.right():
            target_x = x - w - 15
        if target_y + h > sr.bottom():
            target_y = y - h - 15
        target_x = max(sr.left(), min(target_x, sr.right() - w))
        target_y = max(sr.top(), min(target_y, sr.bottom() - h))

        sc.move(target_x, target_y)
        sc.show()
        sc.raise_()

    def cleanup(self):
        return None

    def hide_smart_copilot(self):
        """隐藏 Smart Copilot"""
        if self._smart_copilot and self._smart_copilot.isVisible():
            telemetry().nav_event("V5_NAV_SC_HIDE")
            self._smart_copilot.hide()

    # =========================================================================
    # Studio Window（链路 B）
    # =========================================================================

    def open_studio(self, text: str = "", slides: list = None):
        """打开 PPT 共创工作台（独立窗口，生命周期独立于 Smart Copilot）"""
        t = telemetry()
        t.nav_event("V5_NAV_STUDIO_OPEN", has_text=bool(text),
                    text_len=len(text), has_slides=bool(slides))
        # 已有窗口且可见 → 直接激活
        if (self._studio_window is not None
                and self._studio_window.isVisible()):
            self._studio_window.raise_()
            self._studio_window.activateWindow()
            if text and text.strip():
                QTimer.singleShot(50, lambda: self._studio_window.load_text(text))
            if slides:
                QTimer.singleShot(100, lambda: self._studio_window.load_slides(slides))
            return

        # 清理旧引用
        if self._studio_window is not None:
            try:
                self._studio_window.close()
                self._studio_window.deleteLater()
            except Exception:
                pass
            self._studio_window = None

        from gui.v5.studio_window import StudioWindowV5
        self._studio_window = StudioWindowV5(self)
        self._studio_window.show()

        if text and text.strip():
            QTimer.singleShot(100, lambda: self._studio_window.load_text(text))
        if slides:
            QTimer.singleShot(150, lambda: self._studio_window.load_slides(slides))

        self.studio_opened.emit()

    # =========================================================================
    # CoCreation Window (V5Plus)（链路 B+）
    # =========================================================================

    def open_cocreation(self, text: str = ""):
        """打开 V5Plus PPT 共创工作台（条件路由：有文本→Stage 1，无文本→Stage 0）"""
        t = telemetry()
        t.nav_event("V5_NAV_COCREATION_OPEN", has_text=bool(text and text.strip()),
                    text_len=len(text) if text else 0)

        # 关闭旧的 Studio 窗口（互斥）
        if self._studio_window is not None and self._studio_window.isVisible():
            try:
                self._studio_window.close()
                self._studio_window.deleteLater()
            except Exception:
                pass
            self._studio_window = None

        # 已有窗口且可见 → 直接激活并重新路由
        if (self._cocreation_window is not None
                and self._cocreation_window.isVisible()):
            self._cocreation_window.raise_()
            self._cocreation_window.activateWindow()
            self._cocreation_window.open_with_text(text)
            return

        # 清理旧引用
        if self._cocreation_window is not None:
            try:
                self._cocreation_window.close()
                self._cocreation_window.deleteLater()
            except Exception:
                pass
            self._cocreation_window = None

        from gui.v5plus.cocreation_window import CoCreationWindow
        self._cocreation_window = CoCreationWindow(self)

        # 居中显示
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        sr = screen.geometry()
        w, h = self._cocreation_window.width(), self._cocreation_window.height()
        x = sr.x() + (sr.width() - w) // 2
        y = sr.y() + (sr.height() - h) // 2
        self._cocreation_window.move(x, y)

        self._cocreation_window.show()
        QTimer.singleShot(50, lambda: self._cocreation_window.open_with_text(text))
        self.cocreation_opened.emit()

    def is_cocreation_open(self) -> bool:
        return (self._cocreation_window is not None
                and self._cocreation_window.isVisible())

    # =========================================================================
    # Settings Dialog（链路 C / D）— 单实例控制
    # =========================================================================

    def open_settings(self, section: str = "engine"):
        """打开 Unified Settings 弹窗（单实例，无论从哪个入口调用）"""
        telemetry().nav_event("V5_NAV_SETTINGS_OPEN", section=section)
        if (self._settings_dialog is not None
                and self._settings_dialog.isVisible()):
            # 已打开 → 激活并跳转到指定分区
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            self._settings_dialog.select_section(section)
            return

        from gui.v5.settings_dialog import SettingsDialogV5
        self._settings_dialog = SettingsDialogV5(self, initial_section=section)
        self._settings_dialog.show()
        self.settings_opened.emit()

    # =========================================================================
    # Workspace（链路 E）
    # =========================================================================

    def show_workspace(self):
        """三击右键 → 显示 Agent Workspace"""
        telemetry().nav_event("V5_NAV_WS_SHOW")
        if self._workspace is None:
            from gui.v5.workspace import WorkspaceV5
            self._workspace = WorkspaceV5(self)

        ws = self._workspace
        # 居中显示
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        sr = screen.geometry()
        x = sr.x() + (sr.width() - ws.width()) // 2
        y = sr.y() + (sr.height() - ws.height()) // 2
        ws.move(x, y)
        ws.show()
        ws.raise_()
        self.workspace_opened.emit()

    # =========================================================================
    # 跨 Tab 跳转
    # =========================================================================

    def jump_work_to_chat(self, context_text: str = "", source: str = ""):
        """链路 F: Work Tab → Chat Tab，携带上下文"""
        t = telemetry()
        t.nav_event("V5_NAV_JUMP_W2C", context_len=len(context_text),
                    source=source)
        if self._smart_copilot is None:
            return
        self._smart_copilot.jump_to_chat(context_text, source)

    def jump_studio_to_chat(self, export_path: str = ""):
        """链路 G: Studio 导出后 → Smart Copilot Chat Tab"""
        t = telemetry()
        t.nav_event("V5_NAV_JUMP_S2C", export_path=export_path)
        if self._smart_copilot is None:
            return
        msg = f"刚刚导出了 PPT：{export_path}，有什么需要调整的吗？"
        self._smart_copilot.inject_chat_message(msg)
        self._smart_copilot.switch_to_chat()
        # 确保 Smart Copilot 可见
        self._smart_copilot.show()
        self._smart_copilot.raise_()

    # =========================================================================
    # 全局隐藏
    # =========================================================================

    def hide_all(self):
        """System Tray → 隐藏全部窗口"""
        telemetry().nav_event("V5_NAV_HIDE_ALL")
        for win in (self._smart_copilot, self._workspace):
            if win and win.isVisible():
                win.hide()
        # Studio 和 Settings 不自动隐藏（用户主动操作才关闭）

    # =========================================================================
    # 状态查询（供 Tab 切换时判断）
    # =========================================================================

    def is_studio_open(self) -> bool:
        return (self._studio_window is not None
                and self._studio_window.isVisible())

    def get_studio_slides_count(self) -> int:
        if (self._studio_window is not None
                and hasattr(self._studio_window, 'slides_data')):
            return len(self._studio_window.slides_data or [])
        return 0

    # =========================================================================
    # Review 请求发送
    # =========================================================================

    def send_review_request(
        self,
        text: str,
        context_meta: dict = None,
        on_chunk=None,
        on_done=None,
        on_error=None,
    ):
        """发送审查请求到 Agent Pipeline

        Args:
            text: 待审查文本
            context_meta: 审查参数 (review_type, reference_path, style_target, ...)
            on_chunk: 流式回调 (chunk: str)
            on_done: 完成回调 (full_response: str)
            on_error: 错误回调 (error: str)
        """
        telemetry().nav_event("V5_NAV_REVIEW_REQUEST",
                              text_len=len(text),
                              review_type=context_meta.get("review_type", "") if context_meta else "")

        try:
            from gui.v5.chat_tab import V5AgentWorker
            worker = V5AgentWorker(
                text=text,
                action_type="review",
                context_meta=context_meta or {},
            )
            worker.chunk_received.connect(lambda c: on_chunk(c) if on_chunk else None)
            worker.done_received.connect(lambda r: on_done(r) if on_done else None)
            worker.error_received.connect(lambda e: on_error(e) if on_error else None)
            worker.start()
        except Exception as e:
            if on_error:
                on_error(str(e))
