"""Review Tab — 审查修正交互界面"""
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QScrollArea, QFrame, QFileDialog, QLineEdit,
    QStackedWidget, QSizePolicy,
)
from PyQt6.QtGui import QFont

from gui.v5 import tokens as T
from gui.v5.telemetry import telemetry


class ReviewTypeChip(QPushButton):
    """审查类型选择 Chip"""

    def __init__(self, label: str, review_type: str, parent=None):
        super().__init__(label, parent)
        self.review_type = review_type
        self._selected = False
        self._update_style()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._toggle)

    def _toggle(self):
        self._selected = not self._selected
        self._update_style()

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()

    @property
    def is_selected(self) -> bool:
        return self._selected

    def _update_style(self):
        if self._selected:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {T.TEXT_ACCENT};
                    color: white;
                    border: none;
                    border-radius: 14px;
                    padding: 6px 14px;
                    font-size: {T.FONT_CAPTION[0]}px;
                    font-weight: bold;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {T.BG_ELEVATED};
                    color: {T.TEXT_SECONDARY};
                    border: 1px solid {T.STROKE_BORDER};
                    border-radius: 14px;
                    padding: 6px 14px;
                    font-size: {T.FONT_CAPTION[0]}px;
                }}
                QPushButton:hover {{
                    background-color: {T.BG_SELECTED};
                    border-color: {T.STROKE_FOCUS};
                }}
            """)


class ReviewIssueCard(QFrame):
    """单条审查问题卡片"""

    replace_clicked = pyqtSignal(str, str)  # (claim, suggestion)

    def __init__(self, issue_data: dict, parent=None):
        super().__init__(parent)
        self._issue = issue_data
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_ELEVATED};
                border: 1px solid {T.STROKE_BORDER};
                border-radius: 8px;
                margin: 2px;
            }}
        """)
        self.setFixedHeight(130)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # 头部：严重度 + 问题类型
        header = QHBoxLayout()
        severity = self._issue.get("severity", "medium")
        severity_colors = {"high": "#e74c3c", "medium": "#f39c12", "low": "#3498db"}
        severity_labels = {"high": "高风险", "medium": "中风险", "low": "低风险"}
        color = severity_colors.get(severity, "#999")

        badge = QLabel(f"● {severity_labels.get(severity, severity)}")
        badge.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold; border: none;")
        header.addWidget(badge)

        issue_type = QLabel(self._issue.get("issue_type", ""))
        issue_type.setStyleSheet(f"color: {T.TEXT_TERTIARY}; font-size: 10px; border: none;")
        header.addWidget(issue_type)
        header.addStretch()
        layout.addLayout(header)

        # Claim（原文片段）
        claim = self._issue.get("claim", "")
        claim_label = QLabel(f'"{claim[:60]}{"..." if len(claim) > 60 else ""}"')
        claim_label.setStyleSheet(f"color: {T.TEXT_PRIMARY}; font-size: 12px; border: none;")
        claim_label.setWordWrap(True)
        layout.addWidget(claim_label)

        # 原因
        reason = self._issue.get("reason", "")
        reason_label = QLabel(reason[:80] + ("..." if len(reason) > 80 else ""))
        reason_label.setStyleSheet(f"color: {T.TEXT_SECONDARY}; font-size: 11px; border: none;")
        reason_label.setWordWrap(True)
        layout.addWidget(reason_label)

        # 底部操作栏
        footer = QHBoxLayout()
        footer.addStretch()

        suggestion = self._issue.get("suggestion", "")
        if suggestion:
            btn_replace = QPushButton("替换")
            btn_replace.setStyleSheet(f"""
                QPushButton {{
                    background-color: {T.TEXT_ACCENT};
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 11px;
                }}
                QPushButton:hover {{ opacity: 0.8; }}
            """)
            btn_replace.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_replace.clicked.connect(
                lambda: self.replace_clicked.emit(claim, suggestion)
            )
            footer.addWidget(btn_replace)

        layout.addLayout(footer)


class ReviewTabV5(QWidget):
    """Review Tab — 审查修正交互"""

    review_started = pyqtSignal()
    review_completed = pyqtSignal(dict)  # ReviewResult.to_dict()

    def __init__(self, nav, parent=None):
        super().__init__(parent)
        self.nav = nav
        self._selected_text = ""
        self._review_type = "hallucination"
        self._reference_path = ""
        self._init_ui()
        telemetry().window_event("V5_REVIEW_TAB_CREATE", "review_tab")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── 顶部：审查类型 Chips ──
        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        chip_row.setContentsMargins(4, 4, 4, 0)

        self._chip_hallucination = ReviewTypeChip("幻觉检测", "hallucination")
        self._chip_hallucination.set_selected(True)
        self._chip_hallucination.clicked.connect(lambda: self._select_review_type("hallucination"))

        self._chip_data_check = ReviewTypeChip("数据核对", "data_check")
        self._chip_data_check.clicked.connect(lambda: self._select_review_type("data_check"))

        self._chip_style = ReviewTypeChip("风格检查", "style")
        self._chip_style.clicked.connect(lambda: self._select_review_type("style"))

        chip_row.addWidget(self._chip_hallucination)
        chip_row.addWidget(self._chip_data_check)
        chip_row.addWidget(self._chip_style)
        chip_row.addStretch()
        layout.addLayout(chip_row)

        # ── 参考文件输入（仅 data_check 时显示）──
        self._ref_frame = QFrame()
        self._ref_frame.setStyleSheet(f"QFrame {{ border: none; }}")
        ref_layout = QHBoxLayout(self._ref_frame)
        ref_layout.setContentsMargins(4, 0, 4, 0)
        ref_layout.setSpacing(6)

        ref_label = QLabel("参考文件:")
        ref_label.setStyleSheet(f"color: {T.TEXT_TERTIARY}; font-size: 11px; border: none;")
        ref_layout.addWidget(ref_label)

        self._ref_input = QLineEdit()
        self._ref_input.setPlaceholderText("输入文件路径 或 描述（如：上季度销售数据）")
        self._ref_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.BG_ELEVATED};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.STROKE_BORDER};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }}
            QLineEdit:focus {{
                border-color: {T.STROKE_FOCUS};
            }}
        """)
        ref_layout.addWidget(self._ref_input)

        btn_browse = QPushButton("浏览")
        btn_browse.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.BG_ELEVATED};
                color: {T.TEXT_SECONDARY};
                border: 1px solid {T.STROKE_BORDER};
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {T.BG_SELECTED}; }}
        """)
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self._browse_reference)
        ref_layout.addWidget(btn_browse)

        self._ref_frame.setVisible(False)
        layout.addWidget(self._ref_frame)

        # ── 风格目标输入（仅 style 时显示）──
        self._style_frame = QFrame()
        self._style_frame.setStyleSheet(f"QFrame {{ border: none; }}")
        style_layout = QHBoxLayout(self._style_frame)
        style_layout.setContentsMargins(4, 0, 4, 0)
        style_layout.setSpacing(6)

        style_label = QLabel("目标风格:")
        style_label.setStyleSheet(f"color: {T.TEXT_TERTIARY}; font-size: 11px; border: none;")
        style_layout.addWidget(style_label)

        self._style_input = QLineEdit()
        self._style_input.setPlaceholderText("b2b_formal / academic / marketing / technical")
        self._style_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.BG_ELEVATED};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.STROKE_BORDER};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }}
        """)
        style_layout.addWidget(self._style_input)
        self._style_frame.setVisible(False)
        layout.addWidget(self._style_frame)

        # ── 中部：审查结果列表 ──
        self._result_scroll = QScrollArea()
        self._result_scroll.setWidgetResizable(True)
        self._result_scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background-color: transparent;
            }}
        """)
        self._result_container = QWidget()
        self._result_container.setStyleSheet("background: transparent;")
        self._result_layout = QVBoxLayout(self._result_container)
        self._result_layout.setContentsMargins(4, 0, 4, 0)
        self._result_layout.setSpacing(6)
        self._result_layout.addStretch()
        self._result_scroll.setWidget(self._result_container)
        layout.addWidget(self._result_scroll, 1)

        # ── 底部：操作栏 ──
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 4, 4)
        footer.setSpacing(8)

        self._btn_start = QPushButton("开始审查")
        self._btn_start.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.TEXT_ACCENT};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
            QPushButton:disabled {{ opacity: 0.5; }}
        """)
        self._btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start.clicked.connect(self._on_start_review)
        footer.addWidget(self._btn_start)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {T.TEXT_TERTIARY}; font-size: 11px;")
        footer.addWidget(self._status_label)
        footer.addStretch()

        layout.addLayout(footer)

    # =========================================================================
    # 公共方法
    # =========================================================================

    def set_context_text(self, text: str):
        """设置待审查文本"""
        self._selected_text = text
        telemetry().emit("V5_REVIEW_SET_TEXT", text_len=len(text))

    def start_review_with_type(self, review_type: str, reference: str = ""):
        """外部触发审查（如快捷指令 /check, /verify）"""
        self._select_review_type(review_type)
        if reference:
            if review_type == "data_check":
                self._ref_input.setText(reference)
            elif review_type == "style":
                self._style_input.setText(reference)
        self._on_start_review()

    # =========================================================================
    # 内部方法
    # =========================================================================

    def _select_review_type(self, review_type: str):
        self._review_type = review_type
        self._chip_hallucination.set_selected(review_type == "hallucination")
        self._chip_data_check.set_selected(review_type == "data_check")
        self._chip_style.set_selected(review_type == "style")
        self._ref_frame.setVisible(review_type == "data_check")
        self._style_frame.setVisible(review_type == "style")

    def _browse_reference(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考文件", "",
            "所有支持格式 (*.xlsx *.xls *.csv *.json *.md *.docx *.txt);;Excel (*.xlsx *.xls);;CSV (*.csv)"
        )
        if path:
            self._ref_input.setText(path)
            self._reference_path = path
            telemetry().emit("V5_REVIEW_REF_BROWSE", file=path)

    def _on_start_review(self):
        if not self._selected_text:
            self._status_label.setText("请先选择或输入待审查文本")
            return

        self._status_label.setText("审查中...")
        self._btn_start.setEnabled(False)
        self._clear_results()

        t = telemetry()
        t.emit("V5_REVIEW_START",
               review_type=self._review_type,
               text_len=len(self._selected_text))

        # 构建 context_meta
        context_meta = {
            "review_type": self._review_type,
        }

        if self._review_type == "data_check":
            ref_input = self._ref_input.text().strip()
            if ref_input:
                import os
                if os.path.isfile(ref_input):
                    context_meta["reference_path"] = ref_input
                else:
                    context_meta["reference_description"] = ref_input

        elif self._review_type == "style":
            style = self._style_input.text().strip()
            if style:
                context_meta["style_target"] = style

        # 通过 NavigationManager 发送请求
        try:
            self.nav.send_review_request(
                text=self._selected_text,
                context_meta=context_meta,
                on_chunk=self._on_review_chunk,
                on_done=self._on_review_done,
                on_error=self._on_review_error,
            )
        except Exception as e:
            self._on_review_error(str(e))

    def _on_review_chunk(self, chunk: str):
        """流式接收审查结果"""
        self._status_label.setText(f"接收中... ({len(chunk)} 字符)")

    def _on_review_done(self, full_response: str):
        """审查完成，解析结果"""
        self._btn_start.setEnabled(True)

        # 尝试解析 JSON 结果
        from opencopilot.review import ReviewEngine
        engine = ReviewEngine()
        result = engine.parse_llm_response(full_response, self._review_type, self._selected_text)

        self._status_label.setText(result.summary)
        self._render_results(result.to_dict())

        t = telemetry()
        t.emit("V5_REVIEW_DONE",
               review_type=self._review_type,
               issue_count=result.issue_count,
               high_count=result.high_severity_count,
               elapsed_ms=result.elapsed_ms)

        self.review_completed.emit(result.to_dict())

    def _on_review_error(self, error: str):
        self._btn_start.setEnabled(True)
        self._status_label.setText(f"错误: {error[:50]}")
        telemetry().emit("V5_REVIEW_ERROR", error=error[:100])

    def _clear_results(self):
        while self._result_layout.count():
            item = self._result_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._result_layout.addStretch()

    def _render_results(self, result_dict: dict):
        """渲染审查结果卡片"""
        self._clear_results()

        issues = result_dict.get("issues", [])
        if not issues:
            empty_label = QLabel("未发现明显问题")
            empty_label.setStyleSheet(f"color: {T.TEXT_TERTIARY}; font-size: 13px; border: none;")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._result_layout.insertWidget(0, empty_label)
            return

        for i, issue in enumerate(issues):
            card = ReviewIssueCard(issue)
            card.replace_clicked.connect(self._on_replace_issue)
            self._result_layout.insertWidget(i, card)

    def _on_replace_issue(self, claim: str, suggestion: str):
        """单条替换：通过 Broker 替换选中文本中的对应片段"""
        if not self._selected_text or not suggestion:
            return

        # 计算替换后的完整文本
        new_text = self._selected_text.replace(claim, suggestion, 1)
        if new_text == self._selected_text:
            self._status_label.setText("未找到匹配片段")
            return

        # 通过 Broker 替换
        try:
            from system_probe_client import SystemProbeClient
            probe = SystemProbeClient()
            success = probe.replace_selection(new_text)
            if success:
                self._selected_text = new_text
                self._status_label.setText("已替换")
                telemetry().emit("V5_REVIEW_REPLACE", success=True)
            else:
                self._status_label.setText("替换失败，请手动操作")
                telemetry().emit("V5_REVIEW_REPLACE", success=False)
        except Exception as e:
            self._status_label.setText(f"替换异常: {str(e)[:30]}")

    # =========================================================================
    # 快捷指令解析
    # =========================================================================

    @staticmethod
    def parse_command(text: str) -> tuple:
        """
        解析快捷指令，返回 (review_type, param) 或 (None, None)

        支持指令：
        - /check → 幻觉检测
        - /verify <path或描述> → 数据核对
        - /style <目标> → 风格检查
        """
        text = text.strip()
        if text.startswith("/check"):
            return ("hallucination", "")
        elif text.startswith("/verify"):
            param = text[len("/verify"):].strip()
            return ("data_check", param)
        elif text.startswith("/style"):
            param = text[len("/style"):].strip()
            return ("style", param)
        return (None, None)
