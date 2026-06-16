# -*- coding: utf-8 -*-
"""
PD 系统总控制器 - PDSystemController

整合所有服务层组件并连接 UI 页面:
- AcquisitionService: 数据采集与信号处理
- PDAlarmService: 报警检测与管理
- PDStorageService: 数据持久化
- PDSimulator: 测试数据生成
- PDDataBus: 事件总线（连接服务和 UI）

架构:
    PDSimulator / FPGA
         ↓ (波形数据)
    AcquisitionService → PDDataBus → UI (WaveformWidget, PRPDWidget, FFTWidget)
         ↓ (峰值/事件)
    PDAlarmService → PDDataBus → UI (AlarmPage)
         ↓ (报警记录)
    PDStorageService → SQLite
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import QObject, QTimer

from core.foundation.signal_bus import PDDataBus
from core.services import AcquisitionService, PDAlarmService, PDStorageService

logger = logging.getLogger(__name__)


class PDSystemController(QObject):
    """
    PD 系统总控制器

    负责:
    1. 初始化所有服务
    2. 连接 DataBus 信号到 UI
    3. 启动/停止数据采集
    4. 管理测试模拟器
    """

    def __init__(self, db_manager=None, parent=None):
        super().__init__(parent)
        self._db_manager = db_manager
        self._data_bus = PDDataBus.instance()

        # 服务
        self._acquisition: Optional[AcquisitionService] = None
        self._alarm: Optional[PDAlarmService] = None
        self._storage: Optional[PDStorageService] = None
        self._simulator: Any = None
        self._hardware: Any = None  # HardwareManager 实例

        # UI 页面引用
        self._pages: dict = {}

        # 统计定时器
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_status_stats)
        self._stats_timer.setInterval(2000)

        # 仪表板帧计数（用于快照节流）
        self._waveform_frame_count = 0
        self._ae_hit_count = 0

        logger.info("PDSystemController 已创建")

    # ── 属性 ─────────────────────────────────────────

    @property
    def _main_window(self):
        """获取主窗口引用（控制器作为 window 的子对象）"""
        return self.parent()

    # ── 初始化 ───────────────────────────────────────

    def initialize(self, pages: dict = None) -> None:
        """
        初始化所有服务

        Args:
            pages: UI 页面字典 {key: page_widget}
        """
        if pages:
            self._pages = pages

        # 初始化服务
        self._acquisition = AcquisitionService(
            sample_rate=100_000_000,
            waveform_points=4096,
        )

        self._alarm = PDAlarmService(db_manager=self._db_manager)
        self._storage = PDStorageService(
            db_manager=self._db_manager,
            flush_interval=5.0,
            batch_size=100,
        )

        # 连接服务到 DataBus
        self._connect_services()

        # 启动存储
        if self._storage:
            self._storage.start()

        # 连接 UI
        self._connect_ui()

        # 传递 db_manager 到设备管理页面
        if "device" in self._pages:
            try:
                self._pages["device"].set_db_manager(self._db_manager)
            except Exception as e:
                logger.debug("异常: %s", e)

        # 传递 db_manager 到 AE 分析页面
        if "ae" in self._pages:
            try:
                self._pages["ae"].set_db_manager(self._db_manager)
            except Exception as e:
                logger.debug("异常: %s", e)

        # 初始化 AELocalizer 并传递到 AE 分析页面
        self._init_ae_localizer()

        # 从数据库加载已保存的通道配置
        self.load_channel_configs_from_db()

        self._stats_timer.start()

        logger.info("PDSystemController 初始化完成")

    def _connect_services(self) -> None:
        """连接服务层"""
        bus = self._data_bus

        # 采集 → 报警
        bus.pd_event_detected.connect(self._on_pd_event_for_alarm)
        bus.device_status_changed.connect(self._on_device_status_for_alarm)

        # 采集 → 存储
        bus.pd_event_detected.connect(self._on_pd_event_for_storage)
        bus.waveform_received.connect(self._on_waveform_for_storage)

        logger.debug("服务层信号连接完成")

    def _init_ae_localizer(self) -> None:
        """初始化 AE 声源定位器并连接到 AE 分析页面"""
        if "ae" not in self._pages:
            return

        try:
            from core.processing.ae_localizer import AELocalizer, SensorPosition

            # 默认 4 传感器阵列 (正方形布局, 间距 1m)
            sensors = [
                SensorPosition(sensor_id=1, x=0.0, y=0.0),
                SensorPosition(sensor_id=2, x=1.0, y=0.0),
                SensorPosition(sensor_id=3, x=1.0, y=1.0),
                SensorPosition(sensor_id=4, x=0.0, y=1.0),
            ]
            self._ae_localizer = AELocalizer(sensors=sensors, sound_speed=343.0)
            self._pages["ae"].set_localizer(self._ae_localizer)
            logger.info("AE 声源定位器已初始化 (4 传感器, 正方形布局)")
        except Exception as e:
            logger.warning("AE 声源定位器初始化失败: %s", e)
            self._ae_localizer = None

    def _connect_ui(self) -> None:
        """连接 UI 信号"""
        bus = self._data_bus
        pages = self._pages

        # 波形 → WaveformWidget
        bus.waveform_received.connect(self._on_waveform_for_ui)

        # FFT → FFTWidget
        bus.fft_result_ready.connect(self._on_fft_for_ui)

        # PRPD → PRPDWidget
        bus.prpd_result_ready.connect(self._on_prpd_for_ui)

        # PRPS → PRPSWidget
        bus.prps_result_ready.connect(self._on_prps_for_ui)

        # AE 信号
        bus.ae_hit_detected.connect(self._on_ae_hit_for_ui)
        bus.ae_hit_detected.connect(self._on_ae_hit_for_trend)
        bus.ae_hit_detected.connect(self._on_ae_hit_for_analysis)
        bus.ae_envelope_ready.connect(self._on_ae_envelope_for_ui)

        # 报警 → AlarmPage
        bus.pd_alarm_triggered.connect(self._on_alarm_for_ui)

        # 设备状态
        bus.device_status_changed.connect(self._on_device_status_for_ui)

        # 连接报警服务到报警页面
        if "alarm" in pages:
            pages["alarm"].set_alarm_service(self._alarm)

        logger.debug("UI 信号连接完成")

    # ── 服务回调 ─────────────────────────────────────

    def _on_pd_event_for_alarm(self, device_id: str, channel_id: int, event: dict) -> None:
        """局放事件 → 报警检测"""
        if not self._alarm:
            return
        amplitude = event.get("amplitude", 0)
        alarm = self._alarm.check_amplitude(device_id, channel_id, amplitude)
        if alarm:
            self._alarm.trigger_alarm(alarm)

    def _on_device_status_for_alarm(self, device_id: str, status: int, status_data: dict) -> None:
        """设备状态 → 报警检测"""
        if not self._alarm or status == 1:
            return
        status_alarm = self._alarm.check_device_status(device_id, status_data)
        if status_alarm:
            self._alarm.trigger_alarm(status_alarm)

    def _on_pd_event_for_storage(self, device_id: str, channel_id: int, event: dict) -> None:
        """局放事件 → 存储"""
        if self._storage:
            self._storage.store_pd_event(device_id, channel_id, event)

    def _on_waveform_for_storage(self, device_id: str, channel_id: int, sensor_type: str, waveform: Any) -> None:
        """波形数据 → 存储（每 50 帧记录一次趋势）"""
        if self._storage and hasattr(self._acquisition, "_stats"):
            stats = self._acquisition.get_stats()
            if stats.get("waveforms_processed", 0) % 50 == 0:
                self._storage.store_trend_data(
                    device_id,
                    channel_id,
                    "1h",
                    {
                        "pd_count": stats.get("pd_events_detected", 0),
                        "max_amplitude": 0,
                        "avg_amplitude": 0,
                        "total_energy": 0,
                        "noise_level": 0,
                    },
                )

    # ── UI 回调 ──────────────────────────────────────

    def _on_waveform_for_ui(self, device_id: str, channel_id: int, sensor_type: str, waveform: Any) -> None:
        """波形数据 → UI 更新"""
        import numpy as np

        data = np.array(waveform.samples) if hasattr(waveform, "samples") else np.array(waveform)
        if len(data) < 2:
            return

        # 根据传感器类型选择采样率
        sample_rate = 2_000_000 if sensor_type == "ae" else 100_000_000

        # Monitor: 全功能波形控件
        if "monitor" in self._pages:
            try:
                self._pages["monitor"]._waveform.update_waveform(data, sample_rate=sample_rate)
                # AE 模式自动切换
                if sensor_type == "ae":
                    self._pages["monitor"].switch_to_ae_mode()
                    self._pages["monitor"]._waveform.set_y_unit("μV")
                else:
                    self._pages["monitor"].switch_to_uhf_mode()
                    self._pages["monitor"]._waveform.set_y_unit("mV")
            except Exception as e:
                logger.debug("异常: %s", e)

        # Dashboard: 仅更新快照（每 10 帧一次）
        if "dashboard" in self._pages:
            try:
                if getattr(self, "_waveform_frame_count", 0) % 10 == 0:
                    self._pages["dashboard"].update_waveform_snapshot(data, sample_rate)
                self._waveform_frame_count = getattr(self, "_waveform_frame_count", 0) + 1
            except Exception as e:
                logger.debug("异常: %s", e)

    def _on_fft_for_ui(self, device_id: str, channel_id: int, sensor_type: str, fft_result: Any) -> None:
        """FFT 结果 → UI"""
        pages = self._pages
        freqs = fft_result.frequencies
        mags = fft_result.magnitudes

        # Monitor: 全功能频谱控件
        if "monitor" in pages:
            try:
                peaks = [
                    {
                        "frequency": p.frequency,
                        "magnitude": p.magnitude,
                        "is_harmonic": p.is_harmonic,
                        "harmonic_order": p.harmonic_order,
                    }
                    for p in (fft_result.peaks or [])
                ]
                pages["monitor"]._fft.update_spectrum(
                    freqs,
                    mags,
                    peaks=peaks,
                    noise_floor=fft_result.noise_floor,
                    snr_db=fft_result.snr_db,
                )
                pages["monitor"].update_peak_list(peaks)
            except Exception as e:
                logger.debug("异常: %s", e)

        # Dashboard: 仅更新快照
        if "dashboard" in pages:
            try:
                pages["dashboard"].update_fft_snapshot(freqs, mags)
            except Exception as e:
                logger.debug("异常: %s", e)

    def _on_prpd_for_ui(self, device_id: str, channel_id: int, prpd_result: Any) -> None:
        """PRPD 结果 → UI"""
        pages = self._pages
        # Monitor: 全功能 PRPD 控件
        if "monitor" in pages:
            try:
                prpd_widget = pages["monitor"]._prpd
                # 同步处理器当前 max_amplitude 到控件，确保热力图 Y 轴范围与散点图一致
                effective_max = getattr(prpd_result, "max_amplitude", None)
                if effective_max is not None and effective_max > 0:
                    prpd_widget.set_max_amplitude(effective_max)

                # 先更新散点数据（热力图需要从中构建同源2D直方图）
                if hasattr(prpd_result, "events") and prpd_result.events:
                    evt_phases = [e.phase for e in prpd_result.events]
                    evt_amps = [e.amplitude for e in prpd_result.events]
                    evt_cycles = [getattr(e, "cycle", 0) for e in prpd_result.events]
                    pages["monitor"]._prpd.update_scatter(evt_phases, evt_amps, evt_cycles)

                prpd_widget.update_heatmap(prpd_result.matrix, 360, 256)
            except Exception:
                logger.exception("PRPD UI 更新失败")

        # Dashboard: 仅更新快照
        if "dashboard" in pages:
            try:
                pages["dashboard"].update_prpd_snapshot(prpd_result.matrix)
            except Exception as e:
                logger.debug("异常: %s", e)

    def _on_prps_for_ui(self, device_id: str, channel_id: int, prps_result: Any) -> None:
        """PRPS 结果 → UI"""
        if "monitor" in self._pages:
            try:
                # PRPS 数据应发到 PRPS 控件，而非 PRPD 控件
                # 如果 Monitor 页面有 _prps 控件，更新它
                if hasattr(self._pages["monitor"], "_prps") and self._pages["monitor"]._prps is not None:
                    self._pages["monitor"]._prps.update_from_prps_processor(
                        prps_result.matrix, prps_result.total_cycles
                    )
                # 如果没有独立 PRPS 控件，暂不更新（避免将512x360矩阵错误写入360x256的PRPD控件）
            except Exception:
                logger.exception("PRPS UI 更新失败")

    def _on_alarm_for_ui(self, device_id: str, alarm_dict: dict) -> None:
        """报警 → AlarmPage + Dashboard + 通知"""
        if "alarm" in self._pages:
            self._pages["alarm"].add_alarm(alarm_dict)
        # Dashboard 最新报警
        if "dashboard" in self._pages:
            try:
                desc = alarm_dict.get("description", "")
                if desc:
                    self._pages["dashboard"].set_latest_alarm(desc[:60])
            except Exception as e:
                logger.debug("异常: %s", e)

        # 系统通知（声音 + 托盘 + 任务栏闪烁）
        try:
            level = alarm_dict.get("level", "info")
            desc = alarm_dict.get("description", "局放报警触发")
            device = alarm_dict.get("device_id", device_id)
            parent = self.parent()
            if parent and hasattr(parent, "notify_alarm"):
                parent.notify_alarm(level, device, desc)
            else:
                # 回退：系统 beep
                from PySide6.QtWidgets import QApplication
                QApplication.beep()
        except Exception as e:
            logger.debug("报警提示异常: %s", e)

    def _on_ae_hit_for_ui(self, device_id: str, channel_id: int, ae_hit_dict: dict) -> None:
        """AE hit → Monitor AE 参数面板"""
        if "monitor" in self._pages:
            try:
                # 自动切换到 AE 模式
                self._pages["monitor"].switch_to_ae_mode()
                self._pages["monitor"].update_ae_hit(ae_hit_dict)
            except Exception:
                logger.exception("AE hit UI 更新失败")

    def _on_ae_envelope_for_ui(self, device_id: str, channel_id: int, envelope: object) -> None:
        """AE 包络波形 → UI"""
        import numpy as np

        data = np.array(envelope)
        if len(data) < 2:
            return

        if "monitor" in self._pages:
            try:
                self._pages["monitor"]._waveform.update_waveform(data, sample_rate=2_000_000)
            except Exception as e:
                logger.debug("异常: %s", e)

        if "dashboard" in self._pages:
            try:
                if getattr(self, "_waveform_frame_count", 0) % 10 == 0:
                    self._pages["dashboard"].update_waveform_snapshot(data, 2_000_000)
                self._waveform_frame_count = getattr(self, "_waveform_frame_count", 0) + 1
            except Exception as e:
                logger.debug("异常: %s", e)

    def _on_ae_hit_for_trend(self, device_id: str, channel_id: int, features: dict) -> None:
        """AE hit → 趋势页面"""
        from datetime import datetime

        now = datetime.now()
        if "trend" in self._pages:
            try:
                trend = self._pages["trend"]
                trend.ae_trend.update_series("AE Hit 速率", now, features.get("counts", 0))
                trend.ae_trend.update_series("AE 幅值", now, features.get("amplitude", 0))
                trend.ae_trend.update_series("AE 能量", now, features.get("marse_energy", 0))
            except Exception as e:
                logger.debug("异常: %s", e)

    def _on_ae_hit_for_analysis(self, device_id: str, channel_id: int, features: dict) -> None:
        """AE hit → AE 分析页面 + TDOA 定位"""
        if "ae" in self._pages:
            try:
                self._pages["ae"].new_hit(features)
            except Exception as e:
                logger.debug("异常: %s", e)

        # TDOA 定位 (每 10 个 hit 触发一次)
        if hasattr(self, "_ae_hit_count"):
            self._ae_hit_count += 1
        else:
            self._ae_hit_count = 1

        if self._ae_hit_count % 10 == 0 and hasattr(self, "_ae_localizer") and self._ae_localizer:
            try:
                # 模拟 TDOA: 基于声源位置 (0.5, 0.5) + 随机扰动
                import numpy as np
                source_x, source_y = 0.5, 0.5
                arrival_times = {}
                for s in self._ae_localizer._sensor_list:
                    dist = np.sqrt((source_x - s.x) ** 2 + (source_y - s.y) ** 2)
                    t_arrival = dist / self._ae_localizer._sound_speed + np.random.normal(0, 1e-7)
                    arrival_times[s.sensor_id] = t_arrival
                self._pages["ae"].update_localization(arrival_times)
            except Exception as e:
                logger.debug("异常: %s", e)

    def _on_device_status_for_ui(self, device_id: str, status: int, status_data: dict) -> None:
        """设备状态 → UI 更新"""
        try:
            main_window = self._main_window
            if main_window:
                online = sum(1 for d in status_data.values() if d == 1) if isinstance(status_data, dict) else 0
                if hasattr(main_window, "_lbl_online"):
                    main_window._lbl_online.setText(f"在线设备: {online}")
        except Exception as e:
            logger.debug("设备状态UI更新异常: %s", e)

    def _update_status_stats(self) -> None:
        """定时更新状态栏统计 + 仪表板指标"""
        try:
            main_window = self._main_window
            if main_window and hasattr(main_window, "_lbl_online"):
                # 状态栏在线设备数
                if "device" in self._pages:
                    count = self._pages["device"].online_device_count
                    main_window._lbl_online.setText(f"在线设备: {count}")

            # 仪表板指标卡片
            if "dashboard" in self._pages and self._acquisition:
                dashboard = self._pages["dashboard"]
                stats = self._acquisition.stats if hasattr(self._acquisition, "stats") else {}
                dashboard.update_metric("在线设备", f"{self._pages['device'].online_device_count} 台")
                dashboard.update_metric("局放总数", f"{stats.get('pd_events_detected', 0)} 次")
                dashboard.update_metric("报警总数", f"{stats.get('alarms_triggered', 0)}")
                dashboard.update_metric("今日最大幅值", f"{stats.get('max_amplitude_today', 0):.1f} mV")
                dashboard.update_metric("AE Hits", str(getattr(self, "_ae_hit_count", 0)))
        except Exception as e:
            logger.debug("状态统计更新异常: %s", e)

    # ── 模拟器 ───────────────────────────────────────

    def start_simulator(self, pd_type: str = "internal", fps: int = 20, sim_type: str = "uhf") -> None:
        """
        启动数据模拟器（用于测试和演示）

        Args:
            pd_type: UHF 局放类型 (corona/surface/internal/floating) 或 AE 突发类型 (burst/continuous/mixed)
            fps: 帧率
            sim_type: "uhf" 或 "ae"
        """
        if sim_type == "ae":
            from core.utils.ae_simulator import AESimulator

            sim = AESimulator(
                sample_rate=2_000_000,
                waveform_points=4096,
                burst_rate=5.0,
                noise_level=0.5,
            )
            sim.set_burst_type(pd_type)
            sim.set_device_info("AE-SIM-001", 1)

            # 设置通道耦合类型为 AE
            if self._acquisition:
                self._acquisition.set_channel_coupling_type("AE-SIM-001", 1, "ae")

            self._simulator = sim
        else:
            from core.utils.simulator import PDSimulator

            self._simulator = PDSimulator(
                sample_rate=100_000_000,
                waveform_points=4096,
                pulse_rate=50,
                noise_level=2.0,
            )
            self._simulator.set_pd_type(pd_type)

        # 模拟器 → 采集服务
        self._simulator.set_on_waveform_callback(
            lambda dev, ch, wf: self._acquisition.process_data_frame(dev, ch, wf)
        )

        self._simulator.start(interval_ms=1000 // fps)
        logger.info(
            "模拟器已启动: type=%s/%s, %d FPS",
            sim_type,
            pd_type,
            fps,
        )

    def stop_simulator(self) -> None:
        """停止模拟器"""
        if self._simulator:
            self._simulator.stop()
            self._simulator = None

    # ── 硬件连接 ────────────────────────────────────

    def start_hardware(self, configs: Optional[list] = None) -> bool:
        """
        启动硬件连接模式

        通过 HardwareManager 连接所有 FPGA 设备。
        如果未传入 configs，自动从 config/hardware_config.json 加载。

        Args:
            configs: 设备连接配置列表

        Returns:
            是否成功启动（至少一个设备连接）
        """
        if configs is None:
            configs = self._load_hardware_config()

        if not configs:
            logger.warning("无硬件设备配置，启动模拟器作为回退")
            self.start_simulator()
            return False

        from core.communication.hardware_manager import HardwareManager

        self._hardware = HardwareManager(
            acquisition_service=self._acquisition,
            parent=self,
        )

        for cfg in configs:
            self._hardware.add_device(cfg)

        n_connected = self._hardware.start_all()
        if n_connected == 0:
            logger.warning("所有设备连接失败，启动模拟器作为回退")
            self._hardware.shutdown()
            self._hardware = None
            self.start_simulator()
            return False

        logger.info("硬件连接模式已启动: %d/%d 设备在线", n_connected, len(configs))
        return True

    def _load_hardware_config(self) -> list:
        """
        加载硬件设备配置（优先从数据库，回退到 JSON 文件）

        Returns:
            设备连接配置列表
        """
        # 1. 优先从数据库加载
        if self._db_manager:
            try:
                from core.data.repository.device_repository import PDDeviceRepository
                from core.communication.hardware_config import DeviceConnectionConfig

                repo = PDDeviceRepository(self._db_manager)
                db_devices = repo.get_all_devices()
                if db_devices:
                    configs = []
                    for dev in db_devices:
                        ch_count = len(dev.channels) if dev.channels else dev.channel_count
                        coupling = dev.channels[0].coupling_type if dev.channels else "uhf"
                        configs.append(
                            DeviceConnectionConfig(
                                device_id=dev.id,
                                name=dev.name,
                                host=dev.host or "0.0.0.0",
                                udp_port=dev.udp_port,
                                channel_count=ch_count,
                                coupling_type=coupling,
                                sample_rate_hz=dev.sample_rate_hz or 100_000_000,
                            )
                        )
                    logger.info("从数据库加载 %d 个硬件设备配置", len(configs))
                    return configs
            except Exception as e:
                logger.debug("数据库硬件配置加载失败，回退到 JSON: %s", e)

        # 2. 回退到 JSON 文件
        return self._load_hardware_config_from_json()

    def _load_hardware_config_from_json(self) -> list:
        """
        从 config/hardware_config.json 加载硬件配置

        Returns:
            设备连接配置列表
        """
        import json
        from pathlib import Path

        config_path = Path(__file__).resolve().parent.parent / "config" / "hardware_config.json"
        if not config_path.exists():
            return []

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            from core.communication.hardware_config import DeviceConnectionConfig

            configs = []
            for item in data.get("devices", []):
                configs.append(
                    DeviceConnectionConfig(
                        device_id=item["device_id"],
                        name=item["name"],
                        host=item.get("host", "0.0.0.0"),
                        udp_port=item.get("udp_port", 6000),
                        channel_count=item.get("channel_count", 4),
                        coupling_type=item.get("coupling_type", "uhf"),
                        sample_rate_hz=item.get("sample_rate_hz", 100_000_000),
                        ae_frequency_min_hz=item.get("ae_frequency_min_hz"),
                        ae_frequency_max_hz=item.get("ae_frequency_max_hz"),
                    )
                )
            return configs
        except Exception as e:
            logger.error("硬件配置加载失败: %s", e)
            return []

    def load_channel_configs_from_db(self) -> None:
        """
        从数据库读取已保存的设备/通道配置，自动配置:
        - AcquisitionService.set_channel_coupling_type()
        - PDAlarmService 幅值阈值
        """
        if not self._db_manager or not self._acquisition or not self._alarm:
            return

        try:
            from core.data.repository.device_repository import PDDeviceRepository

            repo = PDDeviceRepository(self._db_manager)
            devices = repo.get_all_devices()

            if not devices:
                logger.debug("数据库无已保存的 PD 设备配置")
                return

            n_channels = 0
            for dev in devices:
                for ch in dev.channels or []:
                    if not ch.enabled:
                        continue

                    self._acquisition.set_channel_coupling_type(dev.id, ch.channel_index, ch.coupling_type)

                    # AE 通道设置独立的幅值阈值（AE 幅值通常远小于 UHF）
                    if ch.coupling_type == "ae":
                        self._alarm.set_channel_max_amplitude(dev.id, ch.channel_index, 100.0)

                    n_channels += 1

                # UHF 设备设置设备级阈值
                if dev.sample_rate_hz and dev.sample_rate_hz > 10_000_000:
                    self._alarm.set_amplitude_threshold(dev.id, max_amplitude=10000.0)

                logger.info(
                    "已加载设备 %s (%s): %d 通道",
                    dev.name,
                    dev.id,
                    len(dev.channels or []),
                )

            logger.info("通道配置加载完成: %d 设备, %d 通道", len(devices), n_channels)
        except Exception as e:
            logger.error("加载通道配置失败: %s", e)

    # ── 生命周期 ─────────────────────────────────────

    def shutdown(self) -> None:
        """关闭所有服务"""
        self._stats_timer.stop()
        self.stop_simulator()
        if self._hardware:
            self._hardware.shutdown()
            self._hardware = None
        if self._acquisition:
            self._acquisition.stop()
        if self._storage:
            self._storage.stop()
        if self._alarm:
            self._alarm.shutdown()
        self._data_bus.shutdown()
        logger.info("PDSystemController 已关闭")
