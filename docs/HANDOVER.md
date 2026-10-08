# 项目交接文档（HANDOVER）

生成日期：2026-10-08 ｜ 交接对象：接手本仓库的工程师

---

## 1. 项目是什么

工业局部放电（Partial Discharge）在线监测主机应用。UHF + AE 双模态传感器，前端 FPGA 经
UDP 采数，做实时波形/频谱/PRPD/PRPS 图谱、报警、历史与趋势分析。

**当前正处在一次 UI 层重写中途，两套界面同时存在**，这是接手时最容易误判的一点：

| 目录 | 行数 | 状态 |
|---|---|---|
| `desktop/` | 5,239（TS+Vue） | **现役开发对象**。Electron 33 + Vue 3 + TS + Vite |
| `core/` | 14,597（Python） | **处理内核**，保留为无头 sidecar，经本机 WebSocket 与前端通信 |
| `ui/` | 23,181（PySide6） | **旧界面，是回滚路径**，仍被 `main.py` 引用，按计划 M8 才删 |

所以 `python main.py` 仍可用，但**不是**日常工作流。改 UI 请改 `desktop/`，不要改 `ui/`。

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
分支：main @ c7f1aaf，[pd-origin/main: ahead 36] —— 36 个提交未推送
未提交：已跟踪且修改 96 个 / 已暂存 1 个 / 未跟踪 81 个
```

### 3.1 远端配置有陷阱，务必先读

```
origin             → Equipment-Management.git      ← 这是【另一个项目】！
pd-origin          → UHF_PD_System.git             ← main 的真正上游
partial-discharge  → Partial_Discharge.git         ← 新增远端
```

**绝不裸跑 `git push`**（会推到设备管理项目去）。任何 push/fetch 前先 `git remote -v`。
当前状态是**故意未推送**的。

### 3.2 `??` 不等于"新文件"——这条会直接导致丢代码

工作区里有大量**早已存在、从未被 `git add`** 的源码。已核实下列关键文件在 HEAD 里**不存在**：

| 文件 | 后果 |
|---|---|
| `AGENTS.md` / `CLAUDE.md` | 全部 agent 指令与项目约定，**只存在于工作区** |
| `build.py` | 构建入口 |
| `docs/PROJECT_CLEANUP_PLAN.md`、`docs/PROJECT_MEMORY.md`、`docs/HANDOVER.md`、`docs/AI_HANDOFF_PROMPT.md` | 文档收尾产物 |
| `tests/test_fft_processor.py`、`test_storage_service.py`、`test_controller_integration.py`、`test_logger_db_handler.py`、`test_simulator_shutdown.py` | 这些测试从未进过版本库 |
| `tools/mem_baseline_probe.py` | 内存基线归因探针（未提交；`tools/soak_memory.py` 已在库内） |
| `ui/widgets/performance_monitor_widget.py` | 旧界面组件（被测试 import） |

判断某个路径是否已跟踪：`git cat-file -e HEAD:<路径>`。
**提交时若只带"已跟踪的修改"，改在这些文件里的代码会静默丢失。**

### 3.3 其它 git 纪律

- 工作区长期存在上百个**用户未提交的在制品**（`ui/` 64 个、`core/` 37 个、`tests/` 35 个）。
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

## 5. 里程碑与剩余工作量

| 里程碑 | 状态 | 剩余 |
|---|---|---|
| M0 边界协议 / M1 sidecar 生产化 / M2 前端骨架 / M3 第一批页面 | ✅ 完成 | — |
| M4 流式渲染层（4.1–4.6） | ✅ 完成，全部截图 + 真点验收 | — |
| **M5 第二批页面** | 🟡 | **5.1 的"连续 1h 无内存增长"验收只跑到 29.5 min，需复跑**；5.3 仪表板（指标卡 + 三类快照 + 最新告警，20FPS 下 CPU < 15%）未开始 |
| M6 第三批页面（AE 与分析） | ⬜ | `ae-view.vue`、`analysis-view.vue` 仍是 **7 行桩** |
| M7 性能长稳 | 🟡 | 7.1 探针就绪待现场复跑；7.2–7.5 未做 |
| M8 切换收尾 | ⬜ | 旧 `main.py`/`ui/` 下线、装机包、文档收尾 |

**建议接手顺序**：① 先把未提交内容按文件名精确提交（见 §3.2 的风险）→ ② 复跑 60 min 长稳
（同时结掉 M5.1 验收和残余 +0.38 MB/min 的定性，且不改代码可并行做别的）→ ③ M5.3 仪表板
→ ④ M6。

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
`docs/architecture.md`、`api.md`、`contributing.md`、`build_guide.md`、`README.md`、
`TESTING.md`、`Update.md` 都由旧架构文档派生，与 `AGENTS.md` 存在冲突描述，**M8 一并校正**。
报警阈值的旧描述（"general ≥50%、info ≥30%"）两边都对不上实际代码。

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
| `docs/PROJECT_MEMORY.md` | **技术决策的来历与被推翻的假设** |
| `docs/ELECTRON_REWRITE_PLAN.md` | 迁移决策与全部实测记录 |
| `AGENTS.md` | 给 AI 助手的指令（`CLAUDE.md` 只是指向它的指针） |

---

## 9. 阅读顺序建议

1. 本文件（现状与风险）
2. `AGENTS.md`（规则与禁区）
3. `docs/PROJECT_MEMORY.md`（**为什么**这么定，以及前人踩过的判断错误）
4. `docs/ELECTRON_REWRITE_PLAN.md`（要动架构时的原始实测依据）
5. `docs/PROJECT_CLEANUP_PLAN.md`（仓库卫生历史）
