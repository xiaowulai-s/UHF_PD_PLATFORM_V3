# AGENTS.md

本文件指导 AI 助手在本仓库中工作。**它是唯一的 agent 指令来源**——`CLAUDE.md` 现在只是指向本文件的指针，改动请只改这里。

## 当前状态：一次进行中的架构迁移（最重要的一条）

这个仓库的 **UI 层重写已完成界面迁移**（M0–M6 完成），旧的 PySide6 界面已下线：

- **现役开发对象**是 `desktop/`：Electron 33 + Vue 3 + TypeScript + Vite 的新界面。
- **处理内核**是 `core/`（14,866 行 numpy/scipy DSP + FPGA 采集），保留为**无头 Python sidecar**，由 `sidecar.py` 启动，通过本机 WebSocket 与前端的 Electron 主进程/渲染进程通信。
- **旧界面 `ui/`（23,183 行 PySide6）已于 M8 归档下线**，移到 `_archive/ui-offline-20261008/`
  （该目录已 gitignore，**不参与运行、也不参与测试**）。归档时一并带走了旧入口 `main.py`/
  `main_v2.py`、旧 PySide6 打包配置 `build.py`/`build.spec`/`PDMonitor.spec`/`installer.iss`，
  以及 8 个 ui 耦合测试文件。**仓库里已无任何 `import ui` 的代码**（`core/`、`tools/`、
  `sidecar.py` 早就与 `ui/` 无关）。
- **没有回滚路径了**：`python main.py` 不再可用。要回滚就把 `_archive/ui-offline-20261008/`
  下的 `ui/` 与 `main.py` 移回仓库根，并把 `pyproject.toml`/`.coveragerc`/`pytest.ini` 的
  覆盖率目标改回 `["core", "ui"]`。改 UI 一律改 `desktop/`。

完整的迁移决策、里程碑状态与实测数据在 `docs/ELECTRON_REWRITE_PLAN.md`——**动架构相关代码前先读它**。

## 快速开始

```bash
# 新架构（主要工作流）：起 Vite + Electron，Electron 主进程会自动 spawn Python sidecar
cd desktop && npm ci && npm run dev

# 只跑内核（不启界面），用于观察协议与推流
python sidecar.py --sim-type uhf --pd-type corona --pulse-rate 60000
python sidecar.py --help          # 全部参数见 --help，不要凭记忆猜 flag

# 测试
python -m pytest -q --no-cov                          # 内核全套（当前 468 passed / 2 skipped，约 20s）
python -m pytest tests/test_sidecar_protocol.py -q    # 跨进程协议
cd desktop && npm run typecheck                       # vue-tsc + tsc(electron)
cd desktop && npm run build                           # 类型检查 + vite 打包 + 主进程编译

# 内核冻结产物（目标机不装 Python，见下文）
cd desktop && npm run build:kernel && npm run verify:kernel
cd desktop && npm run package:dir                     # 出 win-unpacked 装机版
```

## 目标机与构建机的硬约束

这两条是现场/打包才暴露、代码里看不出来的：

1. **部署目标机不装 Python。** 内核必须以 PyInstaller 冻结产物交付（`sidecar.spec` → `dist/uhf-sidecar/`），由 `electron-builder.json` 的 `extraResources` 铺到 `resources/uhf-sidecar/`。已实测装机版运行中 `python.exe` 进程数为 0。不要建议在目标机 `pip install`。
2. **构建机的 Python 不干净。** `python` 解析到全局解释器（`AppData\Local\Programs\Python\Python314`），里面装着与本工程无关的 torch / cv2 / transformers / pandas；项目另有 `venv/`（仓库根没有 `.venv`，别按惯例猜）。**注意 `venv/` 里缺 `msgpack`**，所以 dev 模式跑 sidecar 用的是全局 `python`，而 `venv` 只适合跑部分测试。当前冻结产物实测约 185MB；历史上出现过 833MB 的那次，**成因未证实**——移除 `collect_submodules("core")` 后体积降了，但"导入全部 core 子模块会连带加载 torch"这个假设被实测否证过。别把 833MB 归因给 ML 栈当结论用。
3. 出包时加应用图标会进入签名环节并去 GitHub 拉 `winCodeSign`，而这台机器连 GitHub 时通时不通。项目无代码签名证书，用 `CSC_IDENTITY_AUTO_DISCOVERY=false` 跳过。

## 跨进程边界（协议）

内核与前端之间只有一个契约：`core/protocol/frames.py`（Python 侧）与 `desktop/src/shared/protocol.ts`（前端侧）**手工镜像**。改一边必须改另一边。

- **双通道**：`/stream` 走有损实时数据（最新帧胜出）；`/ctrl` 走可靠事件 + RPC。URL 带 `?token=`，token 每次启动随机，由内核打印到 stdout、主进程正则解析（`PORT_RE` 认 stream URL 行、`TOKEN_RE` 认独立 token 行，且必须先见到 port）。
- **QoS 划分**：lossy = `waveform` `fft` `prpd` `prps` `ae_envelope` `ae_hit` `stats` `log`；reliable = `alarm` `alarm_cleared` `device_status` `acquisition` `source`。无损通道**不允许限流**（报警不能丢）。
- **热图矩阵编码**：uint8 线性量化 + zlib（`meta.qmax` 反量化）。必须先 `np.rint` 再转整——`astype` 向零截断会把误差从半步长放大到一整步长。密度模式也用 uint8（float32 会退化到 322KB）。渲染侧用原生 `DecompressionStream("deflate")` 解压，不要引入 fflate。
- **msgpack 的 bin 字段地址未对齐**（实测 byteOffset 113），直接 `new Float32Array(buf, off)` 会抛 RangeError，必须经 `viewOf` 拷贝。
- **推流节流**：`FLUSH_INTERVAL_MS = 10`；每 topic 目标 Hz 由 `system.set_topic_rates` 显式设置（范围 (0,200]）。三个"没发出去"的计数分开记账——`coalesced`（合并窗口内被后帧顶掉）、`throttled`（被显式速率掐掉）、`dropped`（背压整批丢弃），随 `stats` 帧每 2 秒下发，前端概览页展示。
- **持久化要显式开启**：`--with-storage` 才会启动 SQLite 刷写线程**并且**由 `core/services/persistence_service.py` 的 `PersistenceSubscriber` 订阅 `pd_event_detected` 入队。两者缺一不可——只起线程不订阅的话队列永远空，`storage.query_events` 恒返回 `[]`。

## 里程碑状态（截至 2026-10-08）

| 里程碑 | 状态 |
|---|---|
| M0 边界协议 / M1 sidecar 生产化 / M2 前端骨架 / M3 第一批页面 | ✅ 完成 |
| M4 流式渲染层（4.1–4.6） | ✅ 完成，全部经截图 + 真点验收 |
| M5 第二批页面 | ✅ 5.1 AE 模式、5.2 趋势页、5.3 仪表板均完成。5.1 的"连续 1h 无内存增长"已复跑通过（60.05 min / 120 采样点，四角色全有界；侧车那段 +30 MB/h 是 `prpd_processor` 的 `deque(maxlen=100000)` 填满，40.9 分钟到平台，属有界消耗） |
| M6 第三批页面（AE 与分析） | ✅ 6.1 AE 特征分析、6.2 定位精度热图（真 GDOP，无量纲）、6.3 传感器阵列配置、6.4 数据分析页均完成并截图/真点验收。**实验数据导入已补**（`data.import_file` RPC + 分析页导入区，带放电类型识别与逐周期类型分布，测试 `tests/test_data_rpc.py`）。仍缺：3D 定位视图（内核无 TDOA 通路）、等效放电量（无计数→物理量换算）、最大重复率（口径未定义）、实时事件的类型标签 |
| M7 性能长稳 | 🟡 7.1 探针待现场复跑；**7.5 启动时间已实测达标**（`tools/measure_startup.py`，当前 HEAD 重建产物：日常重启 0.82s、首次建库 1.01s、全新拷贝首启最差 1.05s，线 3s）；**7.3 多通道线性度达标**（`tools/measure_channel_linearity.py`，N=1/2/4/8 每通道 19.8–19.9 fps、扩展比 1.00、`dropped` 恒 0，8 通道 RSS 561 MB；客户端真解码 payload，只数帧会假性把背压测成 0）；7.2 24h 长稳 / 7.4 崩溃恢复未做 |
| M8 切换收尾 | 🟡 旧 `main.py`/`main_v2.py`/`ui/` 已归档下线（`_archive/ui-offline-20261008/`）、AGENTS.md 已更新；装机包与其余文档收尾未做 |

## 内核侧的三个语义陷阱（会毁掉读数可信度）

1. **PRPD 幅值轴只增不减。** `ProcessorManager(default_max_amplitude=100.0)` 给的是**初值**；`publish_throttle.prpd_cycle` 只在观测幅值 > 轴×0.8 时把轴扩到观测值×1.2，**没有任何收缩路径**。所以初值取大了等于永久锁死（此前硬编码 10000 mV 时，mV 级数据全部挤在第 0~1 个幅值箱里）。也不要改成 auto-scale 掩盖问题——纵轴是测量量程，不是显示缩放。
2. **单位口径本身是缺的。** 全链路没有任何计数→mV 换算：FPGA 侧按大端 int16 裸计数解包，`ChannelConfig.gain` 字段存在但从未作用于波形；`WaveformData.samples # (mV)` 只是未经实现的文档口径。真实数据（`prpd_report.json` ← `PD_data.csv`）幅值 max 8462，量级是原始计数；模拟器是 mV（满量程 100）。同一个 `amplitude` 字段承载两种相差约百倍量纲。未做现场标定前，任何"幅值 = mV"的表述都是假设。
3. **事件里的 `device_id` 有两套命名，写死任何一个都只查得到一半。** 2026-10-09 之前 UHF 模拟源不调 `set_device_info`，所有 UHF 事件都落在 `PDSimulator` 的类默认 `"SIM-001"`（多源还共用同一个键，PRPD/FFT/RingBuffer 互相混积）；现在改为与数据源 id 同名（`sim-uhf-0`、`sim-ae-1`…）。既有库里两种都有，所以设备候选只能来自 `storage.device_ids`（读 `pd_events` 里真实出现过的 id），不要用 `device.list`（纯模拟器下为空）或 `stats.sources` 的键（含从未产生事件的源）。趋势页与 `tools/rpc_check.py` 已按此口径改。

另外：趋势**不要读 `pd_trend_data` 表**——那里的行是旧 `signal_router` 每 50 帧写一次的累计值与编造的常量关系（`avg=max*0.6`、`energy=count*max*0.1`、`noise=0`）。用 `storage.query_trend_series`，它从 `pd_events` 按箱即时聚合、空箱补 0。

## 图表与前端必须记住的坑

- **canvas 图表只能用 Electron 截图模式验**（`PD_CAPTURE=路径 PD_ROUTE=页面 PD_CAPTURE_WAIT=毫秒`，加 `PD_INTERACT=1` 会跑真点交互脚本）。IDE 内置浏览器在后台时 `document.hidden` 为 true，rAF 与 uPlot 内部初始化整条停摆，拿不到任何画面。**带写入的界面必须真点提交 + 读回服务端**，静态截图和 DOM 文本看不出接线错误。
- **`v-model` 落在 `type="number"` 上会自动走 `looseToNumber`**（不需要 `.number` 修饰符），所以草稿值是 number 不是 string。按 string 处理再 `.trim()` 会抛 TypeError，且只在真点时暴露——Python 侧测试永远测不到。
- **uPlot 1.6.32**：没有 `draw()`（只有 `redraw(rebuildPaths, recalcAxes)`）；轴网格是 `Axis.grid:{stroke}` 不是 `gridStroke`；`cursor.focus` 是函数；`setData(data, reset)` 若传同一个外层数组会跳过重算范围。
- **three.js**：`LineSegments` 按**顶点对**画线不是折线，画立方体要显式给 12 条棱；`THREE.Color` 只认 3/6 位十六进制，而 `--color-app-border` 经 `getComputedStyle` 取到的是 **8 位**（`#7373731f`，末两位是 alpha），喂进去会 warn 后静默留白——用 `chart-theme.ts` 导出的 `borderSolid` + `borderAlpha`。
- **合成输入驱动交互的三个必要条件**（`sendInputEvent`）：click 必须带 `clickCount: 1`；down/up 之前必须先有一次 `mouseMove` 落到该元素；拖拽中间的 move 必须带 `button: "left"`，否则会被当成无键按下。
- **设计令牌是 `--color-app-*` 前缀**，不是 shadcn 的裸名。canvas 拿不到 CSS 变量，须经 `lib/chart-theme.ts` 解析成具体字符串（带缓存 + MutationObserver 失效）。
- Electron 安全基线：`contextIsolation`、`sandbox`、preload 必须是 CommonJS `.cjs`。

## 性能与操作系统事实

- **Windows 定时器粒度约 15.6ms** 会把任何亚 60ms 的 `QTimer`/`setInterval` 节奏量化到 15.6ms 的整数倍：50ms 的 flush 实测中位 61.97ms = 15.97Hz，与观测速率吻合到小数点后两位。需要精确速率就**按时间戳显式掐帧**，不要靠定时器周期本身限速。
- 遇到"图表卡"先量**入帧率**与**绘制率**两个数再决定查哪边。实测绘制 p95 仅 0.7–0.9ms @4096 点，卡感几乎总在供给侧。
- 性能目标：PRPD ≥500K events/s；FFT(4K) ≥1K 次/s；DataBus p99 <10ms。
- **内存目标按实测分档**（原先写的"峰值内存 <100MB"经定量作废：光库的常驻地板就 ~160 MB，还没算任何通道数据）。2026-09-30 用 `tools/mem_baseline_probe.py` 测得：

  | 块 | MB | 性质 |
  |---|---|---|
  | scipy.signal + scipy.ndimage | 72 | 固定，单项最大 |
  | sqlalchemy + core.data.models | 37 | 见下方"ORM 是被谁拖进来的" |
  | numpy | 12 | 固定 |
  | structlog + rich | 12 | 固定 |
  | PySide6.QtCore | 6.6 | 固定 |
  | 解释器 | 19 | 固定 |
  | linecache | 14.5 | 一次性；运行时会把 scipy 源码读进缓存，涨到 523 个文件后走平 |
  | **每通道** | **39.8** | **严格线性**（灌 8 通道实测 40.1→39.8） |

  实测单通道稳态 RSS **247–250 MB**；生产配置 4 UHF + 4 AE = **8 通道 → 外推 ~510 MB**。
  分档口径：**单通道 <300 MB；8 通道 <600 MB；长稳后半段斜率 <1 MB/min**。

- **每通道 39.8 MB 里约 31 MB 是 `RingBuffer`，而它只写不读**——已决定暂不动，只记档，别重复调查。`uhf_pipeline.py:104` 与 `ae_pipeline.py:203` 各自 `buffer.extend(samples)` 塞进一个 1,000,000 点的 `deque`（元素是装箱 Python float，tracemalloc 归属 `ring_buffer.py:48` = 30.8 MB / 1,015,625 个对象），之后函数内再没碰过；全仓库唯一的读点在它自己的单测（`tests/test_ring_buffer_thread.py`、`tests/test_integration.py`），旧 `ui/` 也没有消费者（且已归档）。附带 CPU：8 通道 @20FPS ≈ 每秒 65 万次 float 装箱。
  不删的理由：它是旧 UI"历史回放/导出"的支撑，属**尚未迁移的意图**而非死代码。真要收，方向是改成 numpy 预分配环形数组（31→8 MB/通道，且不装箱），但要同步改 `get_all/get_last/get_range` 的返回类型。

- **ORM 是被谁拖进来的**：`core/services/__init__.py:12` 急切重导出 `alarm_service`，后者 `:30` 引 `core.data.monitor_models`，经 `core/data/__init__.py:4` → `models.py:13` 拉起 sqlalchemy。所以只要 import 任何 `core.services.X`，这 37 MB 就跑不掉——`--no-db` 也一样。已把 `core/utils/logger.py` 的顶层 ORM 导入改惰性（`setup_logging` 那步从 +60.2 降到 +23.4 MB），但**对 sidecar 总 RSS 无净收益**，因为上面这条路随后就会把 ORM 载回来；要真拿到这 37 MB 得动 `core/services/__init__.py` 的公开导入面，是另一项决策。

## 代码约定（内核 Python）

- 所有公共 API 要有类型注解；用 `from __future__ import annotations` 保 3.10 兼容（`requires-python = ">=3.10"`，开发机实际跑 3.14.2）。
- 导入顺序：标准库 → 第三方 → 项目内（`core.*`）。文档串用 Google 风格。
- 日志用 `logging.getLogger(__name__)`；异常要捕获具体类型（不要裸 `except Exception`），栈追踪用 `logger.exception()`，链式抛出用 `raise ... from e`。
- **argparse 的 help 文本里 `%` 必须写成 `%%`**，否则 `ValueError: badly formed help string` 会让整个 sidecar 起不来，而所有测试仍然全绿（因为没人测过 `parse_args()`）。CLI 的 help 必须真的渲染一遍才算测过。
- **后台线程：置标志位停止的，`stop()` 里必须 join；等周期用 `Event.wait()`，不要用 `time.sleep()`。** 两条都用 `PDSimulator`/`PDStorageService` 实际踩过：只置 `_is_running = False` 不 join，daemon 线程会带着未跑完的一帧进入解释器收尾，往已销毁的 `PDDataBusSignals`(QObject) 上 emit，于是每次退出刷 `RuntimeError: Signal source has been deleted`；而把 `time.sleep(flush_interval)` 睡在循环开头，`stop()` 的 `join(timeout=5.0)` 就是抛硬币，实测留下 55 个 `PDStorageFlush` 线程和整套多花 24 秒。既有的正确先例：`udp_driver.py:145`、`storage_service.py`（已改 Event）、`logger.py:195`。join 前判一下 `is not threading.current_thread()`，否则从工作线程里调 `stop()` 会自杀式 RuntimeError。
- **测试夹具造出带后台线程的对象，必须 `yield` + teardown**；只 `return` 就等于把线程漏到会话结束（`test_controller_integration.py` 的 `ctrl` 夹具踩过，漏出一个跑了 1990 帧的模拟器）。

## 测试与 lint 的仓库级坑

- **`.flake8` 的 `ignore = E501, E203, C901` 是覆盖默认忽略表，不是追加**，因此 W503 与 W504 同时生效：跨行二元运算符放行首还是行尾都被报，只能拆成不把运算符放在换行处。E226 同理是活的。
- `.pre-commit-config.yaml` 的 flake8 args 原先写四个 `--ignore=`，而 **flake8 的 `--ignore` 重复出现只有最后一个生效**（已修为单个加引号的 `--extend-ignore`；不加引号会被 YAML 流式序列按逗号拆开，后三项变成待检查的文件名）。
- 全局解释器没装 `flake8`，pre-commit 用自己的环境——本地想先过一遍得另想办法，别以为命令没报错就是干净。
- 提交会跑 pre-commit（black/isort/flake8 等）。**绝不用 `--no-verify` 绕**；钩子改坏了就修根因。
- 变异验证是常规要求：新增断言后把被测行为注掉，确认测试确实失败。
- 有一条测试曾"单跑绿、全套红"：根因是把 `now` 放在模块级常量——全套跑约 100 秒，等该测试执行时已陈旧几十秒。时间相关的夹具必须现取。

## Git 安全（本仓库特有，违反过会出事）

- **`origin` 指向另一个项目**（`Equipment-Management.git`）。绝不裸跑 `git push`。`pd-origin` 是 `main` 跟踪的上游，`partial-discharge` 是新增远端。任何 push/fetch 前先 `git remote -v`。
- 工作区**长期存在上百个用户未提交的在制品**（`core/`、`tests/`、`docs/`、`desktop/` 下数百文件）。绝不 `git add -A` / `git add <宽目录>`；逐个写明确文件名，提交前用 `git status --short` 与 `git diff --cached --name-only` 复核。
- **`??` 不等于"新文件"**：仓库里有大量**早就存在、从未被 add 过**的源码（曾经的 `processor_manager.py`、`uhf_pipeline.py`、`tests/test_data_bus.py`、`docs/architecture.md` 等）。改在这些文件里的代码，若提交只带"已跟踪的修改"会**静默丢失**。判断用 `git cat-file -e HEAD:<路径>`。
- **工作区测试通过 ≠ HEAD 可运行**。部分提交状态下必须 `git archive HEAD | tar -x -C <tmp>` 再逐个 import 验一遍——曾靠这一步抓出"已跟踪代码 import 了未跟踪模块"的断链。
- 全量暂存会被自动策略直接拦掉（连生成全量清单也拦）。用户即便要求"全部提交"，也要预期只能提交显式点名的那一批，并如实报告剩余项。
- 仓库有 2 个历史 `git stash`（`WIP on main: 211d38b`、`5169ee2`），不要清。
- 全仓库 CRLF/LF 混用，`git diff` 常显示整文件重写；看真实内容差异加 `--ignore-cr-at-eol`。

## 关键文件索引

| 路径 | 作用 |
|---|---|
| `sidecar.py` / `sidecar.spec` | 内核入口 / PyInstaller 冻结配置 |
| `core/protocol/frames.py` ↔ `desktop/src/shared/protocol.ts` | 线格式契约（手工镜像，改一边必改另一边） |
| `core/protocol/sidecar_server.py` | WS 服务、扇出、节流与 RPC 注册 |
| `core/protocol/storage_rpc.py` | 只读查询 RPC（全部 background，避免阻塞 Qt 事件循环） |
| `core/services/persistence_service.py` | 总线事件 → SQLite 队列（`--with-storage` 时启动） |
| `core/processing/processor_manager.py` | per-channel 处理器工厂（含 PRPD 量程初值） |
| `desktop/src/main/sidecar.ts` | 内核子进程守护、端点解析、自动重启 |
| `desktop/src/main/index.ts` | 窗口/生命周期 + 截图验收模式（`PD_CAPTURE`/`PD_INTERACT`） |
| `desktop/src/renderer/lib/store.ts` | 前端全局状态与 `call()` RPC 封装 |
| `docs/ELECTRON_REWRITE_PLAN.md` | 迁移决策、里程碑状态、全部实测数据 |
| `docs/PROJECT_MEMORY.md` | 技术决策的来历、被推翻的假设、前人踩过的判断错误 |
| `docs/HANDOVER.md` | 交接文档（仓库现状与风险、工作量、验收纪律） |
| `docs/AI_HANDOFF_PROMPT.md` | 给新 AI 会话的交接提示词（可直接粘贴） |
| `docs/PROJECT_CLEANUP_PLAN.md` | 文件清理轮次记录与仓库卫生缺陷清单 |

## 仍然成立的传统内容

传感器规格（UHF 100MHz / AE 2MHz / HFCT 50MHz / TEV 100MHz 及各自管线）、`config/default_config.json` 的配置结构、各 DSP 处理器的职责——这些描述的是 `core/`，与 UI 重写无关，仍然有效。

**但报警阈值有一处配置与代码不一致，别照抄任何一方的"权威值"：**

- 代码里的四级（`core/services/alarm_service.py:43-46`）：critical **80%** / high **60%** / medium **40%** / low **20%**。
- `config/default_config.json:55-58` 只有两级：warning **50** / critical **80**。
- 旧版 `AGENTS.md` 写的"general ≥50%、info ≥30%"**两边都对不上**，是过期描述。

去抖是连续 3 次触发（`_hysteresis_threshold`）。百分比的基准是"通道量程"（通道级 `channel_max` 优先，其次设备级，最后全局 100.0 mV），而 AE 通道的基准值本身存在矛盾：已归档的旧 `ui/controller_config.py` 给 100.0、`core/foundation/data_source_manager.py` 给 800.0，注释分别称 mV 与 μV。**这条待领域确认，不要自行统一。**（`ui/` 归档后只剩 `core/` 一侧在生效，但矛盾本身没解决。）

数据库位置 `data/monitor.db`（SQLite + WAL），真实表名是 `pd_devices` / `pd_channels` / `pd_events` / `pd_alarms` / `pd_trend_data`（旧文档里写的 `trend_data`、`alarms` 不存在）。冻结态下可写状态落在 `%LOCALAPPDATA%\UHF-PD`（可用 `PD_STATE_DIR` 覆盖），只读资源走 `sys._MEIPASS`——见 `core/foundation/runtime_paths.py`。

## 模拟器调试

```bash
python sidecar.py --pd-type corona     # 负半周聚集（270~330°）
python sidecar.py --pd-type surface    # 正半周
python sidecar.py --pd-type internal   # 正半周前沿
python sidecar.py --pd-type floating   # 全相位分布
python sidecar.py --pd-type corona --pulse-rate 60000   # 让 PRPD 有足够事件可看
```

每帧 4096 点 @100MHz 只覆盖 41μs，脉冲数按 `poisson(pulse_rate * n / sample_rate)` 抽，所以 `pulse_rate` 默认 50（每秒）会让约 488 帧才落进 1 个脉冲——**验收/演示必须调高它**，否则 PRPD 面板几乎永远是空图。

## 相关文档

- `docs/ELECTRON_REWRITE_PLAN.md` — 迁移计划与实测记录（架构决策的首要来源）
- `docs/PROJECT_MEMORY.md` — **技术决策的来历、被推翻的假设、前人踩过的判断错误**（规则之外的"为什么"）
- `docs/HANDOVER.md` — 交接文档：仓库现状与风险、里程碑与工作量、验收纪律
- `docs/AI_HANDOFF_PROMPT.md` — 可直接粘贴给新 AI 会话的交接提示词
- `docs/architecture.md`、`docs/api.md`、`docs/contributing.md`、`docs/build_guide.md` — 注意这几份由旧架构文档派生，与 `AGENTS.md` 之间存在过期描述，M8 一并校正
- `README.md`、`TESTING.md`、`Update.md` — 面向用户/测试的说明，同样含旧架构内容
- `docs/PROJECT_CLEANUP_PLAN.md` — 冗余/过期文件的处置记录
