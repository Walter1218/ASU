"""CoCreationWindow — V5Plus PPT 共创工作台主窗口

QStackedWidget 管理 3 阶段页面：
  Stage 0: 输入原文（空状态页）
  Stage 1: 策略发现
  Stage 2: IDE 式编辑打磨

条件路由：有文本时跳过 Stage 0，直接进入 Stage 1。
"""
import logging
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QFrame,
    QGraphicsDropShadowEffect, QApplication, QMessageBox, QFileDialog,
)

from gui.v5plus import tokens_plus as T
from gui.v5plus.stage_input import StageInputWidget
from gui.v5plus.stage_strategy import StageStrategyWidget
from gui.v5plus.stage_editor import StageEditorWidget
from gui.v5.telemetry import telemetry
from gui.v5.agent_worker import V5AgentWorker
from gui.v5.ppt_prompt import build_ppt_generation_prompt, parse_slides_from_text

logger = logging.getLogger(__name__)

_STAGE_NAMES = {0: "输入原文", 1: "策略发现", 2: "编辑打磨"}


class CoCreationWindow(QWidget):
    """V5Plus PPT 共创工作台（独立窗口，3 阶段 E2E 流程）"""

    def __init__(self, nav):
        super().__init__()
        self.nav = nav
        self._session_id = telemetry().new_session_id()
        self._drag_pos = None
        self._user_initiated_close = False
        self._init_ui()
        telemetry().window_event("V5PLUS_COCREATION_CREATE", "cocreation_window")
        logger.info("CoCreationWindow: created (session=%s)", self._session_id[:8])

    def _init_ui(self):
        # 窗口属性
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.resize(*T.WINDOW_COCREATION)
        self.setMinimumSize(*T.WINDOW_COCREATION_MIN)

        # 外层 Frame + 阴影
        self._frame = QFrame(self)
        self._frame.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_PRIMARY};
                border-radius: {T.FRAME_RADIUS}px;
                border: 1.5px solid {T.STROKE_BORDER};
            }}
        """)
        self._frame.resize(T.WINDOW_COCREATION[0] - 20, T.WINDOW_COCREATION[1] - 20)
        self._frame.move(T.FRAME_MARGIN, T.FRAME_MARGIN)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(T.SHADOW_BLUR)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 5)
        self._frame.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self._frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 标题栏 ──
        title_bar = self._create_title_bar()
        layout.addWidget(title_bar)

        # ── 阶段指示器 ──
        self._indicator_bar = self._create_indicator_bar()
        layout.addWidget(self._indicator_bar)

        # ── QStackedWidget（3 阶段）──
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget { background: transparent; border: none; }")

        # Stage 0: 输入原文
        self._stage_input = StageInputWidget(session_id=self._session_id)
        self._stage_input.submitted.connect(self._on_stage1_submitted)
        self._stack.addWidget(self._stage_input)

        # Stage 1: 策略发现
        self._stage_strategy = StageStrategyWidget(session_id=self._session_id)
        self._stage_strategy.submitted.connect(self._on_stage2_submitted)
        self._stage_strategy.skipped.connect(self._on_stage2_skipped)
        self._stack.addWidget(self._stage_strategy)

        # Stage 2: 编辑打磨
        self._stage_editor = StageEditorWidget(session_id=self._session_id)
        self._stage_editor.export_requested.connect(self._on_export)
        self._stack.addWidget(self._stage_editor)

        layout.addWidget(self._stack, stretch=1)

    # =========================================================================
    # 标题栏
    # =========================================================================

    def _create_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(40)
        bar.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_ELEVATED};
                border-bottom: 1px solid {T.STROKE_SUBTLE};
                border-top-left-radius: {T.FRAME_RADIUS}px;
                border-top-right-radius: {T.FRAME_RADIUS}px;
            }}
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(8)

        # 图标 + 标题
        icon = QLabel("✨")
        icon.setStyleSheet("font-size: 16px; background: transparent; border: none;")
        self._title_label = QLabel("PPT 共创工作台")
        self._title_label.setStyleSheet(
            f"color: {T.TEXT_ACCENT}; font-weight: bold; "
            f"font-size: {T.FONT_TITLE[0]}px; background: transparent; border: none;"
        )
        self._title_label.setCursor(Qt.CursorShape.OpenHandCursor)

        layout.addWidget(icon)
        layout.addWidget(self._title_label)
        layout.addStretch()

        # session 标签
        self._session_label = QLabel(f"#{self._session_id[:6]}")
        self._session_label.setStyleSheet(
            f"color: {T.TEXT_TERTIARY}; font-size: {T.FONT_TINY[0]}px; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(self._session_label)

        # 关闭按钮
        close_btn = QPushButton("✕")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                font-size: {T.FONT_TITLE[0]}px; color: #888;
            }}
            QPushButton:hover {{ color: #ff5555; }}
        """)
        close_btn.clicked.connect(self._on_close)
        layout.addWidget(close_btn)

        return bar

    # =========================================================================
    # 阶段指示器
    # =========================================================================

    def _create_indicator_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(28)
        bar.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_PRIMARY};
                border-bottom: 1px solid {T.STROKE_SUBTLE};
            }}
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 4, 14, 4)
        layout.setSpacing(4)

        self._indicators = []
        for i in range(3):
            dot = QLabel("●")
            dot.setFixedWidth(12)
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setStyleSheet(
                f"color: {T.TEXT_TERTIARY}; font-size: 8px; "
                f"background: transparent; border: none;"
            )
            self._indicators.append(dot)

            name = QLabel(_STAGE_NAMES[i])
            name.setStyleSheet(
                f"color: {T.TEXT_TERTIARY}; font-size: {T.FONT_TINY[0]}px; "
                f"background: transparent; border: none;"
            )
            self._indicators.append(name)
            layout.addWidget(dot)
            layout.addWidget(name)

            if i < 2:
                arrow = QLabel("→")
                arrow.setStyleSheet(
                    f"color: {T.TEXT_TERTIARY}; font-size: {T.FONT_TINY[0]}px; "
                    f"padding: 0 4px; background: transparent; border: none;"
                )
                self._indicators.append(arrow)
                layout.addWidget(arrow)

        layout.addStretch()
        return bar

    def _update_indicators(self, active_stage: int):
        """更新阶段指示器高亮"""
        idx = 0
        for i in range(3):
            dot = self._indicators[idx]
            name = self._indicators[idx + 1]
            idx += 2

            if i == active_stage:
                dot.setStyleSheet(
                    f"color: {T.ACCENT_CONTROL}; font-size: 8px; "
                    f"background: transparent; border: none;"
                )
                name.setStyleSheet(
                    f"color: {T.TEXT_PRIMARY}; font-size: {T.FONT_TINY[0]}px; "
                    f"font-weight: bold; background: transparent; border: none;"
                )
            elif i < active_stage:
                dot.setStyleSheet(
                    f"color: {T.STATUS_ONLINE}; font-size: 8px; "
                    f"background: transparent; border: none;"
                )
                name.setStyleSheet(
                    f"color: {T.TEXT_SECONDARY}; font-size: {T.FONT_TINY[0]}px; "
                    f"background: transparent; border: none;"
                )
            else:
                dot.setStyleSheet(
                    f"color: {T.TEXT_TERTIARY}; font-size: 8px; "
                    f"background: transparent; border: none;"
                )
                name.setStyleSheet(
                    f"color: {T.TEXT_TERTIARY}; font-size: {T.FONT_TINY[0]}px; "
                    f"background: transparent; border: none;"
                )

            if i < 2:
                idx += 1  # skip arrow

    # =========================================================================
    # 公共方法（供 NavigationManager 调用）
    # =========================================================================

    def open_with_text(self, text: str = ""):
        """条件路由：有文本 → Stage 1（策略发现）；无文本 → Stage 0（输入原文）"""
        telemetry().emit(
            "V5PLUS_COCREATION_OPEN",
            session_id=self._session_id,
            has_text=bool(text and text.strip()),
            text_len=len(text),
        )

        if text and text.strip():
            # 有文本 → 跳过 Stage 0，直接 Stage 1
            self._stage_strategy.load_text(text)
            self._switch_to(1)
            logger.info("CoCreationWindow: open with text → Stage 1 (%d chars)", len(text))
        else:
            # 无文本 → Stage 0
            self._switch_to(0)
            logger.info("CoCreationWindow: open empty → Stage 0")

    # =========================================================================
    # Stage 间跳转
    # =========================================================================

    def _on_stage1_submitted(self, text: str):
        """Stage 0 → Stage 1：用户提交原文"""
        self._stage_strategy.load_text(text)
        self._switch_to(1)
        telemetry().emit(
            "V5PLUS_COCREATION_STAGE_SWITCH",
            session_id=self._session_id,
            from_stage=0,
            to_stage=1,
            text_len=len(text),
        )

    def _on_stage2_submitted(self, strategy_config: dict):
        """Stage 1 → Stage 2：用户选定策略，后台生成 PPT"""
        text = strategy_config.get("text", self._stage_strategy.get_text())
        # 先切换到 Stage 2（显示加载状态）
        self._stage_editor.load_data(text, strategy_config)
        self._switch_to(2)
        telemetry().emit(
            "V5PLUS_COCREATION_STAGE_SWITCH",
            session_id=self._session_id,
            from_stage=1,
            to_stage=2,
            strategy=strategy_config.get("strategy", ""),
        )
        # 后台执行 PPT 生成
        self._run_pipeline_async(text, strategy_config)

    def _on_stage2_skipped(self, text: str):
        """Stage 1 → Stage 2：跳过策略，直接生成"""
        config = {"strategy": "pyramid", "audience": "", "duration": "10", "text": text}
        self._stage_editor.load_data(text, config)
        self._switch_to(2)
        telemetry().emit(
            "V5PLUS_COCREATION_STAGE_SWITCH",
            session_id=self._session_id,
            from_stage=1,
            to_stage=2,
            skipped=True,
        )
        # 后台执行 PPT 生成
        self._run_pipeline_async(text, config)

    def _on_export(self, slides_data: list):
        """Stage 2：导出 PPT"""
        if not slides_data:
            QMessageBox.warning(self, "无法导出", "尚未生成幻灯片数据，请等待生成完成。")
            return
        try:
            from ppt_generator import generate_ppt_from_json
            output_path = generate_ppt_from_json(slides_data)
            if output_path:
                import subprocess
                subprocess.run(["open", output_path])
                QMessageBox.information(self, "导出成功", f"PPT 已导出至：\n{output_path}")
                logger.info("CoCreationWindow: exported PPT to %s", output_path)
            else:
                QMessageBox.warning(self, "导出失败", "PPT 生成失败，请重试。")
        except Exception as e:
            logger.error("CoCreationWindow: export error: %s", e)
            QMessageBox.warning(self, "导出失败", f"导出 PPT 时出错：{e}")

    def _run_pipeline_async(self, text: str, strategy_config: dict):
        """通过 V5AgentWorker 走标准 Agent Pipeline 协议生成 PPT（与 Studio 一致）"""
        self._pending_text = text
        self._pending_config = strategy_config
        print(f"[v5plus] Pipeline: starting V5AgentWorker (text={len(text)} chars, strategy={strategy_config.get('strategy', '?')})")

        # 使用共享 prompt 构建器（与 Studio Tab 完全一致）
        prompt = build_ppt_generation_prompt(
            text=text,
            strategy=strategy_config.get("strategy", "pyramid"),
            audience=strategy_config.get("audience", ""),
            duration=strategy_config.get("duration", "10"),
        )

        # 清理旧 worker
        if hasattr(self, '_agent_worker') and self._agent_worker is not None:
            if self._agent_worker.isRunning():
                self._agent_worker.stop()
                self._agent_worker.wait(2000)
            self._agent_worker = None

        # 启动 V5AgentWorker
        self._agent_worker = V5AgentWorker(
            prompt=prompt,
            action_type="ppt",
            session_id=self._session_id,
            context_source="v5plus_cocreation",
            context_meta={
                "input_text_len": len(text),
                "strategy": strategy_config.get("strategy", "pyramid"),
            },
            is_new_task=True,
        )
        self._agent_worker.finished_signal.connect(self._on_ppt_generated)
        self._agent_worker.error_signal.connect(self._on_ppt_error)
        self._agent_worker.start()
        print("[v5plus] Pipeline: V5AgentWorker started")

    def _on_ppt_generated(self, full_text: str):
        """V5AgentWorker 完成：解析 JSON slides 并加载到 Stage Editor"""
        self._safely_reset_worker()
        print(f"[v5plus] Pipeline: V5AgentWorker done — response {len(full_text)} chars")

        # 解析 slides（使用共享解析逻辑）
        slides = parse_slides_from_text(full_text)
        print(f"[v5plus] Pipeline: parsed {len(slides)} slides from response")

        if self._stage_editor:
            if slides:
                self._stage_editor.load_data(
                    self._pending_text, self._pending_config, slides
                )
                print(f"[v5plus] Pipeline: stage_editor updated with {len(slides)} slides")
            else:
                print("[v5plus] Pipeline: WARNING — no slides parsed!")
                self._stage_editor.show_error_state(
                    "AI 返回内容无法解析为幻灯片格式，请返回重试。"
                )

    def _on_ppt_error(self, error_msg: str):
        """V5AgentWorker 错误"""
        self._safely_reset_worker()
        print(f"[v5plus] Pipeline: V5AgentWorker ERROR — {error_msg}")
        if self._stage_editor:
            self._stage_editor.show_error_state(error_msg)

    def _safely_reset_worker(self):
        """安全清理 V5AgentWorker 引用"""
        if hasattr(self, '_agent_worker') and self._agent_worker is not None:
            worker = self._agent_worker
            self._agent_worker = None
            if worker.isRunning():
                worker.finished.connect(worker.deleteLater)
                if not worker.wait(3000):
                    worker.terminate()
                    worker.wait(1000)
            else:
                worker.deleteLater()

    def _switch_to(self, stage_index: int):
        """切换到指定阶段"""
        old_index = self._stack.currentIndex()
        self._stack.setCurrentIndex(stage_index)
        self._update_indicators(stage_index)
        logger.info("CoCreationWindow: stage %d → %d (%s)",
                     old_index, stage_index, _STAGE_NAMES.get(stage_index, "?"))

    # =========================================================================
    # 关闭
    # =========================================================================

    def _on_close(self):
        telemetry().emit(
            "V5PLUS_COCREATION_CLOSE",
            session_id=self._session_id,
            final_stage=self._stack.currentIndex(),
        )
        logger.info("CoCreationWindow: closed at stage %d", self._stack.currentIndex())
        self._user_initiated_close = True
        self.close()

    def closeEvent(self, event):
        if self._user_initiated_close:
            self._user_initiated_close = False
            event.accept()
            return
        # 非用户关闭 → 隐藏
        event.ignore()
        self.hide()

    # =========================================================================
    # 拖拽移动
    # =========================================================================

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.pos().y() < 40:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        elif event.pos().y() < 40:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)
