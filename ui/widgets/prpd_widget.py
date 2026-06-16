# -*- coding: utf-8 -*-
"""
# TODO: 文件过大(624行)，建议拆分 2D/3D/控制栏
PRPD 图谱控件 - PRPDWidget

基于 PyQtGraph + Matplotlib 实现 Phase Resolved Partial Discharge 图谱。
支持四种显示模式:
- 散点图 (Scatter): 每个放电事件一个点
- 热力图 (Heatmap): 相位-幅值二维直方图 (ImageItem)
- 密度图 (Density): 高斯核平滑后的热力图
- 3D散点图 (PRPS): 三维相位-周期-幅值散点（Matplotlib 3D）

性能: >=10 FPS (2D), >=10 FPS (3D)
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import List, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import DT

pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND, background=DT.C.CHART_BACKGROUND)

logger = logging.getLogger(__name__)

# Matplotlib 3D 支持
try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    HAS_MPL3D = True
except ImportError:
    HAS_MPL3D = False

# 将 Matplotlib 的 jet 颜色映射注册到 PyQtGraph（PyQtGraph 无内置 jet）
# 注意：pg.colormap.get() 只从文件系统查找，内存注册的 ColorMap 无法通过 get() 获取，
# 因此将 jet 映射存储为模块级常量 _JET_CMAP 供 update_heatmap() 直接使用
_JET_CMAP = None  # type: ignore[assignment]
if HAS_MPL3D:
    try:
        import matplotlib
        from matplotlib import cm

        # 兼容 matplotlib 3.7+ API 变更
        try:
            _jet_cmap = cm.get_cmap("jet")
        except (AttributeError, DeprecationWarning):
            _jet_cmap = matplotlib.colormaps.get_cmap("jet")
        _jet_data = _jet_cmap(np.linspace(0, 1, 256))
        _jet_rgba = (_jet_data[:, :4] * 255).astype(np.uint8)
        _JET_CMAP = pg.colormap.ColorMap(
            pos=np.linspace(0.0, 1.0, 256),
            color=_jet_rgba,
        )
        logger.info("PyQtGraph jet 颜色映射创建成功")
    except Exception as e:
        logger.warning("jet 颜色映射创建失败，将 fallback 到 turbo: %s", e)


class PRPDDisplayMode(Enum):
    """PRPD 显示模式"""

    SCATTER = "scatter"
    HEATMAP = "heatmap"
    DENSITY = "density"
    SCATTER_3D = "scatter_3d"

    @property
    def display_name(self) -> str:
        """UI 显示名称"""
        _names = {
            "scatter": "散点图",
            "heatmap": "热力图",
            "density": "密度图",
            "scatter_3d": "3D散点图",
        }
        return _names.get(self.value, self.value)


class PRPDWidget(QWidget):
    """PRPD 图谱控件"""

    mode_changed = Signal(str)

    # 统一颜色映射：2D/3D 均使用 DT 令牌，保持视觉一致性
    COLORMAP_2D = DT.C.CHART_COLORMAP
    COLORMAP_3D = DT.C.CHART_COLORMAP

    def __init__(self, title: str = "PRPD 图谱", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._title = title
        self._mode = PRPDDisplayMode.HEATMAP
        self._phase_bins = 360
        self._amplitude_bins = 256
        self._max_amplitude = 100.0

        self._scatter_phases: List[float] = []
        self._scatter_amplitudes: List[float] = []
        self._scatter_cycles: List[int] = []  # 真实工频周期号（0=未知）
        self._heatmap_data: Optional[np.ndarray] = None
        self._heatmap_log: Optional[np.ndarray] = None
        self._display_mode: str = "heatmap"  # 与 PRPDDisplayMode.value 一致

        # 3D 视图状态（Matplotlib）
        self._mpl_canvas = None  # FigureCanvasQTAgg
        self._mpl_fig = None  # Figure
        self._mpl_ax = None  # Axes3D
        self._mpl_colorbar = None  # ColorBar（持久化引用）
        self._mpl_scatter = None  # Scatter 对象（增量更新用）
        self._3d_elev = 36  # 当前3D视角
        self._3d_azim = -122
        self._3d_initialized = False  # 3D 首次绘制标记
        self._3d_data_dirty = False  # 3D 数据脏标记（非3D模式下数据变更时设为True）
        self._3d_render_timer = None  # 3D 渲染节流定时器

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 标题栏
        header = QHBoxLayout()
        self._title_label = QLabel(self._title)
        self._title_label.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "SemiBold"))
        self._title_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        header.addWidget(self._title_label)

        header.addStretch()

        self._info_label = QLabel("0 事件")
        self._info_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: 11px;")
        header.addWidget(self._info_label)

        # 模式选择（默认热力图）
        self._mode_combo = QComboBox()
        self._mode_combo.blockSignals(True)
        for mode in PRPDDisplayMode:
            if mode == PRPDDisplayMode.SCATTER_3D and not HAS_MPL3D:
                continue
            self._mode_combo.addItem(mode.display_name, mode.value)
        # 设置默认选中热力图
        heatmap_idx = self._mode_combo.findData(PRPDDisplayMode.HEATMAP.value)
        if heatmap_idx >= 0:
            self._mode_combo.setCurrentIndex(heatmap_idx)
        self._mode_combo.blockSignals(False)
        self._mode_combo.setFixedHeight(32)
        self._mode_combo.setStyleSheet(
            f"""
            QComboBox {{
                background: {DT.C.BG_PRIMARY};
                border: 1px solid {DT.C.BORDER_DEFAULT};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                color: {DT.C.TEXT_PRIMARY};
            }}
            QComboBox:hover {{ border-color: {DT.C.BORDER_HOVER}; }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background: {DT.C.BG_PRIMARY};
                border: 1px solid {DT.C.BORDER_DEFAULT};
                selection-background-color: {DT.C.ACCENT_SUBTLE};
                selection-color: {DT.C.ACCENT_PRIMARY};
                font-size: 11px;
            }}
        """
        )
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        header.addWidget(self._mode_combo)

        btn_reset = QPushButton("重置")
        btn_reset.setFixedHeight(32)
        btn_reset.setStyleSheet(self._button_style())
        btn_reset.clicked.connect(self._on_reset)
        header.addWidget(btn_reset)

        layout.addLayout(header)

        # PyQtGraph 绘图控件（2D 视图）
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground(DT.C.CHART_BACKGROUND)
        self._plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._plot_widget.setMinimumHeight(150)
        self._plot_widget.setLabel("left", "幅值", units="mV")
        self._plot_widget.setLabel("bottom", "相位", units="°")
        self._plot_widget.setXRange(0, 360)
        self._plot_widget.showGrid(x=True, y=True, alpha=0.2)

        self._scatter_plot = pg.ScatterPlotItem(
            size=4,
            pen=pg.mkPen(None),
            brush=pg.mkBrush(200, 80, 50, 180),
        )
        self._plot_widget.addItem(self._scatter_plot)
        self._scatter_plot.setVisible(False)

        self._image_item = pg.ImageItem(axisOrder="row-major")
        self._image_item.setVisible(False)
        self._plot_widget.addItem(self._image_item)

        # 用 QStackedWidget 切换 2D/3D 视图
        self._view_stack = QStackedWidget()
        self._view_stack.addWidget(self._plot_widget)  # index 0 = 2D (PyQtGraph)
        # 3D 视图懒加载：首次切换到 scatter_3d 时才创建 Matplotlib Canvas
        self._3d_widget_created = False
        self._view_stack.setCurrentIndex(0)
        layout.addWidget(self._view_stack)

    # ── 数据更新 ─────────────────────────────────────

    def update_heatmap(self, matrix: np.ndarray, phase_bins: int = 360, amplitude_bins: int = 256) -> None:
        self._heatmap_data = matrix
        self._phase_bins = phase_bins
        self._amplitude_bins = amplitude_bins

        # ── 从散点事件数据构建2D直方图（与散点图同源）──
        # axisOrder='row-major' + setRect(0,0,phase_bins,max_amplitude):
        #   img_data[amp_idx, phase_idx] → pixel(row, col) → (X=phase, Y=amp)
        #   H[0, :] = 低幅值 → row=0 → Y=0(底部) → 低幅值在底部 ✅
        #   H[255, :] = 高幅值 → row=255 → Y=max_amp(顶部) → 高幅值在顶部 ✅
        # 无需 flipud（flipud 是 axisOrder='col-major' 时期的残留补偿）

        if len(self._scatter_phases) > 10 and len(self._scatter_amplitudes) > 10:
            ph = np.asarray(self._scatter_phases, dtype=np.float64)
            am = np.asarray(self._scatter_amplitudes, dtype=np.float64)
            img_data, _, _ = np.histogram2d(
                am,
                ph,
                bins=(amplitude_bins, phase_bins),
                range=[[0, self._max_amplitude], [0, float(phase_bins)]],
            )
            logger.info(
                "热力图直方图: %d事件→shape=%s, 非零=%d",
                len(ph),
                img_data.shape,
                int(np.count_nonzero(img_data)),
            )
        else:
            img_data = matrix.T.copy()
            logger.info("热力图矩阵回退: shape=%s", img_data.shape)

        if np.max(img_data) > 0:
            img_data = np.log1p(img_data)

        self._heatmap_log = img_data.copy()

        self._image_item.setImage(img_data, autoLevels=True)
        self._image_item.setRect(QRectF(0.0, 0.0, float(phase_bins), self._max_amplitude))

        self._image_item.setVisible(self._display_mode != "scatter")
        self._scatter_plot.setVisible(self._display_mode == "scatter")

        # 使用 DT 令牌指定的颜色映射，fallback 到 turbo
        try:
            cmap = pg.colormap.get(self.COLORMAP_2D)
        except (FileNotFoundError, OSError):
            cmap = None
        if cmap is None:
            cmap = pg.colormap.get("turbo")
        if cmap is not None:
            self._image_item.setColorMap(cmap)

        total = int(np.sum(matrix))
        self._info_label.setText(f"总事件数 {total}")
        self._sync_3d_view()

    def update_scatter(self, phases: List[float], amplitudes: List[float], cycles: Optional[List[int]] = None) -> None:
        self._scatter_phases = list(phases)
        self._scatter_amplitudes = list(amplitudes)
        self._scatter_cycles = list(cycles) if cycles else [0] * len(phases)

        if len(phases) == 0:
            return

        spots = [{"pos": (p, a), "size": 4} for p, a in zip(phases, amplitudes)]
        self._scatter_plot.setData(spots)
        self._image_item.setVisible(self._display_mode in ("heatmap", "density"))
        self._scatter_plot.setVisible(self._display_mode == "scatter")
        self._info_label.setText(f"{len(phases)} 事件")

        # 任何模式下都更新3D缓存数据（不触发绘制），确保切换到3D时立即可用
        self._3d_data_dirty = True

    # ── 3D 视图（Matplotlib）─────────────────────────

    def _ensure_3d_widget(self) -> None:
        """懒加载：首次调用时创建 Matplotlib 3D Canvas 并加入 QStackedWidget"""
        if self._3d_widget_created or not HAS_MPL3D:
            return
        mpl_w = self._build_mpl_3d_widget()
        self._view_stack.addWidget(mpl_w)  # index 1 = 3D (Matplotlib)
        self._3d_widget_created = True

    def _build_mpl_3d_widget(self):
        """
        构建 Matplotlib 3D 视口。

        参数与 plot_PRPS.py 完全一致：
        - X: 相位 (0~360°)
        - Y: 工频周期 (0~50)
        - Z: 放电量 (真实值)
        - 视角: elev=36°, azim=-122°
        - box_aspect: (1.45, 1.15, 1.20)
        - 颜色映射: jet
        """
        fig = Figure(figsize=(10, 8), facecolor=DT.C.CHART_BACKGROUND, dpi=100)
        ax = fig.add_subplot(111, projection="3d")

        # 中文字体设置
        try:
            import matplotlib

            matplotlib.rcParams["font.sans-serif"] = ["SimHei", "SimSun", "Microsoft YaHei"]
            matplotlib.rcParams["axes.unicode_minus"] = False
        except Exception:
            pass

        canvas = FigureCanvas(fig)
        canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        canvas.setMinimumHeight(200)

        self._mpl_canvas = canvas
        self._mpl_fig = fig
        self._mpl_ax = ax

        # 连接鼠标交互事件，保存用户旋转的视角
        def _on_mouse_release(event):
            if self._mpl_ax is not None:
                self._3d_elev = self._mpl_ax.elev
                self._3d_azim = self._mpl_ax.azim

        canvas.mpl_connect("button_release_event", _on_mouse_release)

        return canvas

    def _update_3d_scatter(self) -> None:
        """使用 Matplotlib 绘制 3D PRPS 散点图（参数与 plot_PRPS.py 一致）"""
        if not HAS_MPL3D:
            return
        # 懒加载 3D Widget（首次调用时创建）
        self._ensure_3d_widget()
        if self._mpl_ax is None or self._mpl_fig is None:
            return
        try:
            self._do_update_3d_scatter_mpl()
            self._3d_data_dirty = False
        except Exception:
            logger.exception("3D 散点图渲染失败")

    def _do_update_3d_scatter_mpl(self) -> None:
        """Matplotlib 3D 散点图内核实现 — 增量更新，不 fig.clear()"""
        ax = self._mpl_ax
        fig = self._mpl_fig

        # 获取数据
        phases = np.array(self._scatter_phases, dtype=np.float64)
        amps = np.array(self._scatter_amplitudes, dtype=np.float64)
        cycles = np.array(self._scatter_cycles, dtype=np.float64)
        n = len(phases)

        # 数据不足时从热力图回退
        used_fallback = False
        if n < 10 and self._heatmap_data is not None:
            h, w = self._heatmap_data.shape
            rows, cols = np.nonzero(self._heatmap_data > 0)
            if len(rows) < 10 and self._heatmap_log is not None:
                # 从 log 矩阵回退：生成所有非零 bin 的中心点
                all_rows, all_cols = np.mgrid[0:h, 0:w].reshape(2, -1)
                vals = self._heatmap_data.ravel()
                mask = vals > 0
                rows, cols = all_rows[mask], all_cols[mask]
            if len(rows) > 0:
                # 生成 bin 中心坐标 + 微小随机扰动，避免网格对齐的视觉伪影
                phase_jitter = np.random.uniform(-0.4, 0.4, size=len(cols)) * (360.0 / w)
                amp_jitter = np.random.uniform(-0.4, 0.4, size=len(rows)) * (self._max_amplitude / h)
                phases = (cols.astype(np.float64) + 0.5) * (360.0 / w) + phase_jitter
                amps = (rows.astype(np.float64) + 0.5) * (self._max_amplitude / h) + amp_jitter
                amps = np.clip(amps, 0, None)  # 幅值不能为负
                cycles = np.zeros(len(phases), dtype=np.float64)  # 回退数据无周期信息
                n = len(phases)
                used_fallback = True

        if n < 10:
            return

        logger.debug(
            "3D-MPL 渲染 %d 个点 (fallback=%s), amp=[%.1f, %.1f]",
            n,
            used_fallback,
            float(amps.min()),
            float(amps.max()),
        )

        # ── 周期编号：优先使用真实周期号，与 plot_PRPS.py 公式一致 ──
        N_CYCLES = 50
        phase = phases
        discharge = np.abs(amps)

        if np.any(cycles > 0):
            # 有真实周期号：与 plot_PRPS.py 公式一致
            # cycle_local = ((raw_cycle - 1) % 50) + 1
            raw_cycle_local = ((cycles - 1) % N_CYCLES) + 1
        else:
            # 无真实周期号：模拟分配（均匀切割）
            events_per_cycle = max(1, n // N_CYCLES)
            cycle_nums = np.clip(np.floor(np.arange(n) / events_per_cycle), 0, N_CYCLES - 1)
            raw_cycle_local = (cycle_nums % N_CYCLES) + 1

        # ── 增量更新：只清除散点数据，保留坐标轴和ColorBar ──
        if not self._3d_initialized:
            # 首次绘制：完整设置
            self._setup_3d_axes(ax, phase, raw_cycle_local, discharge)
            self._3d_initialized = True
        else:
            # 后续更新：只移除旧散点，保留坐标轴框架和ColorBar
            if self._mpl_scatter is not None:
                try:
                    self._mpl_scatter.remove()
                except (ValueError, AttributeError):
                    pass

            # 重新绘制散点
            self._mpl_scatter = ax.scatter(
                phase,
                raw_cycle_local,
                discharge,
                s=18,  # 与 plot_PRPS.py 一致
                c=discharge,
                cmap=DT.C.CHART_COLORMAP,
                marker="o",
                edgecolors="none",
            )

            # 更新 ColorBar 的标量映射范围
            if self._mpl_colorbar is not None:
                self._mpl_colorbar.update_normal(self._mpl_scatter)

            # 保留用户视角（不重置为默认）
            ax.view_init(elev=self._3d_elev, azim=self._3d_azim)

            # 更新 Z 轴范围
            z_max_val = max(1000.0, float(np.max(discharge) * 1.05))
            ax.set_zlim(0, z_max_val)

        # 刷新画布
        self._mpl_canvas.draw_idle()  # draw_idle 比 draw 更高效

    def _setup_3d_axes(self, ax, phase: np.ndarray, cycle: np.ndarray, discharge: np.ndarray) -> None:
        """首次绘制3D坐标轴和散点（完整初始化）"""
        # 绘制 3D 散点（参数与 plot_PRPS.py 一致）
        self._mpl_scatter = ax.scatter(
            phase,
            cycle,
            discharge,
            s=18,  # 与 plot_PRPS.py 一致
            c=discharge,
            cmap=DT.C.CHART_COLORMAP,
            marker="o",
            edgecolors="none",
        )

        # ColorBar（shrink=0.74 与 plot_PRPS.py 一致）
        self._mpl_colorbar = self._mpl_fig.colorbar(self._mpl_scatter, ax=ax, shrink=0.74, pad=0.03)
        self._mpl_colorbar.set_label("放电量", fontsize=10)
        self._mpl_colorbar.ax.tick_params(labelsize=9)

        # 坐标轴设置（参数与 plot_PRPS.py 一致）
        ax.set_xlabel("相位 (°)", fontsize=10, labelpad=8)
        ax.set_ylabel("周期 (n)", fontsize=10, labelpad=8)
        ax.set_zlabel("放电量(mV)", fontsize=10, labelpad=10)
        ax.tick_params(axis="both", which="major", labelsize=9)
        ax.zaxis.set_tick_params(labelsize=9)

        ax.set_xlim(0, 360)
        ax.set_ylim(0, 50)
        ax.set_yticks([0, 10, 20, 30, 40, 50])
        z_max_val = max(1000.0, float(np.max(discharge) * 1.05))
        ax.set_zlim(0, z_max_val)
        ax.set_xticks([0, 60, 120, 180, 240, 300, 360])

        # 视角（参数与 plot_PRPS.py 一致）
        ax.view_init(elev=self._3d_elev, azim=self._3d_azim)

        # 盒子比例（参数与 plot_PRPS.py 一致）
        ax.set_box_aspect((1.45, 1.15, 1.20))

        # 边距调整（仅 subplots_adjust，不调 tight_layout 避免冲突）
        self._mpl_fig.subplots_adjust(left=0.08, right=0.92, bottom=0.08, top=0.95)

    def _sync_3d_view(self) -> None:
        if self._display_mode == "scatter_3d":
            self._update_3d_scatter()

    def set_max_amplitude(self, max_amp: float) -> None:
        self._max_amplitude = max_amp
        self._plot_widget.setYRange(0, max_amp)
        self._sync_3d_view()

    # ── 模式切换 ─────────────────────────────────────

    def _on_mode_changed(self, idx: int) -> None:
        mode_value = self._mode_combo.itemData(idx)
        self._display_mode = mode_value
        self.mode_changed.emit(mode_value)

        if mode_value == "scatter":
            self._scatter_plot.setVisible(True)
            self._image_item.setVisible(False)
            self._view_stack.setCurrentIndex(0)
            self._plot_widget.setLabel("left", "幅值", units="mV")
            self._plot_widget.setLabel("bottom", "相位", units="°")
            if self._scatter_phases:
                # TODO: [:5000] 硬截断应改为可配置上限
                spots = [
                    {"pos": (p, a), "size": 4}
                    for p, a in zip(self._scatter_phases[:5000], self._scatter_amplitudes[:5000])
                ]
                self._scatter_plot.setData(spots)
                y_max = max(self._scatter_amplitudes[:5000]) * 1.1  # TODO: 硬截断应改为可配置上限
                self._plot_widget.setXRange(0, 360)
                self._plot_widget.setYRange(0, y_max)
            self._plot_widget.showGrid(x=True, y=True, alpha=0.2)

        elif mode_value == "density":
            self._scatter_plot.setVisible(False)
            self._image_item.setVisible(True)
            self._view_stack.setCurrentIndex(0)
            self._plot_widget.setLabel("left", "幅值", units="mV")
            self._plot_widget.setLabel("bottom", "相位", units="°")
            self._plot_widget.setXRange(0, 360)
            self._plot_widget.setYRange(0, self._max_amplitude)
            self._apply_density_smooth()
            self._plot_widget.showGrid(x=True, y=True, alpha=0.2)

        elif mode_value == "scatter_3d":
            self._scatter_plot.setVisible(False)
            self._image_item.setVisible(False)
            # 先懒加载创建 3D Widget，再切换视图（避免 setCurrentIndex 越界）
            self._ensure_3d_widget()
            self._view_stack.setCurrentIndex(1)
            # 如果有脏数据（非3D模式下积累的数据变更），立即渲染
            if self._3d_data_dirty or not self._3d_initialized:
                self._update_3d_scatter()

        else:  # heatmap
            self._scatter_plot.setVisible(False)
            self._image_item.setVisible(True)
            self._view_stack.setCurrentIndex(0)
            self._plot_widget.setLabel("left", "幅值", units="mV")
            self._plot_widget.setLabel("bottom", "相位", units="°")
            self._plot_widget.setXRange(0, 360)
            self._plot_widget.setYRange(0, self._max_amplitude)
            if self._heatmap_log is not None:
                self._image_item.setImage(self._heatmap_log, autoLevels=True)
                self._image_item.setRect(QRectF(0.0, 0.0, float(self._phase_bins), self._max_amplitude))
            self._plot_widget.showGrid(x=True, y=True, alpha=0.2)

    def _apply_density_smooth(self) -> None:
        if self._heatmap_data is None:
            return
        try:
            from scipy.ndimage import gaussian_filter

            smoothed = gaussian_filter(self._heatmap_log, sigma=1.5) if self._heatmap_log is not None else None
            if smoothed is not None:
                self._image_item.setImage(smoothed, autoLevels=True)
                self._image_item.setRect(QRectF(0.0, 0.0, float(self._phase_bins), self._max_amplitude))
        except ImportError:
            pass

    def _on_reset(self) -> None:
        self._scatter_plot.setData([])
        self._image_item.clear()
        self._heatmap_data = None
        self._heatmap_log = None
        self._scatter_phases.clear()
        self._scatter_amplitudes.clear()
        self._scatter_cycles.clear()
        self._info_label.setText("0 事件")

        # 清除 3D 视图并重置状态
        self._3d_data_dirty = False
        if self._mpl_ax is not None:
            self._mpl_ax.clear()
            self._3d_initialized = False
            self._mpl_scatter = None
            self._mpl_colorbar = None
            if self._mpl_canvas is not None:
                self._mpl_canvas.draw_idle()

        self._view_stack.setCurrentIndex(0)

    def set_title(self, title: str) -> None:
        self._title = title
        self._title_label.setText(title)

    @property
    def plot_widget(self) -> pg.PlotWidget:
        return self._plot_widget

    @staticmethod
    def _button_style() -> str:
        return (
            f"QPushButton {{ background: transparent; color: {DT.C.TEXT_SECONDARY}; "
            f"border: 1px solid {DT.C.BORDER_DEFAULT}; border-radius: 4px; padding: 4px 10px; "
            f"font-size: 11px; }}"
            f"QPushButton:hover {{ background: {DT.C.BG_HOVER}; border-color: {DT.C.BORDER_HOVER}; }}"
        )
