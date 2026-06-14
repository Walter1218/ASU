"""SmartCopilotV5 — v5.0 三 Tab 主窗口壳 (680×520 frameless)"""
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QCursor, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QTabWidget, QGraphicsDropShadowEffect,
    QApplication,
)

from gui.v5 import tokens as T
from gui.shared import make_panel_persistent
from gui.v5.telemetry import telemetry
from gui.v5 import bridge


class SmartCopilotV5(QWidget):
    """v5.0 Smart Copilot 主窗口：3-Tab (Work / Chat / Studio)"""

    def __init__(self, nav):
        super().__init__()
        self.nav = nav
        self._selected_text = ""
        self._drag_pos = None
        self._resizing = False
        self._resize_edge = None
        self._resize_start_geo = None
        self._resize_start_pos = None
        self._resize_margin = 14
        self._user_initiated_hide = False
        self._allow_close = False
        self._init_ui()
        telemetry().window_event("V5_SC_CREATE", "smart_copilot")

    def _init_ui(self):
        # 窗口属性
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.resize(*T.WINDOW_SMART_COPILOT)

        # 外层 Frame + 阴影
        self.frame = QFrame(self)
        self.frame.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_PRIMARY};
                border-radius: {T.FRAME_RADIUS}px;
                border: 1.5px solid {T.STROKE_BORDER};
            }}
        """)
        self.frame.resize(T.WINDOW_SMART_COPILOT[0] - 20,
                          T.WINDOW_SMART_COPILOT[1] - 20)
        self.frame.move(T.FRAME_MARGIN, T.FRAME_MARGIN)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(T.SHADOW_BLUR)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 5)
        self.frame.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(0)

        # ── 标题栏 ──
        title_bar = QHBoxLayout()
        title_bar.setSpacing(8)

        self._title_label = QLabel("✨ Smart Copilot")
        self._title_label.setStyleSheet(
            f"color: {T.TEXT_ACCENT}; font-weight: bold; "
            f"font-size: {T.FONT_TITLE[0]}px; background: transparent; border: none;"
        )
        self._title_label.setCursor(Qt.CursorShape.OpenHandCursor)

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(
            f"color: {T.STATUS_ONLINE}; font-size: {T.FONT_TINY[0]}px; "
            "background: transparent; border: none;"
        )
        self._status_dot.setToolTip("ASU Agent 在线")

        self._btn_settings = QPushButton("⚙️")
        self._btn_settings.setStyleSheet(self._icon_btn_style())
        self._btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_settings.setToolTip("设置")
        self._btn_settings.clicked.connect(lambda: self.nav.open_settings())

        self._btn_close = QPushButton("✕")
        self._btn_close.setStyleSheet(self._icon_btn_style(close=True))
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.clicked.connect(self._hide_card)

        title_bar.addWidget(self._title_label)
        title_bar.addWidget(self._status_dot)
        title_bar.addStretch()
        title_bar.addWidget(self._btn_settings)
        title_bar.addWidget(self._btn_close)
        layout.addLayout(title_bar)

        # ── TabWidget (3 Tabs) ──
        self.tabs = QTabWidget(self.frame)
        self.tabs.setStyleSheet(self._tab_style())
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.tabs)

        # Tab 1: Work
        from gui.v5.work_tab import WorkTabV5
        self._work_tab = WorkTabV5(self.nav)
        self.tabs.addTab(self._work_tab, "⚡ Work")

        # Tab 2: Chat
        from gui.v5.chat_tab import ChatTabV5
        self._chat_tab = ChatTabV5(self.nav)
        self.tabs.addTab(self._chat_tab, "💬 Chat")

        # Tab 3: Studio
        from gui.v5.studio_tab import StudioTabV5
        self._studio_tab = StudioTabV5(self.nav)
        self.tabs.addTab(self._studio_tab, "🎨 Studio")

        # Tab 4: Review
        from gui.v5.review_tab import ReviewTabV5
        self._review_tab = ReviewTabV5(self.nav)
        self.tabs.addTab(self._review_tab, "🔍 Review")

        self.tabs.currentChanged.connect(self._on_tab_changed)

    # =========================================================================
    # 公共方法（供 NavigationManager 调用）
    # =========================================================================

    def set_selected_text(self, text: str):
        t = telemetry()
        t.emit("V5_SC_SET_TEXT", text_len=len(text),
               has_text=bool(text))
        self._selected_text = text
        self._work_tab.set_context_text(text)
        self._review_tab.set_context_text(text)
        if text:
            self.tabs.setCurrentIndex(0)  # Work Tab
        else:
            self.tabs.setCurrentIndex(1)  # Chat Tab

    def jump_to_chat(self, context_text: str = "", source: str = ""):
        """链路 F: 从 Work 跳转到 Chat，携带上下文"""
        self._chat_tab.inject_context(context_text, source)
        self.tabs.setCurrentIndex(1)
        self._chat_tab.focus_input()

    def inject_chat_message(self, message: str):
        """向 Chat Tab 注入一条系统消息"""
        self._chat_tab.append_message("系统", message)

    def switch_to_chat(self):
        self.tabs.setCurrentIndex(1)
        self._chat_tab.focus_input()

    # =========================================================================
    # Tab 切换
    # =========================================================================

    def _on_tab_changed(self, index: int):
        t = telemetry()
        tab_names = {0: "Work", 1: "Chat", 2: "Studio", 3: "Review"}
        t.emit("V5_SC_TAB_SWITCH", to_index=index,
               tab_name=tab_names.get(index, str(index)))
        QApplication.restoreOverrideCursor()
        self.setCursor(Qt.CursorShape.ArrowCursor)

        if index == 1:  # Chat
            self._chat_tab.focus_input()
        elif index == 2:  # Studio
            self._studio_tab.update_status(
                self.nav.is_studio_open(),
                self.nav.get_studio_slides_count(),
                bool(self._selected_text),
            )

    # =========================================================================
    # 窗口关闭
    # =========================================================================

    def _hide_card(self):
        """✕ 按钮点击 → 真正关闭窗口（非隐藏）"""
        telemetry().window_event("V5_SC_CLOSE", "smart_copilot")
        self._user_initiated_hide = True
        self._allow_close = True
        self.close()

    def hideEvent(self, event):
        if self._user_initiated_hide or self._allow_close:
            self._user_initiated_hide = False
            super().hideEvent(event)
        else:
            event.ignore()
            QTimer.singleShot(0, self._force_reshow)

    def _force_reshow(self):
        if not self._user_initiated_hide and not self._allow_close:
            self.show()
            self.raise_()
            make_panel_persistent(self)

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
            return
        self._hide_card()
        event.ignore()

    # =========================================================================
    # 文件拖放支持
    # =========================================================================

    def dragEnterEvent(self, event):
        """拖拽进入窗口时检查是否为文件或文本"""
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()
            self.setStyleSheet(f"""
                QWidget {{
                    background-color: {T.BG_SELECTED};
                    border: 2px dashed {T.STROKE_FOCUS};
                    border-radius: {T.FRAME_RADIUS}px;
                }}
            """)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """拖拽移动时持续接受"""
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        """拖拽离开时恢复样式"""
        self.setStyleSheet("")

    def dropEvent(self, event):
        """放下时处理文件或文本，同步注入到三个 Tab"""
        self.setStyleSheet("")
        mime = event.mimeData()
        t = telemetry()

        # 优先处理文本拖拽（如从 Qoder 拖拽的文本）
        if mime.hasText() and not mime.hasUrls():
            text = mime.text()
            if text:
                t.emit("V5_SC_DROP_TEXT", text_len=len(text),
                       source_tab=self.tabs.tabText(self.tabs.currentIndex()))
                self._selected_text = text
                # 同步到四个 Tab
                self._work_tab.set_context_text(text)
                self._chat_tab.set_shared_text(text, source="drag_drop")
                self._studio_tab.set_shared_text(text, source="drag_drop")
                self._review_tab.set_context_text(text)
                t.emit("V5_SC_TEXT_SHARED", text_len=len(text),
                       target_tabs=["work", "chat", "studio", "review"])
                print(f"[v5] SmartCopilot: 拖放文本已共享到三个 Tab → {len(text)} 字符")
            event.acceptProposedAction()
            return

        # 处理文件拖拽
        urls = mime.urls()
        if not urls:
            event.ignore()
            return

        t.emit("V5_SC_DROP_FILES", file_count=len(urls))

        for url in urls:
            file_path = url.toLocalFile()
            if not file_path:
                continue

            result = bridge.get_file_content(file_path)
            text = result.get("text", "")
            status = result.get("status", "")
            file_name = result.get("file_path", file_path).split("/")[-1]

            if status == "ok" and text:
                bridge.add_recent_file(file_path, source="drag_drop")
                self._selected_text = text
                # 同步到四个 Tab
                self._work_tab.set_context_text(text)
                self._chat_tab.set_shared_text(text, source=f"file:{file_name}")
                self._studio_tab.set_shared_text(text, source=f"file:{file_name}")
                self._review_tab.set_context_text(text)
                t.emit("V5_SC_FILE_SHARED", file=file_name, text_len=len(text),
                       target_tabs=["work", "chat", "studio", "review"])
                print(f"[v5] SmartCopilot: 拖放文件已共享到三个 Tab → {file_name} ({len(text)} 字符)")
            else:
                t.emit("V5_SC_FILE_ERROR", file=file_name, status=status)
                print(f"[v5] SmartCopilot: 拖放文件读取失败 → {file_name}, status={status}")

        event.acceptProposedAction()

    # =========================================================================
    # 拖拽 + 缩放
    # =========================================================================

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._get_resize_edge(event.pos())
            if edge:
                self._resizing = True
                self._resize_edge = edge
                self._resize_start_geo = self.geometry()
                self._resize_start_pos = event.globalPosition().toPoint()
                QApplication.setOverrideCursor(self._EDGE_CURSORS.get(edge, Qt.CursorShape.ArrowCursor))
                return
            if event.pos().y() < 40:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        if self._resizing:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            g = self._resize_start_geo
            e = self._resize_edge
            x, y, w, h = g.x(), g.y(), g.width(), g.height()
            min_w, min_h = 400, 300
            if 'r' in e: w = max(min_w, g.width() + delta.x())
            if 'l' in e: x = g.x() + delta.x(); w = max(min_w, g.width() - delta.x())
            if 'b' in e: h = max(min_h, g.height() + delta.y())
            if 't' in e: y = g.y() + delta.y(); h = max(min_h, g.height() - delta.y())
            self.setGeometry(x, y, w, h)
            self.frame.resize(w - 20, h - 20)
            return
        edge = self._get_resize_edge(event.pos())
        if edge:
            self.setCursor(self._EDGE_CURSORS.get(edge, Qt.CursorShape.ArrowCursor))
        elif event.pos().y() < 40:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            t = telemetry()
            t.emit("V5_SC_RESIZE", w=self.width(), h=self.height())
            QApplication.restoreOverrideCursor()
            self._resizing = False
            self._resize_edge = None
        if self._drag_pos is not None:
            self._drag_pos = None
        super().mouseReleaseEvent(event)

    def _get_resize_edge(self, pos):
        m = self._resize_margin
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        b, r = y > h - m, x > w - m
        t, l = y < m, x < m
        if b and r: return 'br'
        if b and l: return 'bl'
        if t and r: return 'tr'
        if t and l: return 'tl'
        if b: return 'b'
        if r: return 'r'
        if t: return 't'
        if l: return 'l'
        return None

    _EDGE_CURSORS = {
        'l': Qt.CursorShape.SizeHorCursor, 'r': Qt.CursorShape.SizeHorCursor,
        't': Qt.CursorShape.SizeVerCursor, 'b': Qt.CursorShape.SizeVerCursor,
        'tl': Qt.CursorShape.SizeFDiagCursor, 'br': Qt.CursorShape.SizeFDiagCursor,
        'tr': Qt.CursorShape.SizeBDiagCursor, 'bl': Qt.CursorShape.SizeBDiagCursor,
    }

    # =========================================================================
    # 样式工具
    # =========================================================================

    @staticmethod
    def _icon_btn_style(close=False):
        hover_color = "#ff5555" if close else "#fff"
        base_color = "#888" if close else "#aaa"
        return f"""
            QPushButton {{
                background: transparent; border: none;
                font-size: {T.FONT_TITLE[0]}px; color: {base_color};
            }}
            QPushButton:hover {{ color: {hover_color}; }}
        """

    @staticmethod
    def _tab_style():
        return f"""
            QTabWidget::pane {{ border: none; }}
            QTabBar::tab {{
                background: {T.BG_ELEVATED};
                color: {T.TEXT_SECONDARY};
                padding: 6px 14px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
                font-size: {T.FONT_BODY[0]}px;
            }}
            QTabBar::tab:selected {{
                background: rgba(60, 60, 75, 240);
                color: {T.TEXT_PRIMARY};
                font-weight: bold;
            }}
            QTabBar::tab:hover:!selected {{
                background: {T.BG_HOVER};
                color: {T.TEXT_PRIMARY};
            }}
        """
