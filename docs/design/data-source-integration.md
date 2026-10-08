# 数据源集成方案 — 软硬件一体化设计

> 版本: v2.0 | 日期: 2026-06-16 | 状态: 设计阶段，待实施
> 合并自: `docs/data-source-integration.md` v1.0 + `docs/design/data-source-integration.md` v1.0

---

## 1. 设计目标

将当前分离的**模拟器模式**（`--simulator`）和**硬件模式**（`start_hardware()` 仅编程可调用）合并为统一的**数据源系统**，实现：

- **智能默认**：`python pd_main.py` 自动探测硬件，无硬件时自动回退到模拟器，确保仪表盘始终有数据
- **运行时切换**：UI 界面可添加/移除/启动/停止数据源，无需重启程序
- **多源共存**：多个硬件设备 + 模拟器（UHF + AE）同时运行
- **配置持久化**：硬件配置保存到 SQLite 和配置 JSON，启动时自动恢复
- **向后兼容**：所有现有 CLI 参数继续生效

---

## 2. 现状分析

### 2.1 当前两条数据路径

```
Simulator (--simulator)              Hardware (无 CLI 入口)
    │                                      │
    │ PDSimulator / AESimulator            │ UDPDriver + FpgaProtocol
    │ (in-process, callback-based)         │ (UDP socket, binary protocol)
    │                                      │
    └──────────┬───────────────┘
               │
               ▼
    AcquisitionService.process_data_frame()
               │
               ├─ _process_uhf_waveform()
               └─ _process_ae_waveform()
               │
               ▼
           PDDataBus
               │
               ▼
            UI Pages
```

### 2.2 现存问题

| 问题 | 严重度 | 说明 |
|------|--------|------|
| 无默认数据源 | **高** | `python pd_main.py`（无参数）启动后无任何数据，仪表盘全零 |
| `start_hardware()` 不可达 | **高** | 从未从 `main.py` 调用，仅在 `pd_controller.py` 中定义 |
| 硬件配置孤立 | **中** | `config/pd_hardware_config.json` 存在但无代码读取 |
| 设备页与硬件管理器脱节 | **中** | `DevicePage._devices` 和 `HardwareManager._devices` 互不感知 |
| 无运行时切换 | **中** | 模拟器/硬件一旦启动，不能在不重启程序的情况下切换 |
| 无健康检测 | **低** | `HardwareManager` 无定期 ping/心跳检测 |

### 2.3 已有可复用组件

| 组件 | 位置 | 说明 |
|------|------|------|
| `PDSimulator` | `core/utils/pd_simulator.py` | UHF 模拟数据生成 |
| `AESimulator` | `core/utils/ae_simulator.py` | AE 模拟数据生成 |
| `HardwareManager` | `core/communication/hardware_manager.py` | FPGA 设备连接管理 |
| `DeviceConnectionConfig` | `core/communication/hardware_config.py` | 设备连接配置 dataclass |
| `FpgaProtocol` | `core/communication/fpga_protocol.py` | FPGA 二进制协议解析 |
| `UDPDriver` | `core/communication/udp_driver.py` | UDP 数据通道 |
| `PDDeviceModel` | `core/data/pd_models.py` | 设备数据库模型 |
| `PDDeviceRepository` | `core/data/repository/pd_device_repository.py` | 设备数据仓库 |
| `AcquisitionService` | `core/services/pd_acquisition_service.py` | 数据处理入口（已汇聚） |
| `PDDataBus` | `core/foundation/pd_data_bus.py` | 事件总线 |
| `SensorType` | `core/foundation/sensor_types.py` | 传感器类型枚举 |

---

## 3. 总体架构设计

### 3.1 新增核心组件

```
                    DataSourceManager
                    ┌───────────────────────────┐
                    │  _sources: Dict[str,      │
                    │    DataSource]             │
                    │  auto_detect()             │
                    │  add_source()              │
                    │  remove_source()           │
                    │  start_all() / stop_all()  │
                    │  get_status()              │
                    └───────────┬───────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
    HardwareSource         SimulatorSource     (将来: FileSource)
    ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
    │ UDPDriver    │     │ PDSimulator  │     │ CSV/Data     │
    │ FpgaProtocol │     │ AESimulator  │     │ File Importer│
    │ HealthCheck  │     │ Configurable │     │ Playback     │
    └──────────────┘     └──────────────┘     └──────────────┘
```

### 3.2 与现有系统的关系

```
   pd_main.py (启动策略)
        │
        ▼
   PDSystemController
        ├── DataSourceManager  ←── 新增：管理所有数据源
        │       ├── HardwareSource (包装原 HardwareManager 单设备能力)
        │       ├── SimulatorSource (包装原 PDSimulator/AESimulator)
        │       └── (可扩展 FileSource)
        │
        ├── AcquisitionService (不变：统一数据入口)
        ├── PDAlarmService (不变)
        ├── PDStorageService (不变)
        └── PDDataBus (不变 + 信号增加 source_id)
              │
              ▼
           UI Pages (DevicePage 增强为数据源管理)
```

### 3.3 DataSource 抽象层

```python
# 文件: core/foundation/data_source.py

from enum import Enum, auto
from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class DataSourceType(Enum):
    """数据源类型"""
    HARDWARE = "hardware"   # FPGA 实际硬件
    SIMULATOR = "simulator" # 内置模拟器
    FILE = "file"           # 文件回放 (未来)


class DataSourceStatus(Enum):
    """数据源运行状态"""
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"
    RECONNECTING = "reconnecting"


class DataSource(ABC):
    """数据源抽象基类 — 所有数据源遵循的统一接口"""

    id: str                          # 唯一标识
    name: str                        # 显示名称
    source_type: DataSourceType      # 类型
    sensor_type: str                 # "uhf" / "ae"
    status: DataSourceStatus         # 当前状态

    @abstractmethod
    def start(self) -> bool:
        """启动数据源。返回 True 表示启动成功。"""
        ...

    @abstractmethod
    def stop(self) -> None:
        """停止数据源，释放资源。可安全重复调用。"""
        ...

    @abstractmethod
    def get_status(self) -> dict:
        """获取详细状态信息（用于 UI 显示）"""
        ...

    @abstractmethod
    def get_stats(self) -> dict:
        """获取运行统计（包数、错误数、吞吐量等）"""
        ...
```

### 3.4 PDSystemController 改造

```python
class PDSystemController(QObject):
    def __init__(self, db_manager=None, parent=None):
        super().__init__(parent)
        # ... 现有初始化代码 ...
        self._source_manager = DataSourceManager(
            acquisition_service=self._acquisition,
            data_bus=self._data_bus,
        )

    # 旧方法保留（委托到 DataSourceManager，向后兼容）
    def start_simulator(self, pd_type="internal", fps=20, sim_type="uhf") -> None:
        self._source_manager.add_simulator_source(sim_type, pd_type, fps)
        self._source_manager.start_all()

    def start_hardware(self, configs=None) -> bool:
        if configs is None:
            configs = self._load_hardware_config()
        for cfg in configs:
            self._source_manager.add_hardware_source(cfg)
        return self._source_manager.start_all() > 0

    # 新统一接口
    @property
    def source_manager(self) -> "DataSourceManager":
        return self._source_manager
```

---

## 4. 详细设计

### 4.1 启动策略

#### 4.1.1 CLI 参数

```python
# pd_main.py

parser.add_argument("--simulator", action="store_true",
    help="强制使用模拟器模式（默认: 自动探测硬件")")

parser.add_argument("--hardware", action="store_true",
    help="强制使用硬件模式（无硬件时退出码非零）")

parser.add_argument("--sim-type", choices=["uhf", "ae"], default="uhf",
    help="模拟器类型（仅 --simulator 时有效）")

parser.add_argument("--pd-type",
    choices=["corona", "surface", "internal", "floating"], default="internal",
    help="局放类型（仅 --simulator 时有效）")

parser.add_argument("--fps", type=int, default=20,
    help="模拟器帧率（仅 --simulator 时有效）")
```

#### 4.1.2 启动策略逻辑

```python
def _determine_startup_strategy(args) -> dict:
    """确定启动策略。优先级: CLI > UI配置 > 默认自动探测"""
    if args.simulator:
        return {
            "mode": "simulator_only",
            "sim_type": args.sim_type,
            "pd_type": args.pd_type,
            "fps": args.fps,
        }
    if args.hardware:
        return {"mode": "hardware_only"}
    return {"mode": "auto_detect"}


def main() -> int:
    args = parse_args()
    strategy = _determine_startup_strategy(args)

    # ... 初始化日志/数据库/QApp/主题/窗口/控制器 ...

    controller = PDSystemController(db_manager=db_manager, parent=window)
    controller.initialize(pages=window.pages)
    window.set_controller(controller)

    # ── 启动数据源 ──
    if strategy["mode"] == "simulator_only":
        controller.start_simulator(
            sim_type=strategy["sim_type"],
            pd_type=strategy["pd_type"],
            fps=strategy["fps"],
        )
    elif strategy["mode"] == "hardware_only":
        if not controller.start_hardware():
            logger.critical("--hardware 模式：无可用硬件设备")
            return 1  # 退出码非零
    else:  # auto_detect
        controller.source_manager.auto_detect()

    window.show()
    exit_code = app.exec()
    controller.shutdown()
    return exit_code
```

#### 4.1.3 配置合并优先级

```
命令行参数 (最高优先级)
    ↓ 覆盖
设置页面 UI 配置 (次高)
    ↓ 覆盖
config/pd_default_config.json (中等)
    ↓ 覆盖
代码硬编码默认值 (最低)
```

---

### 4.2 DataSourceManager 设计

#### 4.2.1 核心接口

```python
# 文件: core/foundation/data_source_manager.py

class DataSourceManager(QObject):
    """数据源管理器 — 管理所有数据源的生命周期与状态"""

    # === 信号 ===
    source_added = Signal(str)                     # source_id
    source_removed = Signal(str)                   # source_id
    source_status_changed = Signal(str, str)       # source_id, status
    source_error = Signal(str, str)                # source_id, error_msg
    active_source_changed = Signal(str)            # source_id (UI 显示切换)

    def __init__(self, acquisition_service, data_bus):
        self._sources: Dict[str, DataSource] = {}
        self._active_source_id: Optional[str] = None  # UI 当前显示的数据源
        self._acquisition = acquisition_service
        self._data_bus = data_bus
        self._lock = threading.RLock()  # 线程安全

    # === 生命周期 ===

    def auto_detect(self) -> bool:
        """智能检测: 数据库设备 → UDP广播发现 → 回退模拟器"""
        with self._lock:
            # 1. 从数据库加载已保存的设备配置
            db_devices = self._load_devices_from_db()
            for cfg in db_devices:
                self.add_hardware_source(cfg)

            started = self.start_all()
            if started > 0:
                return True

            # 2. 尝试 UDP 广播发现
            discovered = self._discover_hardware()
            for cfg in discovered:
                self.add_hardware_source(cfg)

            started = self.start_all()
            if started > 0:
                return True

            # 3. 回退: 启动模拟器
            logger.info("无可用硬件设备，自动启动模拟器")
            self.add_simulator_source("uhf", "internal", 20)
            self.start_all()
            return True

    def start_all(self) -> int:
        """启动所有已停止的数据源"""
        started = 0
        for source in self._sources.values():
            if source.status == DataSourceStatus.STOPPED:
                if source.start():
                    started += 1
                    self.source_status_changed.emit(source.id, "running")
                    if self._active_source_id is None:
                        self._active_source_id = source.id
                        self.active_source_changed.emit(source.id)
        return started

    def stop_all(self) -> None:
        """停止所有数据源"""
        for source in self._sources.values():
            source.stop()
            self.source_status_changed.emit(source.id, "stopped")
        self._active_source_id = None

    # === 数据源管理 ===

    def add_hardware_source(self, config: "DeviceConnectionConfig") -> str:
        """添加硬件数据源"""
        with self._lock:
            if config.device_id in self._sources:
                return config.device_id
            source = HardwareSource(config, self._acquisition, self._data_bus)
            self._sources[config.device_id] = source
            self.source_added.emit(config.device_id)
            return config.device_id

    def add_simulator_source(self, sim_type="uhf", pd_type="internal",
                              fps=20) -> str:
        """添加模拟器数据源"""
        with self._lock:
            source_id = f"sim-{sim_type}-{len(self._sources)}"
            source = SimulatorSource(
                source_id, sim_type, pd_type, fps,
                self._acquisition, self._data_bus,
            )
            self._sources[source_id] = source
            self.source_added.emit(source_id)
            return source_id

    def remove_source(self, source_id: str) -> bool:
        """移除数据源"""
        with self._lock:
            source = self._sources.pop(source_id, None)
            if not source:
                return False
            source.stop()
            if self._active_source_id == source_id:
                # 切换到下一个可用数据源
                self._active_source_id = next(
                    iter(self._sources), None)
                if self._active_source_id:
                    self.active_source_changed.emit(self._active_source_id)
            self.source_removed.emit(source_id)
            return True

    # === 查询 ===

    def get_sources(self) -> List[DataSource]:
        return list(self._sources.values())

    def get_source(self, source_id: str) -> Optional[DataSource]:
        return self._sources.get(source_id)

    def get_all_status(self) -> Dict[str, dict]:
        return {sid: src.get_status() for sid, src in self._sources.items()}

    def set_active_source(self, source_id: str) -> bool:
        """切换 UI 当前显示的数据源"""
        if source_id in self._sources:
            self._active_source_id = source_id
            self.active_source_changed.emit(source_id)
            return True
        return False

    @property
    def active_source(self) -> Optional[DataSource]:
        if self._active_source_id:
            return self._sources.get(self._active_source_id)
        return None

    # === 硬件发现 ===

    def _discover_hardware(self) -> list:
        """UDP 广播发现局域网内 FPGA 设备

        协议:
          1. 发送广播包: {"cmd": "discover", "port": 7001}
          2. 设备响应: {"device_id", "host", "port", "type", "channels"}
          3. 等待 3 秒收集所有响应
        """
        # TODO: 实现 UDP 广播发现
        return []

    def _load_devices_from_db(self) -> list:
        """从 SQLite 加载已保存的设备配置"""
        try:
            from core.data.repository.pd_device_repository import PDDeviceRepository
            from core.communication.hardware_config import DeviceConnectionConfig

            repo = PDDeviceRepository(self._acquisition._db_manager)
            devices = repo.get_all_devices()
            configs = []
            for dev in devices:
                configs.append(DeviceConnectionConfig(
                    device_id=dev.id,
                    name=dev.name,
                    host=dev.host or "0.0.0.0",
                    udp_port=dev.udp_port or 6000,
                    channel_count=dev.channel_count or 4,
                    coupling_type="ae" if dev.sample_rate_hz and dev.sample_rate_hz < 10_000_000 else "uhf",
                    sample_rate_hz=dev.sample_rate_hz or 100_000_000,
                ))
            return configs
        except Exception:
            return []
```

### 4.3 HardwareSource 设计

```python
# 文件: core/communication/hardware_source.py

class HardwareSource(DataSource):
    """FPGA 硬件数据源 — 封装单设备的 UDP 连接 + 协议解析 + 健康检测"""

    def __init__(self, config: "DeviceConnectionConfig",
                 acquisition, data_bus):
        self._config = config
        self._acquisition = acquisition
        self._data_bus = data_bus
        self._driver: Optional[UDPDriver] = None
        self._protocol = FpgaProtocol()
        self._status = DataSourceStatus.STOPPED
        self._health_timer: Optional[QTimer] = None
        self._reconnect_count = 0
        self._max_reconnect = 3
        self._stats = {"packets": 0, "errors": 0, "bytes": 0, "reconnects": 0}

        # 预设通道耦合类型
        for ch_idx in range(config.channel_count):
            coupling = config.get_channel_config(ch_idx).coupling_type
            acquisition.set_channel_coupling_type(
                config.device_id, ch_idx, coupling)

    @property
    def id(self) -> str:
        return self._config.device_id

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.HARDWARE

    @property
    def sensor_type(self) -> str:
        return self._config.coupling_type

    @property
    def status(self) -> DataSourceStatus:
        return self._status

    def start(self) -> bool:
        """启动 UDP 连接"""
        if self._status == DataSourceStatus.RUNNING:
            return True

        buffer_size = 16384 if self._config.coupling_type == "ae" else 65536
        self._driver = UDPDriver(
            host=self._config.host,
            port=self._config.udp_port,
            buffer_size=buffer_size,
        )

        # 信号连接
        self._driver.on_data_callback = self._on_data_received
        self._driver.connected.connect(self._on_connected)
        self._driver.disconnected.connect(self._on_disconnected)
        self._driver.error_occurred.connect(self._on_error)

        if not self._driver.connect():
            self._status = DataSourceStatus.ERROR
            return False

        # 健康检测定时器（10 秒间隔）
        self._health_timer = QTimer()
        self._health_timer.timeout.connect(self._health_check)
        self._health_timer.start(10000)

        self._status = DataSourceStatus.RUNNING
        return True

    def stop(self) -> None:
        if self._health_timer:
            self._health_timer.stop()
            self._health_timer = None
        if self._driver:
            self._driver.disconnect()
            self._driver = None
        self._status = DataSourceStatus.STOPPED
        self._reconnect_count = 0

    def get_status(self) -> dict:
        return {
            "id": self._config.device_id,
            "name": self._config.name,
            "type": "hardware",
            "sensor": self._config.coupling_type,
            "host": self._config.host,
            "port": self._config.udp_port,
            "status": self._status.value,
            "channels": self._config.channel_count,
            "packets": self._stats["packets"],
            "errors": self._stats["errors"],
            "reconnects": self._stats["reconnects"],
        }

    def get_stats(self) -> dict:
        return dict(self._stats)

    def _on_data_received(self, data: bytes) -> None:
        """UDP 数据 → FpgaProtocol 解析 → AcquisitionService"""
        try:
            frames = self._protocol.feed(data)
            self._stats["packets"] += 1
            self._stats["bytes"] += len(data)
            for frame in frames:
                self._acquisition.process_data_frame(
                    self._config.device_id, frame.channel_id, frame.data)
        except Exception as e:
            self._stats["errors"] += 1
            logger.error("HardwareSource 数据解析失败: %s", e)

    def _health_check(self) -> None:
        """定期健康检测"""
        if self._driver and not self._driver.is_connected:
            self._status = DataSourceStatus.ERROR
            self.source_error.emit(self._config.device_id, "连接断开")
            self._attempt_reconnect()

    def _attempt_reconnect(self) -> None:
        """指数退避重连（最多 3 次）"""
        if self._reconnect_count >= self._max_reconnect:
            logger.error("硬件数据源重连失败: %s (已达上限)", self._config.device_id)
            return

        self._reconnect_count += 1
        delay = min(2 ** self._reconnect_count, 10)  # 2s, 4s, 8s
        self._status = DataSourceStatus.RECONNECTING
        logger.info("重连硬件 %s (%d/%d) 等待 %ds",
                    self._config.device_id, self._reconnect_count,
                    self._max_reconnect, delay)

        QTimer.singleShot(delay * 1000, self._do_reconnect)

    def _do_reconnect(self) -> None:
        if self._driver and self._driver.connect():
            self._status = DataSourceStatus.RUNNING
            self._reconnect_count = 0
            self._stats["reconnects"] += 1
            logger.info("硬件重连成功: %s", self._config.device_id)
```

### 4.4 SimulatorSource 设计

```python
# 文件: core/utils/simulator_source.py

class SimulatorSource(DataSource):
    """模拟器数据源 — 包装 PDSimulator / AESimulator"""

    def __init__(self, source_id: str, sim_type: str, pd_type: str,
                 fps: int, acquisition, data_bus):
        self._source_id = source_id
        self._sim_type = sim_type
        self._pd_type = pd_type
        self._fps = fps
        self._acquisition = acquisition
        self._data_bus = data_bus
        self._simulator = None
        self._status = DataSourceStatus.STOPPED
        self._generated = 0

    @property
    def id(self) -> str: return self._source_id
    @property
    def name(self) -> str:
        return f"模拟器-{'UHF' if self._sim_type == 'uhf' else 'AE'}"
    @property
    def source_type(self) -> DataSourceType: return DataSourceType.SIMULATOR
    @property
    def sensor_type(self) -> str: return self._sim_type
    @property
    def status(self) -> DataSourceStatus: return self._status

    def start(self) -> bool:
        device_id = self._source_id
        channel_id = 1

        if self._sim_type == "ae":
            from core.utils.ae_simulator import AESimulator
            sim = AESimulator(
                sample_rate=2_000_000, waveform_points=4096,
                burst_rate=5.0, noise_level=0.5,
            )
            sim.set_burst_type(self._pd_type)
            sim.set_device_info(device_id, channel_id)
            self._acquisition.set_channel_coupling_type(device_id, channel_id, "ae")
        else:
            from core.utils.pd_simulator import PDSimulator
            sim = PDSimulator(
                sample_rate=100_000_000, waveform_points=4096,
                pulse_rate=50, noise_level=2.0,
            )
            sim.set_pd_type(self._pd_type)

        sim.set_on_waveform_callback(
            lambda dev, ch, wf: self._acquisition.process_data_frame(dev, ch, wf))
        sim.start(interval_ms=1000 // self._fps)
        self._simulator = sim
        self._status = DataSourceStatus.RUNNING
        return True

    def stop(self) -> None:
        if self._simulator:
            self._simulator.stop()
            self._simulator = None
        self._status = DataSourceStatus.STOPPED

    def get_status(self) -> dict:
        return {
            "id": self._source_id,
            "name": self.name,
            "type": "simulator",
            "sensor": self._sim_type,
            "pd_type": self._pd_type,
            "fps": self._fps,
            "status": self._status.value,
            "generated": self._generated,
        }

    def get_stats(self) -> dict:
        return {"generated": self._generated}

    def set_pd_type(self, pd_type: str) -> None:
        """运行时更改局放类型（无需重启模拟器）"""
        self._pd_type = pd_type
        if self._simulator:
            if hasattr(self._simulator, "set_pd_type"):
                self._simulator.set_pd_type(pd_type)
            elif hasattr(self._simulator, "set_burst_type"):
                self._simulator.set_burst_type(pd_type)
```

---

### 4.5 UI 设计

#### 4.5.1 设备页面 → 数据源管理页面

**`ui/pages/device_page.py`** 重构为数据源管理页面：

```
┌──────────────────────────────────────────────────────────┐
│  数据源管理              [+ 添加硬件]  [+ 启动模拟器]      │
├────────────────────────┬─────────────────────────────────┤
│  ● 运行中 │ 4ch        │  名称: UHF-DEV-001               │
│  UHF-DEV-001           │  ▶ 类型: FPGA硬件 · UHF超高频    │
│  │ 192.168.1.101:6000  │  Host: 192.168.1.101            │
│  │ 12,845 pkt/s        │  Port: 6000 · 4通道              │
│  ├──────────────────────┤  状态: ● 运行中                  │
│  │ ○ 已停止 │ 4ch       │  包数: 12,845 | 错误: 0         │
│  │ AE-DEV-001           │                                 │
│  │ 192.168.1.102:7000  │  [▶ 启动]  [✎ 编辑]  [✕ 移除]   │
│  ├──────────────────────┤                                 │
│  │ ● 运行中             │  ── 数据源统计 ──                │
│  │ 模拟器-UHF           │  总数据源: 3 | 运行中: 2         │
│  │ internal | 20 FPS    │  总吞吐: 1245 pkt/s             │
└────────────────────────┴─────────────────────────────────┘
```

关键交互：
- 左侧列表：显示所有数据源，带状态指示圆点
- 右侧详情：选中数据源的详细信息和操作按钮
- 顶部按钮：添加硬件（弹出对话框）、启动模拟器（下拉选择 UHF/AE）
- 当前激活的数据源高亮显示（UI 实时监测页展示其数据）

#### 4.5.2 添加硬件对话框

```
┌── 添加硬件数据源 ──────────────────────┐
│                                        │
│  设备名称: [________________]          │
│  主机地址: [________________]          │
│  UDP 端口: [6000    ▾]                │
│  传感器类型: ○ UHF  ● AE              │
│  通道数:    [4       ▾]               │
│                                        │
│          [取消]   [添加并连接]          │
└────────────────────────────────────────┘
```

#### 4.5.3 设置页面 — 数据源默认策略

```
┌─ 数据源默认策略 ─────────────────────────────────┐
│                                                    │
│  启动时默认行为:                                    │
│  ● 自动探测硬件（无硬件时启动模拟器）               │
│  ○ 始终启动模拟器                                  │
│  ○ 仅硬件模式（无硬件时不启动）                    │
│                                                    │
│  UDP 广播发现端口: [7001     ]                     │
│  发现超时(秒):     [3        ]                     │
│                                                    │
│  已保存的硬件设备:                                  │
│  │ UHF-DEV-001  192.168.1.101:6000  4ch  uhf  │    │
│  │ AE-DEV-001   192.168.1.102:7000  4ch  ae   │    │
│  │                                        │    │    │
│  [+ 添加] [导入配置] [导出配置]                   │
└────────────────────────────────────────────────────┘
```

#### 4.5.4 状态栏数据源指示器

```python
# pd_main_window.py 状态栏新增

# 数据源状态指示（状态栏左侧）
self._source_indicator = QLabel("●")
self._source_indicator.setFixedSize(16, 16)
status.addWidget(self._source_indicator)

self._source_label = QLabel("数据源: UHF-DEV-001")
self._source_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: 12px;")
status.addWidget(self._source_label)

def update_source_status(self, source_id: str, status: str, sensor: str):
    colors = {"running": "#4CAF50", "stopped": "#9CA3AF",
              "error": "#CF222E", "reconnecting": "#D29922"}
    self._source_indicator.setStyleSheet(
        f"color: {colors.get(status, '#9CA3AF')}; font-weight: bold;")
    self._source_label.setText(f"数据源: {source_id}")
```

#### 4.5.5 实时监测页数据源选择器

当多数据源运行时，用户需要选择查看哪个数据源的实时数据：

```
┌── 实时监测 ────────────────────────────────────┐
│                                  数据源: [▾]    │
│  ┌───────────────────────────────────────────┐  │
│  │ 波形图 (显示当前选中数据源)                │  │
│  └───────────────────────────────────────────┘  │
│  ┌─────┐ ┌─────┐ ┌─────┐                      │
│  │PRPD │ │ FFT │ │峰值 │                      │
│  └─────┘ └─────┘ └─────┘                      │
└──────────────────────────────────────────────────┘
```

---

### 4.6 PDDataBus 信号扩展

多源共存时，需要区分数据来自哪个数据源。现有信号签名不变，但内部加入 `source_id` 追踪：

```python
# PDDataBus 现有信号 (不修改签名，保持向后兼容)
# waveform_received(str, int, str, Any)  → (device_id, channel_id, sensor_type, data)
# pd_event_detected(str, int, dict)       → (device_id, channel_id, event)

# 新增信号 (用于多源场景)
source_attached = Signal(str)       # source_id (新数据源接入总线)
source_detached = Signal(str)       # source_id (数据源移除)
active_source = Signal(str)         # source_id (UI 切换激活的数据源)
```

数据流中 `device_id` 天然携带来源信息：硬件设备用 `UHF-DEV-001`，模拟器用 `sim-uhf-0`，无需额外字段。

---

### 4.7 配置持久化

**`config/pd_default_config.json`** 新增 `data_sources` 段：

```json
{
    "data_sources": {
        "startup_policy": "auto_detect",
        "discovery_port": 7001,
        "discovery_timeout_s": 3,
        "saved_devices": [
            {
                "device_id": "UHF-DEV-001",
                "name": "超高频采集-01",
                "host": "192.168.1.101",
                "udp_port": 6000,
                "channel_count": 4,
                "coupling_type": "uhf",
                "sample_rate_hz": 100000000
            },
            {
                "device_id": "AE-DEV-001",
                "name": "超声波采集-01",
                "host": "192.168.1.102",
                "udp_port": 7000,
                "channel_count": 4,
                "coupling_type": "ae",
                "sample_rate_hz": 2000000
            }
        ]
    }
}
```

配置管理类：

```python
# core/foundation/data_source_config.py

@dataclass
class StartupPolicy:
    mode: str = "auto_detect"  # "auto_detect" | "simulator_only" | "hardware_only"
    discovery_port: int = 7001
    discovery_timeout_s: int = 3

@dataclass
class SavedDevice:
    device_id: str = ""
    name: str = ""
    host: str = ""
    udp_port: int = 6000
    channel_count: int = 4
    coupling_type: str = "uhf"
    sample_rate_hz: int = 100_000_000

@dataclass
class DataSourceConfig:
    startup_policy: StartupPolicy = field(default_factory=StartupPolicy)
    saved_devices: List[SavedDevice] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "startup_policy": asdict(self.startup_policy),
            "saved_devices": [asdict(d) for d in self.saved_devices],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DataSourceConfig":
        sp_data = data.get("startup_policy", {})
        devices_data = data.get("saved_devices", [])
        return cls(
            startup_policy=StartupPolicy(**sp_data),
            saved_devices=[SavedDevice(**d) for d in devices_data],
        )
```

---

## 5. 设计审核 — 关键问题与决策

### 5.1 错误恢复流程

**问题**: 硬件运行中断线后如何处理？

**决策**: 二级恢复策略:
1. **本地重连**: `HardwareSource` 内置指数退避重连（最多 3 次，2s/4s/8s）
2. **系统回退**: 若所有硬件数据源均失败，提示用户是否需要启动模拟器保障数据不中断
3. **用户通知**: 每次状态变更通过信号通知 UI（状态栏变色 + 托盘通知）

### 5.2 多源 UI 路由

**问题**: 多个数据源同时运行，实时监测页显示谁的数据？

**决策**:
- 任何时候只有一个"激活数据源"，其数据显示在实时监测页
- 数据源管理页和实时监测页均可切换激活源（通过点击或下拉选择）
- 非激活源的数据仍然被采集和处理（报警、存储），但不显示在实时监测波形/PRPD/FFT
- 仪表板聚合显示所有数据源的统计摘要，悬停显示详细信息

### 5.3 线程安全

**问题**: `DataSourceManager` 可能被 UI 线程和模拟器线程同时访问。

**决策**:
- `DataSourceManager` 所有公开方法使用 `threading.RLock()` 保护
- `add_source()` / `remove_source()` / `start_all()` / `stop_all()` 互斥
- `DataSource` 的 `start()`/`stop()` 由管理器统一调度，避免竞态

### 5.4 `--hardware` 失败行为

**问题**: `--hardware` 模式但无硬件响应时，应该退出还是等待？

**决策**:
- `--hardware` 是"严格硬件模式"，无可用硬件时立即退出，返回码 1
- 适合生产环境脚本（CI/CD 监控系统），确保不会误用模拟数据
- 自动探测模式（默认）则静默回退到模拟器

### 5.5 同一类型多模拟器

**问题**: 能否同时运行 UHF 模拟器和 AE 模拟器？

**决策**: 支持。`SimulatorSource` 实例之间完全独立（独立线程、独立设备 ID）。用户可以：
- 启动 UHF 模拟器 + AE 模拟器 对比两种数据
- 启动多个同类型模拟器（不同参数）对比
- 硬件 + 模拟器并行

### 5.6 配置合并优先级

```
命令行参数 (--simulator 等)  →  最高优先级，覆盖一切
UI 设置页配置               →  次高，持久化到 JSON
config/pd_default_config.json →  基础默认值
代码硬编码                   →  最终回退
```

### 5.7 为什么保留命令行参数？

- **自动化测试**: CI/CD 中可通过命令行快速启动特定模式
- **批量部署**: 可通过脚本批量部署多台设备
- **调试便利**: 开发时可快速指定参数而不需改配置

### 5.8 为什么热切换而非重启？

- 切换数据源时仅重启数据源管理器，不重启整个 QApplication
- 避免丢失当前会话数据、图表状态、报警记录
- 流程: 停止旧数据源 → 清理资源 → 初始化新数据源 → 启动

---

## 6. 实施路线

### Phase 1: 抽象层 + DataSourceManager（3-5天）

| 任务 | 文件 | 说明 |
|------|------|------|
| 创建 `DataSource` 抽象基类 + 枚举 | `core/foundation/data_source.py` | 定义统一接口 |
| 创建 `DataSourceConfig` 配置类 | `core/foundation/data_source_config.py` | 配置序列化 |
| 实现 `HardwareSource` | `core/communication/hardware_source.py` | 封装单设备硬件连接 |
| 实现 `SimulatorSource` | `core/utils/simulator_source.py` | 封装模拟器 |
| 实现 `DataSourceManager` | `core/foundation/data_source_manager.py` | 管理器核心 |
| 单元测试 | `tests/test_data_source.py` | 覆盖生命周期 |

### Phase 2: Controller 集成（1-2天）

| 任务 | 文件 | 说明 |
|------|------|------|
| `PDSystemController` 集成 `DataSourceManager` | `ui/pd_controller.py` | 替换旧字段 |
| 旧方法委托 | `ui/pd_controller.py` | 向后兼容 |
| 启动策略 + CLI 解析 | `pd_main.py` | 合并配置 |
| `--hardware` CLI 参数 | `pd_main.py` | 新增严格模式 |

### Phase 3: UI 数据源管理（3-4天）

| 任务 | 文件 | 说明 |
|------|------|------|
| 设备页 → 数据源管理页 | `ui/pages/device_page.py` | 重构 |
| 添加硬件对话框 | `ui/dialogs/add_device_dialog.py` | 新建 |
| 数据源状态定时器 | `ui/pd_controller.py` | 定时刷新 |
| 状态栏指示器 | `ui/pd_main_window.py` | 数据源状态 |
| 实时监测页源选择器 | `ui/pages/realtime_monitor_page.py` | 多源切换 |

### Phase 4: 配置持久化（1天）

| 任务 | 文件 | 说明 |
|------|------|------|
| 默认配置更新 | `config/pd_default_config.json` | 添加 data_sources 段 |
| 设置页集成 | `ui/pages/settings_page.py` | 策略选择 |
| 设备 DB 同步 | `core/data/pd_models.py` | 字段完善 |

### Phase 5: 集成测试 + 文档（1-2天）

| 任务 | 说明 |
|------|------|
| 端到端测试 | 模拟器→硬件→混合 三种场景 |
| 回退测试 | 硬件无响应→模拟器自动启动 |
| 配置持久化测试 | 保存→重启→恢复 |
| 多源 RPS 测试 | 多个数据源同时运行性能无退化 |

---

## 7. 实施建议

### 执行顺序建议

1. **先 Phase 1 + Phase 2**：打好抽象层基础，验证设计合理性，不影响现有功能
2. **再 Phase 4**：配置先落地，便于后续 UI 开发调试
3. **最后 Phase 3**：UI 改动最大，但底层已完备，可独立开发

### 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 硬件连接中断 | 数据丢失 | 自动重连机制 + 模拟器回退 |
| 参数校验不足 | 配置损坏 | `DataSourceConfig` 字段校验 |
| 多源性能 | UI 卡顿 | 数据源级别的 publish 节流 |
| 切换竞态 | 资源泄漏 | `RLock` 保护 + `stop()` 幂等性 |

---

## 8. 文件修改清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `core/foundation/data_source.py` | **新建** | `DataSource` 抽象基类 + `DataSourceType`/`DataSourceStatus` 枚举 |
| `core/foundation/data_source_config.py` | **新建** | `DataSourceConfig`/`StartupPolicy`/`SavedDevice` 数据类 |
| `core/foundation/data_source_manager.py` | **新建** | `DataSourceManager` 核心管理器 |
| `core/communication/hardware_source.py` | **新建** | `HardwareSource` 单设备数据源 |
| `core/utils/simulator_source.py` | **新建** | `SimulatorSource` 模拟器数据源 |
| `core/foundation/pd_data_bus.py` | **修改** | 新增 `source_attached`/`source_detached`/`active_source` 信号 |
| `core/data/pd_models.py` | **修改** | 确保 `PDDeviceModel` 覆盖所有硬件参数字段 |
| `ui/pd_controller.py` | **修改** | 集成 `DataSourceManager`，替换 `_simulator`/`_hardware` 字段 |
| `pd_main.py` | **修改** | 启动策略逻辑，新增 `--hardware` 参数 |
| `ui/pages/device_page.py` | **修改** | 重构为数据源管理页面 |
| `ui/dialogs/add_device_dialog.py` | **新建** | 添加硬件对话框 |
| `ui/pd_main_window.py` | **修改** | 状态栏数据源状态指示器 |
| `ui/pages/realtime_monitor_page.py` | **修改** | 数据源选择下拉框 |
| `ui/pages/settings_page.py` | **修改** | 数据源默认策略设置 |
| `config/pd_default_config.json` | **修改** | 新增 `data_sources` 配置节 |
| `tests/test_data_source.py` | **新建** | 数据源单元测试 |

---

## 9. 验收标准

1. `python pd_main.py` → 程序启动后自动显示模拟数据（仪表盘非零，悬停显示详细信息：RPS、数据源状态）
2. `python pd_main.py --hardware` → 尝试连接硬件，无硬件时退出码=1
3. `python pd_main.py --simulator` → 保持现有行为不变
4. 设备页面显示所有数据源列表（硬件 + 模拟器混合），悬停显示详细信息：RPS、数据源类型、状态
5. 可运行时添加/启动/停止/删除数据源，无需重启程序
6. 多个数据源可同时运行，UI 可选择查看任意源的数据
7. 硬件断线后自动重连，重连失败时通知用户是否需要启动模拟器保障数据不中断
8. 配置保存后重启恢复，数据源状态保持一致
9. 所有现有 84 个测试通过
