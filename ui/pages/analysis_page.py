# -*- coding: utf-8 -*-
"""数据分析页面 — PRPD 实验数据导入与分析"""
from __future__ import annotations
import os
from datetime import datetime
from typing import List, Optional
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QMessageBox,
    QPushButton, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from core.data.data_importer import DataImporter
from core.data.discharge_classifier import DischargeClassifier
from core.data.report_exporter import ReportExporter
from ui.design_tokens import DT
try:
    import pyqtgraph as pg
    pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND)
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False


class AnalysisPage(QWidget):
    """数据分析页面"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("analysisPage")
        self.setAcceptDrops(True)

        self._phases: Optional[np.ndarray] = None
        self._amplitudes: Optional[np.ndarray] = None
        self._metadata: dict = {}
        self._analysis_result: dict = {}
        self._threshold_min = 0

        self._setup_ui()

    # ── UI 构建 ───────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(DT.S.XL, DT.S.LG, DT.S.XL, DT.S.LG)
        layout.setSpacing(DT.S.MD)

        self._build_header(layout)
        self._build_import_bar(layout)
        self._build_content(layout)

    def _build_header(self, layout: QVBoxLayout) -> None:
        h = QHBoxLayout()
        title = QLabel("数据分析")
        title.setFont(DT.T.get_font(*DT.T.TITLE_XLARGE[:2], "Bold"))
        title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        h.addWidget(title)
        h.addStretch()
        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px;")
        h.addWidget(self._status_label)
        layout.addLayout(h)

    def _build_import_bar(self, layout: QVBoxLayout) -> None:
        bar = QFrame()
        bar.setObjectName("cardContainer")
        bar.setStyleSheet(DT.sheet.sheet_card() + f"""
            QFrame#cardContainer {{
                border: 2px dashed {DT.C.BORDER_DEFAULT};
            }}
            QFrame#cardContainer:hover {{
                border-color: {DT.C.ACCENT_PRIMARY};
            }}
        """)
        bar.setFixedHeight(80)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(DT.S.LG, DT.S.MD, DT.S.LG, DT.S.MD)

        btn_import = QPushButton("选择文件")
        btn_import.setFixedHeight(32)
        btn_import.setStyleSheet(
            f"""
            QPushButton {{
                background: {DT.C.ACCENT_PRIMARY}; color: white; border: none;
                border-radius: 4px; padding: 6px 18px; font-size: {DT.FS.BODY}px;
            }}
            QPushButton:hover {{ background: {DT.C.ACCENT_HOVER}; }}
        """
        )
        btn_import.clicked.connect(self._on_import_click)
        bl.addWidget(btn_import)

        # 提示文字（无文件时显示）
        self._prompt_label = QLabel("选择文件或拖放 CSV/Excel/TXT 文件到此处")
        self._prompt_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY}px;")
        bl.addWidget(self._prompt_label)

        # 文件信息（有文件时显示）
        self._file_label = QLabel("")
        self._file_label.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: {DT.FS.BODY_SMALL}px;")
        self._file_label.setVisible(False)
        bl.addWidget(self._file_label, 1)

        bl.addStretch()

        # 清除按钮
        self._clear_btn = QPushButton("清除")
        self._clear_btn.setFixedHeight(28)
        self._clear_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent; color: {DT.C.TEXT_SECONDARY};
                border: 1px solid {DT.C.BORDER_DEFAULT}; border-radius: 4px;
                padding: 4px 14px; font-size: {DT.FS.BODY_SMALL}px;
            }}
            QPushButton:hover {{ background: {DT.C.BG_HOVER}; }}
        """
        )
        self._clear_btn.clicked.connect(self._clear_analysis)
        self._clear_btn.setVisible(False)
        bl.addWidget(self._clear_btn)

        # 阈值过滤
        bl.addWidget(QLabel("最低幅值:", styleSheet=f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px;"))
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(0, 100000)
        self._threshold_spin.setValue(0)
        self._threshold_spin.setSuffix(" (过滤噪声)")
        self._threshold_spin.setFixedHeight(28)
        self._threshold_spin.setStyleSheet(
            f"""
            QSpinBox {{ background: {DT.C.BG_PRIMARY}; border: 1px solid {DT.C.BORDER_DEFAULT};
            border-radius: 4px; padding: 2px 6px; font-size: {DT.FS.BODY_SMALL}px; color: {DT.C.TEXT_PRIMARY}; }}
        """
        )
        self._threshold_spin.valueChanged.connect(self._on_threshold_changed)
        bl.addWidget(self._threshold_spin)

        layout.addWidget(bar)

    def _build_content(self, layout: QVBoxLayout) -> None:
        outer = QHBoxLayout()
        outer.setSpacing(DT.S.MD)

        # 左列: PRPD 图谱 + 幅值分布（上下排列，宽度一致）
        left_col = QVBoxLayout()
        left_col.setSpacing(DT.S.MD)
        self._build_prpd_panel(left_col)
        self._build_distribution_panel(left_col)

        outer.addLayout(left_col, 3)

        # 右列: 分析结果（高度撑满到幅值分布底部）
        self._build_result_panel(outer)

        layout.addLayout(outer)

    def _build_prpd_panel(self, parent: QLayout) -> None:
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        if HAS_PYQTGRAPH:
            from ui.widgets import PRPDWidget

            self._prpd = PRPDWidget(title="PRPD 图谱")
            self._prpd._mode_combo.setCurrentIndex(0)  # 默认散点图
            fl.addWidget(self._prpd)
        else:
            self._prpd = None
            fl.addWidget(QLabel("需要 pyqtgraph 支持", alignment=Qt.AlignmentFlag.AlignCenter))

        parent.addWidget(frame, 3)

    def _build_result_panel(self, parent: QHBoxLayout) -> None:
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        # ── 标题栏 ──
        lbl = QLabel("分析结果")
        lbl.setFont(DT.T.get_font(*DT.T.TITLE_MEDIUM[:2], "SemiBold"))
        lbl.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        fl.addWidget(lbl)

        # ── 参考结果卡片 ──
        self._ref_frame = QFrame()
        self._ref_frame.setStyleSheet(
            f"QFrame {{ background: {DT.C.BG_SECONDARY}; border-radius: {DT.R.MD}px; }}"
        )
        rfl = QHBoxLayout(self._ref_frame)
        rfl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
        self._ref_type_label = QLabel("—")
        self._ref_type_label.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "Bold"))
        self._ref_type_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        self._ref_mark_label = QLabel("[参考结果]")
        self._ref_mark_label.setStyleSheet(
            f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION_SMALL}px; background: transparent;"
        )
        rfl.addWidget(self._ref_type_label)
        rfl.addWidget(self._ref_mark_label)
        rfl.addStretch()
        self._ref_conf_label = QLabel("—")
        self._ref_conf_label.setStyleSheet(
            f"color: {DT.C.TEXT_SECONDARY}; font-size: {DT.FS.BODY_SMALL}px; background: transparent;"
        )
        rfl.addWidget(self._ref_conf_label)
        fl.addWidget(self._ref_frame)

        # ── 严重等级 ──
        self._sev_card = self._make_result_card("严重等级", "—")
        fl.addWidget(self._sev_card)

        # ── 采样次数 ──
        self._cycle_card = self._make_result_card("采样次数", "—")
        fl.addWidget(self._cycle_card)

        fl.addWidget(QLabel("", styleSheet=f"color: {DT.C.DIVIDER}; max-height: 1px;"))

        # ── 各放电类型统计表 ──
        self._type_table = QTableWidget()
        self._type_table.setColumnCount(4)
        self._type_table.setHorizontalHeaderLabels(["放电类型", "出现次数", "占比", "说明"])
        self._type_table.setAlternatingRowColors(True)
        self._type_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._type_table.verticalHeader().setVisible(False)
        self._type_table.setStyleSheet(
            f"""
            QTableWidget {{
                background: transparent; border: none; font-size: {DT.FS.BODY_SMALL}px;
            }}
            QTableWidget::item {{ padding: 3px 6px; }}
            QHeaderView::section {{
                background: {DT.C.BG_SECONDARY}; color: {DT.C.TEXT_SECONDARY};
                border: none; font-size: {DT.FS.CAPTION}px; font-weight: 600;
            }}
        """
        )
        self._type_table.horizontalHeader().setStretchLastSection(True)
        fl.addWidget(self._type_table)

        fl.addWidget(QLabel("", styleSheet=f"color: {DT.C.DIVIDER}; max-height: 1px;"))

        # ── 幅值统计表 ──
        self._stat_table = QTableWidget()
        self._stat_table.setColumnCount(2)
        self._stat_table.setHorizontalHeaderLabels(["指标", "值"])
        self._stat_table.setAlternatingRowColors(True)
        self._stat_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._stat_table.verticalHeader().setVisible(False)
        self._stat_table.setStyleSheet(
            f"""
            QTableWidget {{
                background: transparent; border: none; font-size: {DT.FS.BODY_SMALL}px;
            }}
            QTableWidget::item {{ padding: 3px 6px; }}
            QHeaderView::section {{
                background: {DT.C.BG_SECONDARY}; color: {DT.C.TEXT_SECONDARY};
                border: none; font-size: {DT.FS.CAPTION}px; font-weight: 600;
            }}
        """
        )
        self._stat_table.horizontalHeader().setStretchLastSection(True)
        fl.addWidget(self._stat_table, 1)

        # 导出按钮
        btn_row = QHBoxLayout()
        for text, slot in [
            ("导出 JSON", self._export_json),
            ("导出 Excel", self._export_excel),
            ("导出 Word", self._export_word),
        ]:
            btn = QPushButton(text)
            btn.setFixedHeight(28)
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: transparent; color: {DT.C.TEXT_SECONDARY};
                    border: 1px solid {DT.C.BORDER_DEFAULT}; border-radius: 4px;
                    padding: 3px 10px; font-size: {DT.FS.CAPTION}px;
                }}
                QPushButton:hover {{ background: {DT.C.BG_HOVER}; }}
            """
            )
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        fl.addLayout(btn_row)

        parent.addWidget(frame, 2)

    def _build_distribution_panel(self, layout: QVBoxLayout) -> None:
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        lbl = QLabel("幅值分布")
        lbl.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "SemiBold"))
        lbl.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        fl.addWidget(lbl)

        if HAS_PYQTGRAPH:
            self._dist_plot = pg.PlotWidget()
            self._dist_plot.setMinimumHeight(100)
            self._dist_plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._dist_plot.showGrid(x=True, y=True, alpha=0.2)
            self._dist_plot.setLabel("left", "事件数")
            self._dist_plot.setLabel("bottom", "幅值")
            fl.addWidget(self._dist_plot)
        else:
            self._dist_plot = None

        layout.addWidget(frame, 1)

    @staticmethod
    def _make_result_card(title: str, value: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background: {DT.C.BG_SECONDARY}; border-radius: {DT.R.MD}px; }}")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
        cl.addWidget(QLabel(title, styleSheet=f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px;"))
        cl.addWidget(QLabel(value, styleSheet=f"color: {DT.C.TEXT_PRIMARY}; font-size: {DT.FS.DATA_SMALL}px; font-weight: 600;"))
        return card

    # ── 导入 ─────────────────────────────────────────

    def _on_import_click(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 PRPD 数据文件",
            "",
            "数据文件 (*.csv *.xlsx *.xls *.txt);;CSV (*.csv);;Excel (*.xlsx *.xls);;TXT (*.txt)",
        )
        if path:
            self._import_file(path)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in DataImporter.SUPPORTED_EXTENSIONS:
                self._import_file(path)
                break

    def _import_file(self, path: str) -> None:
        try:
            fmt, phases, amplitudes, cycles, meta = DataImporter.load(path)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            self._status_label.setText(f"导入失败: {e}")
            return

        if len(phases) == 0:
            QMessageBox.warning(
                self, "导入失败", "未能从文件中解析到有效数据。\n请确保文件包含相位(0-360°)和幅值两列数据。"
            )
            return

        self._raw_phases = phases
        self._raw_amplitudes = amplitudes
        self._raw_cycles = cycles
        self._metadata = meta
        self._file_path = path

        # 切换显示: 隐藏提示，显示文件信息
        self._prompt_label.setVisible(False)
        self._file_label.setText(f" {Path(path).name} — {len(phases)} 条")
        self._file_label.setVisible(True)
        self._clear_btn.setVisible(True)
        self._status_label.setText(f"已加载 {len(phases)} 条数据")

        # 自动设置阈值建议（P5 作为底噪估计）
        suggested = int(np.percentile(amplitudes, 5))
        self._threshold_spin.setValue(suggested)
        self._threshold_min = suggested

        self._run_analysis()

    def _on_threshold_changed(self, val: int) -> None:
        self._threshold_min = val
        if hasattr(self, "_raw_phases") and len(self._raw_phases) > 0:
            self._run_analysis()

    def _clear_analysis(self) -> None:
        """清除已加载的文件和分析结果，恢复到默认状态"""
        self._raw_phases = np.array([])
        self._raw_amplitudes = np.array([])
        self._raw_cycles = np.array([], dtype=np.int32)
        self._metadata = {}
        self._analysis_result = {}
        self._file_path = ""
        self._threshold_spin.setValue(0)
        self._threshold_min = 0

        # 恢复提示文字，隐藏文件信息和清除按钮
        self._prompt_label.setVisible(True)
        self._file_label.setVisible(False)
        self._file_label.setText("")
        self._clear_btn.setVisible(False)

        # 清除 PRPD 图谱
        if HAS_PYQTGRAPH and self._prpd is not None:
            self._prpd._on_reset()

        # 清除分析结果面板
        self._ref_type_label.setText("—")
        self._ref_conf_label.setText("—")
        self._update_card(self._sev_card, "严重等级", "—", DT.C.TEXT_TERTIARY)
        self._update_card(self._cycle_card, "采样次数", "—", DT.C.TEXT_TERTIARY)
        self._type_table.setRowCount(0)
        self._stat_table.setRowCount(0)

        # 清除幅值分布图
        if HAS_PYQTGRAPH and self._dist_plot is not None:
            self._dist_plot.clear()

        self._status_label.setText("就绪")

    # ── 分析 ─────────────────────────────────────────

    def _run_analysis(self) -> None:
        phases = self._raw_phases
        amps = self._raw_amplitudes
        # 阈值过滤
        mask = amps >= self._threshold_min
        phases = phases[mask]
        amps = amps[mask]
        cycles = self._raw_cycles[mask] if hasattr(self, "_raw_cycles") and self._raw_cycles.size == len(phases) else np.zeros(len(phases), dtype=np.int32)

        if len(phases) < 5:
            self._status_label.setText("过滤后数据不足，请降低阈值")
            return

        # 幅值统计（用于 PRPD 控件参数）
        stats = {
            "total_events": int(len(phases)),
            "max_amplitude": float(np.max(amps)),
            "min_amplitude": float(np.min(amps)),
            "avg_amplitude": float(np.mean(amps)),
            "median_amplitude": float(np.median(amps)),
            "p90": float(np.percentile(amps, 90)),
            "concentration": float(np.max(np.histogram(phases, bins=36, range=(0, 360))[0]) / len(phases)),
        }

        # 放电类型分类（Bootstrap 多次采样，统计各类型占比）
        dc_result = DischargeClassifier.classify_cycles(phases, amps)

        self._analysis_result = {
            "file_name": Path(self._file_path).name if hasattr(self, "_file_path") else "",
            "analysis_time": datetime.now().isoformat(),
            "statistics": dc_result.get("amplitude_stats", stats),
            "discharge_classification": dc_result,
            "threshold": self._threshold_min,
        }

        # 更新 PRPD 图谱（热力图 + 散点数据）
        if HAS_PYQTGRAPH and self._prpd is not None:
            from core.processing import PRPDProcessor
            from core.processing.prpd_processor import PRPDMode

            prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=stats["max_amplitude"] * 1.1)
            for p, a in zip(phases, amps):
                prpd.add_event(phase=float(p), amplitude=float(a))
            prpd_result = prpd.compute(mode=PRPDMode.HEATMAP)
            effective_max = stats.get("max_amplitude", 0) * 1.1
            if effective_max > 0:
                self._prpd.set_max_amplitude(effective_max)
            self._prpd.update_scatter(phases.tolist(), amps.tolist())
            self._prpd.update_heatmap(prpd_result.matrix, 360, 256)

        # 更新分析结果面板（新格式）
        self._update_result_display(dc_result, stats)

        # 更新幅值分布
        self._update_distribution(amps)

        self._status_label.setText(f"分析完成 — {len(phases)} 条事件, {dc_result.get('total_samples', 0)} 次采样")

    def _update_result_display(self, dc_result: dict, stats: dict) -> None:
        """更新分析结果面板（按周期统计格式）"""

        # ── 参考结果 ──
        ref = dc_result.get("reference_result", {})
        ref_name = ref.get("name", "—")
        ref_conf = ref.get("confidence", 0)
        self._ref_type_label.setText(ref_name)
        self._ref_conf_label.setText(f"置信度 {ref_conf:.0%}")

        # ── 严重等级 ──
        sev = dc_result.get("severity", "normal")
        self._update_card(
            self._sev_card, "严重等级",
            LEVEL_NAMES.get(sev, "—"), LEVEL_COLORS.get(sev, DT.C.TEXT_SECONDARY),
        )

        # ── 采样次数 ──
        total_samples = dc_result.get("total_samples", 0)
        self._update_card(self._cycle_card, "采样次数", str(total_samples), DT.C.TEXT_PRIMARY)

        # ── 各放电类型统计表 ──
        type_counts = dc_result.get("type_counts", {})
        type_ratios = dc_result.get("type_ratios", {})
        sorted_types = sorted(type_counts.keys(), key=lambda t: type_counts[t], reverse=True)

        self._type_table.setRowCount(len(sorted_types))
        for i, t in enumerate(sorted_types):
            count = type_counts[t]
            ratio = type_ratios.get(t, 0)
            pattern = DischargeClassifier.PATTERNS.get(t, {})
            is_noise = t == "noise"

            # 类型名称
            display_name = "随机噪声" if is_noise else pattern.get("name", t)
            name_item = QTableWidgetItem(display_name)
            name_item.setForeground(QColor(DT.C.TEXT_TERTIARY) if is_noise else (QColor(DT.C.ACCENT_PRIMARY) if t == ref.get("type") else QColor(DT.C.TEXT_PRIMARY)))
            self._type_table.setItem(i, 0, name_item)

            # 出现次数
            cnt_item = QTableWidgetItem(str(count))
            cnt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_noise:
                cnt_item.setForeground(QColor(DT.C.TEXT_TERTIARY))
            self._type_table.setItem(i, 1, cnt_item)

            # 占比
            ratio_item = QTableWidgetItem(f"{ratio:.1%}")
            ratio_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_noise:
                ratio_item.setForeground(QColor(DT.C.TEXT_TERTIARY))
            self._type_table.setItem(i, 2, ratio_item)

            # 说明
            noise_desc = "信号为随机噪声，无明显放电特征" if is_noise else pattern.get("desc", "")[:40]
            desc_item = QTableWidgetItem(noise_desc)
            desc_item.setForeground(QColor(DT.C.TEXT_TERTIARY))
            self._type_table.setItem(i, 3, desc_item)

        # ── 幅值统计表 ──
        amp_stats = dc_result.get("amplitude_stats", stats)
        stat_items = [
            ("总事件数", str(amp_stats.get("total_events", 0))),
            ("最大幅值", f'{amp_stats.get("max_amplitude", 0):.1f} mV'),
            ("最小幅值", f'{amp_stats.get("min_amplitude", 0):.1f} mV'),
            ("平均幅值", f'{amp_stats.get("avg_amplitude", 0):.1f} mV'),
            ("幅值中位数", f'{amp_stats.get("median_amplitude", 0):.1f} mV'),
            ("幅值 P90", f'{amp_stats.get("p90", 0):.1f} mV'),
        ]
        self._stat_table.setRowCount(len(stat_items))
        for i, (k, v) in enumerate(stat_items):
            self._stat_table.setItem(i, 0, QTableWidgetItem(k))
            val_item = QTableWidgetItem(v)
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._stat_table.setItem(i, 1, val_item)

    def _update_card(self, card: QFrame, title: str, value: str, color: str) -> None:
        cl = card.layout()
        # 删除旧标签重新添加
        while cl.count():
            item = cl.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cl.addWidget(QLabel(title, styleSheet=f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px;"))
        cl.addWidget(QLabel(value, styleSheet=f"color: {color}; font-size: {DT.FS.TITLE_MEDIUM}px; font-weight: 700;"))

    def _update_distribution(self, amps: np.ndarray) -> None:
        if not HAS_PYQTGRAPH or self._dist_plot is None:
            return

        self._dist_plot.clear()
        if len(amps) < 2:
            return
        # 自动分箱
        n_bins = min(50, len(amps) // 10)
        n_bins = max(10, n_bins)
        y, x = np.histogram(amps, bins=n_bins)
        # 柱状图
        bg = pg.BarGraphItem(x0=x[:-1], height=y, width=(x[1] - x[0]) * 0.9, brush=QColor(DT.C.ACCENT_PRIMARY))
        self._dist_plot.addItem(bg)

    # ── 导出 ─────────────────────────────────────────

    def _export_json(self) -> None:
        if not self._analysis_result:
            QMessageBox.information(self, "提示", "请先导入数据并完成分析")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 JSON", "prpd_report.json", "JSON (*.json)")
        if path:
            try:
                ReportExporter.export_json(self._analysis_result, path)
                self._status_label.setText(f"报告已导出: {path}")
            except Exception as e:
                QMessageBox.warning(self, "导出失败", f"JSON 导出失败: {str(e)[:200]}")

    def _export_excel(self) -> None:
        if not self._analysis_result:
            QMessageBox.information(self, "提示", "请先导入数据并完成分析")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 Excel", "prpd_report.xlsx", "Excel (*.xlsx)")
        if path:
            try:
                ReportExporter.export_excel(self._analysis_result, path)
                self._status_label.setText(f"Excel 已导出: {path}")
            except ImportError as e:
                QMessageBox.warning(self, "导出失败", str(e))

    def _export_word(self) -> None:
        if not self._analysis_result:
            QMessageBox.information(self, "提示", "请先导入数据并完成分析")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 Word", "prpd_report.docx", "Word (*.docx)")
        if path:
            try:
                # 先尝试截图 PRPD 图谱
                chart_path = None
                if HAS_PYQTGRAPH and self._prpd is not None:
                    try:
                        import io

                        # 保存 PRPD 控件截图
                        pixmap = self._prpd.grab()
                        chart_dir = Path(path).parent / ".charts"
                        chart_dir.mkdir(exist_ok=True)
                        chart_path = str(chart_dir / "prpd_chart.png")
                        pixmap.save(chart_path)
                    except Exception:
                        chart_path = None

                ReportExporter.export_word(self._analysis_result, path, chart_path)
                self._status_label.setText(f"Word 已导出: {path}")
            except ImportError as e:
                QMessageBox.warning(self, "导出失败", str(e))
            except Exception as e:
                QMessageBox.warning(self, "导出失败", f"报告导出异常: {e}")
