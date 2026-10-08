# API 文档

本文档描述 UHF_PD_PLATFORM_Python 核心模块的公共 API。

## 目录

- [信号总线](#信号总线)
- [数据处理](#数据处理)
- [服务层](#服务层)
- [UI 控制器](#ui-控制器)

## 信号总线

### PDDataBus

单例事件总线，负责模块间的解耦通信。

```python
from core.foundation.signal_bus import PDDataBus

bus = PDDataBus.instance()
```

#### 方法

##### `instance() -> PDDataBus`

获取单例实例。

**返回:** PDDataBus 单例

##### `publish_waveform(device_id: str, channel_id: int, sensor_type: str, waveform: object)`

发布波形数据信号。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `sensor_type` - 传感器类型 ("uhf" 或 "ae")
- `waveform` - 波形数据对象

##### `publish_fft_result(device_id: str, channel_id: int, fft_data: object, sensor_type: str)`

发布 FFT 计算结果。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `fft_data` - FFT 结果对象
- `sensor_type` - 传感器类型

##### `publish_pd_event(device_id: str, channel_id: int, event: dict)`

发布局放事件。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `event` - 事件字典，包含 amplitude, phase 等字段

##### `publish_alarm(device_id: str, alarm: dict)`

发布报警信号。

**参数:**
- `device_id` - 设备 ID
- `alarm` - 报警字典

##### `publish_ae_hit(device_id: str, channel_id: int, features: dict)`

发布 AE hit 特征。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `features` - AE hit 特征字典

##### `publish_ae_envelope(device_id: str, channel_id: int, envelope: np.ndarray)`

发布 AE 包络波形。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `envelope` - 包络数组

##### `publish_prpd_result(device_id: str, channel_id: int, prpd_data: object)`

发布 PRPD 结果。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `prpd_data` - PRPD 数据对象

##### `shutdown() -> None`

关闭总线，释放所有信号连接。

---

## 数据处理

### FFTProcessor

FFT 计算和频段分析处理器。

```python
from core.processing.fft_processor import FFTProcessor

# UHF 频段 (默认)
fft = FFTProcessor(sample_rate=100_000_000)

# AE 频段
fft_ae = FFTProcessor(sample_rate=2_000_000, bands=FFTProcessor.AE_BANDS)
```

#### 类属性

- `DEFAULT_BANDS: dict` - 默认 UHF 频段配置
- `AE_BANDS: dict` - AE 频段配置
- `UHF_BANDS: dict` - UHF 频段配置 (同 DEFAULT_BANDS)

#### 方法

##### `compute(samples: np.ndarray) -> FFTResult`

计算 FFT。

**参数:**
- `samples` - 输入波形数组

**返回:** FFTResult 对象，包含 frequencies, magnitudes, peaks 等字段

##### `compute_band_stats(frequencies: np.ndarray, magnitudes: np.ndarray, bands: dict = None) -> dict`

计算指定频段的统计信息。

**参数:**
- `frequencies` - 频率数组
- `magnitudes` - 幅值数组
- `bands` - 频段字典 (可选，默认使用构造时传入的 bands)

**返回:** 频段统计字典

---

### PRPDProcessor

PRPD (Phase Resolved Partial Discharge) 图谱处理器。

```python
from core.processing.prpd_processor import PRPDProcessor

prpd = PRPDProcessor(
    phase_bins=360,      # 相位分辨率
    amplitude_bins=256,  # 幅值分辨率
    max_amplitude=100.0, # 最大幅值
)
```

#### 方法

##### `add_event(phase: float, amplitude: float, polarity: int = 0, energy: float = 0.0, cycle: int = 0) -> None`

添加单个局放事件。

**参数:**
- `phase` - 相位 (0~360°)
- `amplitude` - 幅值
- `polarity` - 极性 (0=正, 1=负)
- `energy` - 放电能量
- `cycle` - 工频周期编号

##### `add_waveform(waveform: np.ndarray, phase_offset: float = 0.0) -> None`

从波形数据提取 PRPD 事件。

**参数:**
- `waveform` - 波形数组
- `phase_offset` - 相位偏移

##### `compute(mode: PRPDMode = PRPDMode.HEATMAP) -> PRPDResult`

计算 PRPD 结果。

**参数:**
- `mode` - 显示模式 (HEATMAP / DENSITY / SECTORS)

**返回:** PRPDResult 对象

#### 属性

- `matrix: np.ndarray` - PRPD 矩阵 [phase_bins × amplitude_bins]
- `phase_bins: int` - 相位分辨率
- `amplitude_bins: int` - 幅值分辨率

---

### AELocalizer

AE 声源定位器 (TDOA 算法)。

```python
from core.processing.ae_localizer import AELocalizer, SensorPosition

sensors = [
    SensorPosition(sensor_id=1, x=0.0, y=0.0),
    SensorPosition(sensor_id=2, x=1.0, y=0.0),
    SensorPosition(sensor_id=3, x=1.0, y=1.0),
]
localizer = AELocalizer(sensors=sensors, sound_speed=343.0)
```

#### 方法

##### `locate(arrival_times: dict) -> LocalizationResult`

定位声源。

**参数:**
- `arrival_times` - 到达时间字典 `{sensor_id: time_seconds}`

**返回:** LocalizationResult 对象 (包含 x, y, confidence 等字段)，失败返回 None

##### `get_cluster_center(results: list) -> LocalizationResult`

获取聚类中心。

**参数:**
- `results` - 多次定位结果列表

**返回:** LocalizationResult 对象

---

## 服务层

### AcquisitionService

数据采集服务（协调器）。

```python
from core.services import AcquisitionService

svc = AcquisitionService(
    sample_rate=100_000_000,  # 采样率
    waveform_points=4096,     # 波形点数
)
svc.start()
```

#### 方法

##### `set_channel_coupling_type(device_id: str, channel_id: int, coupling_type: str) -> None`

设置通道耦合类型。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `coupling_type` - 耦合类型 ("uhf" 或 "ae")

##### `process_data_frame(device_id: str, channel_id: int, data: Any) -> AcquisitionResult`

处理单帧数据。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `data` - 波形数据对象

**返回:** AcquisitionResult 对象

##### `get_stats() -> dict`

获取采集统计。

**返回:** 统计字典，包含 frames_received, waveforms_processed 等字段

##### `get_observed_max_amp() -> dict`

获取观测到的最大幅值。

**返回:** 字典 `{channel_key: max_amplitude}`

##### `reset_stats() -> None`

重置统计计数器。

##### `stop() -> None`

停止采集服务。

---

### PDAlarmService

报警检测与管理服务。

```python
from core.services import PDAlarmService

alarm_svc = PDAlarmService(db_manager=db_manager)
```

#### 方法

##### `check_amplitude(device_id: str, channel_id: int, amplitude: float) -> dict`

检查幅值是否触发报警。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `amplitude` - 当前幅值

**返回:** 报警字典（如果触发），否则 None

##### `trigger_alarm(alarm: dict) -> None`

触发报警（发布到 DataBus + 持久化）。

**参数:**
- `alarm` - 报警字典

##### `ack_alarm(alarm_id: int) -> bool`

确认报警已处理。

**参数:**
- `alarm_id` - 报警 ID

**返回:** 是否成功

##### `delete_alarm(alarm_id: int) -> bool`

删除报警记录。

**参数:**
- `alarm_id` - 报警 ID

**返回:** 是否成功

##### `get_alarms(limit: int = 100) -> list`

获取报警列表。

**参数:**
- `limit` - 最大返回数量

**返回:** 报警字典列表

##### `shutdown() -> None`

关闭服务。

---

### PDStorageService

数据持久化服务。

```python
from core.services import PDStorageService

storage = PDStorageService(
    db_manager=db_manager,
    flush_interval=5.0,  # 写入间隔 (秒)
    batch_size=100,      # 批量大小
)
storage.start()
```

#### 方法

##### `store_pd_event(device_id: str, channel_id: int, event: dict) -> None`

存储 PD 事件。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `event` - 事件字典

##### `store_trend_data(device_id: str, channel_id: str, period: str, data: dict) -> None`

存储趋势数据。

**参数:**
- `device_id` - 设备 ID
- `channel_id` - 通道 ID
- `period` - 时间周期 ("1h", "1d", "1w", "1m", "1y")
- `data` - 数据字典

##### `stop() -> None`

停止存储服务（flush 剩余数据）。

---

## UI 控制器

### PDSystemController

系统总控制器，整合所有服务并连接 UI 页面。

```python
from ui.controller import PDSystemController

controller = PDSystemController(db_manager=db_manager)
controller.initialize(pages=pages_dict)
```

#### 方法

##### `initialize(pages: dict = None) -> None`

初始化所有服务并连接 UI。

**参数:**
- `pages` - UI 页面字典 `{key: page_widget}`

##### `start_simulator(pd_type: str = "internal", fps: int = 20, sim_type: str = "uhf") -> None`

启动数据模拟器。

**参数:**
- `pd_type` - 局放类型
- `fps` - 帧率
- `sim_type` - 模拟器类型 ("uhf" 或 "ae")

##### `stop_simulator() -> None`

停止模拟器。

##### `start_hardware() -> bool`

启动硬件接口。

**返回:** 是否成功

##### `shutdown() -> None`

关闭所有服务（释放信号订阅 → 停止服务 → 停止总线）。

##### `get_stats() -> dict`

获取采集统计。

**返回:** 统计字典

##### `reset_stats() -> None`

重置统计计数器。

---

## 数据处理管道

### 完整数据流

```
硬件/模拟器 → AcquisitionService → DataBus → UI 页面
                 ↓
            AlarmService → DataBus → AlarmPage
                 ↓
            StorageService → SQLite
```

### AE 处理流程

```
AESimulator → AcquisitionService._process_ae_waveform
                 ↓
            RingBuffer (缓存)
                 ↓
            带通滤波 → 包络提取
                 ↓
            Hit 检测 → 参数提取
                 ↓
            DataBus.publish_ae_hit → UI
            DataBus.publish_pd_event → Alarm/Storage
            DataBus.publish_ae_envelope → UI
```

### UHF 处理流程

```
FPGA → AcquisitionService._process_uhf_waveform
         ↓
    RingBuffer (缓存)
         ↓
    峰值检测 → 幅值计算
         ↓
    DataBus.publish_waveform → WaveformWidget
    DataBus.publish_pd_event → Alarm/Storage
    DataBus.publish_fft_result → FFTWidget
```

---

## 性能基准

| 指标 | 数值 |
|------|------|
| PRPD 事件处理 | 846K events/s |
| PRPD 矩阵更新 | 33.5M updates/s |
| FFT 计算 (4K) | 4.4K FFT/s |
| Alarm 发布延迟 p99 | 0.005ms |
| AE Hit 发布延迟 p99 | 0.003ms |

完整基准测试脚本位于 `tools/benchmarks/`。
