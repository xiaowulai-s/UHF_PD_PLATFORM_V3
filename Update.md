## 程序问题清单

> 审查日期: 2026-06-12 | 基准版本: v1.1.0
> 全代码重扫（MCGS 已移除），识别 **39 个待解决问题**

---

## P0 — 必须立即修复（2 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P0-1 | `ui/pages/device_page.py` | 369-391 | `get_session()` 获取数据库会话后从未 `close()`，每次保存参数泄露一个连接 |
| P0-2 | `ui/controller.py` | 461,471 | `self._main_window` 从未初始化赋值，`_on_device_status_for_ui` / `_update_status_stats` 中设备状态更新代码为死代码，状态栏数据永远不刷新 |

---

## P1 — 应当修复（8 项）

### 线程安全（4 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-1 | `core/services/acquisition_service.py` | 320,457 | `_sample_rates[key]` 在 `_process_uhf/ae_waveform` 中未持锁写入，多通道并发竞态 |
| P1-2 | `core/services/acquisition_service.py` | 364-387, 549-563 | `_prpd_frame_count` / `_prpd_decay_count` / `_prps_frame_count` / `_observed_max_amp` 读写未持锁 |
| P1-3 | `core/services/acquisition_service.py` | 607-696 | `_ae_alarm_state` / `_ae_signal_loss_counters` / `_ae_noise_counters` 在 `_check_ae_alarms` 中读写未持锁 |
| P1-4 | `core/foundation/config_store.py` | 42-44 | `instance()` 单例方法无双重检查锁定，多线程可能创建多个实例 |

### 资源泄漏 / 错误处理（2 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-5 | `ui/pages/analysis_page.py` | 147-152 | `openpyxl.load_workbook()` 打开的工作簿无 `try/finally` 保护，异常时 `wb` 永不关闭 |
| P1-6 | `ui/pages/settings_page.py` | 79 | `except Exception: self._config = {}` — 配置文件加载失败静默置空，所有配置丢失且无任何 UI 警告 |

### 代码重复（1 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-7 | `core/services/acquisition_service.py` | 308-603 | `_process_uhf_waveform` 与 `_process_ae_waveform` ~200 行高度重复（FFT/PRPD衰减/PRPS节流/动态幅值扩展），提取公共 Pipeline 可减少 40%+ 代码 |

### 关键 TODO（1 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P1-8 | `core/services/acquisition_service.py` | 402-404 | `# TODO: 当前假设波形覆盖恰好一个工频周期` — 非标准采样率下相位映射错误，影响 PRPD/PRPS 精度 |

---

## P2 — 建议优化（12 项）

### 错误处理（5 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-1 | `ui/animation_scheduler.py` | 327-328 | `except Exception: pass` — 动画注销失败静默吞异常 |
| P2-2 | `ui/pages/ae_page.py` | 285-286 | `except Exception: pass` — TDOA 定位更新失败完全静默 |
| P2-3 | `ui/widgets/prpd_widget.py` | 331, 583 | 2 处 `except Exception/ImportError: pass` — matplotlib 字体/scipy 导入失败静默 |
| P2-4 | `ui/main_window.py` | 250-251, 475-477 | FlashWindow 失败 pass；`_setup_menu()` 方法体为空，应加注释或删除 |
| P2-5 | `core/utils/serial_utils.py` | 149-150 | `except Exception: pass` — 串口清空失败静默 |

### 硬编码值（3 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-6 | `ui/controller.py` + `acquisition_service.py` | 15+ 处 | 采样率 `100_000_000` / `2_000_000` 在多个文件中重复硬编码，应从 `sensor_types.py` 统一引用 |
| P2-7 | `ui/pages/device_page.py` | 304, 353-357 | 耦合类型列表、频段范围硬编码，应从 `sensor_types.py` 导入 |
| P2-8 | `ui/pages/settings_page.py` | 265-273 | `_on_reset` 中所有默认值硬编码，应从配置常量统一 |

### 输入验证缺失（2 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-9 | `ui/pages/device_page.py` | 323-337 | `_on_save_params` 不验证 host 格式（接受任意字符串），不检查设备 ID 重复 |
| P2-10 | `ui/controller.py` | 481-529 | `start_simulator` 中 fps 无下限检查（可传 0 或负数） |

### 类型安全 / None 检查（2 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P2-11 | `ui/controller.py` | 270-271, 439-442 | `_waveform_frame_count` / `_ae_hit_count` 通过 `getattr` 懒初始化，未在 `__init__` 声明，类型不明确 |
| P2-12 | `ui/pages/analysis_page.py` | 168-179 | `_load_excel` 中 `float(row[idx])` 前未检查 `None`，全依赖 try/except 容错 |

---

## P3 — 低优先级（17 项）

### 静默异常（10 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P3-1 | `core/foundation/config_store.py` | 115-116 | DB 查询设备配置失败 `except Exception: pass` |
| P3-2 | `core/foundation/config_store.py` | 131-132 | DB 设备列表读取失败 `except Exception: pass` |
| P3-3 | `core/foundation/config_store.py` | 152-153 | 备份文件复制失败 `except Exception: pass` |
| P3-4 | `core/foundation/data_bus.py` | 335-336 | DataBus reset 资源释放失败 `except Exception: pass` |
| P3-5 | `ui/controller.py` | 107-108, 113-114 | set_db_manager 异常仅 log debug，无用户反馈 |
| P3-6 | `ui/controller.py` | 264-274, 302-310 | 波形/FFT UI 更新异常仅 log debug，用户不可见 |
| P3-7 | `ui/controller.py` | 332-333, 353-354 | PRPD/PRPS UI 更新异常仅 `logger.exception` |
| P3-8 | `ui/controller.py` | 376-382 | Beep 报警通知失败仅 log debug |
| P3-9 | `ui/controller.py` | 391-392 | AE hit UI 更新异常仅 log exception |
| P3-10 | `ui/controller.py` | 367 | Dashboard 报警更新异常仅 log debug |

### 硬编码与结构（5 项）

| # | 文件 | 行 | 问题 |
|---|------|-----|------|
| P3-11 | `ui/main_window.py` | 226 | 托盘图标路径 `"assets/icons/ems.png"` 硬编码，ems.png 已删除，应有回退 |
| P3-12 | `ui/main_window.py` | 63 | `KEY_TO_INDEX` 字典推导依赖 `PAGE_KEYS` 顺序，耦合脆弱 |
| P3-13 | `ui/design_tokens.py` | 700-722 | `adjust_color` 函数作为独立 `@staticmethod` 定义再绑定到 `Colors`，结构不清晰 |
| P3-14 | `ui/design_tokens.py` | 328-411 | `DarkColors` 与 `Colors` 无继承关系，属性同步修改易遗漏 |
| P3-15 | `ui/pages/device_page.py` | 211-226 | `_on_device_selected` 未处理 `_devices` 中找不到 `selected_device_id` 的边界情况 |

### 历史遗留（2 项）

| # | 文件 | 问题 | 原因 |
|---|------|------|------|
| P3-16 | `core/processing/prps_processor.py` | PRPS 相位映射过于简单 | 需外部同步信号，有 TODO |
| P3-17 | UI 全局 | 设备页网格响应式列数 + PRPS ColorBar 字号 | 功能影响小 |

---

## 统计总览

| 严重程度 | 数量 | 关键问题 |
|---------|------|----------|
| **P0** | 2 | DB session 泄漏、状态栏死代码 |
| **P1** | 8 | 线程安全 x4、资源泄漏 x2、代码重复、关键 TODO |
| **P2** | 12 | 静默异常 x5、硬编码 x3、输入验证 x2、类型安全 x2 |
| **P3** | 17 | 静默异常 x10、结构优化 x5、遗留 x2 |
| **合计** | **39** | |
