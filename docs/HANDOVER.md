# 项目交接文档（HANDOVER）

生成日期：2026-10-08 ｜ 最后更新：**2026-10-12** ｜ 交接对象：接手本仓库的工程师

> **本文第 1、3、5 节已按 2026-10-12 的仓库状态重写**（原版描述的是迁移归档**之前**的状态）。
> 第 5.1 节是 M8 当时的追加记录，保留作历史。里程碑与剩余工作量以第 5 节为准。

---

## 1. 项目是什么

工业局部放电（Partial Discharge）在线监测主机应用。UHF + AE 双模态传感器，前端 FPGA 经
UDP 采数，做实时波形/频谱/PRPD/PRPS 图谱、报警、历史与趋势分析。

**UI 层重写已完成并收尾**，现在只有一套界面：

| 目录 | 规模 | 状态 |
|---|---|---|
| `desktop/` | Electron 33 + Vue 3 + TS + Vite | **现役界面**，唯一开发对象 |
| `core/` | 约 15k 行 Python（numpy/scipy DSP + FPGA 采集） | **处理内核**，以无头 sidecar 运行，经本机 WebSocket 与前端通信 |
| `_archive/ui-offline-20261008/` | 23,183 行 PySide6 | **旧界面，已归档下线**（该目录已 gitignore，不参与运行与测试） |

**没有回滚路径**：`python main.py` 已不可用（`main.py` / `main_v2.py` 连同旧打包配置一起归档）。
要回滚就把 `_archive/ui-offline-20261008/` 下的 `ui/` 与 `main.py` 移回仓库根，并把
`pyproject.toml` / `.coveragerc` / `pytest.ini` 的覆盖率目标改回 `["core", "ui"]`。
改 UI 一律改 `desktop/`。

启动方式、测试命令、出包流程见 `AGENTS.md`（唯一的 agent 指令来源）与 `README.md`。

---

## 2. 上手（照抄即可）

```bash
# 主工作流：Vite + Electron，主进程会自动 spawn Python sidecar
cd desktop && npm ci && npm run dev

# 只跑内核（观察协议与推流）
python sidecar.py --sim-type uhf --pd-type corona --pulse-rate 60000
python sidecar.py --help          # 参数全在 --help，别凭记忆猜 flag

# 测试与类型检查
python -m pytest -q --no-cov                  # 当前 577 passed / 2 skipped
python -m pytest tests/test_sidecar_protocol.py -q   # 跨进程协议
cd desktop && npm run typecheck && npm run build

# 内核冻结产物（目标机不装 Python）
cd desktop && npm run build:kernel && npm run verify:kernel
cd desktop && npm run package:dir             # win-unpacked 装机版
```

工具链实测版本：Python 3.14.2（`requires-python >= 3.10`）、Node v24.14.0、npm 11.9.0、
Electron 33.4.11。测试 33 个文件。

**环境有两个坑**：① 构建机的全局 Python 装着与本工程无关的 torch/cv2/transformers/pandas；
② 仓库根有 `venv/` 但里面**缺 `msgpack`**，所以 dev 模式跑 sidecar 用的是全局 `python`，
`venv` 只适合跑部分测试。全局解释器**没装 flake8**，本地想先过一遍 lint 得另想办法。

---

## 3. 仓库当前状态 ⚠️ 交接时最要紧的一节

```
分支：main @ 96b7be5，[pd-origin/main: ahead 50]
工作区：2026-10-13 统一提交后**已全部清空**——无未暂存、无未跟踪、无待删。
       仅剩少数"只差行尾"的文件（LICENSE、.flake8、tests/__init__.py、5 个 tests/*.csv）
       未纳入提交：内容与 HEAD 一致，属仓库长期 CRLF/LF 混用的噪声。
```

（原版这里记的"32 个 `ui/` 删除 + 86 改 + 39 未跟踪"已完成入库，见提交
`578cb55`、`00087e5`、`7e9f18c`、`96b7be5`。）

### 3.1 远端配置

```
origin             → UHF_PD_PLATFORM_V3.git   ← 本项目发布仓（2026-10-13 改指）
pd-origin          → UHF_PD_System.git        ← 上游老仓，UI 重写前
partial-discharge  → Partial_Discharge.git    ← 历史远端，保留未删
```

2026-10-13 之前 `origin` 指向**另一个项目**（`Equipment-Management.git`），那时裸跑
`git push` 会把提交推到设备管理项目去。URL 现已修正，但**push/fetch 前仍然先
`git remote -v` 核对**。当前 main 已推送到 `origin`。

### 3.2 `??` 不等于"新文件"——这条会直接导致丢代码

工作区里仍有一批**早已存在、从未被 `git add`** 的源码。原版列的那批（`AGENTS.md`、
`CLAUDE.md`、`docs/HANDOVER.md`、`docs/AI_HANDOFF_PROMPT.md`、
`tools/mem_baseline_probe.py`）**已在 2026-10-08 入库**，现在 HEAD 里都有。

**2026-10-12 复核后仍未入库的**（用 `git cat-file -e HEAD:<路径>` 逐个验过）：

| 文件 | 后果 |
|---|---|
| `pytest.ini` | pytest 配置（`--cov=core` 等），**HEAD 里没有** |
| `docs/migration_coverage.md` | 新旧功能对照表 + P0–P5 实施计划 |
| `desktop/src/renderer/lib/gdop.ts`、`colormap.ts`、`ae-stats.ts`、`snapshot-draw.ts` | 前端被 import 的库模块 |
| `desktop/src/renderer/components/charts/gdop-map.vue`、`prpd-scatter.vue`、`snapshot-mini.vue` | 前端组件 |
| `config/themes/`、`tools/benchmarks/`、`tools/make_app_icon.py`、`scripts/ui_lint.py` | 配置与工具 |
| 多个 `tests/*.py`（`test_data_bus.py`、`test_peak_detector.py`、`test_ring_buffer_thread.py` 等） | 从未进过版本库 |

判断某个路径是否已跟踪：`git cat-file -e HEAD:<路径>`。
**提交时若只带"已跟踪的修改"，改在这些文件里的代码会静默丢失**——尤其危险的是
`analysis-view.vue` 这类**已跟踪**文件正 import 上面那些**未跟踪**模块：
只提已跟踪修改会让 HEAD 直接 ImportError。

### 3.3 其它 git 纪律

- 工作区长期存在上百个**用户未提交的在制品**（当前 86 个已跟踪修改 + 39 个未跟踪）。
  **绝不 `git add -A` / `git add <宽目录>`**；逐个点名，提交前用 `git status --short` 与
  `git diff --cached --name-only` 复核。全量暂存会被自动策略直接拦掉。
- 仓库有 2 个历史 `git stash`（`WIP on main: 211d38b`、`5169ee2`），**不要清**。
- 行尾 CRLF/LF 混用且无 `.gitattributes`，审改动一律加 `--ignore-cr-at-eol`。
- 绝不用 `--no-verify` 绕 pre-commit；钩子改坏了就修根因。
- 未跟踪文件删掉**不可恢复** → 移到 `_archive/<name>-YYYYMMDD/`（已 gitignore）而不是删。

### 3.4 工作区脏不影响 HEAD 可运行性
`git archive HEAD | tar -x -C <tmp>` 逐个 import 验过。曾靠这一步抓出"已跟踪代码 import
了未跟踪模块"的断链（`b0bc517` 修的）。**每次部分提交后都要重跑这个检查。**

---

## 4. 架构：跨进程边界（改任何一侧都要改另一侧）

内核与前端之间只有一个契约：

```
core/protocol/frames.py   ↔   desktop/src/shared/protocol.ts     （手工镜像）
```

- **双通道**：`/stream` 走有损实时数据（最新帧胜出）；`/ctrl` 走可靠事件 + RPC。
  URL 带 `?token=`，token 每次启动随机，由内核打印到 stdout、主进程正则解析
  （`PORT_RE` 认 stream URL 行、`TOKEN_RE` 认独立 token 行，且必须先见到 port）。
- **QoS 划分**：lossy = `waveform` `fft` `prpd` `prps` `ae_envelope` `ae_hit` `stats` `log`；
  reliable = `alarm` `alarm_cleared` `device_status` `acquisition` `source`。
  无损通道**不允许限流**（报警不能丢）。
- **热图矩阵编码**：uint8 线性量化 + zlib（`meta.qmax` 反量化）。必须先 `np.rint` 再转整
  ——`astype` 向零截断会把误差从半步长放大到一整步长。渲染侧用原生
  `DecompressionStream("deflate")`，不要引入 fflate。
- **msgpack 的 bin 字段地址未对齐**（实测 byteOffset 113），直接 `new Float32Array(buf, off)`
  会抛 RangeError，必须经 `viewOf` 拷贝。
- **推流节流**：`FLUSH_INTERVAL_MS = 10`；每 topic 目标 Hz 由 `system.set_topic_rates` 显式
  设置（(0,200]）。三个"没发出去"的计数分开记账：`coalesced` / `throttled` / `dropped`。
- **持久化要显式开启**：`--with-storage` 才同时启动 SQLite 刷写线程**和**
  `PersistenceSubscriber` 订阅 `pd_event_detected`。只起线程不订阅 → 队列永远空。

---

## 5. 里程碑与剩余工作量（2026-10-12 复核）

| 里程碑 | 状态 | 剩余 |
|---|---|---|
| M0 边界协议 / M1 sidecar / M2 前端骨架 / M3 第一批页面 / M4 流式渲染层 | ✅ 完成（全部截图 + 真点验收） | — |
| M5 第二批页面 | ✅ 完成。5.1 的"连续 1h 无内存增长"已复跑通过：**60.05 min / 120 采样点**，四角色全有界；侧车那段 +30 MB/h 是 `prpd_processor` 的 `deque(maxlen=100000)` 填满，40.9 分钟到平台，属**有界消耗** | — |
| M6 第三批页面（AE 与分析） | ✅ 完成（含数据导入、报告导出内核侧）。**实时事件的放电类型标签已于 2026-10-12 补上** | 3D 定位视图（内核无 TDOA 通路）、等效放电量（无计数→物理量换算）、最大重复率（口径未定义）——三项都卡在领域口径，不是工程量 |
| M7 性能长稳 | 🟡 7.3 线性度 / 7.4 崩溃恢复 / 7.5 启动时间均已实测结案 | **7.1 探针待现场复跑**；**7.2 24h 长稳按用户决定跳过** |
| M8 切换收尾 | 🟡 归档、装机包、`AGENTS.md` 都已做 | 本次做：出包与文档收尾；统一提交见 §3 |
| P0–P5 迁移缺口 | ✅ 全部交付：P0 日志导出落盘 / P1 导入筛选+幅值直方图 / P2 报告导出（JSON+Excel+Word）/ P3 运行时数据源启停 / P4 AE 历史持久化+散点+导出 / P4b 内核 CPU-RSS / P5a 暗色主题 / P5b 真 PRPD 3D / P5c 实时类型标签 | — |

**接手顺序建议**：① 读 `AGENTS.md`（规则与禁区）→ ② 要动架构时读 `docs/ELECTRON_REWRITE_PLAN.md`
的原始实测依据 → ③ 需要补功能缺口时看 `docs/migration_coverage.md`（新旧对照 + P0–P5 的处置结论）
→ ④ 按 §3 的提交纪律统一提交。

---

## 5.1 M8 进展（2026-10-08 追加，本文件上方各节仍描述归档前的状态）

**旧 `ui/` 已归档下线**，本文件第 1 节那张"两套界面同时存在"的表**已经过期**：

- `ui/`（PySide6）、旧入口 `main.py`/`main_v2.py`、旧打包配置 `build.py`/`build.spec`/
  `PDMonitor.spec`/`installer.iss`、8 个 ui 耦合测试 → 全部移到
  `_archive/ui-offline-20261008/`（已 gitignore，不参与运行与测试）。
- 因此 **§1 的"`python main.py` 仍可用、`ui/` 是回滚路径"不再成立**：没有回滚路径了。
  回滚办法见 `AGENTS.md` 的"当前状态"一节。
- 实测前提：`core/`、`tools/`、`sidecar.py` 对 `ui` 的 import 数为 0；归档后全仓库只剩注释引用。
- 连带改的配置：`pyproject.toml` / `.coveragerc` / `pytest.ini` / `.github/workflows/tests.yml`
  的覆盖率目标从 `core,ui` 改成 `core`。
- 效果：**450 passed / 2 skipped / 0 failed，整套 21.8s**（原先 ~98s、578 passed）。
- **§3.2 那张"从未入库的文件"表也过期了**：`AGENTS.md`、`CLAUDE.md`、多份 `docs/*.md`、
  多个 `tests/*.py`、`tools/mem_baseline_probe.py` 已在 2026-10-08 入库（提交 `fe276d3`、
  `75415bd`）；`ui/widgets/performance_monitor_widget.py` 随 `ui/` 一起归档。

仍未做：装机包、其余过期文档校正（见 `docs/ELECTRON_REWRITE_PLAN.md` 的 M8 进展一节）。

---

## 6. 验收纪律：这个项目里"做完"的定义

这一节不是形式，是因为**多次出现"测试全绿但功能是坏的"**。

1. **canvas 图表必须用 Electron 截图模式验**。IDE 内置浏览器在后台时 `document.hidden=true`，
   rAF 与 uPlot 初始化整条停摆，拿不到画面。
   ```bash
   PD_CAPTURE=绝对路径 PD_ROUTE=/页面 PD_CAPTURE_WAIT=毫秒 PD_INTERACT=1 npm run dev
   ```
   （Git Bash 下 `PD_ROUTE=/` 会被路径改写，加 `MSYS_NO_PATHCONV=1`。）
2. **带写入的界面必须真点提交 + 读回服务端**。静态截图和 DOM 文本看不出接线错误。
3. **新增断言要做变异验证**：把被测行为注掉，确认测试确实失败。本项目已多次遇到
   "看起来在测、实际恒真"的断言（`gc.get_objects()` 数不到 numpy 数组、`assert
   rpc.handlers` 恒真、夹具计数放错位置导致对错实现给出同样结果）。
4. **CLI 的 help 必须真的渲染一遍**才算测过（`%` 未转义会让整个 sidecar 起不来而测试全绿）。
5. **改完要重跑整套**，单文件重跑不算——已有测试"单跑绿、全套红"的先例。

---

## 7. 已知缺陷与待决事项

### 7.1 会影响读数可信度（优先级最高）
- **幅值单位口径缺失**：全链路无计数→mV 换算。FPGA 按大端 int16 裸计数解包，
  `ChannelConfig.gain` 存在但从未作用于波形。真实数据幅值 max 8462（原始计数），
  模拟器是 mV（满量程 100）——同一个 `amplitude` 字段承载两套相差约百倍量纲。
  **需现场标定，未标定前"幅值 = mV"都是假设。**
- **报警阈值三处对不上**：代码四级 critical 80/high 60/medium 40/low 20
  （`alarm_service.py:43-46`）；`config/default_config.json:55-58` 两级 warning 50/critical 80；
  AE 通道基准值本身矛盾（`ui/controller_config.py` 100.0 vs
  `core/foundation/data_source_manager.py` 800.0，注释分别称 mV 与 μV）。
  **属领域问题，需人来统一，不要自行决定。**

### 7.2 性能/内存（已定量，未处置）
- 每通道 39.8 MB 中约 31 MB 是**只写不读**的 `RingBuffer`（8 通道 ≈ 248 MB）。
  已决定**暂不动只记档**——它是旧 UI 历史回放/导出的支撑，属未迁移意图。
- 修掉结果缓存层后仍有 **+0.38 MB/min** 残余慢增长，待 60 min 长稳定性。
- 37 MB ORM 不可避免，根因是 `core/services/__init__.py:12` 急切重导出。

### 7.3 测试卫生（判给 M8）
- `tests/test_controller_integration.py` 的 `TestControllerInitialization` 有 5 个用例
  （:103/112/118/125/131）绕过带 teardown 的夹具，各漏一个存储刷写线程。
  属 `ui/` 旧界面测试，M8 删 `ui/` 时一并消失。

### 7.4 文档过期项
2026-10-13 已处理：`docs/architecture.md`、`api.md`、`contributing.md`、`build_guide.md`、
`docs/PROJECT_MEMORY.md`、`docs/PROJECT_CLEANUP_PLAN.md` 与根目录 `TESTING.md`、`Update.md`
均为旧架构（PySide6 单体）派生、与 `AGENTS.md` 冲突，**已删除**；`README.md` 已按新架构重写。
报警阈值的旧描述（"general ≥50%、info ≥30%"）两边都对不上实际代码，仍是待确认项。

---

## 8. 关键文件索引

| 路径 | 作用 |
|---|---|
| `sidecar.py` / `sidecar.spec` | 内核入口 / PyInstaller 冻结配置 |
| `core/protocol/frames.py` ↔ `desktop/src/shared/protocol.ts` | 线格式契约（手工镜像） |
| `core/protocol/sidecar_server.py` | WS 服务、扇出、节流与 RPC 注册 |
| `core/protocol/storage_rpc.py` | 只读查询 RPC（全部 background） |
| `core/services/persistence_service.py` | 总线事件 → SQLite 队列 |
| `core/processing/processor_manager.py` | per-channel 处理器工厂（含 PRPD 量程初值） |
| `core/processing/uhf_pipeline.py` / `ae_pipeline.py` | 两条模态管线 |
| `desktop/src/main/sidecar.ts` | 内核子进程守护、端点解析、自动重启 |
| `desktop/src/main/index.ts` | 窗口/生命周期 + 截图验收模式 |
| `desktop/src/renderer/lib/store.ts` | 前端全局状态与 `call()` RPC 封装 |
| `desktop/src/renderer/lib/chart-theme.ts` | canvas 取色（CSS 变量→具体值） |
| `tools/mem_baseline_probe.py` | 内存基线归因探针（A 库成本 / B 启动路径） |
| `tools/soak_memory.py` | 长稳采样与斜率判定（M5.1 验收与 M7.2 共用） |
| `tools/rpc_check.py` | 协议 RPC 手测 |
| `docs/migration_coverage.md` | 旧界面功能在新端的覆盖度与缺口处置记录 |
| `docs/ELECTRON_REWRITE_PLAN.md` | 迁移决策与全部实测记录 |
| `README.md` | 面向使用者的项目说明（架构、特性、快速开始、打包） |
| `AGENTS.md` | 给 AI 助手的指令（`CLAUDE.md` 只是指向它的指针） |

---

## 9. 阅读顺序建议

1. 本文件（现状与风险）
2. `AGENTS.md`（规则与禁区）
3. `docs/ELECTRON_REWRITE_PLAN.md`（要动架构时的原始实测依据）
4. `docs/migration_coverage.md`（新旧功能对照与缺口处置）
5. `README.md`（面向使用者的说明）
