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

## M3 · 第一批页面（纯表单表格） — 8 pd

`settings`(349) → `log`(343) → `alarm`(436) → `device`(262)。选它们的理由：**不依赖任何图表**，可验证 M1 的 RPC 与 M2 的壳，且失败成本低。

| # | 任务 | 验收 |
|---|---|---|
| 3.1 | 系统设置 | 改阈值能写回并即时影响告警 |
| 3.2 | 系统日志 | 增量滚动、级别过滤、10k 行不卡 |
| 3.3 | 报警管理 | 列表/多选批量确认/删除，**无损通道 0 丢帧**。注意"加载既有告警"是**新增能力**（旧版只在运行时逐行插入），无旧版可对比，验收以 `storage.query_alarms` 返回内容为准 |
| 3.4 | 设备管理 | 增删设备、通道耦合类型 UHF↔AE 切换 |
| 3.5 | 主题令牌与暗/亮两套走查 | 与稿子像素级比对 |

**进度锚点**：M3 结束时旧 `main.py` 仍可独立运行，两套并行，可随时回退。

---

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

---

## M5 · 第二批页面（实时与趋势） — 8 pd

`realtime_monitor`(260) → `trend`(321) → `dashboard`(498/516)。业务价值最高，依赖 M4 全部图元。

| # | 任务 | 验收 |
|---|---|---|
| 5.1 | 实时监测页（波形/FFT/PRPD/PRPS 四视图 + AE 模式切换） | 连续 1h 无内存增长 |
| 5.2 | 趋势分析（1h/24h/7d/30d 多时窗） | 数据点与旧版一致 |
| 5.3 | 仪表板（指标卡 + 三类快照 + 最新告警） | 20 FPS 下 CPU < 15% |

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

---

## 汇总

| 里程碑 | 人日 | 状态 |
|---|---|---|
| M0 边界协议 | 6 | ✅ 已完成（实测 1.81 MB/s，负载全对） |
| M1 sidecar 生产化 | 3 | ✅ 1.1–1.4 完成；仅剩 1.5（已并入 M2 的 SidecarSupervisor） |
| M2 前端骨架 | 4 | ✅ 已完成（端到端实测） |
| M3 第一批页面 | 8 | ⬜ 未开始 |
| M4 流式渲染层 | 10 | ⬜ 未开始 |
| M5 第二批页面 | 8 | ⬜ 未开始 |
| M6 第三批页面 | 10 | ⬜ 未开始 |
| M7 性能长稳 | 5 | 🟡 7.1 探针已就绪并在开发机实测，待现场复跑 |
| M8 切换收尾 | 3 | ⬜ 未开始 |
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
| R1 | `prps` 占 69% 带宽，512×360 float32 整幅传输 | 四通道时近 5 MB/s 全是 PRPS | 滚动窗口只传新增行，或 uint16 量化 | **待办 M4.4** |
| R2 | 目标工控机 Chromium 软件渲染 | 高帧率 canvas 重绘掉帧，方案被推翻 | M7.1 探针已实测：无 GPU 时四类图元单帧 p95 仅 1.10ms、余量 45x | 🟢 大幅缓解，待现场复跑 |
| R3 | 波形游标/框选在 Web 端重实现 | 单点最贵，易延期 | M4.1 留缓冲；必要时砍触发线 | 中 |
| R4 | 23.5K 行 UI 重写期间旧版仍要维护 | 双份成本 | 每里程碑保持 `main.py` 可运行可回退 | 中 |
| R5 | `.design` 稿与 405 个未入库文件 | 设计目标与 v2 实现可能丢失 | 用户已明确暂不提交；**开工前须自行备份** | 未解决 |
| R6 | SQLite 查询阻塞 Qt 循环连带停推流 | 一次慢查询卡死全画面 | M1 强制走 `QThreadPool` | ✅ 已实现并有断言保护（`test_background_rpc_does_not_block_stream`） |
| R7 | AGENTS.md 表名清单有误 | 照它写查询会查不到表 | 以 `core/data/monitor_models.py` 为准；AGENTS.md 待订正 | 新发现 |
| R8 | `next-app/` 是废弃脚手架，易被误读成"前端已选 Next.js" | 技术路线漂移，重复搭建 | 实为 2026-06-17 一次性生成、三个月未动、引用缺失组件导致 build 失败、全目录无任何桌面化/集成线索。**本项目不使用它**，前端在 `desktop/` 新建 | 待处置（删或移出） |

## 不做清单

- **d3-force** —— 全库无力导向/图布局场景。SCADA 一次接线图（审计报告列为二期）是固定坐标 SVG，同样不该用力导向。
- 前端直连 SQLite / 引入原生模块 —— 持久化统一留在 Python 侧。
- `pd_event` 逐事件跨进程推送 —— 洪流，由 `prpd` 帧聚合携带。
- 共享内存传输 —— 实测 1.81 MB/s，WebSocket 二进制帧余量充足，不值得这个复杂度。
