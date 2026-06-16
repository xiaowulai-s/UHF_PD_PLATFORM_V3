## 程序问题清单

> 审查日期: 2026-06-16 | 基准版本: v1.0.7
> P0(5) + P1(32) 已全部修复 | P2 剩余 **60 项**建议优化
> 扫描维度: 代码质量 / UI用户体验 / 安全 / 稳定性 / 兼容性 / 可维护性

---

## P2 — 建议优化（60 项）

### 安全（3 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-1 | `core/data/models.py` | 496 | DB 路径相对路径，打包后不可预测 |
| P2-2 | `core/services/alarm_service.py` vs `monitor_models.py` | 37-39, 239-241 | 告警颜色映射两处重复定义 |
| P2-3 | `config/default_config.json` | — | 数据库密码明文存储 |

### 稳定性（4 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-4 | `ui/pages/analysis_page.py` | 59-229 | DataImporter 全量加载到内存，大文件 OOM |
| P2-5 | `ui/pages/analysis_page.py` | 1544 vs 1559 | _export_json 无异常处理 vs _export_excel 有 |
| P2-6 | `core/services/storage_service.py` | 218-220 | 重试 key 拼接可能冲突 |
| P2-7 | `ui/pages/analysis_page.py` | 168-179 | _load_excel float(row[idx]) 未检查 None |

### 兼容性（3 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-8 | `ui/main_window.py` | 251-258 | FlashWindow Win32 API 不可跨平台 |
| P2-9 | `core/services/acquisition_service.py` | 111-114 | AE 阈值硬编码不可配置 |
| P2-10 | `core/communication/serial_driver.py` | 43-48 | 串口默认 COM1/9600 |

### 空状态 / 引导（6 项）

| # | 文件 | 问题 |
|---|------|------|
| P2-11 | `ui/pages/dashboard_page.py` | 无设备时指标为 0，无引导提示 |
| P2-12 | `ui/pages/alarm_page.py` | 无报警时表格空白 |
| P2-13 | `ui/pages/ae_page.py` | Hit 历史表初始空白 |
| P2-14 | `ui/pages/analysis_page.py` | 结果面板无数据时显示"—" |
| P2-15 | `ui/` 全局 | 无 QProgressBar，长操作无进度反馈 |
| P2-16 | `ui/pages/device_page.py` | "扫描设备"按钮可点击但未实现 |

### 可恢复性（3 项）

| # | 文件 | 问题 |
|---|------|------|
| P2-17 | `ui/pages/device_page.py` | 删除设备后通道表未清空 |
| P2-18 | `ui/pages/device_page.py` | 删除/清除无软删除机制 |
| P2-19 | `ui/` 全局 | 无 Ctrl+Z 撤销 |

### UI 标签 / 单位 / 一致性（6 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-20 | `ui/pages/device_page.py` | 139 | AE/UHF 采样率无区分提示 |
| P2-21 | `ui/pages/alarm_page.py` | 203-249 | "防抖次数"无业务含义说明 |
| P2-22 | `ui/widgets/trend_chart_widget.py` | 104 | Y 轴固定"幅值(mV)"不适用多曲线 |
| P2-23 | `ui/widgets/ae_*.py` | — | AE 控件标题 11px 偏小 |
| P2-24 | `ui/` 全局 | — | 按钮样式在 4 个文件中独立定义 |
| P2-25 | `ui/design_tokens.py` | — | 部分 QSS 绕过令牌直接内联字号 |

### 响应式 / 布局（4 项）

| # | 文件 | 问题 |
|---|------|------|
| P2-26 | `ui/pages/realtime_monitor_page.py` | 1024px 下峰值列表仅 126px |
| P2-27 | `ui/pages/ae_page.py` | 三栏等宽，声源定位图过小 |
| P2-28 | `ui/main_window.py` | 关于对话框 600x500 覆盖大部区域 |
| P2-29 | `ui/main_window.py` | 1024-1199px 导航折叠仅图标 |

### 导航 / 无障碍（6 项）

| # | 文件 | 问题 |
|---|------|------|
| P2-30 | `ui/main_window.py` | 无面包屑/页面位置指示器 |
| P2-31 | `ui/main_window.py` | F1-F8 快捷键无可见提示 |
| P2-32 | `ui/widgets/nav_menu.py` | 图标 Unicode 特殊字符 |
| P2-33 | `ui/` 全局 | 状态/报警仅靠颜色区分 |
| P2-34 | `ui/` 全局 | 无 setTabOrder |
| P2-35 | `ui/` 全局 | 按钮无 setEnabled 状态管理 |

### 可维护性（7 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-36 | `ui/pages/analysis_page.py` | 1592 | 单文件含 4 类 |
| P2-37 | `core/services/acquisition_service.py` | 824 | 超大文件 |
| P2-38 | `ui/design_tokens.py` | 769 | 超大文件 + DarkColors 无继承 |
| P2-39 | `ui/controller.py` | — | 超大文件 + numpy 重复导入 |
| P2-40 | `ui/widgets/prpd_widget.py` | 624 | 超大文件 |
| P2-41 | `core/communication/fpga_protocol.py` | 594 | 超大文件 |
| P2-42 | 多个文件 | — | 公有方法缺 docstring |

### 类型安全（3 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-43 | `core/foundation/config_store.py` | 50 | set_db_repo 缺类型注解 |
| P2-44 | `core/services/storage_service.py` | 45 | db_manager 缺类型注解 |
| P2-45 | `core/services/acquisition_service.py` | 91 | _protocol: Any → Optional[FpgaProtocol] |

### 硬编码 / 冗余（11 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-46 | `ui/widgets/prpd_widget.py` | 532,535 | 散点 [:5000] 硬截断 |
| P2-47 | `core/services/acquisition_service.py` | 111-114 | AE 报警阈值硬编码 |
| P2-48 | `main.py` | 128 | 版本号与 core/version.py 重复 |
| P2-49 | `ui/pages/settings_page.py` | 303-312 | 默认值与构造函数重复 |
| P2-50 | `core/foundation/config_store.py` | 152-153 | 备份失败静默 |
| P2-51 | `ui/pages/settings_page.py` | 80-81 | 配置加载异常无日志 |
| P2-52 | `ui/controller.py` | 447-453 | TDOA 模拟数据硬编码混入生产 |
| P2-53 | `core/services/acquisition_service.py` | 80 | PRPD_DECAY_FACTOR 应可配置 |
| P2-54 | `core/services/acquisition_service.py` | 324 | samples.tolist() numpy→list 开销 |
| P2-55 | `core/processing/ring_buffer.py` | 45 | extend(List[Any]) → Iterable[Any] |
| P2-56 | `core/communication/udp_driver.py` | 229 | addr: tuple → tuple[str, int] |

### 其他代码质量（4 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-57 | `core/foundation/data_bus.py` | 240-241 | unregister() O(n) 列表推导 |
| P2-58 | `core/services/acquisition_service.py` | 324 | samples.tolist() 每帧内存分配 |
| P2-59 | `core/communication/fpga_protocol.py` | 54-64 | DataType(IntEnum) 未充分利用 |
| P2-60 | `ui/pages/alarm_page.py` | 375 | 通过 self.parent() 迂回访问窗口 |

---

## 历史遗留（4 项，暂不处理）

| # | 类别 | 问题 | 暂缓原因 |
|---|------|------|----------|
| L-1 | 架构 | 无全局 VERSION 常量 | 影响小 |
| L-2 | 算法 | PRPS 相位映射需外部同步信号 | 有 TODO |
| L-3 | UI | 设备页网格响应式列数 | 固定 3 列够用 |
| L-4 | UI | PRPS ColorBar 字号 | 无独立控件 |

---

## 修复日志

### v1.0.7 (2026-06-16) — 37 项修复

**P0（5/5 ✅）** — `ac22c10`
- 仪表板指标接入真实数据、_main_window @property 修复
- ConfigStore 双重检查锁定、按钮文字 bug、waveform tooltip

**P1（32/32 ✅）**
- `ac22c10`: 设备保存反馈、PRPD 文案、9处 debug→warning
- `82cc3a5`: TrendPage 空状态、4处 except:pass、空 CSV 检测
- `d8e150a`: release_device 15循环去重(-40行)、_sample_rates 加锁
- `956ff09`: PRPD/PRPS 管线去重(-70行)、常量提取
- `0fb17a4`: config_store 日志、store_pd_event 校验、NaN/Inf 防护
- `0fb17a4`: RingBuffer 优化、死代码标记、UDP 参数化

**P2 附带（3 项）** — structlog try/except、DatabaseLogHandler 标记、__import__ 修正

---

## 统计总览

| 严重程度 | 已修复 | 待优化 | 状态 |
|---------|--------|--------|------|
| **P0** | 5 | 0 | ✅ |
| **P1** | 32 | 0 | ✅ |
| **P2** | 3 | 60 | ⏸ |
| **历史遗留** | 0 | 4 | ⏸ |
| **合计** | **40** | **64** | |
