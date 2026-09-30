# 上位机前端重构进度计划表

**目标**：`ui/`（PySide6，23,462 行 / 79 文件）全量重写为 Electron 33 + Vue 3 + TypeScript + Vite；`core/`（12,798 行处理与采集）保留为无界面 sidecar 进程。

**基线约定**：观感以 `.design/uhf-pd-light/` 的 9 页 HTML 稿为准；行为以 git 在册的 v1 页面为参考，`*_v2.py` 仅在实现更优处借用。

**工作量单位**：人日（pd），按 1 名熟悉本仓库的开发者全职投入估算，**尚未用第一个里程碑的实际耗时校准**，M1 结束后需回填修正。

---

## M0 · 跨进程边界协议 — 已完成

| 交付物 | 说明 |
|---|---|
| `core/protocol/frames.py` | msgpack 信封，13 个 topic，ndarray 二进制打包 |
| `core/protocol/payloads.py` | 把 `Signal(..., object)` 收敛为固定字段 |
| `core/protocol/sidecar_server.py` | `/stream` 有损合并 + `/ctrl` 无损与 RPC |
| `core/protocol/frames.ts` | 前端镜像类型契约 |
| `sidecar.py` | 无界面内核进程启动入口 |
| `tools/benchmarks/bench_sidecar.py` | 端到端吞吐探针 |
| `tests/test_sidecar_protocol.py` | 11 用例，原 77 项无回归 |

### 实测（2026-09-24，单 UHF 通道，模拟器 20 FPS，8 秒）

| topic | 帧数 | 均字节 | KB/s | seq 跳变 | 解码错误 | 形状 |
|---|---|---|---|---|---|---|
| waveform | 113 | 16,501 | 227.6 | 30 | 0 | (4096,) |
| fft | 116 | 16,694 | 236.4 | 27 | 0 | (2049, 2) |
| prpd | 2 | 368,831 | 90.0 | 0 | 0 | (360, 256) |
| prps | 14 | 737,383 | **1260.1** | 0 | 0 | (512, 360) |

合计 **1.81 MB/s、30.6 帧/s**，背压丢弃 0，负载形状全部校验通过。

**结论：架构成立。** 带宽不是瓶颈，四通道线性外推约 7 MB/s，仍远低于本机回环吞吐。

**发现的唯一实质问题**：`prps` 一个 topic 占 **69%** 的带宽。`PRPSResult.matrix` 是 512×360 的滚动窗口，每帧只写入新行（`prps_processor.py:93-96` 用 `np.roll` 后填第 0 行），却整幅重量化传输。见 R1。

### 已确立的技术决定

- 主题分两个投递等级：有损（`waveform`/`fft`/`prpd`/`prps`/`ae_*`/`stats`，传输层按 `(topic, device, channel)` 合并最新帧）与无损（`alarm`/`alarm_cleared`/`device_status`/`acquisition`/`source`/`log`）。报警类绝不允许丢帧。
- 数值负载走 msgpack bin + float32，不走 JSON：避开浮点十进制化，且 NaN/Inf 原样过边界。
- `pd_event_detected` **不**跨进程转发（PRPD 目标 500K/s 的洪流）；前端所需事件序列由 `prpd` 帧携带，且服务端对 `events` 做 2000 条截断并回传 `events_truncated`。
- sidecar 只绑 127.0.0.1 且要求一次性 `?token=`。PySide6 6.10 的 `QWebSocket` 读不到 Origin 头、没有 `setMaximumMessageSize()`，仅靠回环地址挡不住本机网页握手。

---

## M1 · sidecar 生产化 — 3 pd（1.1 已完成）

补齐 UI 真正需要的反向调用，否则第一批页面无法开工。

| # | 任务 | 验收 | 状态 |
|---|---|---|---|
| 1.1 | `storage.query_*` RPC 族：告警/事件/趋势 + 设备通道配置，命名查询、参数化，不暴露裸 SQL | 前端能拉到告警列表与趋势点 | ✅ 已完成 |
| 1.1b | **后台 RPC 执行能力**：`register_rpc(background=True)` 走 `QThreadPool`，序列化也移出主线程 | 慢查询期间 `/stream` 照常出帧 | ✅ 已完成 |
| 1.2 | 设备与通道 CRUD 写入、耦合类型切换、阈值下发 | `device`/`settings` 页可写回配置 | ✅ 已完成 |
| 1.2b | 告警**确认/删除**的持久化 | — | ⚠️ 见下：旧版根本没有这条写路径 |

### M1.2 实施要点

- `core/protocol/write_rpc.py`：运行时配置（耦合类型、AE/幅值阈值）走主线程，设备 CRUD 落库走后台线程。**参数字段白名单**（`DEVICE_FIELDS` / `CHANNEL_FIELDS`）拒绝 `created_at`、`status`、通道 `id`/`device_id` 等列被前端透传，避免批量赋值。
- **旧版没有"告警确认落库"这条路径**：`alarm_page.py:347` 的确认只改表格行状态，`PDAlarmService` 无 `acknowledge`，`pd_alarms.acknowledged` 列存在但从不写。所以 M3.3 的"批量确认"是新增功能，需要连带补 core 侧的持久化方法——不能假设 RPC 层包一下就有。
- 设备通道耦合的**运行时生效**与**持久化**是两条路：`config.set_channel_coupling` 只改内存，`device.save` 才落库。前端切耦合类型时需按需各调一次。

### 仓库卫生缺陷（与重构无关，但会持续制造噪音）

`data/monitor.db-wal` 与 `data/monitor.db-shm` **被 git 跟踪了**。SQLite 在最后一个连接干净关闭时会 checkpoint 并删除这两个文件，因此任何人只要开关一次数据库，工作区就会出现两条莫名的"已删除"。根因是 `.gitignore:61-62` 只写了 `data/*.db` 和 `data/*.db-journal`，漏了 `-wal` / `-shm`。

建议处置：补 `.gitignore` 两行，并把这两个文件从索引移除（`git rm --cached`）。**注意**：当前 `.git/index.lock` 是一个 2026-07-08 留下的 0 字节陈旧锁，会阻塞一切写索引的 git 操作，需先由你确认清理。
| 1.3 | `log` topic + 环形缓冲快照 | 日志页首屏有历史、后续有增量 | ✅ 已完成 |
| 1.4 | `stats` topic（替换 `StatsManager` 的 2s 轮询） | 仪表板指标由推送驱动 | ✅ 已完成 |
| 1.5 | Electron 侧启动/守护 sidecar 子进程：崩溃重启、端口与 token 经 env 传递 | 杀掉 sidecar 后前端 5s 内重连并提示 | ⬜ 依赖 M2 |

### M1.3 / M1.4 实施要点

- **`log` 从 reliable 改成了 LOSSY，但单帧携带一批日志行**。原设为 reliable 会让 DEBUG 刷屏积压无损通道、把告警一起拖慢；改成批量有损后，客户端落后时丢的是最旧的一批而非零散日志行。环形缓冲默认 2000 条，新客户端接入时通过 `register_catchup` 补发历史快照。
  ⚠️ 曾踩坑：`TOPICS` 字典里 `"log"` 被写了两次，后一条 RELIABLE 静默覆盖了 LOSSY——改完必须确认真的生效，已加断言 `TOPICS["log"] is Qos.LOSSY` 钉住。
- **`attach()` 必须同时放宽 logger 级别**。只设 `Handler.level` 不够：记录要先通过调用方 logger 的 effective level 才到 handler。root 常为 WARNING，于是 INFO 日志被静默丢弃。`detach()` 还原原级别。
- **`stats` 推的是原始数值**，不带中文标签与格式化（那是前端的职责）。字段只从确实取得到数据的服务聚合，可选依赖为 None 时整块省略，不凭空造字段。
- **`active_source_id` / `source_count` 是 property 不是方法**。按方法调用会抛 `TypeError: 'str' object is not callable`；而用 `SimpleNamespace(active_source_id=lambda: ...)` 写的替身会让这个错误一路通过单测、只在真进程上爆。已补 `test_stats_pusher_timer_fires_without_manual_call` 覆盖 QTimer 接线路径。
- 采集服务的 `start_time` 只在显式 `start()` 时写入，模拟器直推路径下恒为 0，故 `uptime_s` 由 `SidecarServer` 自己记录；采集侧的改名 `acq_uptime_s` 避免歧义。

### M1.1 实施发现（对计划的修正）

**1. 旧 UI 几乎不读数据库。** 全仓库只有 3 处读点，且都是 `get_all_devices()`：

- 告警列表由实时信号逐行插入（`alarm_page.py:282`），统计卡数的是 `QTableWidget` 行数，**既有告警从不入库加载**
- 趋势页是纯内存累加 + 示例数据，1h/24h/7d/30d **没有查表逻辑**
- 分析页读 CSV/Excel 文件（`analysis_page_v2.py:381-388`），不碰库
- AE 传感器阵列配置**未持久化**（`sensor_array_config_dialog.py:364` 返回内存 dict）
- 日志页走 `logging.Handler`，不读 `system_logs` 表

→ 因此 `storage.query_*` RPC 是**新增能力**而非行为迁移。**M3.3「列表能加载既有告警」是功能增强，不是等价重写**，验收时不能拿旧版截图对比。

**2. AGENTS.md 的表名清单是错的。** 实际生效的是 `core/data/monitor_models.py`：`pd_devices` / `pd_channels` / `pd_events` / `pd_trend_data` / `pd_alarms`。**没有 `classification_results` 表**，`trend_data` 真名 `pd_trend_data`，`alarms` 是另一套遗留表（`core/data/models.py`），监测链路不用它。

**3. 真实库校验（`data/monitor.db`，只读访问，主文件未被修改）**

| 查询 | 结果 | 判读 |
|---|---|---|
| `events/wide`（SIM-001，start 2026-06-01） | 209 行 | 与库内 `device_id='SIM-001'` 计数精确一致 |
| `events/narrow`（start 2026-07-08 01:55） | 5 行 | 时间过滤真的在收窄 |
| `events/future`（start 2027-01-01） | 0 行 | 不恒返回全表 |
| `events/other-dev`（NOSUCH） | 0 行 | 设备过滤生效 |
| `query_trends` | 5 行 | aware UTC 入参对比库内 naive 列**不失配**，时区坑排除 |

**4. 线程安全前提已确认**：`DatabaseManager` 用 `check_same_thread=False` + 每次调用独立 `session()` 上下文 + WAL，因此线程池并发读安全。

**5. 副作用治理**：sidecar 退出必须 `db_manager.close()`，否则残留 `monitor.db-wal` / `-shm` 边车文件。已修复并验证。

### 手工验证工具

`tools/rpc_check.py <port> <token>` — 对所有只读 RPC 打一轮真实查询，含上面那组判别性用例。

---

## M2 · 前端工程骨架 — 4 pd ✅ 已完成

代码在 `desktop/`（**不复用已归档的 `next-app/`**）。

| # | 任务 | 验收 | 状态 |
|---|---|---|---|
| 2.1 | Vite 7 + Vue 3.5 + TS + Electron 33，主/渲染分离，`contextIsolation:true`、`nodeIntegration:false`、`sandbox:true` | 空白窗口起得来 | ✅ |
| 2.2 | 持久化全留 Python 侧，前端零原生模块 | 无 `electron-rebuild` | ✅ |
| 2.3 | Tailwind v4 + 设计令牌 | 令牌与稿子逐色对齐 | ✅ 主色 `#4B3FE3` 已核对一致 |
| 2.4 | 应用壳 + lucide 图标 + 9 路由（hash 模式）+ F1–F9 | 9 个路由可切换 | ✅ |
| 2.5 | sidecar ws 客户端：hello/订阅/RPC/退避重连/端点热切换 | 收到实时帧 | ✅ |

**说明**：Electron 33 = Node 20.x，无 `node:sqlite`。把数据库留在 Python 侧，既避免原生模块 ABI 矩阵，也让前端包体更干净。`src/shared/protocol.ts` 是 `core/protocol/frames.ts` 的前端镜像，改一边必须改另一边。

### M2 实测（浏览器 + 真 sidecar 端到端）

仪表盘显示"已连接"，实时帧计数 waveform 673 / fft 674 / prpd 19 / prps 94 / stats 152，
服务端累计推送 **17,861 帧、因滞后丢弃 0 帧**；指标卡读数与 Python 侧一致。计算样式核对：
`--color-background` → `rgb(255,255,255)`、主色 → `rgb(75,63,227)`、边框 →
`rgba(115,115,115,.12)`、侧栏 → `rgb(245,245,245)`。控制台零错误。

### M2 踩到并修掉的三个坑

1. **设计稿令牌是 `--color-*` 前缀命名**（`--color-background` / `--color-primary` /
   `--color-surface` / `--color-error`），不是 shadcn 的裸名。按裸名引用会全部静默落空，
   表现为背景透明、边框纯黑而**不报任何错**——只有查计算样式才发现。现经 `style.css`
   的 `app-` 别名层映射。
2. **CSP 的 `connect-src` 少了 `'self'`**，会把同源 `fetch` 一起挡掉。
3. **`sandbox:true` 的 preload 不能是 ESM**，故 preload 手写为 `.cjs`。

另验证到一条运行期性质：sidecar 重启后 token 会变，浏览器预览模式下前端拿不回新凭证、
只能无限退避；Electron 下由主进程经 `sidecar:endpoint` 推送（已实现）。

---

## M3 · 第一批页面（纯表单表格） — 8 pd（部分完成）

| # | 任务 | 验收 | 状态 |
|---|---|---|---|
| 3.1 | 系统设置 | 改阈值能写回并即时影响告警 | ✅ 已实测闭环 |
| 3.2 | 系统日志 | 增量滚动、级别过滤、10k 行不卡 | ✅ 10k 行 DOM 恒定 52 节点 |
| 3.3 | 报警管理 | 列表/多选批量确认，无损通道 0 丢帧 | ✅ 已补内核写路径并实测持久化 |
| 3.4 | 设备管理 | 增删设备、通道耦合类型切换 | ✅ 按 settings/alarm 模式推导实现，CRUD 已实测 |
| 3.5 | 主题令牌走查 | 与稿子逐色比对 | 🟡 亮色已对齐；稿子本身无暗色方案 |

### M3 实测数据

- **设置页闭环**：下发 PD 幅值阈值 250 mV 后，`config.get_thresholds` 回读
  critical 80.0 → **200.0 mV**、high 150、medium 125、low 75，读写指向同一份状态。
- **日志页**：注入 10,000 行后 DOM 子节点恒为 **52**，滚动到 250,000px 处仍是 52；
  占位高 260,000px = 10,000 × 26px 正确。筛选按钮带分级计数。

### M3 发现并修掉的问题

1. **`ref<LogLine[]>` 的深度响应式代理是性能杀手**：赋值 1,000 行实测 913ms
   （≈0.9ms/行），外推 10k 行需 9 秒以上，直接违背 M3.2 验收。窗口化只限制 DOM
   数量，管不了代理开销。`logs`/`alarms`/`latest`/`stats` 改 `shallowRef` 后
   赋值降到 7.5ms。
2. **视图在 `onMounted` 直接发 RPC 会撞上 WebSocket 未握手的竞态**，表现为偶发
   "sidecar 未连接"、刷新即好。新增 `waitCtrlOpen()`，挂载期读数据必须先 await。
3. **阈值读写分家**：`set_amplitude_threshold` 写的是按设备覆盖值，而
   `get_thresholds` 报告全局量程，前端提示"已下发 250"却读回旧值。改为
   `scope` 显式区分，且未给 scope 时按是否传 `device_id` 推断（传了设备却静默
   改全局是最难查的错）。
4. `config.get_thresholds` 返回的百分比字典必须是拷贝，否则前端改返回值会反过来
   污染内核状态（已有断言）。

### M3.4 设备管理页（无稿可依，按既有模式推导）

`.design/uhf-pd-light/pages/device.html` 是空壳：28,958 字节里只有 2 条可见文本，
`<table>`/`<input>`/`<select>` 全为 0。且**全部稿子都没有弹层先例**（modal / drawer /
`fixed inset-0` / `z-50` 均为 0 次），所以不引入模态框，改用设置页已有的
「内联表单卡片 + 表格内联编辑 + 展开行」模式。

实测（对 `data/monitor.db` 的副本操作，真库未动）：
- 新增：填表保存 → 提示"设备 界面测试设备 已新增"，列表 1 → 2 台，服务端可查见
- 校验：TCP 与 UDP 端口相同时被前端拦下（"TCP 与 UDP 端口不能相同"），未发请求
- 删除：两步内联确认 → "设备 UI-TEST 已删除"，服务端 `select count(*) = 0` 确认已清除
- 通道展开行：设备无通道记录时给出明确说明，而不是显示空表

### M3.3 告警确认写路径（本次补齐）

按"先落库拿主键、再发布带 `alarm_id` 的帧"实现，**不新增列、不需要 alembic 迁移**
（`pd_alarms.acknowledged / acknowledged_at / acknowledged_by` 三列早已建好并有
`idx_pd_alarm_ack` 索引，只是全仓无人写入）。

- `PDAlarmService._save_alarm_to_db()` 现在返回自增主键；`trigger_alarm()` 改为
  **先落库后发布**，把 `alarm_id` 注入告警帧。
- 新增 `PDAlarmService.acknowledge(ids, by)`，ORM 批量更新且只挑未确认的行，因此
  重复确认是幂等的（第二次返回 `acknowledged: 0`）。
- RPC `alarm.acknowledge`，带 `MAX_ACK_BATCH=500` 上限，防一次塞进上万个 id 变成大事务。
- 落库失败时 `alarm_id` 为 `None`：前端把该条显式标为**「未持久化」**并禁用勾选，
  而不是给一个永远确认不掉的假标识。
- 实测：加载 6 条历史 → 全选 → 确认 → 提示"已确认 6 / 6 条" → 状态全部转「已确认」、
  复选框禁用 → 直查数据库确认三列均已写入。

### 未采纳：物理删除告警

按建议把"删除"换成了状态流转。报警记录是设备状态演变的一手证据，现场通常有追溯
要求；真要清理应走已有的 `cleanup_old_data()` 按保留期回收，而不是列表页一个按钮。

### 新发现的既有缺陷（未修，需决定）

**AE 告警从不落库。** `ae_pipeline.py:100,125` 的两处 `publish_alarm`（信号丢失、
噪声超限）直接发到总线，绕开了 `PDAlarmService.trigger_alarm()`，而全仓只有
`trigger_alarm` 会调 `_save_alarm_to_db`。后果：AE 类告警在实时界面能看到，但历史
查询和确认都找不到它们，且帧里没有 `alarm_id`。修它需要把 `alarm_service` 接到
`AEAlarmChecker`（其构造在 `acquisition_service.py:76`），涉及改动范围较大，未擅自进行。

### M3 剩余项

- ~~3.4 设备管理没有设计稿~~ → 已按 settings/alarm 的既有模式推导实现（见下）。
- **3.3 的确认/删除缺内核写路径。** `pd_alarms.acknowledged` 列存在但全仓无人写入，
  `PDAlarmService` 也没有 `acknowledge`；旧版 `alarm_page.py:347` 的"确认"只改表格行
  状态。要落地必须先补 core 侧方法与 RPC。

## M4 · 流式渲染层 — 10 pd

技术栈最不确定的一段，也是 `.design` 稿**零覆盖**的部分（稿中图表全是 `chart-data.json` 里的静态 SVG path 字符串）。

| # | 任务 | 对应旧实现 | 验收 |
|---|---|---|---|
| 4.1 | uPlot 封装波形：缩放/平移/**十字游标读数**/可拖 InfiniteLine/触发标记线 | `waveform_widget.py:156-353` | 交互功能对齐，20 FPS 不掉帧 |
| 4.2 | FFT 频谱 + 峰值标注 + `LinearRegionItem` 等价频段带 | `fft_widget.py:130,291-297` | 峰值表与旧版一致 |
| 4.3 | PRPD 热图（WebGL/regl 或 ECharts）+ scatter/density/heatmap 四模式 | `prpd_widget.py:77-95,218` | 360×256 热图着色与旧版视觉一致 |
| 4.4 | **R1：PRPS 带宽优化** | `prps_processor.py:93` | 单帧 737KB → <50KB |
| 4.5 | PRPS 三维散点（three.js） | `prps_widget.py`、`prpd_3d_renderer.py` | 旋转/缩放 60 FPS |
| 4.6 | 前端节流契约：把 `%10`/`%50` 帧计数式节流换成显式采样率 | `signal_router.py:167,267` | 每 topic 频率可配置可观测 |

**4.1 是全项目最难单项**，游标与框选在 Canvas 上重写，务必留足缓冲。

### M4.4 实施结果：R1 关闭（2026-09-28）

先量后改。在真实处理器产物上测了几种编码，结论与直觉相反：

| 矩阵 | 原始 | float32+zlib | uint8 量化+zlib |
|---|---|---|---|
| PRPS 512×360（滚动窗口，88% 为零） | 720 KB | — | **19~42 KB** |
| PRPD 360×256 计数模式 | 360 KB | 4.2~48 KB | **3~35 KB** |
| PRPD 360×256 **密度模式**（`gaussian_filter` 之后） | 360 KB | **222~322 KB** | 39~79 KB |

三个关键点：

1. **PRPD 密度模式推翻了"保持 float32 以免丢密度精度"的原计划**。`prpd_processor.py:266`
   的 `gaussian_filter(sigma=1.5)` 把全矩阵变成非零连续值，zlib 完全无从下手——
   322KB/帧比不压还大。热图精度用不到 float32，量化到 uint8 才是稳的档位。
2. **沿相位轴/幅值轴一阶差分被否决**：稀疏数据上差分破坏零游程，实测 19KB→28KB 反而变大。
3. **量化必须先 `np.rint` 再转整**。`astype` 向零截断会让误差从半步膨胀到一整步；
   这个 bug 是测试用稠密合成矩阵逼出来的（maxErr 恰等于一个步长）。修完 maxErr = 半步。

体积判据改用"绝对字节数"而不是相对倍率：合成数据稀疏度不同会给出完全不同的倍率。

**前端解码**：`decodeCells()` 走浏览器原生 `DecompressionStream("deflate")`，不引 fflate。
实测 inflate PRPS 1.12ms、PRPD 0.63ms，且解压跑在 Chromium 非主线程池上，不占 50ms 帧预算。
量化主题刻意停在 0..255 的 `Uint8Array` 不展开（展开成 float32 每帧要多 270~540KB），
只有游标读数才调 `toValues()`。

**顺带修掉一个会让 M4.1 当场崩溃的缺陷**：msgpack 把 bin 字段按字节偏移从整包 buffer
上切下来，实测偏移=113（不是 4 的倍数），而不对齐的 `new Float32Array(...)` 直接抛
`RangeError`。原来的 `toFloat32()` 没做对齐处理，之所以没暴露，是因为全仓库还没有调用点。
这条靠"Python 产物 → Node 解码 → 与 `unpack_array` 逐元素比对"的跨语言校验发现，
现在两边 maxErr=0。

**端到端实测（冻结产物，8 秒）**：259 帧 / 3868 KB ≈ **483 KB/s**，对比 M0 的
1.81 MB/s 基线降到约 1/4；PRPD/PRPS 均带 `codec=zlib` 且解码形状正确。

### 4.4 遗留

密度模式在 20 万事件级别仍是 79KB，超过 <50KB 目标。交给 4.6 的节流契约解决
（累积统计类 topic 不需要 20FPS），不再继续压编码。

### M4.2 FFT 面板（2026-09-29 完成，截图验收）

`components/charts/fft-chart.vue`，与 `ui/widgets/fft_widget.py` 逐字段对齐：MHz 轴、
dB/线性可切（换算留在显示层，协议只传 Hz 与线性幅值）、峰值散点（基波红 / 谐波橙）、
噪声底红虚线、频段带 wash、10 行峰值表（第 1 名 error、2–3 名 warning、类型列谐波用主色）。

协议侧补了 `fft.bands`：`fft_processor.py:252` 每帧都算了 `band_stats`，但 `build_fft`
原先把它丢了，前端没有频段边界可画。同时发现 `BandStats` 自身单位不一致
（`f_min/f_max` 在 `compute_band_stats` 里除了 1e6 成 MHz，`peak_frequency` 仍是 Hz；
旧 Qt 控件只读前者所以从未暴露），协议里统一成 `_hz` 后缀字段，单位不再靠猜。

**实测 `绘制 16 FPS` 不是渲染掉帧**，而是内核侧 FFT 发布节奏本就约 15 帧/秒（旧版
`%N` 计数的遗留）。这条是 4.6 必须做的实证：频率不显式化，这个读数在现场一定会被
当成性能问题。

峰值表刻意没有照抄旧版列头 "幅值 (dBm)"——它填的是线性原始值，单位标错了；数值保持一致，列头改为"幅值"。

### M4.1 波形面板（2026-09-29 完成，交互逐项截图验收）

`components/charts/waveform-chart.vue`。**先查清旧版到底接了什么线**，结果和计划假设差很多：

| 旧 API | 调用点数 | 处理 |
|---|---|---|
| `set_trigger_position` | **0** | 不做。触发标记线在旧版是死代码 |
| `zoom_x` / `zoom_y` / `fit_to_view` / `set_y_unit` | **0** | 不做 |
| `cursor_position` 信号 | 只 emit，**无人 connect** | 旧版其实没有游标读数；这里的读数是新增能力 |
| `_toggle_pause` | 1（实时监测页唯一的外部调用） | 做 |

所以计划里"触发标记线"这一项按证据砍掉（计划本就写了"必要时砍触发线"）。

实现的交互全部用合成输入跑通并逐张截图取证：暂停/继续、游标开关 + `t=/v=` 读数、
框选放大（x 0–41 μs → 0–4.2 μs，脉冲逐个可辨）、复位、自动量程开关。

**实测绘制成本 p95 ≈ 0.7–0.9ms @ 4096 点**，与 M7.1 合成探针的 0.80ms 吻合——
真实图表库没有引入计划担心的额外开销，R2 结论在真管线上成立。

**读数口径**（`use-frame-draw` 现在同时给四个数）：

- `入帧` = 客户端每秒收到的帧数，实测约 11–22/s 波动，**达不到 20**。采集侧确实是
  ~19.85 FPS（15 分钟跑 17870 帧），所以缺口在服务端推流节奏，是 4.6 的活。
- `绘制` = 每秒真正画的次数。
- `丢帧` = 到达后被更新的帧覆盖、因而永远不会单独画出的次数。
- `p95` = 单次绘制耗时。

`丢帧` 这个指标是被实测纠正过的：最初按"绘制是否进行中"计数，于是 16 入帧只画 12 次
时读数显示 0——纯节奏合并（两个入帧落进同一个 rAF tick）完全没被算进去。改成
"pending 里是否还堵着一帧"后，丢帧数与 `入帧 − 绘制` 对上了。

### uPlot 三个必须记住的坑

1. **`setData` 传回同一个外层数组会被当成"数据没变"而跳过重排。** `plot.setData(plot.data)`
   复位缩放完全无效；必须传新数组字面量 `plot.setData([xBuf, yBuf])`。内层 typed array
   复用是安全的（已验证 uPlot 会原地复读改动）。
2. **没有 `draw()` 方法**，只有 `redraw(rebuildPaths?, recalcAxes?)`；`setData` 内部已经画。
3. **`getImageData` 返回未预乘颜色。** uPlot 画布没有背景填充，直接读像素会得到
   `rgb(47,116,255) @a18` 这种"看起来是不透明蓝"的值，误判成 alpha 失效。要按
   `dst = src*a + bg*(1-a)` 合成后再判断。

### 合成输入驱动交互的三个必要条件（`PD_INTERACT=1`）

`webContents.sendInputEvent` 少任一条都不会生效，逐条都是实测踩出来的：

1. `mouseDown`/`mouseUp` 必须显式 `clickCount: 1`，否则 Chromium 不合成 `click`，
   表现是"鼠标到位了、按钮像没被点过"；
2. 点击前先 `mouseMove` 到目标，否则同样不派发 click；
3. 拖拽中间的 `mouseMove` 必须带 `button: "left"`——Electron 不暴露 CDP 的 `buttons`
   位掩码，不给就等于"松着鼠标在拖"，uPlot 会把整段当成 click 而不是框选。

按钮坐标用 `getBoundingClientRect()` 在页面里量，别按窗口尺寸猜。

### 图表验收方法（M4 全程依赖）

canvas + rAF 驱动的面板**无法用 DOM 快照验证**。更关键的坑：IDE 内置浏览器在后台时
`document.hidden === true`，此时 rAF 与 uPlot 的内部初始化整条停摆，连
`plot.redraw(true, true)` 都算不出 scale（`scales.x.max` 仍为 null），表现为"页面结构
正常、canvas 存在、但一个像素都没画"，极易误判成代码 bug。

因此用主进程的截图模式，从真 Electron 可见表面取证：

```bash
PD_CAPTURE=capture-m4.png PD_ROUTE=monitor PD_CAPTURE_WAIT=12000 \
  VITE_DEV_SERVER_URL=http://localhost:5199/ npx electron .
```

它会回读 `main` 的 innerText、canvas 数量、`document.hidden`，并把渲染进程控制台搬到
stdout（图表组件在 setup/mount 抛错时页面只会安静地少一个 canvas）。
配套 `python tools/dev_preview.py` 一条命令起 sidecar+vite 并对齐随机 token。

### M4.3 PRPD 面板（2026-09-29 完成三模式，截图验收）

`components/charts/prpd-chart.vue`，手写 canvas（uPlot 不是热图库）。散点图 / 热力图 /
密度图三模式全部实测切通：密度模式下帧自报 `内核 density`，且单个事件从热力图的
**一个红色满格**变成密度图的**一片绿色扩散斑**——高斯平滑确实生效。

关键设计决定：**热力图与密度图的开关必须落在内核**，不能做成纯前端状态。两者共用同一份
计数矩阵，`gaussian_filter(sigma=1.5)` 发生在 `PRPDProcessor.compute(mode=...)` 里，
原先 `publish_throttle.py:73` 写死 `compute()` 不带 mode，所以内核永远只发计数矩阵——
做成前端切换会得到一个"按了没反应"的按钮，正是我在 M4.1 批评旧版的那种死代码。
新增 `config.set_prpd_mode` / `config.get_prpd_mode` RPC，并让帧 meta 自报 `mode`，
前端以此为准而不是相信自己按钮的状态。

**顺带修掉两个既有缺陷**：

1. `prpd_processor.py` 的缓存命中路径会返回**未平滑**的计数矩阵，却给它盖上
   `mode=density` 的戳（缓存存的是计数，不含平滑结果）。已让密度模式不走缓存。
2. 旧版点击读数按 `[amp_idx, phase_idx]` 索引**未转置**的矩阵（显示时才 `.T`），
   读到的是错格子。新实现矩阵按 (phase, amplitude) 行主序，索引 `[phase][amplitude]`。

没照抄的旧版项：`set_colormap` 调用点为 0，所以只实现默认 jet。

**一个待拍板问题**：

- **空区域是饱和蓝**。`jet(0)` 是蓝色，而 pyqtgraph `ImageItem(autoLevels=True)`
  把数据最小值映射到色带起点，所以"无事件"和"低计数"都渲染成饱和蓝。
  即使把事件密度提上去，92160 个格子里仍约 99.6% 为零，实测截图里数据只表现为
  底边一条细带，几乎读不出来。这与旧版**视觉一致**（旧版同样如此），但确实难用。
  建议改成"零值透明 + 低值淡出"，属于对旧版的有意偏离，**确认后再做**。

原先并列的"事件太稀疏"已定位并修好，见下一节。

PRPD 面板的**点击读数尚未验证**（脚本没覆盖），其余三模式已验证。

### M4 的两个内核侧阻塞项（2026-09-29 已定量程并修好）

零值透明改完后，PRPD/PRPS 反而暴露出数据本身是退化的。两条都动内核语义，
先确认再改；下面是确认过程与结论。

**结论：PRPD 幅值轴初值 = 100 mV（按通道可配），保留既有的"只增不减"动态扩展。**

调查得到的关键事实（都实测或读到过，别重复调查）：

1. **全链路没有任何单位换算。** FPGA 侧 `fpga_protocol._parse_waveform` 按大端 **int16**
   裸计数解包，不乘 gain/offset；`ChannelConfig.gain` 字段存在但**从未作用于波形**。
   `protocol_data_structures` 里 `samples # 采样点幅值 (mV)` 是未经实现的文档口径。
   也就是说同一个 `amplitude` 字段承载了"原始计数"和"mV"两种相差约百倍量纲。
2. **10000 不是物理标定，而是为覆盖真实计数数据取的经验上限**：`prpd_report.json`
   （来自真实 `PD_data.csv`）统计 max 8462 / p90 2870 / avg 936，量级正是 int16 计数。
3. **轴只增不减**，所以取大初值是**锁死**而不是"保守"：`publish_throttle.prpd_cycle`
   只在观测幅值 > 轴×0.8 时扩到观测值×1.2，没有任何收缩路径。10000 配 mV 级数据
   （实测 ≤30）永远触发不了扩展 → 500 个事件全挤在 3 个幅值箱里。
4. 换成 100 对真机**零代价**：8462 的计数数据第一帧就把轴扩到 10154，恰好回到今天这个值。
5. 100 mV 是本仓库既有的满量程共识：`PRPDProcessor` 类默认值、`alarm_service` 全局基准、
   模拟器自身 PRPD、模拟器 floating 类幅值上限，四处都是 100。

改动：`ProcessorManager(default_max_amplitude=100.0)`（原为无条件字面量 10000），
未做 FPGA→mV 标定的现场可显式传大初值而不改 DSP 源码。四条测试钉住，其中
"mV 事件必须铺满多个幅值箱"与"计数级数据仍自动扩到约 1 万"互为对照——
变异回 10000 时前者实测报"500 个事件只占了 3 个幅值箱"。

**#20 模拟器脉冲偏低已修**：振荡项 `sin(2π·i/w·2)` 在 i=0 正好过零，实测峰值只有
标称幅值的 **0.30~0.68**（随 pulse_width 变，此前文档写的"约 35%"偏低）。改成 `cos`
后 i=0 取 1，峰值即标称值。测试必须钉成**单脉冲 + 零噪声**：只钉幅值的话
20 个脉冲会全落在同一位置叠加，峰值上千，`sin` 一样能过——第一版就是这么假绿过的。

**仍然遗留（需要现场标定，不是显示问题）**：
`detect_peaks_simple` 只找正峰（`data[i] > threshold`），所以 PRPD 的负半波根本没有事件；
`add_waveform` 里 `polarity=1 if amplitude > 0` 与文档约定（0=正, 1=负）正好反了。
两条都会改变事件计数与报警行为，属于内核语义变更，未动。
另有一处口径矛盾：AE 通道阈值 `ui/controller_config.py` 给 100.0、
`core/foundation/data_source_manager.py` 给 800.0，注释分别称 mV 与 μV。

### PRPD 点击读数的验收要点

不能只点一下：483 个事件摊进 360×256 个格，簇内单格期望值约 0.1，随机点一下读到 0
**是正确结果**，什么也测不出来。脚本改成在簇区扫 15 个点，并用"非零命中落在哪个相位
列"与电晕已知相位带（265~335°）对撞：读反轴（`cells[ampIdx*phaseBins+phaseIdx]`
不越界也不报错）命中就会跑到别处。实测命中相位 287~312°、最大计数 66，空区对照为 0。

### 模拟器事件稀疏的根因（2026-09-29 定位并修好）

现象：15 分钟长跑只检出 64 个事件（约 4/分钟），PRPD 面板永远是空图。

根因是**时间基不匹配**，不是阈值也不是检测器的问题：
`simulator.py` 每帧脉冲数按 `poisson(pulse_rate * n / sample_rate)` 抽，
默认 `pulse_rate=50`（每秒）配 4096 点 @100MHz = **41μs 一帧**，
期望值 = 50 × 4096 / 1e8 = **0.002**，即约 488 帧才落进一个脉冲。
实测吻合：300 帧里只有 0.3% 的帧含事件。

叠加效应让问题更难看出来：`detect_peaks_simple` 的固定下限是 10 mV，
而纯噪声帧的峰值中位数是 7.7 mV（noise σ=2），所以没脉冲的帧必然零检出。

**排除掉的假因**（都实测过，别再重复调查）：
- 检测阈值不是问题——脉冲幅值 30~100 mV，远高于 10 mV 下限；
- PRPD 相位映射不是问题——`add_waveform` 用 `position/n*360` 反演，而模拟器正是按
  相位把脉冲摆到 `phase/2π*n` 的位置，两者互逆。实测电晕（注入 270~330°）
  的散点相位 p5/p50/p95 = **273°/301°/328°**，矩阵能量集中在 bin 275~325，完全正确；
- 前端与协议不是问题——同一条链路解出的矩阵形状、量化、mode 都对。

修法：把 `pulse_rate` 从 `simulator_source.py` 的硬编码 50 一路透到
`sidecar.py --pulse-rate`，默认值不变（50/s 是真实量级），验收/演示时用
`PD_SIDECAR_ARGS="--pd-type corona --pulse-rate 60000"`。
改后 PRPD 帧 `total_events` 从 1 变成 678，面板上能直接看到电晕的相位聚集。

**过程中又踩一个坑并补了守卫**：`--pulse-rate` 的 help 文本里写了 "0.2%"，
argparse 把 `%` 当格式符，直接 `ValueError: badly formed help string`，
**整个 sidecar 起不来**——而 518 个测试全绿，因为没有任何测试碰过 `parse_args()`。
已加 `test_sidecar_cli_parses_and_formats_help`，并做了一次变异验证
（把 `%%` 改回 `%` 确实会失败）：CLI 的 help 必须真的渲染一遍才算测过。



### M4.2 期间修掉的三个启动路径缺陷

为了修"内核起不来时应用静默空转"，把窗口创建提前到了 `app.whenReady()`。这开出一条
新路径——端点晚于页面加载到达——并暴露出三个此前休眠的问题：

1. **`updateEndpoint()` 在没有活动 socket 时是静默空操作。** `restart()` 只做
   `ws?.close()`，重连完全靠 `onclose` 回调链触发；socket 从未建立过时那条链不会启动，
   端点被保存下来然后无人问津。现在无活动连接就直接 `open()`。
2. **帧订阅原先写在 `connect()` 里。** 离线启动路径不会调 `connect()`，于是 socket
   连上、帧也进来了，但没有任何 `onFrame` 监听者——不报错、不显示。订阅已移到模块
   初始化，与连接顺序彻底解耦。
3. **端点推送与页面加载存在竞态**，早到的一次永久丢失；`did-finish-load` 现在补推一次。

第 1、2 条在我改动前不会触发（那时 `connect()` 必然在启动时执行），准确说法是改动把
既有潜伏缺口激活成了可见故障，而不是新写坏了它。

### M4.5 PRPS 三维（2026-09-29 完成，截图 + 自转验收）

- 只把**非零格**送进 GPU 当点画：预分配 40000 点的 `Float32Array`，每帧用
  `setDrawRange(0, n)` 截断。每帧重建 `BufferGeometry` 会持续吃内存，20FPS 下必炸。
- 轨道控制是手写的（pointer + wheel）。three 的 `OrbitControls` 在 `examples/` 子路径下，
  为它多引一个入口不值得，而需求只有旋转/缩放。
- `LineSegments` 是**按顶点对**画线的，不是折线。早先直接塞一圈闭合顶点，画出来是一团
  乱麻；改成显式 12 条棱才对。
- 按需渲染时"渲染 N 次/秒"天生很低（只有帧到达才画），所以页面上标了
  "（按需，开自转看上限）"。不标就会被读成"三维链路只有 3 fps"。

### M4.6 节流契约（2026-09-29 完成）

**前置发现（#18）**：内核侧采集 19.87/s，前端只收到 15.96/s，且传输层零丢失。
根因是 **Windows 定时器粒度 ~15.6ms 把 50ms 的 flush `QTimer` 量化成 61.97ms
= 15.97Hz**——与实测速率吻合到小数点后两位。`FLUSH_INTERVAL_MS` 50→10 后实测
waveform 19.89/s、fft 19.94/s、coalesced 0、dropped 0。

但真正的缺陷不是"慢"，而是**隐形**：`_emit` 用 `client.pending[key] = frame` 覆盖
前一帧，而只有背压丢弃才计数。现在 `coalesced / throttled / dropped` 三个计数分开，
`diagnostics()` 全暴露，并随 stats 帧每 2 秒推到前端。新增 `system.set_topic_rates`
RPC 按 topic 显式限 Hz，而不是靠合并窗口宽度间接决定速率——后者会被操作系统定时器
粒度改写，换台机器就变。无损通道（报警等）拒绝限流：报警不能丢。

前端落点：设置页新增"推流节流"表，概览页吞吐表加"服务端发出/限流"两列与三个计数。

**真点闭环**（`PD_INTERACT=1 PD_ROUTE=settings`）：键入 5 → 点应用 → 内核当前值变
"5 Hz" → 概览页"限流掐掉 79" → 越界 9999 把内核 `ValueError` 原样渲染成错误条 →
留空回到"跟随管线"。

**这条真点链路抓到一个 Python 侧永远测不到的 bug**：`v-model` 落在
`type="number"` 上会自动走 `looseToNumber`（不需要 `.number` 修饰符），草稿值因此是
number 而不是 string，`(draft ?? "").trim()` 直接抛 `TypeError`，速率从来没下发成功过。
静态截图和 DOM 文本都看不出异常——只有真点一次"应用"才暴露。

### 验收脚本自身的两个坑

- 交互驱动器抛异常会让应用永不退出：异常逃进没人接的 promise，表现是进程挂着干等到
  超时，既拿不到后续截图也看不到错误。现在 `takeCapture` 用 try/catch 兜住并打印 stack。
- **Git Bash 会把 `PD_ROUTE=/` 路径转换成 `C:/Program Files/Git/`**，路由纠偏于是强制
  跳到不存在的页面，整片空白，看着像前端渲染坏了。加 `MSYS_NO_PATHCONV=1`，或别用裸 `/`。
- 概览页"已收帧"短暂高于"服务端发出"不是记账漏洞：服务端数字随 stats 帧每 2 秒才到，
  计数时刻本就落后；而 log 的接入补历史走 `_send_raw`，不计入发送数。页面已就地写明，
  免得下次再查一遍。

### 间歇性路由漂移：已定性为外部鼠标输入，不是应用缺陷（2026-09-29 关闭）

M4 期间多次拍到"错页面"，当时记成了"游离的 F6/F3 keydown 把页面跳走"——**那个诊断是错的**。
在截图模式里给渲染层装了导航探针（按时序记录 keydown / hashchange / pointerdown），
装机版复现到的序列是：

```
3.80s pointerdown BUTTON → 5.37s BUTTON → 5.97s BUTTON → 6.48s BUTTON
→ 8.15s SPAN → 10.36s A → 12.05s hashchange #/monitor <- #/alarm
```

一长串真实鼠标点击打在侧栏元素上。根因是截图窗口带 `alwaysOnTop: true`，
谁在机器前动鼠标就会点到它并把它点走；开发态两次跑探针全空、路由稳定，也印证了这点。

所以不必改前端跳转逻辑。`snap()` 现在先分诊再纠正，日志会写明是"外部鼠标点击"
还是"无 pointerdown 的跳转"——只有后者才需要查应用。

### M5.2 的前置发现：趋势数据通路在 sidecar 里不存在

`store_trend_data` 的**唯一调用者是 `ui/controllers/signal_router.py:124`**，
即旧 PySide6 控制器；sidecar 路径完全不写趋势。只把页面移植过来会得到一个
永远空白的趋势页。另外三条实测事实决定了 M5.2 不能按"对齐旧版"验收：

- 旧版趋势页**不查库**，画的是 `get_example_trend_data(points=100)` 的**未播种随机数**，
  每次启动都不一样 → 验收项"数据点与旧版一致"无法成立，必须重述。
- 旧版切时窗**不做任何查询或聚合**（`_set_time_range` 只是重渲染，
  `_refresh_plot` 在内存 deque 上按时间过滤），根本没有"1h→60 个一分钟箱"这种概念。
- 现存写入路径存的不是分箱统计而是**累计/派生值**：`pd_count` 是累计计数、
  `max_amplitude` 是运行最大值、`avg_amplitude = max*0.6`、
  `total_energy = count*max*0.1`、`noise_level = 0`（后三个是**编造的常量关系**）。
  库里 19789 行全是 `period='1h'`，所以 24h/7d/30d 查询恒空。

结论：M5.2 要先在**内核侧**补一条真实的按箱聚合写入通路，前端才有东西可画；
`avg_amplitude/total_energy/noise_level` 在修好之前不应作为测量值展示。

### 另一处配置级静默失效（同日发现并修复）

`.pre-commit-config.yaml` 的 flake8 args 写成
`--ignore=E203, --ignore=E402, --ignore=E501, --ignore=C901`，而 flake8 的 `--ignore`
重复出现只有最后一个生效 → 实际只忽略了 C901，且把 `.flake8` 的 ignore 表整个覆盖掉。
E203 与 black 天然冲突（black 就是要在切片冒号前加空格），手工删必被加回。
已改为单个**加引号**的 `--extend-ignore`——不加引号会被 YAML 流式序列按逗号拆开，
后三项变成待检查的文件名，报 `E902 FileNotFoundError: 'C901'`。

---

## M5 · 第二批页面（实时与趋势） — 8 pd
`realtime_monitor`(260) → `trend`(321) → `dashboard`(498/516)。业务价值最高，依赖 M4 全部图元。

| # | 任务 | 验收 |
|---|---|---|
| 5.1 | 实时监测页（波形/FFT/PRPD/PRPS 四视图 + AE 模式切换） | 连续 1h 无内存增长 |
| 5.2 | 趋势分析（1h/24h/7d/30d 多时窗） | ~~数据点与旧版一致~~ 见下，该标准无法成立 |
| 5.3 | 仪表板（指标卡 + 三类快照 + 最新告警） | 20 FPS 下 CPU < 15% |

### M5.2 趋势分析页（2026-09-30 完成，截图 + 多窗口实测）

**验收标准必须重述。** 原写"数据点与旧版一致"无法成立：旧版 `trend_page.py`
根本不查库，画的是 `get_example_trend_data(points=100)` 的**未播种随机数**，
每次启动都不同；而且它切时窗不做任何查询或聚合（`_set_time_range` 只是重渲染，
`_refresh_plot` 在内存 deque 上按时间过滤），压根没有分箱概念。
本页对齐的是旧版的**交互契约**（四个时窗、本地时区横轴），不是那组随机数。

数据源用 `storage.query_trend_series`（从 `pd_events` 按等宽时间箱即时聚合，
空箱补 0），**不读 `pd_trend_data` 表**——那里的行是累计值与编造的常量关系
（`avg=max*0.6`、`energy=count*max*0.1`、`noise=0`），画出来会把"自启动累计"
当成"这一箱多少事件"。

**过程中查出一个命名空间错配，值得单独记住**：趋势页第一版设备下拉取自
`device.list` 与 `stats.sources`，结果下拉显示"模拟器-UHF（数据源）"、
库里有 1937 条事件、页面却是空图。原因是**事件以采集设备 id（`SIM-001`）入库，
而数据源 id 是另一套命名（`sim-uhf-N`），且纯模拟器场景下 `pd_devices` 是空表**——
查询条件完全正确、结果恒为 0，症状和"没有数据"一模一样。
修法是新增 `storage.device_ids`，历史页的候选集只能来自"库里真实出现过的 device_id"。

实测（种子库跨 26 小时 + 实时写入）：

| 窗口 | 箱数 | 事件数 | 非空箱 | 箱宽 |
|---|---|---|---|---|
| 1h | 60 | 4278 | 15 | 60 s |
| 24h | 96 | 5009 | 96 | 900 s |
| 30d | 90 | 5087 | 4 | 28800 s |

三窗单调递增（30d 包含全部），30d 只有 4 个非空箱也正确——数据只跨 26 小时。
不存在的设备返回**等长全 0 序列**而不是报错或空数组；非法 window 被内核拒绝。

顺带修掉的两个前端问题：

- 把响应式变量命名为 `window` 会**遮蔽全局 `window`**，`window.setInterval` 变成
  对一个 Ref 取属性；模板里的 `window === w.key` 也会静默失效（按钮永不高亮）。
- uPlot 的图例是画在容器下方的 DOM，会溢出叠到下面的说明文字上。两条 y 轴各自
  带标题且颜色与曲线一致，图例本就冗余，直接关掉。

`tools/rpc_check.py` 补了 `device_ids` 与四个 `trend/*` 用例（含"期望失败"的
非法窗口），这样多窗口不必开界面就能回归。

---

## M6 · 第三批页面（AE 与分析） — 10 pd

| # | 任务 | 难点 |
|---|---|---|
| 6.1 | AE 特征分析（997 行，最大页面）：hit 散点、参数、统计、历史 | OpenGL `GLViewWidget` → three.js，含覆写的鼠标交互（`ae_localization_3d_widget.py:104-124`） |
| 6.2 | 定位热图 + ColorBar | 残差 map 着色对齐 |
| 6.3 | 传感器阵列配置对话框 | 表格内联编辑 + 散点预览联动 |
| 6.4 | 数据分析页 | v1/v2 分叉需先合并；含导入/分类/导出长流程 |

---

## M7 · 性能与长稳 — 5 pd

| # | 任务 | 通过线 | 状态 |
|---|---|---|---|
| 7.1 | **目标硬件实测**：现场工控机上跑渲染探针 | 四类图元单帧绘制 p95 远低于 50ms 预算 | 🟡 探针已就绪，待现场复跑 |
| 7.2 | 24h 长稳 + 内存增长 | RSS 增长 < 10% | ⬜ |
| 7.3 | 双通道/四通道线性度 | 无背压丢弃 | ⬜ |
| 7.4 | sidecar 崩溃恢复与采集断点续传 | 恢复 < 5s，无告警丢失 | ⬜ |
| 7.5 | 启动时间 | 冷启 < 3s | ⬜ |

### M7.1 渲染探针（提前到 M2 之后做，因为它能推翻整个方案）

`desktop/src/renderer/lib/render-probe.ts` + `views/probe-view.vue`，主进程 `--probe=秒`
进入，跑完写 `render-probe.json` 并自动退出。负载刻意对齐旧版 pyqtgraph 的真实绘制量：
波形 4096 点、FFT 2049 点、PRPD 360×256 热图、PRPS 512×360 热图（均走 `ImageData` +
`putImageData` + 缩放合成，与 `ImageItem` 等价）。

**现场跑法**：把 `desktop/` 拷到目标机 → `npm ci` → `npm run probe`（或
`npx electron . --probe=30 --disable-gpu` 模拟无 GPU 驱动的最坏情况）。

#### 开发机实测（AMD Radeon RX590，Electron 33.4.11 / Chrome 130）

| 场景 | 绘制 p50 | p95 | p99 | max | 超预算 | 余量 |
|---|---|---|---|---|---|---|
| 硬件加速（ANGLE/D3D11） | 0.50ms | **0.80ms** | 1.40ms | 2.10ms | 0 / 592 | **62.5x** |
| `--disable-gpu`（无 WebGL 上下文） | 0.50ms | **1.10ms** | 1.90ms | 3.40ms | 0 / 601 | **45.5x** |

20 FPS 的预算是每帧 50ms。**即使完全关掉 GPU、退到软件渲染路径，四类图元一帧也只花
约 1.1ms**，余量 45 倍。rAF 稳定 60fps。

→ **R2 风险大幅缓解**：弱显卡不再是否决 Electron 的理由；真正需要盯的是 CPU 极弱的
面板机，但 45 倍余量足以吸收 5–10 倍的 CPU 差距。

#### 这个结论的边界（不要过度解读）

探针量的是 **2D canvas 绘制成本**，不含：uPlot / ECharts / three.js 等真实图表库的开销、
Vue 响应式与 DOM 更新、msgpack 解码。所以它是"能不能行"的指示器，不是最终验收。
M4 图表层落地后需在同一台机器上复测。

---

## M8 · 切换收尾 — 3 pd

旧 `main.py` / `main_v2.py` 下线，`ui/` 归档删除，打包（electron-builder）与安装器，文档与 `AGENTS.md` 更新。

### M8 前置：内核冻结产物（2026-09-28 已完成）

**动因**：目标工控机**不装 Python**。原实现 `sidecar.ts` 是
`PD_SIDECAR_PYTHON ?? "python"`，且 `repoRoot()` 从源码文件上溯三级取"仓库根"——
那是开发树布局假设，打进 asar 后该位置是 `resources/`，里面既没有 `sidecar.py`
也没有 `core/`。也就是说装机版在内核缺失时会 `spawn` 一个不存在的路径。

**更糟的是失败不可见**：窗口只在 `endpoint` 事件里创建，而 `endpoint` 只在内核 ready
时才发。内核起不来 → 5 次退避重启 → `dead` 状态发往仍然是 `null` 的 window →
表现是"双击图标没反应"，现场只能看到一个活着的 Electron 进程。现在
`app.whenReady()` 先建窗口再起内核，`App.vue` 本来就能显示"内核已停止/重启中"。

交付物：

| 文件 | 作用 |
|---|---|
| `sidecar.spec` | PyInstaller onedir，`console=True`（端口/token 走 stdout，被 windowsHide 隐藏） |
| `core/foundation/runtime_paths.py` | 只读资源走 `_MEIPASS`，可写状态走 `%LOCALAPPDATA%\UHF-PD` |
| `core/foundation/hardware_config_loader.py` | 从 `ui/controller_config.py` 迁入，切断 sidecar→`ui/` 的依赖 |
| `desktop/electron-builder.json` | `extraResources: ../dist/uhf-sidecar → resources/uhf-sidecar` |
| `tools/verify_frozen_sidecar.py` | 产物冒烟：起 exe、解析端点、订阅、校验解码 |

#### 2026-09-29 复验（含量程改动后的新产物）

1. `npm run build:kernel` 重新出包（185MB，`dist/uhf-sidecar/uhf-sidecar.exe`）。
2. `python tools/verify_frozen_sidecar.py --seconds 15 --expect-prps` 通过：端点解析成功、
   15 秒 607 帧 / 9346 KB = **623 KB/s**、prpd `(360,256)` 与 prps `(512,360)` 均 zlib、
   waveform 283 帧 ≈ 18.9/s。**EXIT=0**。
3. **补了一个开发态缺口**：`launchFrom()` 的 `PD_SIDECAR_PYTHON` 分支原先无条件塞
   `sidecar.py` 参数，而 PyInstaller 会把 `argv[1:]` 原样交给脚本，argparse 于是收到
   多余位置参数并以 exit 2 退出——表现是"内核起不来"但日志只有 usage。现在识别
   `.exe` 后缀就走 bundled 形态（不塞脚本名、cwd 取 exe 所在目录）。
4. 用改后的启动路径**以冻结内核跑当前整套界面**：`PD_SIDECAR_PYTHON=.../uhf-sidecar.exe`
   + 截图模式，运行中采样进程表 = **python.exe 0 个、uhf-sidecar.exe 1 个**，
   波形/FFT/PRPD/PRPS 四面板全部出数。"目标机不装 Python"这条从设计变成可复现实测。

**装机版组装已复验通过**（`--dir`，输出到 `desktop/release-verify/` 以避开一个被系统
锁住的旧暂存目录，见下）：

| 检查项 | 结果 |
|---|---|
| `CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder --win --dir` | EXIT=0 |
| `resources/uhf-sidecar/`（内核铺在 asar **之外**） | 185MB，exe 在位 |
| `resources/app.asar` | **29.6MB**（旧产物是 210MB） |
| 装机版 exe 启动后进程采样 | **python.exe 0 个 / uhf-sidecar.exe 1 个** |
| 内核来源 | 日志显示 `...\resources\uhf-sidecar\uhf-sidecar.exe`，`内核就绪 port=50151` |
| 四面板出数 | 波形/FFT/PRPD/PRPS 全部渲染，PRPD 纵轴 0~100 mV |

asar 从 210MB 降到 29.6MB 证实了"内核被 `files: ["dist/**/*"]` 重复打进 asar"那个坑
已经消失（现在 `desktop/dist` 只有 789K）。**剩下的 29.6MB 主要是 electron-builder
自动收进来的生产依赖 `node_modules`（3066 项，含 `@babel/parser`、`three` 等）**——
渲染层依赖已由 vite 打进 `dist/assets`，所以这部分对装机版是冗余。属于 M8 的体积优化，
不是缺陷。

**顺带修掉一个只在 WebGL 侧发作的配色 bug**：`--color-app-border` 经 `getComputedStyle`
取到的是 **8 位十六进制 `#7373731f`**（末两位 `1f` = 31/255 ≈ 0.12 的 alpha）。canvas 的
`strokeStyle` 认它，所以 uPlot 网格一直正常；而 `THREE.Color` 的正则只接受 3/6 位，
于是 warn `Invalid hex color` 后**静默留在默认白色**——PRPS 的参照框从头到尾都是白色，
不是设计的淡灰，而白框配浅背景在截图上几乎看不出来。修法：`chart-theme.ts` 额外导出
`borderSolid` + `borderAlpha`，WebGL 侧显式拿实色与透明度两个值。复验：装机版与开发态
日志中该警告均为 0，截图上参照框已是淡灰。

**两个遗留问题**：

1. `desktop/release/win-unpacked.tmp/resources/default_app.asar` 被系统级占用
   （`rm` 报 Device or resource busy），但**按模块映射与 ExecutablePath 两种方式都查不到
   任何进程持有它**——即占用者不是可识别的进程（极可能是 Defender/索引器或资源管理器）。
   该目录是失败构建留下的暂存，可安全忽略；旧的 `release/win-unpacked/`（11:43，内含
   210MB asar）仍在，需要时手动删。
2. **路由漂移仍会复发**：装机版第一次跑时 `PD_ROUTE=monitor` 被强制纠正后又被跳回
   `#/settings`（截图取证到的是设置页），第二次跑同样参数则稳定在 `#/monitor`。
   与 M4 期间记录的"游离 F6/F3 keydown 把页面跳走"是同一个既有缺陷，
   间歇性、未定位到输入源。

**冻结踩到的两个坑，都值得记住**：

1. **不要排 stdlib，不要排"看着用不到"的第三方。** 我曾按"sidecar 用不到"排掉
   `unittest`/`structlog`，结果是 `scipy.signal → numpy.testing → unittest` 断链，
   `peak_detector` 每帧抛 `ModuleNotFoundError`，而 sidecar **照样打印 "sidecar ready"
   并保持存活**，只是处理波形数恒为 0。`structlog` 同样是硬依赖（`pyproject.toml:19`），
   `logger.py:19` 那个 `try/except ImportError` 是假的可选。PyInstaller 本来就只收可达
   模块，手写排除表只会制造这类事故。现在只排 Qt GUI 栈和构建机上的 ML 栈。
2. **构建机是共享全局解释器会污染产物。** 加 `collect_submodules("core")` 兜底时产物
   833MB（torch 364MB + cv2 99MB + transformers 42MB），去掉并显式排除后 **183MB**。
   具体经哪条边被收进去没查清（逐个 import `core.*` 子模块并不加载 torch），
   结论按实测写下，不作为因果结论。

验收：`npm run package:dir` 产出的 `resources/uhf-sidecar/uhf-sidecar.exe` 与
`resolveLaunch()` 的拼接路径一致；`verify_frozen_sidecar.py` 在产物上收到
waveform/fft/prpd/prps 帧且 `codec=zlib` 解码正确。

---

## 汇总

| 里程碑 | 人日 | 状态 |
|---|---|---|
| M0 边界协议 | 6 | ✅ 已完成（实测 1.81 MB/s，负载全对） |
| M1 sidecar 生产化 | 3 | ✅ 1.1–1.4 完成；仅剩 1.5（已并入 M2 的 SidecarSupervisor） |
| M2 前端骨架 | 4 | ✅ 已完成（端到端实测） |
| M3 第一批页面 | 8 | ✅ 3.1–3.4 全部完成（3.5 暗色走查待稿子） |
| M4 流式渲染层 | 10 | ✅ 4.1–4.6 全部完成并截图/真点验收（544 passed）。#21 量程、#20 脉冲偏低两条内核阻塞项均已修好 |
| M5 第二批页面 | 8 | 🟡 前置已完成：持久化通路迁入内核（`persistence_service.py`）+ 按箱趋势聚合 RPC（`storage.query_trend_series`）。剩 5.1 收尾、5.2 趋势页、5.3 仪表板 |
| M6 第三批页面 | 10 | ⬜ 未开始 |
| M7 性能长稳 | 5 | 🟡 7.1 探针已就绪并在开发机实测，待现场复跑；7.2–7.5 未做 |
| M8 切换收尾 | 3 | ⬜ 未开始（其中"AGENTS.md 更新"已于 2026-09-29 提前做完） |
| **合计** | **57 pd** | 约 11.5 周（单人全职） |

关键路径：**M1 → M2 → M4 → M5**。M3 可与 M4 并行（不同人）。M7.1 建议提前到 M2 结束就做，它可能推翻整个方案。

---

## 存量缺陷修复记录（2026-09-28）

整套 `pytest` 此前**根本无法跑完**，现在 **493 passed / 2 skipped / 0 failed（EXIT=0，约 80 秒）**。

| 缺陷 | 根因 | 修复 |
|---|---|---|
| 全套测试在 41% 处**进程硬终止**（无 traceback，EXIT=127） | `test_controller_integration.py` 与 `test_ui_pages.py` 的 `qt_app` fixture 建的是 **`QCoreApplication`**，而 6 个 widget 测试模块建 `QApplication`。Qt 禁止在已有 QCoreApplication 后再构造 QApplication，且 `QApplication.instance()` 遇到 QCoreApplication 单例返回 None，于是 widget 测试去新建 → abort。两处 fixture 的 docstring 都写着"QApplication 单例"，名实不符 | 两个 fixture 统一改为 `QApplication.instance() or QApplication(sys.argv)` |
| `test_pdf_export_with_charts` 报 `NameError: name 'cm' is not defined` | `ui/pages/ae_page.py:695` 在导出方法内**局部**导入 `cm`，而 `_capture_widget_as_image` 是另一个方法，取不到 | 在该方法内补 `from reportlab.lib.units import cm` |
| 数据留存清理在**首个 flush tick 就全表执行**，而非按 `86400s` 周期 | `_last_cleanup_time` 初值 `0.0`，`now - 0 > 86400` 恒真；返回 MagicMock 时还会在守护线程抛 `TypeError` 污染整套输出 | `start()` 里把 `_last_cleanup_time` 初始化为当前时间，并加 `test_cleanup_not_triggered_on_first_tick` |
| `data/monitor.db-wal` / `-shm` 被 git 跟踪，任何开关库的动作都产生伪"已删除" | `.gitignore` 只有 `data/*.db` 与 `*.db-journal`，漏了 WAL 边车文件；SQLite 在最后连接干净关闭时会删除它们 | 补两条 ignore 规则 + `git rm --cached` 解除跟踪 |
| `.git/index.lock` 陈旧锁阻塞一切写索引操作 | 2026-07-08 留下的 0 字节锁，无 git 进程持有、无进行中的合并/变基 | 已删除 |

### 尚未处理的存量问题

- **告警确认从未落库**：`pd_alarms.acknowledged` 列存在但无人写，`PDAlarmService` 也没有 `acknowledge`。M3.3 需要连带补 core 侧写方法，属于新增功能而非 bug。
- **`Signal source has been deleted` stderr 噪音**（全套 10 条）：既有测试里模拟器线程在总线析构后仍在 publish。单独跑我的测试文件为 0 条，可确认与重构无关。不判失败，仅污染输出。
- `Task was destroyed`（1 条）：`asyncio.shield` 包住 websockets 内部 future，`cancel()` 无效，只能走真实关闭握手且期间泵 Qt 循环；已按此实现，从 28 条降到 1 条。

---

## 风险登记

| ID | 风险 | 影响 | 应对 | 状态 |
|---|---|---|---|---|
| R1 | `prps` 占 69% 带宽，512×360 float32 整幅传输 | 四通道时近 5 MB/s 全是 PRPS | 热图矩阵统一 uint8 线性量化 + zlib（PRPS 720KB→19~42KB，PRPD 360KB→3~73KB）；密度模式 float32 会退化到 322KB，故不选 float32 | ✅ M4.4 已完成 |
| R2 | 目标工控机 Chromium 软件渲染 | 高帧率 canvas 重绘掉帧，方案被推翻 | M7.1 探针已实测：无 GPU 时四类图元单帧 p95 仅 1.10ms、余量 45x | 🟢 大幅缓解，待现场复跑 |
| R3 | 波形游标/框选在 Web 端重实现 | 单点最贵，易延期 | M4.1 留缓冲；必要时砍触发线 | 中 |
| R4 | 23.5K 行 UI 重写期间旧版仍要维护 | 双份成本 | 每里程碑保持 `main.py` 可运行可回退 | 中 |
| R5 | `.design` 稿与 405 个未入库文件 | 设计目标与 v2 实现可能丢失 | 用户已明确暂不提交；**开工前须自行备份** | 未解决 |
| R6 | SQLite 查询阻塞 Qt 循环连带停推流 | 一次慢查询卡死全画面 | M1 强制走 `QThreadPool` | ✅ 已实现并有断言保护（`test_background_rpc_does_not_block_stream`） |
| R7 | AGENTS.md 表名清单有误 | 照它写查询会查不到表 | 以 `core/data/monitor_models.py` 为准；AGENTS.md 待订正 | 新发现 |
| R8 | `next-app/` 是废弃脚手架，易被误读成"前端已选 Next.js" | 技术路线漂移，重复搭建 | 实为 2026-06-17 一次性生成、三个月未动、引用缺失组件导致 build 失败、全目录无任何桌面化/集成线索。**本项目不使用它**，前端在 `desktop/` 新建 | 待处置（删或移出） |
| R9 | **目标机不装 Python**，而装机版靠 `spawn("python")` 起内核 | 应用静默不启动：窗口只在内核 ready 后才建，`dead` 状态发往 null window | `sidecar.spec` 冻结出 `uhf-sidecar.exe` 经 extraResources 交付；`resolveLaunch()` 按 `app.isPackaged` 二选一；窗口改为启动即建 | ✅ 已实现，产物 183MB 且冒烟通过 |

## 不做清单

- **d3-force** —— 全库无力导向/图布局场景。SCADA 一次接线图（审计报告列为二期）是固定坐标 SVG，同样不该用力导向。
- 前端直连 SQLite / 引入原生模块 —— 持久化统一留在 Python 侧。
- `pd_event` 逐事件跨进程推送 —— 洪流，由 `prpd` 帧聚合携带。
- 共享内存传输 —— 实测 1.81 MB/s，WebSocket 二进制帧余量充足，不值得这个复杂度。
