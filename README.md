# 超高频超声波局部放电检测系统

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PySide6](https://img.shields.io/badge/PySide6-6.6%2B-green.svg)](https://pypi.org/project/PySide6/)
[![PyQtGraph](https://img.shields.io/badge/PyQtGraph-0.14%2B-green.svg)](https://pypi.org/project/pyqtgraph/)
[![NumPy](https://img.shields.io/badge/NumPy-1.24%2B-blue.svg)](https://numpy.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

基于 **PySide6 + PyQtGraph + NumPy** 的工业级局部放电在线监测上位机软件，支持 **UHF 超高频** + **AE 超声波** 双模态局放检测与声源定位。

> **v1.0.7** — MCGS 系统全量移除、UI 配色统一优化、样式代码去重、双模态通道切换

## 核心特性

### 信号分析

| 模块 | 功能 | 性能 |
|------|------|------|
| **实时波形** | 缩放/拖拽/游标测量/自动量程/触发标记 | >=20 FPS |
| **PRPD 图谱** | 散点图/热力图/密度图三种模式、相位分布分析 | >=10 FPS |
| **FFT 频谱** | 峰值检测(含谐波识别)、频段统计、噪声估计、SNR | >=10 FPS |
| **PRPS 图谱** | 3D 散点图（Matplotlib）、512x360 周期脉冲序列 | 实时 |
| **趋势分析** | 1h/24h/7d/30d 多时间维度、多指标叠加 | 实时 |
| **AE 声发射** | Hilbert 包络提取、hit 检测、参数提取、TDOA 声源定位 | 实时 |

### 报警管理

- **三级报警体系**：严重(>=80%) / 一般(>=50%) / 提示(>=30%)
- **六种报警类型**：局放超限 / 设备离线 / 光模块异常 / ADC异常 / 同步异常 / 存储不足
- **防抖机制**：连续 N 次触发确认报警，消除误报
- 报警事件列表 + 统计 + 规则配置 + 确认弹窗
- 报警持久化到 SQLite 数据库

### FPGA 通信协议

```
数据帧格式:
+------+------+--------+--------+----------+--------+----------+------+
| 帧头 | 长度 | 设备ID | 通道ID | 时间戳   |数据类型| 数据内容 | CRC  |
| 2Byte| 2Byte| 2Byte  | 2Byte  | 8Byte    | 1Byte  | N Byte   | 2Byte|
+------+------+--------+--------+----------+--------+----------+------+

数据类型:
0x01 WAVEFORM    波形数据      TCP 端口 5000: 控制通道
0x02 PD_EVENT    局放事件      UDP 端口 6000: 数据通道
0x03 FFT_RESULT  FFT 频谱      CRC-16/MODBUS 校验
0x04 PRPD_RESULT PRPD 图谱
0x05 PRPS_RESULT PRPS 图谱
0x06 DEVICE_STATUS 设备状态
0x07 AE_WAVEFORM  AE 波形数据
0x08 AE_PARAMETERS AE 参数数据
```

### 数据处理 Pipeline

```
UDP 接收 -> FpgaProtocol.feed() -> 波形数据
                                       |
                             +- 峰值检测 -> DataBus(PD事件)
                             +- FFT计算  -> DataBus(FFT)
                             +- PRPD更新 -> DataBus(PRPD)
                             +- PRPS更新 -> DataBus(PRPS)
                             +- AE包络   -> DataBus(AE)
```

### 信号处理算法

| 模块 | 算法 | 说明 |
|------|------|------|
| **FFTProcessor** | numpy.rfft + scipy.signal.find_peaks | 频谱计算、峰值检测、频段统计、SNR |
| **PRPDProcessor** | 360x256 相位-幅值矩阵 | 散点/热力/密度、指数衰减、统计特征 |
| **PRPSProcessor** | 512x360 滚动矩阵 | 周期脉冲序列、滑动平均 PRPD |
| **PeakDetector** | scipy 自适应阈值 | 脉冲参数(幅值/宽度/面积/能量/SNR) |
| **RingBuffer** | collections.deque | 1,000,000 点线程安全环形缓冲 |
| **AEEnvelopeProcessor** | 带通滤波 + Hilbert | AE 包络提取 (20-200kHz) |
| **AEPeakDetector** | 相对阈值 hit 检测 | AE 到达时间、峰值幅值 |
| **AEParameterExtractor** | 时域/频域特征 | 上升时间、持续时间、振铃计数、MARSE |

### 模拟器（无需 FPGA）

内置四种局放类型的波形模拟器，支持可调参数：

```bash
# 电晕放电 (270-330 负半周集中)
python main.py --simulator --pd-type corona

# 沿面放电 (30-90 正半周集中)
python main.py --simulator --pd-type surface

# 内部放电 (0-60 正半周初期)
python main.py --simulator --pd-type internal

# 悬浮放电 (0-360 全相位分布)
python main.py --simulator --pd-type floating
```

### 系统架构

```
+---------------------------------------------------+
|                  UI 层 (PySide6)                    |
|  +---------+----------+----------+----------+      |
|  | 仪表板  | 实时监测  | 趋势分析  | 报警管理  |      |
|  +---------+----------+----------+----------+      |
|  | 设备管理 | 系统设置  | 数据分析  | AE分析   |      |
|  +---------+----------+----------+----------+      |
+---------------------------------------------------+
|               控制器层 (PDSystemController)          |
|    +----------------------------------------+       |
|    |          PDDataBus 事件总线             |       |
|    +----------------------------------------+       |
+---------------------------------------------------+
|              服务层 (Services)                      |
|  AcquisitionService  PDAlarmService                |
|  PDStorageService                                   |
+---------------------------------------------------+
|              通信层 (Communication)                  |
|  FpgaProtocol  UDPDriver  HardwareManager           |
+---------------------------------------------------+
|              信号处理 (Processing)                   |
|  RingBuffer  FFTProcessor  PRPDProcessor            |
|  PRPSProcessor  PeakDetector                        |
|  AEEnvelopeProcessor  AEPeakDetector  AEParameterExtractor |
+---------------------------------------------------+
```

### 项目结构

```
├── main.py                       # 系统入口
├── build.spec                  # PyInstaller 打包配置
├── core/
│   ├── processing/                # 信号处理核心
│   │   ├── fft_processor.py
│   │   ├── prpd_processor.py
│   │   ├── prps_processor.py
│   │   ├── peak_detector.py
│   │   ├── ring_buffer.py
│   │   ├── ae_envelope.py
│   │   ├── ae_peak_detector.py
│   │   └── ae_parameter_extractor.py
│   ├── communication/             # FPGA 通信
│   │   ├── fpga_protocol.py
│   │   ├── udp_driver.py
│   │   ├── hardware_manager.py
│   │   └── hardware_config.py
│   ├── services/                  # 业务服务
│   │   ├── acquisition_service.py
│   │   ├── alarm_service.py
│   │   └── storage_service.py
│   ├── data/monitor_models.py     # 监测数据库模型
│   └── foundation/                # 基础设施
│       ├── signal_bus.py
│       ├── data_bus.py
│       └── sensor_types.py
├── ui/
│   ├── main_window.py             # 主窗口
│   ├── controller.py              # 系统总控制器
│   ├── design_tokens.py           # Fluent Design 设计令牌
│   ├── theme_manager.py           # 主题管理器
│   ├── pages/                     # 8 个功能页面
│   │   ├── dashboard_page.py
│   │   ├── realtime_monitor_page.py
│   │   ├── analysis_page.py
│   │   ├── trend_page.py
│   │   ├── alarm_page.py
│   │   ├── device_page.py
│   │   ├── settings_page.py
│   │   └── ae_page.py
│   └── widgets/                   # 分析控件
│       ├── waveform_widget.py
│       ├── prpd_widget.py
│       ├── prps_widget.py
│       ├── fft_widget.py
│       ├── trend_chart_widget.py
│       ├── ae_parameters_widget.py
│       ├── ae_scatter_widget.py
│       └── ae_localization_widget.py
├── config/default_config.json
└── tests/test_integration.py   # 25 个集成测试
```

## 安装与运行

```bash
# 安装基础依赖
pip install -e .

# 安装可选依赖（推荐: scipy+OpenGL）
pip install -e ".[full]"

# 运行 PD 监测系统（需 FPGA 硬件）
python main.py

# 模拟器模式（自动生成局放数据，无需硬件）
python main.py --simulator
```

---

## 设计令牌系统

Fluent Design 风格统一 UI 规范：

| 令牌 | 值 | 用途 |
|------|-----|------|
| 主色 | `#0969DA` | 按钮/选中态/链接 |
| 成功 | `#1A7F37` | 在线/正常 |
| 警告 | `#D29922` | 一般报警 |
| 错误 | `#CF222E` | 严重报警/错误 |
| 字体 | Segoe UI Variable | Windows 11 默认 |
| 间距 | 4px 基数的 8pt 栅格 | 统一间距 |
| 圆角 | 8px (标准) / 12px (卡片) | 统一圆角 |

## 数据库

| 数据库 | 表数量 | ORM |
|--------|--------|-----|
| `data/monitor.db` | 5 表 | SQLAlchemy |

## 测试

```bash
# PD 集成测试（25 个用例）
pytest tests/test_integration.py -v
```

---

# 版本历史

## PD 系统

### v1.0.7 (2026-06-12)
- **MCGS 系统全量移除**：删除 30+ 个 MCGS 独占文件（Modbus 协议栈、TCP/串口驱动、MCGS 服务与工具、历史数据模块），5 个 `__init__.py` 导出清理
- **README 重构**：移除双系统描述与 MCGS 完整章节，仅保留 PD 系统文档
- **UI 配色统一**：全工程 Material Blue → Fluent Blue #0969DA（QSS 9 处 + 设计令牌统一）
- **样式代码去重**：新增 DT.sheet.card/table/combo/spin/input 5 个工厂方法，6 个页面统一引用
- **Dashboard 自适应**：指标卡片 fixedHeight → minimumHeight，合并迷你图表工厂函数
- **AlarmPage 优化**：批量插入延迟刷新、清除确认弹窗、QButtonGroup 互斥管理
- **DevicePage 修复**：左侧 stretch 0→1、删除确认弹窗、耦合类型动态循环

### v1.0.6 (2026-06-11)
- **AE 声发射子系统集成**：新增 AE 协议数据类型（0x07/0x08），完整 AE 信号处理管线（Hilbert 包络 → hit 检测 → 参数提取 → PRPD/PRPS/FFT）
- **双模态通道切换**：AcquisitionService 自动识别 UHF/AE 耦合类型
- **AE 特征分析页**：散点图/趋势图/TDOA 声源定位
- **AnalysisPage 子包化**：创建 ui/pages/analysis/ 子包，向后兼容导入
- 程序图标标准化、关于对话框 Tab 式重构（QButtonGroup）、UI 布局全面审计（26 项）

### v1.0.5 (2026-06-10)
- 关于对话框重构：从 QMessageBox 升级为 Tab 式 QDialog
- 分类置信度优化：移除 0.85 硬上限
- 冗余文档清理：删除 CHANGELOG.md 及 16 个旧文档，减少 12000+ 行

### v1.0.4 (2026-06-10)
- 放电类型分类策略重设计：Profile Matching + 8 维特征 + 高斯核相似度 + Softmax
- 新增幅值分布特征、Bootstrap 50 次采样统计、随机噪声识别

### v1.0.3 (2026-06-09)
- 热力图团簇方向修复、Jet 颜色映射注册修复
- _version 崩溃修复、动态幅值范围扩展
- PRPS 3D 散点图重构（Matplotlib 懒加载）

### v1.0.2 (2026-06-05)
- 新增数据分析页面、放电类型自动分类、报告导出（JSON/Excel/Word）
- PRPD 三种显示模式、文件拖放导入

### v1.0.1 (2026-06-03)
- 设备/通道切换下拉框、报警同步修复
- 设备列表持久化、性能优化（deque/原地切片/OpenGL）

### v1.0.0 (2026-06-02)
- 初始版本：实时波形、PRPD/FFT/PRPS 分析、趋势分析、三级报警
- FPGA 通信协议、数据模拟器、Fluent Design 界面

---

## License

MIT License

---

**PD 系统**: v1.0.7 | **更新**: 2026-06-12
