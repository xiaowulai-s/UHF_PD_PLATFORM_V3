## 程序问题清单

> 审查日期: 2026-06-16 | 基准版本: v1.0.7
> 已修复: P0(5) + P1(15) = 20 项 | 当前待解决: **91 项**
> 扫描维度: 代码质量 / UI用户体验 / 安全 / 稳定性 / 兼容性 / 可维护性

---

## P1 — 应当修复（25 项）

### 用户可见问题

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-1 | `ui/pages/dashboard_page.py` | — | 无设备连接时指标显示 0，无"请连接设备"引导提示 |
| P1-2 | `ui/pages/alarm_page.py` | — | 无报警时表格空白 |
| P1-3 | `ui/pages/device_page.py` | 399-401 | "扫描设备"可点击但未实现，应禁用+tooltip |
| P1-3 | `ui/pages/analysis_page.py` | 1311 | 错误提示直接显示 Python 异常原文 |
| P1-4 | `ui/widgets/prpd_widget.py` | 281 | PRPD 信息标签格式不一致（热力图 vs 散点图） |

### 代码重复

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-5 | `core/services/acquisition_service.py` | 356-565 | UHF/AE PRPD 管线 ~110 行重复（幅值扩展+衰减+发布） |
| P1-6 | `core/services/acquisition_service.py` | 396-584 | UHF/AE PRPS 管线 ~24 行重复 |
| P1-7 | `core/services/acquisition_service.py` | 779-824 | `release_device()` 15 个重复 `for d in list(dict.keys())` |
| P1-8 | `ui/pages/analysis_page.py` vs `tools/prpd_analyzer.py` | — | CSV 格式检测逻辑重复 |
| P1-9 | `core/utils/simulator.py` vs `ae_simulator.py` | — | 两个模拟器接口相同无共同基类 |

### 线程安全

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-10 | `core/services/acquisition_service.py` | 317-320,457 | `_sample_rates` 无锁写入，与有锁读取形成竞态 |
| P1-11 | `core/foundation/config_store.py` | 37-38,48-49 | `_json_devices`/`_global_config` 读写无锁 |
| P1-12 | `core/foundation/config_store.py` | 115-116,131-132 | DB 查询异常静默 pass |
| P1-13 | `core/foundation/plugin_registry.py` | 49-75 | `_plugins` 无锁保护 |

### 错误处理 / 稳定性

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-14 | `core/services/storage_service.py` | 119-151 | `store_pd_event()` 不验证必填字段 |
| P1-15 | `core/services/alarm_service.py` | 105-168 | `check_amplitude()` 未校验 NaN/Inf |
| P1-16 | `ui/pages/analysis_page.py` | 1544 | `_export_json` 无 try/except，导出失败直接崩溃 |

### 性能

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-17 | `core/processing/ring_buffer.py` | 56,62-63 | `get_last(n)` 千万级数据 O(n) 全量 list 转换 |
| P1-18 | `core/services/acquisition_service.py` | 324,460-461 | `samples.tolist()` 每帧分配内存 |

### 死代码 / 资源泄漏

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-19 | `core/services/acquisition_service.py` | 117-118,728-732 | `_on_waveform_cb`/`_on_pd_event_cb` 从未调用 |
| P1-20 | `core/communication/udp_driver.py` | 175-197 | `send_to()` 无调用者 |
| P1-21 | `core/services/storage_service.py` | 202-206 | session 无显式 commit |
| P1-22 | `core/services/alarm_service.py` | 408-420 | session 无显式 commit |

### 硬编码 / 配置

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-23 | `core/communication/udp_driver.py` | 56-57 | ���程主机 127.0.0.1:6000 硬编码 |
| P1-24 | `ui/controller.py` | 82-83 | 采样率/波形点数硬编码 |
| P1-25 | `core/services/acquisition_service.py` | 402-404 | **唯一 TODO**: 假设波形恰好一个工频周期 |

---

## P2 — 建议优化（63 项）

### 安全

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-1 | `core/data/models.py` | 496 | DB 路径相对 `os.path.join("data", ...)` 工作目录依赖，打包后路径不可预测 |
| P2-2 | `core/services/alarm_service.py` vs `monitor_models.py` | 37-39, 239-241 | 告警颜色映射两处重复定义，无单一来源 |
| P2-3 | `config/default_config.json` | — | 无认证/加密机制，数据库密码明文存储 |

### 稳定性

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-4 | `ui/pages/analysis_page.py` | 59-229 | `DataImporter` 全量加载文件到内存，2GB+ 文件 OOM |
| P2-5 | `ui/pages/analysis_page.py` | 1544 vs 1559 | `_export_json` 无异常处理 vs `_export_excel` 有，行为不一致 |
| P2-6 | `ui/pages/analysis_page.py` | 168-179 | `_load_excel` 中 `float(row[idx])` 未检查 None |
| P2-7 | `core/services/storage_service.py` | 218-220 | 重试 key 用 `str(ts)+str(amp)`，相同事件误判 |

### 兼容性

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-8 | `ui/main_window.py` | 251-258 | FlashWindow 使用 Windows 专有 API，不可跨平台 |
| P2-9 | `core/services/acquisition_service.py` | 111-114 | AE 阈值硬编码不可配置 |
| P2-10 | `core/communication/serial_driver.py` | 43-48 | 串口默认 COM1/9600 Windows 限定 |

### 空状态 / 引导

| # | 文件 | 问题 |
|---|------|------|
| P2-11 | `ui/pages/ae_page.py` | Hit 历史表初始空白 |
| P2-12 | `ui/pages/analysis_page.py` | 结果面板无数据时"—"，可加引导 |
| P2-13 | `ui/` 全局 | 无 QProgressBar，长操作无进度反馈 |

### 可恢复性

| # | 文件 | 问题 |
|---|------|------|
| P2-14 | `ui/pages/device_page.py` | 删除设备后通道表未清空 |
| P2-15 | `ui/pages/device_page.py` | 删除/清除无软删除机制 |
| P2-16 | `ui/` 全局 | 无 Ctrl+Z 撤销 |

### 单位 / 标签 / UI 一致性

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-17 | `ui/pages/device_page.py` | 139 | AE 采样率 2MHz vs UHF 100MHz 无区分提示 |
| P2-18 | `ui/pages/alarm_page.py` | 203-249 | "防抖次数"无业务含义说明 |
| P2-19 | `ui/widgets/trend_chart_widget.py` | 104 | Y 轴固定"幅值(mV)"，多曲线混合不适用 |
| P2-20 | `ui/widgets/ae_*.py` | — | AE 控件标题 11px 偏小 |
| P2-21 | `ui/` 全局 | — | 按钮样式在 4 个文件中独立定义 |
| P2-22 | `ui/design_tokens.py` | — | 部分 QSS 绕过令牌直接内联字号 |

### 可维护性

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-23 | `ui/pages/analysis_page.py` | 1592 | 单文件含 4 个类，远超 500 行 |
| P2-24 | `core/services/acquisition_service.py` | 824 | 超大文件 |
| P2-25 | `ui/design_tokens.py` | 769 | 超大文件（含 DarkColors 无继承关系） |
| P2-26 | `ui/controller.py` | 747 | 超大文件 |
| P2-27 | `ui/widgets/prpd_widget.py` | 624 | 超大文件 |
| P2-28 | `core/communication/fpga_protocol.py` | 594 | 超大文件 |
| P2-29 | 多个文件 | — | 公有方法缺 docstring |

### 响应式 / 布局

| # | 文件 | 问题 |
|---|------|------|
| P2-30 | `ui/pages/realtime_monitor_page.py` | 1024px 下峰值列表仅 126px |
| P2-31 | `ui/pages/ae_page.py` | 三栏等宽，声源定位图过小 |
| P2-32 | `ui/main_window.py` | 关于对话框 600x500 覆盖大部区域 |
| P2-33 | `ui/main_window.py` | 1024-1199px 导航折叠仅图标 |

### 导航 / 无障碍

| # | 文件 | 问题 |
|---|------|------|
| P2-34 | `ui/main_window.py` | 无面包屑/页面位置指示器 |
| P2-35 | `ui/main_window.py` | F1-F8 快捷键无可见提示 |
| P2-36 | `ui/widgets/nav_menu.py` | 图标用 Unicode 特殊字符，跨平台不一致 |
| P2-37 | `ui/` 全局 | 设备状态/报警仅靠颜色区分（色盲不友好） |
| P2-38 | `ui/` 全局 | 无 setTabOrder |
| P2-39 | `ui/` 全局 | 按钮无 setEnabled 状态管理 |
| P2-40 | `ui/` 全局 | 主题切换需重启 |

### 类型安全 / 导入

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-41 | `core/foundation/config_store.py` | 50 | `set_db_repo` 缺类型注解 |
| P2-42 | `core/services/storage_service.py` | 45 | `db_manager` 缺类型注解 |
| P2-43 | `core/services/acquisition_service.py` | 91 | `_protocol: Any` 应为 `Optional[FpgaProtocol]` |
| P2-44 | `ui/controller.py` | 59 | `_pages: dict` 应为 `Dict[str, QWidget]` |
| P2-45 | `ui/controller.py` | 244,396,447 | `import numpy` 3 处重复导入 |
| P2-46 | `core/foundation/signal_bus.py` | 80 | `__import__("threading").Lock()` |
| P2-47 | `core/utils/logger.py` | 17 | `import structlog` 无回退 |

### 硬编码 / 冗余

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-48 | `ui/widgets/prpd_widget.py` | 532,535 | 散点 `[:5000]` 硬截断 |
| P2-49 | `core/services/acquisition_service.py` | 111-114 | AE 报警阈值硬编码 |
| P2-50 | `main.py` | 128 | 版本号与 `core/version.py` 重复 |
| P2-51 | `ui/pages/settings_page.py` | 303-312 | 默认值与构造函数重复 |
| P2-52 | `core/utils/logger.py` | 188-189 | `DatabaseLogHandler` 别名未引用 |
| P2-53 | `core/communication/fpga_protocol.py` | 54-64 | `DataType(IntEnum)` 未充分利用 |
| P2-54 | `core/foundation/config_store.py` | 152-153 | 备份失败静默 |
| P2-55 | `ui/pages/settings_page.py` | 80-81 | 配置加载异常无日志 |
| P2-56 | `ui/controller.py` | 447-453 | TDOA 模拟数据硬编码混入生产 |
| P2-57 | `core/services/acquisition_service.py` | 80 | `PRPD_DECAY_FACTOR=0.995` 应可配置 |

### 其他代码质量

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-58 | `core/foundation/data_bus.py` | 240-241 | `unregister()` O(n) 列表推导 |
| P2-59 | `core/services/storage_service.py` | 218-220 | 重试 key 拼接冲突风险 |
| P2-60 | `core/services/acquisition_service.py` | 324 | `samples.tolist()` numpy→list 开销 |
| P2-61 | `core/processing/ring_buffer.py` | 45 | `extend(List[Any])` 应为 `Iterable[Any]` |
| P2-62 | `core/communication/udp_driver.py` | 229 | `addr: tuple` 应为 `tuple[str, int]` |
| P2-63 | `ui/pages/alarm_page.py` | 375 | `_on_alarm_for_ui` 通过 `self.parent()` 迂回访问窗口 |

---

## 历史遗留（4 项，暂不处理）

| # | 类别 | 问题 | 暂缓原因 |
|---|------|------|----------|
| L-1 | 架构 | 无全局 VERSION 常量 | 功能影响小 |
| L-2 | 算法 | PRPS 相位映射需外部同步信号 | 有 TODO |
| L-3 | UI | 设备页网格响应式列数 | 固定 3 列够用 |
| L-4 | UI | PRPS ColorBar 字号 | 无独立控件 |

---

## 修复日志

### v1.0.7 (2026-06-16)

**P0 — 已修复 (5/5)**
- ✅ 仪表板指标：`_update_status_stats()` 定期推送真实数据
- ✅ `_main_window` 未初始化：`@property` 获取 `self.parent()`
- ✅ ConfigStore 单例：双重检查锁定
- ✅ 按钮文字：`_on_apply_rules` 正确修改 `btn_apply`
- ✅ Tooltip：waveform 控件按钮添加提示

**P1 — 已修复 (15/32)**
- ✅ 设备保存参数 QMessageBox 反馈
- ✅ PRPD "矩阵总和" → "总事件数"
- ✅ acquisition_service 9处 `debug` → `warning`
- ✅ TrendPage 空状态提示
- ✅ 4处 `except:pass` → `logger.debug`（serial_utils/ae_page/data_bus/main_window）
- ✅ analysis_page 空 CSV 文件检测
- ✅ 6项合并/归档

---

## 统计总览

| 严重程度 | 数量 |
|---------|------|
| **P0** | 0 ✅ |
| **P1** | 25 |
| **P2** | 63 |
| **历史遗留** | 4 |
| **合计** | **92** |

| 维度 | P1 | P2 | 小计 |
|------|----|----|------|
| 代码质量 | 21 | 28 | 49 |
| UI/UX | 5 | 16 | 21 |
| 安全 | 0 | 3 | 3 |
| 稳定性 | 6 | 4 | 10 |
| 兼容性 | 0 | 3 | 3 |
| 可维护性 | 0 | 9 | 9 |
| **合计** | **32** | **63** | **99** |
