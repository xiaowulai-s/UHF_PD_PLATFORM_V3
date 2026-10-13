# 迁移覆盖对照表（旧 PySide6 `ui/` → 新 Electron `desktop/`）

> **复核日期：2026-10-09。** 旧界面已归档在 `_archive/ui-offline-20261008/ui/`；本表按当前工作区代码复核，纠正了 2026-10-08 初稿中的过期状态。
>
> **怎么读状态**：✅ 已迁移/有等效功能；🟡 部分迁移、体验/范围不同或现实现有缺陷；❌ 当前新端无对应功能；➖ 旧代码是未接入的原型/死路径，不作为已交付功能计算。
>
> 旧归档快照按 Python 源文件重数为 **77 个 `.py` 文件、23,181 行**。不以源代码行数作为“功能丢失比例”：同一个功能会拆成多个模块，归档里也含未接线的原型与框架代码。

## 结论摘要

- **核心业务页大多已覆盖**：仪表板、实时波形/FFT/PRPD/PRPS、趋势、报警、设备配置、分析、AE、日志均有新视图；实验数据导入在 2026-10-08 已补上。
- 初稿把“监测页峰值列表”误判为缺失：峰值表已在 `components/charts/fft-chart.vue`，只是从独立卡片改成 FFT 面板内嵌；不需再做一份。
- **仍值得处理的真实差异**：运行时数据源启停/移除控制、实验数据筛选与幅值分布图、完整报表导出、AE 历史/散点/导出、进程 CPU/RSS 面板、暗色主题、PRPD 3D。
- **一个看似迁移了但实际可能坏掉的按钮**：日志页导出仍用 `blob:` + `<a download>`；Electron `loadFile` 的 `file://` 页面上该方式已实测静默不落盘。应改用已有的主进程文件保存桥。
- 旧 AE 声源定位有界面和调用链，但路由每 10 个 hit 都调用 `simulate_arrival_times()` 合成到达时刻；因此它是**演示级定位**，不是从真实多传感器采集得到的源位置。新端没有丢失经现场数据验证过的定位能力。

## 一、页面级对照（9 个主业务页 + 2 个 V2 变体）

| 旧页面 | 新端对应 | 当前覆盖 | 主要差异/备注 |
|---|---|---|---|
| `dashboard_page.py` | `views/dashboard-view.vue` | ✅ | 指标卡、快照、告警、跨进程吞吐可见。旧 `PerformanceMonitorWidget` 的进程 CPU/RSS 细项未迁。 |
| `realtime_monitor_page.py` | `views/monitor-view.vue` + `components/charts/*` | ✅ | 波形/FFT/PRPD/PRPS、AE/UHF 显式模式均有；FFT 峰值前 10 项内嵌于 FFT 面板（MHz/幅值/类型），旧版独立右侧表只是布局变化。 |
| `trend_page.py` | `views/trend-view.vue` | ✅ | 1h/24h/7d/30d 查询真实事件按箱聚合；旧页面的未播种随机示例趋势没有照搬。**趋势预测控件另见下表**。 |
| `alarm_page.py` | `views/alarm-view.vue` | ✅ / 增强 | 历史查询、筛选、批量确认有持久化；旧版确认只改表格状态。旧版有“删除选中/清空”，但仅删除当前表格行、没有数据库写路径；新端不提供物理删除，以保留告警追溯记录。 |
| `device_page.py` | `views/device-view.vue` | 🟡 | 设备/通道配置 CRUD 已接 RPC、可持久化；旧页底部另有运行时数据源状态/启停/移除控件，新端没有对应 UI/RPC 调用。旧版“新增数据源”回调本身是 TODO。 |
| `settings_page.py` | `views/settings-view.vue` | 🟡 | 新端有阈值、通道耦合、推流速率、界面偏好；旧端的网络绑定/端口/超时/缓冲、数据库路径/启动备份/保留天数/最大事件数及暗色切换没有一一对应。网络现由 sidecar 固定回环地址+动态端口/token，DB 状态目录由运行时路径管理；其余设置项需单独决定是否重新开放。 |
| `log_page.py` | `views/log-view.vue` | 🟡 | 日志级别过滤、搜索、窗口化列表都有；CSV 导出代码存在，但走 `blob:` 下载，在本应用 `file://` origin 下不可靠，需改用 `window.files.saveText`。 |
| `ae_page.py` | `views/ae-view.vue` + `components/charts/ae-panel.vue` + `gdop-map.vue` | 🟡 | 统计、参数、分桶直方图、最近 hit、可编辑传感器阵列、真 GDOP 热图已迁；AE 特征散点、较完整历史表及 CSV/PDF 导出未迁。旧定位调用使用合成 TDOA，不是硬件实测定位。 |
| `analysis_page.py` | `views/analysis-view.vue` + `core/protocol/data_rpc.py` | 🟡 | 文件导入（9 种格式）、整体类型识别、逐周期类型分布、质量摘要、散点、数据库事件表、事件 CSV 导出已有；缺旧分析页的筛选控件、幅值分布直方图、JSON/Excel/Word 报告导出。 |
| `dashboard_page_v2.py` | 单一 `dashboard-view.vue` | ➖ | 旧暗色运维风格变体未保留为独立页面；新端只有亮色主题。 |
| `analysis_page_v2.py` | 单一 `analysis-view.vue` | ➖ | V2 是另一套分析页视觉/实现，不形成独立业务路由。 |

新端还有不在旧导航中的 `views/probe-view.vue`（渲染性能诊断页，不属于业务页）。产品导航是 F1–F9 九页；probe 是隐藏诊断路由，不应把“10 个视图文件”误数成 10 个业务页。

## 二、已迁移的关键控件

| 旧模块/功能 | 新端对应 | 结论 |
|---|---|---|
| `waveform_widget.py` | `waveform-chart.vue` | ✅ 缩放、游标、暂停、时间基等；旧版未接线的触发线没有照搬。 |
| `fft_widget.py` + 峰值表 | `fft-chart.vue` | ✅ 频谱、基波/谐波标注、噪声底、频带、前 10 峰值。峰值表不是缺项。 |
| `prpd_widget.py`（散点/热力/密度） | `prpd-chart.vue` | ✅ 三种实时显示模式；旧色带自定义调用点为 0，未迁自定义色带。 |
| `prps_widget.py` + PRPS 3D | `prps-chart.vue` | ✅ PRPS 热图/3D。**更正（2026-10-12）**：旧 `prpd_3d_renderer.py` 的轴是 相位/周期/幅值（色条=幅值、周期 %50），docstring 自称 3D **PRPS**，与 `plot_PRPS.py` 一致——即旧界面 `prpd_widget.py` 的 `scatter_3d` 模式其实是 PRPS 3D，本行已经覆盖，它不是"PRPD 3D"。 |
| `trend_chart_widget.py` | `trend-view.vue` | ✅ 数据查询与时窗交互迁移；趋势预测是另一项。 |
| `ae_statistics_widget.py` / `ae_parameters_widget.py` | `ae-view.vue` | ✅ 四张统计卡、参数明细；口径注释比旧页更明确。 |
| `localization_heatmap_widget.py` | `gdop-map.vue` + `lib/gdop.ts` | ✅ 改为真二维 TDOA 几何精度 GDOP；不把几何精度冒充声源位置。 |
| `data_import_widget.py` | `data.import_file` RPC + analysis 导入区 | ✅ 已补。支持 csv/xlsx/xls/txt/npy/npz/h5/hdf5/json；只读，不落入实时采集库。 |
| `prpd_classification_widget.py` | 同一导入 RPC 的分类结果 | 🟡 对导入文件做整体识别/逐周期分布；实时流事件仍没有类型标签。 |
| `device_list/detail_widget.py` | `device-view.vue` | ✅ 配置 CRUD 及通道编辑已持久化；运行时来源控制另见第三节。 |
| `sensor_array_config_dialog.py` | `ae-view.vue` 内联表格 | ✅ 新端位置写入本地 prefs；旧对话框仅返回临时 dict、重启即丢。 |
| `log_page.py` 搜索/过滤 | `log-view.vue` | ✅；导出落盘问题见上表。 |
| `nav_menu/status_indicator/card_panel` | `App.vue` 侧栏/连接状态/Tailwind 卡片 | ✅ 框架与组件形式变化。 |
| `controller_config.py` | `core/foundation/hardware_config_loader.py` | ✅ 已迁入内核，解除 sidecar 对旧 UI 的依赖。 |

## 三、确认的真实缺口 / 有条件迁移项

| 优先级 | 功能 | 证据与影响 | 处理建议 |
|---|---|---|---|
| 高 | **运行时数据源控制** | 旧 `device_page.py` 把 `start_source_requested` / `stop_source_requested` / `remove_source_requested` 接到 `source_manager`；新端 `device-view.vue` 只有保存设备配置，前端全仓没有 `acquisition.start_simulator` / `acquisition.stop` 调用。旧“添加来源”本身是 TODO。 | 如果目标机需要不重启应用切换/停采，增加 sidecar 的来源列表/启停 RPC 和设备页入口；否则明确定位为“随应用配置启动，页面只管理持久化设备”。 |
| 高 | **日志 CSV 导出实际落盘** | `log-view.vue` 使用 `URL.createObjectURL` + `<a download>`；同一 Electron `file://` 环境的 Blob 下载已实测不生成文件。 | 接已有 `window.files.saveText`（保存路径由主进程原生对话框确定），补“真点+文件读回”验收。 |
| 中高 | **导入数据的分析交互** | 新端已能导入/分类/画 PRPD，但旧分析页还有最低幅值过滤、时间范围筛选、按放电类型筛选、重置，以及幅值分布直方图；这些未在导入区重现。旧“时间范围”代码实际按数据序号比例取尾段，不是时间戳查询。 | 先决定旧筛选项是否保留；最低幅值和分类筛选可在前端对导入数组过滤，幅值直方图可复用同批数据。不要把序号比例伪称时间戳筛选。 |
| 中 | **分析报告导出 JSON / Excel / Word** | 旧 `ReportExportWidget` 实际按钮是 JSON/Excel/Word（不是 PDF）；新分析页只有事件 CSV。`core/data/report_exporter.py` 仍在，但图表路径分支使用 `os.path.exists`、模块没有 `import os`，且无 RPC。 | 先修并测试内核导出器，再决定经 RPC 保存，或在主进程文件保存桥落盘；按格式逐项验收。 |
| 中 | **AE 记录、散点与历史导出** | 新端有最近 hit 面板（有限滚动列表）、统计和直方图；无 AE 特征散点，也无历史表的 CSV/PDF 导出。旧页历史初始数据来自示例数据，实时记录仍是进程内存，不等于持久化历史库。 | 如果需要复盘/导出，先定“本次会话”还是持久化历史；之后补散点与对应导出。 |
| 中 | **进程 CPU/RSS 面板** | 旧 `dashboard_page.py` 可挂 `PerformanceMonitorWidget`；新端的吞吐表只显示帧数/限流/丢弃，probe-view 是绘制耗时探针，两者都不是进程 CPU/RSS。 | 长稳/现场诊断确有需要时，将只读进程统计作为独立监控卡；不应把传输吞吐当内存监控的等价替代。 |
| 设计决策 | **暗色主题切换** | **已交付（2026-10-12）**：`styles/tokens.css` 加 `[data-theme="dark"]` 覆盖块（取值来自 `config/themes/dark_monitoring.json` 的 background/border/text/status），`prefs.theme` 持久化并在设置页可切。覆盖范围是 **DOM 令牌 + canvas（每次绘制重取）+ WebGL（两个 3D 组件订阅 chartTheme 重建材质）+ uPlot（换 key 强制重建）**，不是局部换色。品牌色刻意未换（详见 tokens.css 里的说明）。 |
| 低/视觉功能 | **PRPD 3D** | **已处置（2026-10-12）**。原判据有误：旧 `prpd_3d_renderer.py` 的轴是 相位/周期/幅值，实为 PRPS 3D（新端 `prps-chart.vue` 已覆盖）。用户裁决新做**真正的** PRPD 3D，已实现：`prpd-3d.vue`，轴 = 相位 × 幅值 × 强度（计数，取对数），作为 `prpd-chart.vue` 的第 4 个显示模式，纯前端渲染（同一份 360×256 计数矩阵，不发 RPC）。 | 已交付并截图验收；不需要再排期。 |
| 需领域/协议 | **真实 AE 声源定位** | 旧路由确实触发 2D/3D 控件，但 `signal_router.py` 每 10 个 hit 调 `simulate_arrival_times(source_x=0.5, source_y=0.5)` 合成数据。新端 GDOP 是阵列几何精度，不输出源坐标。 | 不能当普通 UI 遗漏补画。要真实定位，先接多传感器同步到达时间与坐标，再改协议/算法。 |

## 四、不是已交付功能的旧代码（从缺失数里剔除）

| 旧模块 | 复核结果 |
|---|---|
| `add_device_dialog.py` | 有对话框类，但旧 `device_page.py::_on_add_device` 仍写着“TODO: 实现添加设备对话框”；新端 inline CRUD 是实际增强，不是退化。 |
| `tag_editor_dialog.py` / 设备标签 | 旧列表的数据来自示例设备；保存/通道更新在旧页是 TODO，核心数据库模型及新 `device.save` 白名单都没有 tags。它不是持久化标签系统。新端没迁标签编辑器，但也没有丢掉已持久化能力。 |
| `MultiDeviceSyncWidget` / `DischargePatternComparator` / `TrendPredictionWidget` | 归档代码里找不到业务页的构造/导入调用点，属于孤立原型，不应直接算作已使用功能遗漏。 |
| `AEHistoryWidget` 数据源 | 旧 AE 页启动时调用 `get_example_ae_hits()` 填示例历史，后续接收内存 hit；不是数据库历史记录。新端少了表格/导出 UX，但没有丢掉持久化历史。 |
| AE 2D/3D 定位的结果 | 旧 UI 有完整绘图控件，但到达时刻是模拟器造的；不代表现场硬件定位曾可用。 |
| `controller_config.py`、三个旧 controllers、动画/异步/窗口工具 | 配置已迁内核；数据总线/生命周期由 sidecar 接管；动画与事件循环由 Vue/Chromium/Electron 承担。 |
| 老版网络绑定与数据库路径设置 | 旧设置页把这些值写入 ConfigStore，但现架构刻意固定 sidecar 在 127.0.0.1、使用随机端口+一次性 token，并由 runtime_paths 管理数据目录；不能照旧直接暴露网络绑定。 |

## 五、需要更正的初稿结论

1. **已补数据导入与文件分类**：分析页行、`data_import_widget` 行和“导入链”说明必须标“已补/部分”，不能再写“实验数据导入未迁移”或“分类器没有 RPC”。分类只针对导入文件，实时事件仍没分类标签。
2. **峰值表已迁移**：FFT 子组件里按幅值降序取前 10；旧版独立卡布局不等于功能缺失。
3. **日志导出不能简单标等效**：按钮和 CSV 组装代码存在，但 Electron `file://` 下载路径不可靠，列为导出落盘缺陷。
4. **声源定位要避免误报**：旧 UI 的 TDOA 输入由模拟器合成；新端没有物理定位，但这不是丢掉经硬件验证的能力。
5. **不要用代码行数计算“遗漏百分比”**：旧归档包含原型、示例数据和未接线控件。按用户可见流程逐项比较，并注明实际调用/持久化证据。

## 六、必要性评估

| 等级 | 项目 | 评估 | 建议 |
|---|---|---|---|
| **必须补** | 日志 CSV 真正落盘 | 不是新功能需求，而是现有导出按钮在 Electron `file://` 下可能静默失败；用户点了却没有文件是功能缺陷。 | 修复并验收，范围小，先做。 |
| **应补** | 导入数据的基本筛选 + 幅值分布 | CSV 导入已恢复，但旧分析页的最低幅值/类型筛选与幅值直方图缺失。没有这些仍能看散点和分类，但“分析”能力明显不完整。 | 补最低幅值过滤、可解释的类型过滤与幅值直方图。旧“时间范围”按行序号切片，不是真时间戳；不建议原样搬，只有在文件有可靠 timestamp 列时才做时间筛选。 |
| **建议补** | 分析报告导出 | 新端只有数据库事件 CSV；旧分析流程还提供 JSON/Excel/Word 报告。若现场要交付分析结果，缺项明显；若仅查看趋势/散点，则可延后。 | 先修 `ReportExporter` 的 Word 图表路径 `os` 缺失，再定格式；建议首批 JSON+Excel，Word 在确有正式报告需求时做。统一经主进程原生保存对话框落盘。 |
| **条件补** | 运行时数据源启停/移除 | 旧页 start/stop/remove 有连接到 source_manager，新端不能通过设备页改变正在运行的 source；但旧 add-source 回调本身是 TODO，新端按启动参数/配置启动也可能符合当前部署方式。 | 先决定目标机是否要求“不重启应用切换/停采”。需要则补 sources list/status/start/stop/remove API + UI；不需要则在设备页/文档写清重启生效边界。 |
| **按现场需求** | AE 会话历史/散点/导出 | 旧 AE 历史不是可靠的持久化数据库历史（含示例数据与进程内记录）；新端已有近期 hit 和统计。主要差异是记录表更短、没有散点和 CSV/PDF 导出。 | 先确认需要“本次运行记录”还是跨重启追溯。前者可扩展内存环形列表；后者须先有 AE 持久化查询通路。 |
| **可选运维增强** | CPU/RSS 监控 | 对长稳排障有帮助，但不是采集/分析主流程；现有 probe 与吞吐表测的是绘制/传输，不替代 CPU/RSS。 | 只有现场需要在 UI 里诊断时做；否则继续使用 soak/系统工具。 |
| **已交付** | 暗色主题 | **2026-10-12 交付**：完整 token 主题（DOM + canvas + WebGL + uPlot 四类消费方都覆盖），设置页可切、持久化。 | 无（如需换成稿子里的琥珀强调色，那是另一次改版，见 tokens.css 的说明）。 |
| **低优先视觉项** | PRPD 3D | **已交付（2026-10-12）**：真 PRPD 3D（相位×幅值×对数强度）已作为 PRPD 面板第 4 个模式实现并验收。 | 无。 |
| **暂不补** | 真实 AE 声源定位 | 旧界面显示的是用 `simulate_arrival_times()` 合成输入的演示结果；新端不输出源点是诚实边界，不是丢失了实测能力。 | 等多传感器同步到达时刻/坐标进入内核协议后再立项，不能先画假源点。 |
| **不补（无证据）** | 多设备同步、放电模式对比、趋势预测 | 旧归档中这些控件无业务页接线，属于孤立原型；目前没有需求/数据链佐证。 | 不迁移。若用户确认需要，再定义数据契约与算法后单独立项。 |
| **不补（旧功能未完成）** | 设备标签编辑、独立新增设备对话框 | 标签未进入数据库模型，保存/通道更新仍 TODO；旧新增对话框也未接入页面。新端已提供可持久化的设备 inline CRUD。 | 不补旧版标签 UI；若需要标签，先设计持久化字段与筛选语义。 |

## 七、必要内容的分阶段实施计划

| 阶段 | 范围 | 主要实施项 | 验收标准 | 前置/边界 |
|---|---|---|---|---|
| **P0：修复现有导出** | `log-view.vue` | 用现有 `window.files.saveText` 替换 Blob 下载；保留 CSV 表头、UTF-8 BOM、过滤后内容。 | Electron 真点“导出”→ 原生保存对话框→选择路径→文件存在且 CSV 行数/中文可读与当前列表吻合；取消保存不显示成功。 | 仅修复日志导出，不扩成报表系统。 |
| **P1：补齐导入分析闭环** | `analysis-view.vue` + 必要 RPC/测试 | 最低幅值过滤；按已导入分类结果筛选（先明确逐周期/分组口径，不把单一整体分类硬套到每条事件）；幅值直方图；筛选结果点数与散点同步；重置回原始导入集合。可选补拖放/导入前预览。 | 用 `tests/PD_data.csv` 真点导入；阈值升高后过滤点数单调不增；分类筛选只显示所选类；reset 恢复 6657 点；直方图各 bin 计数和等于当前筛选点数；多选文件各自隔离状态。 | 文件的“时间范围”旧口径按行号/百分比，首期不迁；只有文件含可识别 timestamp 列才定义真实时间筛选。保留 50k 上限并清楚提示。 |
| **P2：分析报告导出**（2026-10-10：内核侧已入库 759984c；前端按决定暂不入库；JSON/Excel/Word 三种都已在冻结产物上实测可导并重新打开核对） | `core/protocol/report_rpc.py` + `core/data/report_exporter.py` + 主进程 `file:save-binary` | 修 Word 图表路径缺失的 `import os`；定义只读导出输入；选 JSON、Excel、Word 的一期范围；用原生保存对话框保存，不能把任意路径写权限暴露给 renderer。 | JSON 可解析；Excel 用 openpyxl 重新打开并核对关键字段/类型分布；Word 用 python-docx 重新打开；Word 有图与无图两条路径都通过；取消对话框无副作用。 | 本阶段不含 PDF（旧控件实际提供 JSON/Excel/Word）。 |
| **P3：运行时数据源管理（条件项）** | sidecar source RPC + `device-view.vue` | 先暴露 `source.list/status`，再按决策补 start/stop/remove；新增来源类型/配置验证另定，不把持久化设备 CRUD 与运行时 source 混为一体。 | 不重启应用即可停止/重启选中来源；source 状态与 stats/可靠 `source` 事件一致；移除有确认且不会误删设备配置；无 source 时有明确空态。 | 开工前需产品确认目标机确实需要在线启停；若工作流始终重启应用切换参数则不做。 |
| **P4：AE 历史与现场诊断（条件项）** | AE 存储/RPC、AE 页面、运维 UI | 先确认 AE hit 是否需跨重启；若要追溯，先做持久化 schema/query，再做表格、散点与导出；CPU/RSS 独立运维卡另评估。 | 清晰区分本次会话累计与数据库历史；分页/上限如实显示；散点与表格同一数据源；重启后持久化历史可回读。 | 不把 200 条 ring buffer 当完整历史；本阶段不做真实定位。 |
| **P5：视觉/领域扩展** | 主题、PRPD 3D、TDOA | 暗色/PRPD 3D 按用户决策；真实 TDOA 是独立的内核协议与同步采集项目。 | 各自独立验收，不能作为 P0/P1 的阻塞项。 | 暗色需检查 canvas/WebGL 对比度；TDOA 前置多通道时间同步。 |

**进度（2026-10-10 复核）**：P0（日志 CSV 走 `window.files.saveText`）与 P1（最低幅值/类型筛选 + 32 桶幅值直方图 + 重置）都已在工作区实现，但随他人的在途改动一起、尚未提交。P2 内核侧已入库：`report.export` 生成字节回传、`report.formats` 自报可导格式，落盘由主进程原生对话框决定；真点验收用 CDP 给 `input[type=file]` 塞真实文件路径导入 `tests/PD_data.csv`（6657 点）后点 Excel/JSON，文件落盘并分别用 openpyxl 与 json 重新打开核对通过；装 `python-docx` 并重出冻结内核（194MB）后，Word 按钮自动解除禁用，真点导出 122.8KB 的 .docx，重新打开确认 `inline_shapes == 1`。P3–P5 仍需产品/现场决策。
**建议立即执行的仅 P0 + P1**；P2 虽有价值，但先确认需要哪些报告格式；P3–P5 需产品/现场决策。此表是实施计划，不表示这些阶段已开始。

**进度（2026-10-12 追加，P3–P5 已获用户裁决"全部纳入、逐项做"）**：
- **P3 运行时数据源管理**：已交付。内核新增 `source.list/start/stop/remove/set_active`（`core/protocol/source_rpc.py`，全部主线程），`DataSourceManager` 补 `start_source`/`stop_source`；设备页新增「运行时来源」面板。
- **P4 AE 历史持久化**：已交付。新表 `ae_hits`（补上 `pd_events` 装不下的 `rms/asl_db/signal_strength/avg_frequency_khz`）+ `PersistenceSubscriber` 增订 `ae_hit_detected` + `storage.query_ae_hits`；AE 页加「本次会话 / 数据库历史」切换、特征散点与 CSV 导出。
- **P5b PRPD 3D**：已交付（见上表更正）。**P5c 实时放电类型标签**：已交付（新增 `discharge_type` 可靠帧 + PRPD 面板徽标）。
- **P5a 暗色主题**：已交付。`[data-theme="dark"]` 覆盖底层令牌 + `prefs.theme` + 设置页开关；
  四类图表消费方（DOM/canvas/WebGL/uPlot）各自换色，真选验收通过（浅↔深双向）。
- **P4b 内核 CPU/RSS 监控卡**：已交付（`core/utils/process_stats.py`，零依赖 ctypes/stdlib；仪表板「内核进程」面板）。
