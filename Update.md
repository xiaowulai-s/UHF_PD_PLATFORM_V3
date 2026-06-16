## 程序问题清单

> 审查日期: 2026-06-15 | 基准版本: v1.0.7
> 全面代码质量 + UI/UX 用户友好性双维度扫描，识别 **96 个待解决问题**

---

## P0 — 必须立即修复（5 项）

### 代码质量

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P0-1 | `core/foundation/config_store.py` | 41-44 | `instance()` 单例无双重检查锁定，多线程可能创建多个实例导致配置不一致 |
| P0-2 | `ui/controller.py` | 461,471 | `self._main_window` 从未初始化，状态栏在线设备数永远不更新 |
| P0-3 | `ui/pages/dashboard_page.py` | 64-70 | 5 个核心指标卡片（在线设备/局放总数/报警总数/最大幅值/AE Hits）从未被 controller 调用 `update_metric()` 更新，永久显示 0（严重信息误导） |
| P0-4 | `ui/pages/alarm_page.py` | 401 | "应用规则"按钮修改了 `_refresh_btn`（刷新按钮）的文字为"规则已应用 ✓"，而非修改 `btn_apply`（应用规则按钮），点击反馈对象错误 |

### UI/UX

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P0-5 | `ui/` 全局 | — | 全应用仅 1 个 tooltip（系统托盘），所有按钮/输入框/图表无提示，用户无法理解控件功能 |

---

## P1 — 应当修复（39 项）

### 信息误导 / 无反馈

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-1 | `ui/pages/dashboard_page.py` | — | 无设备连接时 5 个指标全显示 0，无"请连接设备"引导提示，用户以为系统故障 |
| P1-2 | `ui/pages/trend_page.py` | — | 无数据时图表完全空白，无"暂无趋势数据"占位提示 |
| P1-3 | `ui/pages/alarm_page.py` | — | 无报警时表格为空，无"暂无报警，系统正常"友好提示 |
| P1-4 | `ui/pages/device_page.py` | 324-397 | "保存参数"按钮点击后无任何 UI 反馈（无 toast、无状态栏消息），仅 logger.info |
| P1-5 | `ui/pages/device_page.py` | 399-401 | "扫描设备"按钮可点击但功能未实现，应禁用并加 tooltip |
| P1-6 | `ui/pages/analysis_page.py` | 1311 | 错误提示直接显示 Python 异常（如 `NoneType object`），对用户不友好 |

### 错误处理 / 线程安全

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-7 | `core/services/acquisition_service.py` | 356-565 | UHF PRPD 和 AE PRPD 管线 ~110 行高度重复（动态幅值扩展+衰减+发布），bug 修复需同步两处 |
| P1-8 | `core/services/acquisition_service.py` | 396-584 | UHF PRPS 和 AE PRPS 管线 ~24 行完全重复 |
| P1-9 | `core/services/acquisition_service.py` | 779-824 | `release_device()` 含 15 个重复 `for d in list(dict.keys())` 循环 |
| P1-10 | `core/services/acquisition_service.py` | 344,353,393,418,517-590 | 10+ 处关键异常用 `logger.debug()` 静默吞错，生产环境默认 WARNING 级别数据丢失不可见 |
| P1-11 | `core/services/acquisition_service.py` | 317-320,457 | `_sample_rates` 字典无锁写入，与有锁读取形成竞态 |
| P1-12 | `core/foundation/config_store.py` | 37-38,48-49 | `_json_devices`/`_global_config` 读写无锁，`reset()` 与 `instance()` 竞态 |
| P1-13 | `core/foundation/config_store.py` | 115-116,131-132 | DB 查询和列表读取异常静默 pass，数据不一致无感知 |
| P1-14 | `ui/pages/analysis_page.py` | 89-90 | 空 CSV 文件导致 IndexError，未验证 rows 非空 |
| P1-15 | `core/services/storage_service.py` | 119-151 | `store_pd_event()` 不验证必填字段，缺失数据静默入库 |
| P1-16 | `core/services/alarm_service.py` | 105-168 | `check_amplitude()` 未校验 NaN/Inf，可能导致报警逻辑异常 |

### 性能

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-17 | `core/processing/ring_buffer.py` | 56,62-63 | `get_last(n)` / `get_all()` 对百万级数据做 O(n) 全量 list 转换，高频调用性能瓶颈 |
| P1-18 | `core/services/acquisition_service.py` | 324,460-461 | `buffer.extend(samples.tolist())` 每帧分配一次 Python list 内存 |

### 死代码 / 资源泄漏

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-19 | `core/services/acquisition_service.py` | 117-118,728-732 | `_on_waveform_cb` / `_on_pd_event_cb` 有 setter 但从未被调用，完全死代码 |
| P1-20 | `core/communication/udp_driver.py` | 175-197 | `send_to()` 方法在整个项目中无调用者 |
| P1-21 | `core/services/storage_service.py` | 202-206 | `_flush_events()` session 无显式 commit，依赖 context manager 隐式行为 |
| P1-22 | `core/services/alarm_service.py` | 408-420 | `_save_alarm_to_db()` session 无显式 commit |

### UI 一致性 / 无障碍

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-23 | `ui/widgets/prpd_widget.py` | 281 | 热力图模式显示"矩阵总和 N"（开发调试信息），应改为"总事件数 N" |
| P1-24 | `ui/` 全局 | — | 设备状态/报警级别仅靠颜色区分（红/黄/绿），色盲用户无法辨识 |
| P1-25 | `ui/` 全局 | — | 无 `setTabOrder`，Tab 键导航依赖默认布局顺序 |
| P1-26 | `ui/` 全局 | — | 全部按钮无 `setEnabled(False)`，不可用状态下仍可点击但无反应 |

### 代码重复

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-27 | `ui/pages/analysis_page.py` vs `tools/prpd_analyzer.py` | 59-101 vs 40-100 | CSV 格式自动检测逻辑完全重复，应提取到共享模块 |
| P1-28 | `core/utils/simulator.py` vs `ae_simulator.py` | — | 两个模拟器接口相同但无共同基类/Protocol |

### 硬编码关键值

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-29 | `core/communication/udp_driver.py` | 56-57 | 远程主机 `127.0.0.1:6000` 硬编码，不可配置 |
| P1-30 | `ui/controller.py` | 82-83 | 采样率 `100_000_000` / 波形点数 `4096` 硬编码 |
| P1-31 | `core/services/acquisition_service.py` | 402-404 | **唯一的 TODO**："假设波形覆盖恰好一个工频周期" — 非标准采样率下相位映射错误 |

---

## P2 — 建议优化（52 项）

### 空状态 / 引导缺失

| # | 文件 | 问题 |
|---|------|------|
| P2-1 | `ui/pages/ae_page.py` | Hit 历史表初始空白无提示 |
| P2-2 | `ui/pages/analysis_page.py` | 分析结果面板无数据时显示"—"，可加引导文字 |
| P2-3 | `ui/` 全局 | 没有任何 `QProgressBar` 的实际使用，长操作无进度反馈 |

### 可恢复性 / 安全

| # | 文件 | 问题 |
|---|------|------|
| P2-4 | `ui/pages/device_page.py` | 删除设备后通道配置表未清空，保留已删除设备信息 |
| P2-5 | `ui/pages/device_page.py` | 删除设备和清除报警均提示"不可撤销"，但无软删除/回收站机制 |
| P2-6 | `ui/` 全局 | 没有任何 Ctrl+Z 撤销功能 |

### 单位 / 标签

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-7 | `ui/pages/device_page.py` | 139 | AE 设备采样率默认 2 MHz 但 Spinner 范围 1-500 无提示区分 UHF(100MHz) vs AE(2MHz) |
| P2-8 | `ui/pages/alarm_page.py` | 203-249 | "防抖次数"无说明（连续超阈值次数？采样次数？） |
| P2-9 | `ui/widgets/trend_chart_widget.py` | 104 | Y 轴固定"幅值(mV)"但图表含多种曲线（次数/幅值/能量） |
| P2-10 | `ui/pages/analysis_page.py` | 1267 | 幅值分布图 X 轴"幅值"无单位 |

### 响应式 / 布局

| # | 文件 | 问题 |
|---|------|------|
| P2-11 | `ui/pages/realtime_monitor_page.py` | 1024px 下三栏(3:2:1)峰值列表仅 ~126px，列宽截断 |
| P2-12 | `ui/pages/ae_page.py` | 三栏等宽(1:1:1)在 1024px 下声源定位图不可辨识 |
| P2-13 | `ui/main_window.py` | 关于对话框 minSize(600,500) 在低分辨率窗口下覆盖大部区域 |
| P2-14 | `ui/main_window.py` | 1024-1199px 范围导航折叠仅图标，需额外交互展开 |

### 导航 / 快捷键

| # | 文件 | 问题 |
|---|------|------|
| P2-15 | `ui/main_window.py` | 无面包屑/页面位置指示器，窗口标题栏文字长时截断 |
| P2-16 | `ui/main_window.py` | F1-F8 快捷键无任何可见提示，用户除非看源码不会发现 |
| P2-17 | `ui/widgets/nav_menu.py` | 图标用 Unicode 特殊字符，跨平台渲染效果不一致 |

### 类型安全 / 导入

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-18 | `core/foundation/config_store.py` | 50 | `set_db_repo()` 缺类型注解 |
| P2-19 | `core/services/storage_service.py` | 45 | `db_manager=None` 缺类型注解 |
| P2-20 | `core/services/acquisition_service.py` | 91 | `self._protocol: Any` 应为 `Optional[FpgaProtocol]` |
| P2-21 | `ui/controller.py` | 59 | `self._pages: dict` 应为 `Dict[str, QWidget]` |
| P2-22 | `ui/controller.py` | 244,396,447 | `import numpy as np` 在 3 个方法内重复导入 |
| P2-23 | `core/foundation/signal_bus.py` | 80 | `__import__("threading").Lock()` 应改为正常 import |
| P2-24 | `core/utils/logger.py` | 17 | `import structlog` 无 try/except 回退 |

### 硬编码值

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-25 | `ui/widgets/prpd_widget.py` | 532,535 | 散点数据 `[:5000]` 硬截断 |
| P2-26 | `core/services/acquisition_service.py` | 111-114 | AE 报警阈值全部硬编码 |
| P2-27 | `main.py` | 128 | 版本号 `"1.0.7"` 与 `core/version.py` 重复定义 |
| P2-28 | `ui/pages/settings_page.py` | 303-312 | 重置默认值与构造函数默认值重复，不一致风险 |

### 死代码

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-29 | `core/utils/logger.py` | 188-189 | `DatabaseLogHandler` 别名类未被引用 |
| P2-30 | `core/communication/fpga_protocol.py` | 54-64 | `DataType(IntEnum)` 枚举未充分利用 |

### 其他代码问题

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-31 | `core/foundation/data_bus.py` | 240-241 | `unregister()` 列表推导 O(n) 遍历，大量订阅时性能差 |
| P2-32 | `core/foundation/config_store.py` | 152-153 | 备份 `shutil.copy2()` 失败静默 pass |
| P2-33 | `ui/pages/settings_page.py` | 80-81 | 配置加载异常静默吞，无日志 |
| P2-34 | `core/services/storage_service.py` | 218-220 | 重试 key 用 `str(ts)+str(amp)` 拼接，相同 ts+amp 事件被误认为同一重试 |
| P2-35 | `ui/` 全局 | — | 主题切换需重启应用，未实现热切换 |
| P2-36 | `ui/widgets/ae_*.py` | — | AE 控件标题字体 11px 偏小，与其他区域 DT 令牌(12-14px)不协调 |
| P2-37 | `ui/` 全局 | — | 按钮样式在 4 个文件中独立定义，应统一使用 widgets/__init__.py 组件类 |

---

## 历史遗留（4 项，暂不处理）

| # | 类别 | 问题 | 暂缓原因 |
|---|------|------|----------|
| L-1 | 架构 | 无全局 VERSION 常量，版本号分散在多处 | 功能影响小 |
| L-2 | 算法 | PRPS 相位映射过于简单 | 需外部同步信号 |
| L-3 | UI | 设备页网格响应式列数 | 固定 3 列够用 |
| L-4 | UI | PRPS ColorBar 字号 | 无独立控件 |

---

## 统计总览

| 严重程度 | 代码质量 | UI/UX | 合计 |
|---------|----------|-------|------|
| **P0** | 3 | 2 | **5** |
| **P1** | 28 | 11 | **39** |
| **P2** | 33 | 19 | **52** |
| **历史遗留** | — | — | **4** |
| **合计** | **64** | **32** | **96** |
