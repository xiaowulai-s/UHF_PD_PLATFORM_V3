# 架构文档

本文档描述 UHF_PD_PLATFORM_Python 的系统架构、模块划分和数据流。

## 目录

- [系统概览](#系统概览)
- [模块架构](#模块架构)
- [数据流](#数据流)
- [关键设计模式](#关键设计模式)

## 系统概览

UHF_PD_PLATFORM_Python 是一个基于 PySide6 的局放检测平台，支持 UHF 和 AE 两种传感器类型。

```
┌─────────────────────────────────────────────────────────┐
│                     UI Layer (PySide6)                    │
├─────────────────────────────────────────────────────────┤
│  PDSystemController                                      │
│  ├── 页面: Monitor / Alarm / Device / Trend / AE / ...   │
│  └── 控件: WaveformWidget / PRPDWidget / FFTWidget / ...  │
└─────────────────────────────────────────────────────────┘
                              ↓ ↑ (Qt Signals)
┌─────────────────────────────────────────────────────────┐
│                  Service Layer                           │
├─────────────────────────────────────────────────────────┤
│  AcquisitionService  ──→ 数据采集与路由                   │
│  PDAlarmService      ──→ 报警检测与管理                   │
│  PDStorageService    ──→ 数据持久化 (SQLite)              │
│  DataSourceManager   ──→ 数据源管理 (模拟器/硬件)         │
└─────────────────────────────────────────────────────────┘
                              ↓ ↑ (PDDataBus)
┌─────────────────────────────────────────────────────────┐
│               Processing Layer (Core)                     │
├─────────────────────────────────────────────────────────┤
│  UHF Pipeline:                                            │
│  ├── RingBuffer          ── 数据缓存                      │
│  ├── PeakDetector        ── 峰值检测                      │
│  ├── FFTProcessor        ── FFT 计算                      │
│  ├── PRPDProcessor       ── PRPD 图谱                     │
│  └── PRPSProcessor       ── PRPS 图谱                     │
│                                                            │
│  AE Pipeline:                                             │
│  ├── RingBuffer          ── 数据缓存                      │
│  ├── AEEnvelopeProcessor ── 带通滤波 + 包络提取           │
│  ├── AEPeakDetector      ── Hit 检测                      │
│  ├── AEParamExtractor    ── 参数提取                      │
│  ├── AELocalizer         ── TDOA 声源定位                 │
│  └── AEAlarmChecker      ── AE 报警检测                   │
└─────────────────────────────────────────────────────────┘
                              ↓ ↑
┌─────────────────────────────────────────────────────────┐
│                 Hardware / Simulator                      │
├─────────────────────────────────────────────────────────┤
│  UHFSimulator  /  FPGA  (UHF 数据源)                     │
│  AESimulator              (AE 数据源)                    │
└─────────────────────────────────────────────────────────┘
```

## 模块架构

### 1. Foundation 层 (`core/foundation/`)

#### PDDataBus (signal_bus.py)

单例事件总线，基于 Qt Signals 实现模块间解耦通信。

**职责:**
- 定义所有信号 (waveform_received, fft_result_ready, pd_event_detected, ...)
- 提供 publish/subscribe 接口
- 自动管理订阅生命周期

**关键特性:**
- 线程安全
- 自动断开连接防止信号泄漏

#### SubscriptionManager (data_bus.py)

信号订阅管理器，跟踪所有 DataBus 连接。

**职责:**
- 注册信号 → 槽连接
- 批量释放所有订阅 (shutdown 时)
- O(1) 注册/注销（Dict 实现）

**性能优化:**
- 使用 `Dict[Tuple[id(signal), id(slot)]]` 替代 List，避免 O(n) 线性扫描

#### DataSourceManager (data_source_manager.py)

数据源生命周期管理。

**职责:**
- 统一管理模拟器和硬件数据源
- 启动/停止所有数据源
- 错误恢复

### 2. Processing 层 (`core/processing/`)

#### UHF Pipeline

**RingBuffer** (`ring_buffer.py`)
- 线程安全的环形缓冲区
- 用于数据缓存

**PeakDetector** (`peak_detector.py`)
- 简单峰值检测
- 返回峰值位置和幅值

**FFTProcessor** (`fft_processor.py`)
- FFT 计算
- 频段统计分析
- 支持 UHF 和 AE 不同频段

**PRPDProcessor** (`prpd_processor.py`)
- PRPD 图谱数据收集
- 相位-幅值二维矩阵
- 支持 HEATMAP / DENSITY / SECTORS 显示模式

**PRPSProcessor** (`prps_processor.py`)
- PRPS 时序图谱
- 工频周期 × 相位矩阵

#### AE Pipeline

**AEEnvelopeProcessor** (`ae_envelope.py`)
- 带通滤波 (20kHz~1MHz)
- Hilbert 包络提取

**AEPeakDetector** (`ae_peak_detector.py`)
- AE hit 检测
- 基于能量和阈值

**AEParamExtractor** (`ae_param_extractor.py`)
- 提取 AE hit 特征: 幅值、能量、上升时间、持续时间等

**AELocalizer** (`ae_localizer.py`)
- TDOA 声源定位
- 最小二乘法 + 多传感器聚类

**AEAlarmChecker** (`ae_alarm_checker.py`)
- AE 信号丢失检测
- 噪声基底线检测

### 3. Services 层 (`core/services/`)

#### AcquisitionService

采集服务协调器。

**职责:**
- 接收原始波形数据
- 根据耦合类型路由到 UHF 或 AE 处理管线
- 统计计数

**架构:**
```
process_data_frame()
    ↓
  [检查数据类型]
    ↓
  ├─ 耦合类型 == "ae" → AEPipelineHandler.process()
  └─ 默认 (UHF)       → UHFPipelineHandler.process()
```

#### PDAlarmService

报警检测与管理。

**职责:**
- 幅值阈值检查
- 报警去重和抑制
- 报警持久化
- 确认/删除报警

**数据存储:**
- SQLite (通过 PDStorageService)

#### PDStorageService

数据持久化。

**职责:**
- 批量写入 SQLite
- 定时 flush 机制
- 趋势数据存储

**数据表:**
- `pd_events` - PD 事件
- `alarms` - 报警记录
- `trend_data` - 趋势数据

#### DataSourceManager

数据源管理。

**职责:**
- 管理模拟器 (UHF/AE)
- 管理硬件接口
- 统一启动/停止

### 4. UI 层 (`ui/`)

#### PDSystemController

系统总控制器。

**职责:**
- 初始化所有服务
- 连接 DataBus 信号到 UI 页面
- 管理数据源

**初始化流程:**
```
initialize()
    ↓
  创建服务实例
    ↓
  连接服务 → DataBus (通过 SubscriptionManager)
    ↓
  连接 DataBus → UI
    ↓
  传递 db_manager 到页面
    ↓
  启动统计定时器
```

#### 页面 (`ui/pages/`)

- **RealtimeMonitorPage** - 实时监控（波形/FFT/PRPD/PRPS/AE）
- **AlarmPage** - 报警管理（表格展示、确认、删除）
- **DevicePage** - 设备管理
- **TrendPage** - 趋势分析
- **AEPage** - AE 分析（特征分布、定位）
- **AnalysisPage** - 综合分析
- **DashboardPage** - 仪表板
- **SettingsPage** - 设置
- **LogPage** - 日志

#### 控件 (`ui/widgets/`)

- **WaveformWidget** - 波形显示
- **FFTWidget** - FFT 频谱显示
- **PRPDWidget** - PRPD 热力图
- **PRPSWidget** - PRPS 时序图
- **AEPlotWidget** - AE 特征分布
- **AELocalizationWidget** - 声源定位图
- **TrendChartWidget** - 趋势图表

### 5. Communication 层 (`core/communication/`)

#### HardwareManager

硬件接口管理。

**职责:**
- FPGA 通信
- 协议解析 (FPGAProtocol)
- 配置管理 (HardwareConfig)

## 数据流

### UHF 数据流

```
FPGA → UDP Driver → AcquisitionService
                      ↓
              UHFPipelineHandler
                      ↓
              [RingBuffer → PeakDetector]
                      ↓
              FFTProcessor.compute()
                      ↓
              PRPDProcessor.add_events()
                      ↓
              DataBus.publish_waveform → WaveformWidget
              DataBus.publish_fft_result → FFTWidget
              DataBus.publish_pd_event → AlarmService/StorageService
```

### AE 数据流

```
AESimulator → AcquisitionService
                    ↓
            AEPipelineHandler
                    ↓
            [RingBuffer → BandpassFilter → Envelope]
                    ↓
            AEPeakDetector.detect_hits()
                    ↓
            AEParamExtractor.extract()
                    ↓
            DataBus.publish_ae_hit → MonitorPage/TrendPage
            DataBus.publish_pd_event → AlarmService/StorageService
            DataBus.publish_ae_envelope → MonitorPage
                    ↓
            AELocalizer.locate() (多个传感器)
                    ↓
            AEPage 声源定位图
```

### 报警流

```
PD Event → PDAlarmService.check_amplitude()
              ↓
         [超过阈值?]
              ↓ 是
         trigger_alarm()
              ↓
         DataBus.publish_alarm()
              ↓
         AlarmPage.add_alarm()
         DashboardPage.update_latest_alarm()
         MainWindow.notify_alarm()
              ↓
         PDStorageService.store_alarm()
```

## 关键设计模式

### 1. 单例模式 (Singleton)

**PDDataBus** - 全局事件总线

**优点:**
- 全局唯一
- 避免重复连接
- 简化订阅管理

**实现:**
```python
class PDDataBus:
    _instance = None

    @classmethod
    def instance(cls) -> PDDataBus:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
```

### 2. 观察者模式 (Observer)

**DataBus Signals** - 发布-订阅模式

**优点:**
- 解耦发布者和订阅者
- 支持多播
- 动态订阅/取消订阅

**实现:**
```python
# 发布
self._data_bus.publish_waveform(device_id, channel_id, sensor_type, waveform)

# 订阅
self._data_bus.waveform_received.connect(callback)
```

### 3. 策略模式 (Strategy)

**Pipeline Handler** - UHF vs AE 处理管线

**优点:**
- 算法可互换
- 避免类型检查分支
- 易于扩展新管线

**实现:**
```python
if coupling_type == "ae":
    handler = AEPipelineHandler(...)
else:
    handler = UHFPipelineHandler(...)
```

### 4. 工厂模式 (Factory)

**ProcessorManager** - 按需创建处理器

**优点:**
- 延迟初始化
- 统一生命周期管理
- 线程安全

**实现:**
```python
def get_or_create_prpd(self, device_id, channel_id):
    key = self._channel_key(device_id, channel_id)
    if key not in self._prpd_processors:
        self._prpd_processors[key] = PRPDProcessor(...)
    return self._prpd_processors[key]
```

### 5. 装饰器模式 (Decorator)

**PublishThrottle** - 发布节流

**优点:**
- 透明化限速
- 指数退避
- 可配置

**实现:**
```python
class PublishThrottle:
    def should_publish(self, key: str) -> bool:
        # 检查是否达到发布阈值
        ...
```

### 6. 门面模式 (Facade)

**PDSystemController** - 系统总控制器

**优点:**
- 简化客户端调用
- 统一初始化流程
- 隐藏子系统复杂性

**实现:**
```python
controller = PDSystemController()
controller.initialize(pages)  # 一键初始化所有服务
```

## 性能优化

### 1. O(1) 订阅管理

- 使用 `Dict[Tuple[int,int], Dict]` 替代 List
- 避免 O(n) 线性扫描

### 2. RingBuffer 数据复用

- 避免重复分配数组
- 减少 GC 压力

### 3. PublishThrottle

- PRPD/PRPS 发布节流 (1s → 3s 指数退避)
- 减少 UI 更新频率

### 4. AE Pipeline buffer.extend()

- 直接使用 numpy 数组 (无需 tolist())
- 减少拷贝开销

### 5. 批量存储

- PDStorageService 批量写入
- 定时 flush (默认 5s)
- 减少 I/O 次数

## 线程模型

```
┌────────────────────────────────────────┐
│         Main Thread (UI)                │
│  ┌──────────────────────────────────┐  │
│  │  QApplication Event Loop          │  │
│  │  ┌────────────────────────────┐   │  │
│  │  │  PDSystemController         │   │  │
│  │  │  - UI 回调                  │   │  │
│  │  │  - 信号路由                 │   │  │
│  │  └────────────────────────────┘   │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
                   ↑ Qt Signals (线程安全)
                   │
┌────────────────────────────────────────┐
│         Worker Threads                  │
│  ┌──────────────────────────────────┐  │
│  │  PDStorageFlush (定时 flush)     │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
```

**原则:**
- UI 更新必须在主线程
- DataBus Signals 自动线程安全
- 长时间计算使用 Worker Thread (如存储 flush)
