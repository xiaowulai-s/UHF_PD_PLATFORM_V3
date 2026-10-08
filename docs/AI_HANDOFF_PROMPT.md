# AI 交接提示词（AI HANDOFF PROMPT）

用法：把下面「=== 复制这整段 ===」之间的内容，作为首条消息发给新的 AI 会话
（或填进 IDE 的自定义指令 / `--append-system-prompt`）。
它自足到不依赖本仓库之外的任何上下文。

两点说明：
- 它**不重复** `AGENTS.md` 的全部内容——`AGENTS.md` 会在会话里自动注入。这里只放
  "必须先知道才能不闯祸"的部分 + 当前任务队列 + 事实速查。
- 里面的数字全部是实测值，别当作估计。

---

## === 复制这整段 ===

你接手的是 `UHF_PD_PLATFORM_v3`（工作目录 `D:\Demo\UHF_PD_PLATFORM_v3`）。开始动手前，
先按下面第 3 节读完必读文件；不要凭本提示词里的摘要就下结论。

### 1. 这个项目在做什么，以及立身之处

工业局部放电（PD）在线监测主机。UHF + AE 双模态，FPGA 经 UDP 采数，做实时波形/频谱/
PRPD/PRPS 图谱、报警、历史趋势。

**它的价值是"读数可信"，不是"界面好看"。** 图谱画得漂亮但极性标反、或纵轴量程锁死让
所有事件挤成一行，等于交付了一个会说谎的仪表。判断改动优先级时先问：影响不影响读数可信度。

### 2. 最容易立刻做错的 8 件事

1. **`origin` 指向另一个项目**（`Equipment-Management.git`）。`pd-origin` 才是 `main` 的上游，
   `partial-discharge` 是新增远端。**绝不裸跑 `git push`**；任何 push/fetch 前先 `git remote -v`。
   当前 main 领先 pd-origin/main 36 个提交，是**故意未推送**的。
2. **绝不 `git add -A` / `git add <宽目录>`**。工作区有上百个用户未提交的在制品
   （已跟踪修改 96 / 未跟踪 81）。逐个点名文件，提交前用 `git status --short` 和
   `git diff --cached --name-only` 复核。
3. **`??` 不等于新文件**。`AGENTS.md`、`CLAUDE.md`、`build.py`、多个 `tests/*.py`、
   `docs/PROJECT_CLEANUP_PLAN.md` 等**从未被 add**。改在这些文件里的代码，若提交只带
   "已跟踪的修改"会**静默丢失**。用 `git cat-file -e HEAD:<路径>` 判断。
4. **绝不 `--no-verify`** 绕 pre-commit。钩子改坏了就修根因。
5. **审改动一律 `git diff --ignore-cr-at-eol`**。仓库无 `.gitattributes`，HEAD 内行尾本身
   就不一致，工作区约 60 个文件已整体 CRLF；普通 diff 会把"删 30 行加 8 行"报成整文件重写。
6. **未跟踪文件删掉不可恢复** → 移到 `_archive/<name>-YYYYMMDD/`（已 gitignore），不要删。
7. **不要动** 仓库里 2 个历史 `git stash`（`WIP on main: 211d38b`、`5169ee2`）和 `venv/`。
8. **UI 改 `desktop/`，不要改 `ui/`**。仓库正处在 UI 重写中途，两套界面并存：
   `desktop/`（Electron 33 + Vue 3，5,239 行）是现役开发对象；`core/`（Python DSP，14,597 行）
   留作无头 sidecar；`ui/`（PySide6，23,181 行）是回滚路径，计划 M8 才删。
   `python main.py` 可用但不是日常工作流。

### 3. 开工前必读（按序）

1. `AGENTS.md`（指令与禁区；`CLAUDE.md` 只是指向它的指针，改动只改 `AGENTS.md`）
2. `docs/PROJECT_MEMORY.md`（**技术决策的来历、被推翻的假设、前人踩过的判断错误**）
3. `docs/HANDOVER.md`（现状、仓库风险、里程碑、验收纪律）
4. `docs/ELECTRON_REWRITE_PLAN.md`（要动架构代码时的原始实测依据）

### 4. 验证纪律：这里"做完"的定义

多次出现过"测试全绿但功能是坏的"，所以下面不是形式：

- **canvas 图表只能用 Electron 截图模式验**：
  `PD_CAPTURE=绝对路径 PD_ROUTE=/页面 PD_CAPTURE_WAIT=毫秒 PD_INTERACT=1 npm run dev`。
  IDE 内置浏览器在后台时 `document.hidden=true`，rAF 与 uPlot 初始化整条停摆，拿不到画面。
  Git Bash 下 `PD_ROUTE=/` 会被路径改写，加 `MSYS_NO_PATHCONV=1`。
- **带写入的界面必须真点提交 + 读回服务端**。静态截图和 DOM 文本看不出接线错误。
- **新增断言必须做变异验证**：把被测行为注掉，确认测试确实失败。本仓库反复出现
  "看起来在测、实际恒真"的断言（例：`gc.get_objects()` 数不到 numpy 数组）。
- **改完重跑整套**，单文件重跑不算（已有"单跑绿、全套红"的先例）。
- **部分提交状态下要验 HEAD 可运行**：`git archive HEAD | tar -x -C <tmp>` 再逐个 import。
- 常用命令：
  ```bash
  python -m pytest -q --no-cov              # 当前 577 passed / 2 skipped
  cd desktop && npm run typecheck && npm run build
  python sidecar.py --help                  # 参数看 --help，别凭记忆猜 flag
  ```

### 5. 事实速查（已实测，别重新发现）

**内存基线（sidecar）**
- 库的常驻地板 ~160 MB：scipy.signal+ndimage 72、sqlalchemy+core.data.models 37、
  numpy 12、structlog+rich 12、PySide6.QtCore 6.6、解释器 19。另有一次性 linecache 14.5。
- **每通道 39.8 MB，严格线性**；其中约 31 MB 是**只写不读**的 `RingBuffer`
  （1M 点 deque 装**装箱 Python float**，不是 float64 数组）。
  已决定**暂不动、只记档**（它是旧 UI 历史回放/导出的支撑，属未迁移意图）。**别重复调查。**
- 单通道稳态 247–250 MB；生产 4 UHF + 4 AE = 8 通道 → 外推 ~510 MB。
- 目标口径：单通道 <300 MB；8 通道 <600 MB；长稳后半段斜率 <1 MB/min。
  （原"峰值 <100MB"已作废，不可达。）

**PRPD 幅值轴**
- 初值 100 mV（`ProcessorManager(default_max_amplitude=100.0)`）。`publish_throttle` 只在
  观测 > 轴×0.8 时扩到观测×1.2，**只增不减、无收缩路径** → 初值取大了等于永久锁死。
- 不要改成 auto-scale：纵轴是测量量程，不是显示缩放。

**结果缓存层已整层删除**（`core/processing/result_cache.py` + `use_cache`）。
缓存键取累积事件流/波形字节的 md5，而事件只增不减 → 每帧必换键，实测 60 次 compute
命中 0 次且变慢 1.8×。`add_event` 本就增量维护矩阵，`compute()` 只剩一次 `.copy()`，
**没有可缓存的东西**。别重新加回来。

**趋势数据不要读 `pd_trend_data` 表**——那是旧 `signal_router` 每 50 帧写一次的累计值，
带编造的常量关系（`avg=max*0.6`、`energy=count*max*0.1`、`noise=0`）。
用 `storage.query_trend_series`（从 `pd_events` 按箱即时聚合、空箱补 0）。

**37 MB ORM 的根因不是 logger**：`core/services/__init__.py:12` 急切重导出 `alarm_service`
→ `:30` 引 `core.data.monitor_models` → `core/data/__init__.py:4` → `models.py:13` 拉起
sqlalchemy。所以 import 任何 `core.services.X` 都会拖上 ORM，`--no-db` 也一样。

**编码/线程约定**
- 热图矩阵：uint8 线性量化 + zlib；必须先 `np.rint` 再转整（`astype` 向零截断会把误差
  从半步长放大到一整步长）。渲染侧用原生 `DecompressionStream("deflate")`，不要引入 fflate。
- msgpack bin 字段地址未对齐（byteOffset 113），必须经 `viewOf` 拷贝，否则 RangeError。
- **后台线程：置标志位停止的，`stop()` 里必须 join；等周期用 `Event.wait()` 不用
  `time.sleep()`**；join 前判 `is not threading.current_thread()`。
- **测试夹具造出带后台线程的对象必须 `yield` + teardown**。
- `FLUSH_INTERVAL_MS = 10`；`--with-storage` 才同时启动刷写线程和 `PersistenceSubscriber`
  订阅，缺一个则 `storage.query_events` 恒返回 `[]`。

**平台事实**
- Windows 定时器粒度 ≈ 15.6 ms，会把亚 60ms 的节奏量化：50ms flush 实测中位 61.97ms
  = 15.97Hz。要精确速率就按时间戳显式掐帧。
- 遇到"图表卡"先量**入帧率**与**绘制率**两个数。实测绘制 p95 仅 0.7–0.9ms @4096 点，
  卡感几乎总在供给侧。
- 模拟器每帧 4096 点 @100MHz 只覆盖 41μs；`pulse_rate` 默认 50 会让约 488 帧才落 1 个脉冲
  → **验收/演示必须给 2e4~1e5**，否则 PRPD 面板永远近乎空图。实测入帧 ≈ 16.6 fps。
- 目标机不装 Python：内核以 PyInstaller 冻结产物交付（实测装机版 `python.exe` 进程数 0）。
  构建机全局 Python 有 torch/cv2 污染；`venv/` 缺 `msgpack`；全局没装 flake8。
- 出包加图标会去 GitHub 拉 `winCodeSign` 而这台机器连 GitHub 时通时不通；无签名证书，
  用 `CSC_IDENTITY_AUTO_DISCOVERY=false` 跳过。

**前端库的坑**
- uPlot 1.6.32：没有 `draw()`（只有 `redraw(rebuildPaths, recalcAxes)`）；轴网格是
  `Axis.grid:{stroke}` 不是 `gridStroke`；`Legend` 没有 `label`；`setData` 传同一个外层数组
  会跳过范围重算。
- three.js：`LineSegments` 按顶点对画线；`THREE.Color` 只认 3/6 位十六进制，而
  `--color-app-border` 取到的是 8 位（末两位 alpha）→ 用 `chart-theme.ts` 的
  `borderSolid` + `borderAlpha`。
- Vue：`v-model` 落在 `type="number"` 上会自动走 `looseToNumber`，草稿值是 number 不是
  string；按 string 处理再 `.trim()` 会抛 TypeError，且只在真点时暴露。
- 设计令牌是 `--color-app-*` 前缀（不是 shadcn 裸名）；canvas 拿不到 CSS 变量，须经
  `chart-theme.ts` 解析成具体字符串。
- Electron 安全基线：`contextIsolation`、`sandbox`、preload 必须是 CommonJS `.cjs`。

**lint**
- `.flake8` 的 `ignore = E501, E203, C901` 是**覆盖**默认忽略表而非追加，于是 W503 与 W504
  同时生效：运算符放行首或行尾都会被报，只能不把运算符放在换行处。E226 同理是活的。
- argparse 的 help 文本里 `%` 必须写 `%%`，否则 `ValueError: badly formed help string`
  让 sidecar 起不来，而所有测试仍全绿。

### 6. 尚未定论、需要人类决策的（**不要自行统一或"顺手修好"**）

| 事项 | 现状 |
|---|---|
| 幅值单位口径 | 全链路无计数→mV 换算；`ChannelConfig.gain` 从未作用于波形。真实数据 max 8462 是原始计数，模拟器是 mV 满量程 100，同一字段两套量纲。需现场标定 |
| 报警阈值 | 代码四级 critical 80/high 60/medium 40/low 20；`config/default_config.json` 两级 50/80；AE 通道基准 100.0 vs 800.0（注释分别称 mV 与 μV）互相矛盾 |
| `.gitattributes`（统一行尾） | 会一次性搅动数百个在途文件的 diff |
| `core/services/__init__.py` 公开导入面 | 改它才能拿到那 37 MB |
| 历史回放/导出是否真需求 | 决定 `RingBuffer` 那 31 MB/通道是删是改 |

### 7. 当前任务队列

按建议顺序（先做 ① 因为风险最大且不需要写代码）：

1. **把未提交内容按文件名精确提交**。重点确认 §2.3 那批从未提交的文件是否已入库。
   提交后跑 `git archive HEAD` 的 import 验证。
2. **复跑 60 min 长稳**（M5.1 的"连续 1h 无内存增长"验收上次截在 29.5 min）。
   `python tools/soak_memory.py --minutes 60 --sim-args "--pd-type corona --pulse-rate 60000"`。
   同时给残余 +0.38 MB/min 定性。不改代码，可与 ③ 并行。
3. **M5.3 仪表板**：指标卡 + 三类快照 + 最新告警；验收 20FPS 下 CPU < 15%。
   `desktop/src/renderer/views/dashboard-view.vue` 已有 103 行（M3/M4 时期产物），是扩展
   不是新建。
4. **M6 第三批页面**：`ae-view.vue`、`analysis-view.vue` 目前是 **7 行桩**。
   AE 数据通路（`ae_envelope` / `ae_hit` 帧、`AeHit` 协议类型、`ae-panel.vue`）已在 M5.1 打通。
5. **M7.2–7.5**、**M8**（旧 `main.py`/`ui/` 下线、装机包、文档收尾）。
   M8 时顺带清掉 `tests/test_controller_integration.py` 里 5 个绕过夹具、各漏一个存储线程的
   用例（`:103/112/118/125/131`）——现在不修是因为 `ui/` 整体要删。

### 8. 工作方式要求

- **中文协作**。动手前先给计划与主要权衡，尤其"探索型"问题用 2–3 句给建议 + 取舍，
  不要直接开写。
- **逐件事显式授权改动**；提交、推送、删除等动作**先问再做**（推送在本仓库尤其危险，见 §2.1）。
- 回复简洁。不要在每次动作后写总结段落；用户能看 diff。
- 遇到"证据看着够但结论可疑"时，**先把曲线/原始数据拉出来看形状，再下结论**。
  前任协作者在这里犯过 5 次判断错误并记录在 `docs/PROJECT_MEMORY.md` 第 5 节，值得先读。
- 遇到工具/探针本身改变被测系统的迹象（数字反常地好或坏），先怀疑夹具。

## === 复制结束 ===

---

### 附：给人类的一句话

这段提示词里的数字来自实测，会随代码演进过期。**每次里程碑变更后请更新
`docs/PROJECT_MEMORY.md` 与本文第 7 节的任务队列**，否则新的 AI 会话会拿着过期的
"当前状态"做判断——这个仓库已经因为过期描述（报警阈值、`trend_data` 表名、
"峰值 <100MB"）返工过多次。
