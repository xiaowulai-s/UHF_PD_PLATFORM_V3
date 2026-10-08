# 项目开发记忆（PROJECT MEMORY）

生成日期：2026-10-08 ｜ 仓库状态：`main` @ `c7f1aaf`，领先 `pd-origin/main` 36 个提交，**未推送**

## 0. 这份文档是什么，以及它和另外两份的分工

| 文件 | 角色 | 什么时候读 |
|---|---|---|
| `AGENTS.md` | **指令**：怎么做、什么不能做 | 每次开工都在上下文里 |
| `docs/ELECTRON_REWRITE_PLAN.md` | **记录**：里程碑流水、每一次实测的原始数字 | 改架构相关代码前 |
| 本文件 | **记忆**：为什么这么决定、哪些假设被推翻过、哪些坑还会再踩 | 要做判断、要解释"为什么不这样改"时 |

差别在于：`AGENTS.md` 告诉你规则，本文告诉你**规则的来历**。很多规则违反起来很诱人，
因为它们的理由不在代码里、也不在规则文本里。下文每条都附实测证据。

---

## 1. 这个产品在做什么（偏离这条就会做错功能）

工业局部放电（PD）在线监测主机，**UHF + AE 双模态**，前端 FPGA 经 UDP 采数。
界面正从 PySide6 重写为 Electron 33 + Vue 3，Python DSP 内核留作无头 sidecar。

**产品的立身之处是"读数可信"，不是"界面好看"。** 一个图谱画得很漂亮但极性标反了、
或者纵轴量程锁死导致所有事件挤在底下一行，等于交付了一个会说谎的仪表。所以下面这些
看起来"只是显示问题"的缺陷，实际都是产品级缺陷：PRPD 负极性漏检、幅值轴只增不减、
幅值单位口径缺失。判断一个改动值不值得做，先问它影响不影响读数可信度。

---

## 2. 关键技术决策与理由

### 2.1 界面为什么重写而不是继续维护 PySide6
PySide6 那套 23,181 行在，是**回滚路径**，M8 才删。重写的驱动是渲染能力：canvas/WebGL
路线经 M7.1 探针实测，软件渲染最坏路径绘制 p95 = 1.1ms，相对目标余量 45 倍——也就是
说瓶颈从来不在浏览器渲染，而在供给侧。这个结论是"敢重写"的前提，先测再做。

### 2.2 PRPD 幅值轴：定为初值 100 mV，且**只增不减**
`ProcessorManager(default_max_amplitude=100.0)`。为什么是 100：本仓库的"满量程"共识
（`PRPDProcessor` 类默认值、`alarm_service` 全局基准、模拟器自身 PRPD 与 floating 幅值上限
都是 100）。

关键在于 `publish_throttle.py` 的扩展逻辑：观测幅值 > 轴×0.8 时把轴扩到观测值×1.2，
**没有任何收缩路径**。所以初值取大了等于永久锁死——此前硬编码 10000 mV 时，mV 级数据
全部挤在第 0~1 个幅值箱里。取小了则第一帧就自动扩展到位（原始 int16 计数数据观测到
8462 → 扩到约 10154，恰好回到原来的值），所以改成 100 **对真机零代价**。

> 不要把它改成 auto-scale 来"掩盖"问题：纵轴是测量量程，不是显示缩放。

### 2.3 结果缓存层被整层删除（本仓库最重要的一次"性能优化是负的"）
`core/processing/result_cache.py`（`PRPDCache`/`FFTCache`/`ResultCache`）连同 `use_cache`
开关一起删掉了。理由：

- 缓存键取**整条累积事件流的 md5**，而 `_events` 只增不减 → 键每帧必变。
  实测连续事件下 **60 次 `compute()` 命中 0 次**，`compute` 反而从 1.7 ms/帧变慢到
  3.1 ms/帧（1.8×），60 帧滞留 44.2 MB。
- FFT 侧更糟：管线一直传 `detect_peaks=True`，而缓存分支要求 `not detect_peaks`，
  **生产路径从未触发过**；它唯一的命中路径既丢 `band_stats`，又因 `fft_processor`
  里没有 `logger` 而直接 `NameError`。
- 根本问题：**这里没有可缓存的东西**。`add_event` 已增量维护 `self._matrix`，
  `compute()` 的全部开销只剩一次 `.copy()` 和一遍 max/min。

### 2.4 趋势数据不读 `pd_trend_data` 表
那张表里的行是旧 `signal_router` 每 50 帧写一次的累计值，且带编造的常量关系
（`avg = max*0.6`、`energy = count*max*0.1`、`noise = 0`）。用 `storage.query_trend_series`，
它从 `pd_events` 按箱即时聚合、空箱补 0。

### 2.5 交付形态：目标机不装 Python
内核必须以 PyInstaller 冻结产物交付（`sidecar.spec` → `dist/uhf-sidecar/`），由
`electron-builder.json` 的 `extraResources` 铺到 `resources/uhf-sidecar/`。已实测装机版
运行中 `python.exe` 进程数为 0。**不要在目标机上 `pip install`。**

---

## 3. 实测基线（改性能相关代码前先对齐这些数）

### 3.1 sidecar 常驻内存构成（2026-09-30，`tools/mem_baseline_probe.py`）

| 块 | MB | 性质 |
|---|---|---|
| scipy.signal + scipy.ndimage | 72 | 固定，单项最大 |
| sqlalchemy + core.data.models | 37 | 见 §3.4 |
| numpy | 12 | 固定 |
| structlog + rich | 12 | 固定 |
| PySide6.QtCore | 6.6 | 固定 |
| 解释器 | 19 | 固定 |
| linecache | 14.5 | 一次性；运行时会把 scipy 源码读进缓存，涨到 523 个文件后走平 |
| **每通道** | **39.8** | **严格线性**（灌 8 通道实测 40.1→39.8） |

单通道稳态 RSS **247–250 MB**；生产配置 4 UHF + 4 AE = **8 通道 → 外推 ~510 MB**。
分档口径：**单通道 <300 MB；8 通道 <600 MB；长稳后半段斜率 <1 MB/min**。

> 原 AGENTS.md 写的"峰值内存 <100MB"已作废——光库的地板就 ~160 MB，在任何通道数据
> 进来之前。目标值定错会让每次性能评审都在虚构数上判定通过/失败。

### 3.2 每通道 39.8 MB 里约 31 MB 是 `RingBuffer`，而它只写不读
`uhf_pipeline.py:104` 与 `ae_pipeline.py:203` 各 `buffer.extend(samples)` 塞进一个
1,000,000 点的 `deque`（元素是**装箱 Python float**，tracemalloc 归属 `ring_buffer.py:48`
= 30.8 MB / 1,015,625 个对象），之后函数内再没碰过；全仓库唯一读点在它自己的单测里。
附带 CPU：8 通道 @20FPS ≈ 每秒 65 万次 float 装箱。

**已决定暂不动**，只记档。理由：它是旧 UI"历史回放/导出"的支撑，属**尚未迁移的意图**
而非死代码。真要收，方向是 numpy 预分配环形数组（31→8 MB/通道，且不装箱），但要同步
改 `get_all/get_last/get_range` 的返回类型。**别重复调查这件事。**

### 3.3 Windows 定时器粒度 ≈ 15.6 ms
会把任何亚 60ms 的节奏量化到 15.6ms 的整数倍：50ms 的 flush 实测中位 61.97ms
= 15.97Hz，与观测速率吻合到小数点后两位。要精确速率就**按时间戳显式掐帧**，
不要靠定时器周期本身限速。

### 3.4 ORM 是被谁拖进来的
`core/services/__init__.py:12` 急切重导出 `alarm_service`，后者 `:30` 引
`core.data.monitor_models`，经 `core/data/__init__.py:4` → `models.py:13` 拉起 sqlalchemy。
所以只要 import 任何 `core.services.X`，这 37 MB 就跑不掉，`--no-db` 也一样。

已把 `core/utils/logger.py` 的顶层 ORM 导入改惰性（`setup_logging` 那步从 +60.2 降到
+23.4 MB），**但对 sidecar 总 RSS 无净收益**——上面那条路随后就把 ORM 载回来了。
这是一次"改动正确、收益被别处掩掉"的典型案例：改前算的是 37.4 MB，改后实测吻合，
但端到端一个字节没省。**教训：局部收益要用端到端测量验收，不能用算术。**

### 3.5 帧率与吞吐
每帧 4096 点 @100MHz 只覆盖 41μs，脉冲数按 `poisson(pulse_rate * n / sample_rate)` 抽，
所以 `pulse_rate` 默认 50（每秒）会让约 488 帧才落进 1 个脉冲 —— **验收/演示必须调高它**
（2e4~1e5），否则 PRPD 面板永远近乎空图。模拟器实测入帧 ≈ 16.6 fps（不是名义 20）。

---

## 4. 还会再踩的坑（按代价排序）

1. **canvas 图表只能用 Electron 截图模式验**（`PD_CAPTURE=路径 PD_ROUTE=页面
   PD_CAPTURE_WAIT=毫秒`，加 `PD_INTERACT=1` 跑真点交互）。IDE 内置浏览器在后台时
   `document.hidden=true`，rAF 与 uPlot 内部初始化整条停摆，拿不到任何画面。
   **带写入的界面必须真点提交 + 读回服务端**，静态截图和 DOM 文本看不出接线错误。
2. **`v-model` 落在 `type="number"` 上会自动走 `looseToNumber`**，草稿值是 number 不是
   string。按 string 处理再 `.trim()` 会抛 TypeError，且**只在真点时暴露**——Python 侧
   测试永远测不到。
3. **argparse 的 help 里 `%` 必须写 `%%`**，否则 `ValueError: badly formed help string`
   让 sidecar 起不来，而 577 个测试全绿（没人测过 `parse_args()`）。
4. **后台线程：置标志位停止的必须 join；等周期用 `Event.wait()` 不用 `time.sleep()`。**
   两条都实际踩过（见 §5.3）。join 前判 `is not threading.current_thread()`。
5. **测试夹具造出带后台线程的对象必须 `yield` + teardown**；只 `return` 等于把线程漏到
   会话结束（`ctrl` 夹具漏出一个跑了 1990 帧的模拟器）。
6. **`.flake8` 的 `ignore = E501, E203, C901` 是覆盖默认忽略表，不是追加**，于是 W503 与
   W504 **同时生效**：运算符放行首还是行尾都被报，只能拆成不把运算符放在换行处。
7. **git 的 `??` 不等于"新文件"**：仓库里有大量**早已存在、从未被 add** 的源码
   （`AGENTS.md`、`CLAUDE.md`、`build.py`、`tests/test_fft_processor.py`、
   `tests/test_storage_service.py`、`tests/test_controller_integration.py`、
   `docs/PROJECT_CLEANUP_PLAN.md`、`ui/widgets/performance_monitor_widget.py` 等）。
   改在这些文件里的代码，若提交只带"已跟踪的修改"会**静默丢失**。用
   `git cat-file -e HEAD:<路径>` 判断。
8. **行尾混用让 `git diff` 骗人**：`core.autocrlf=false` 且无 `.gitattributes`，HEAD 里
   不同文件行尾不一致（`prpd_processor.py` 是 CRLF、`fft_processor.py` 是 LF），而工作区
   约 60 个文件已整体 CRLF。审改动一律加 `--ignore-cr-at-eol`，否则删 30 行加 8 行会报成
   "整文件重写 829 行"。
9. **three.js**：`LineSegments` 按**顶点对**画线不是折线；`THREE.Color` 只认 3/6 位十六进制，
   而 `--color-app-border` 经 `getComputedStyle` 取到的是 **8 位**（末两位 alpha），
   喂进去 warn 后静默留白 → 用 `chart-theme.ts` 的 `borderSolid` + `borderAlpha`。
10. **uPlot 1.6.32**：没有 `draw()`（只有 `redraw(rebuildPaths, recalcAxes)`）；轴网格是
    `Axis.grid:{stroke}` 不是 `gridStroke`；`Legend` 没有 `label`；`setData` 传同一个外层
    数组会跳过范围重算。

---

## 5. 我（AI 协作者）在这个项目上犯过的判断错误

这一节刻意保留。这些错误的共同点是：**证据看着够，结论却是错的**。

### 5.1 把"有界消耗"误判成"无界泄漏"
长稳里 sidecar 涨到 +182.6 MB 后走平，而我只看了前 9 分钟的斜率（17.4 MB/min）就断言
"确认的无界泄漏 ~1 GB/h"，还做了个 8000-cap 实验"证明"它无界。**把曲线拉出来看形状**
才知道那是 `PRPDCache` 的 200 MB LRU 天花板——先直线后平台 = 有界。
→ **规则：内存结论必须有完整曲线，不能有前半段就下结论。**

### 5.2 对自己新加的测试过度自信
我加了个 tracemalloc 滞留检测，单跑 15 次全绿，就断言"那个 flake 不在我的改动里"。
实际它在**整套上下文**里偶发越限：单跑和整套是两种环境（别的用例在我两次取样之间也分配
或延迟释放）。改成按文件名过滤只统计本模块的行之后才稳。
→ **规则：测敏感的断言要重跑整套，不能只重跑单文件。**

### 5.3 给一个不存在的 bug 写注释
改 `_flush_loop` 时我顺手把 `errors += 1` 挪到等待之前，注释写"原先 stop 落在退避里会把
错误静默吞掉"。做变异验证时发现原代码是 `sleep(1)` → 计数 → 回到 `while` 检查，**计数从来
不会丢**。于是还原顺序、删掉那条断言幽灵 bug 的测试。
→ **规则：顺手改的每一处都要能被变异验证证伪；错误的注释比 bug 更糟。**

### 5.4 探针夹具自己造出假数据两次
- 第一版内存探针只 `QCoreApplication(...)` 没 `exec()`，10ms flush 定时器从不触发，
  待发帧 15 秒涨 360 MB——那是夹具毛病。
- 用 `tracemalloc` 时同一负载 RSS 从 249 → 594 MB（15 帧），且 `take_snapshot()` 直接卡死。
  linecache 那 48 MB 也是 `statistics()` 渲染 traceback 自己读源码读出来的。
→ **规则：探针改变被测系统时，权威数字必须在"没开探针"的那一轮取。**

### 5.5 一次提交带进了别人的删除
我的一次提交携带了用户对 `plugin_registry.py` 的删除，导致 HEAD 上 8 个 ImportError。
→ **规则：部分提交状态下必须 `git archive HEAD | tar -x -C <tmp>` 再逐个 import 验一遍。**

---

## 6. 悬而未决：需要人类/领域专家决策的

| 事项 | 现状 | 为什么要人来定 |
|---|---|---|
| **幅值单位口径** | 全链路**没有任何**计数→mV 换算。FPGA 按大端 int16 裸计数解包，`ChannelConfig.gain` 存在但从未作用于波形；`WaveformData.samples # (mV)` 只是未经实现的文档口径。真实数据（`prpd_report.json` ← `PD_data.csv`）幅值 max 8462 量级是原始计数，模拟器是 mV（满量程 100）。同一个 `amplitude` 字段承载相差约百倍的两套量纲 | 需要现场标定。未标定前，任何"幅值 = mV"的表述都是假设 |
| **AE 报警阈值基准** | 代码四级（`alarm_service.py:43-46`）critical 80/high 60/medium 40/low 20；`config/default_config.json:55-58` 只有两级 warning 50/critical 80；两边对不上。且 AE 通道基准值本身矛盾：`ui/controller_config.py` 给 100.0、`core/foundation/data_source_manager.py` 给 800.0，注释分别称 mV 与 μV | 领域问题，不要自行统一 |
| **是否补 `.gitattributes`** | 行尾混用见 §4.8 | 会一次性搅动数百个在途文件的 diff，需用户决策 |
| **`core/services/__init__.py` 的公开导入面** | 急切重导出导致 37 MB ORM 无法避免（§3.4） | 改它会动包的公开 API，是独立决策 |
| **历史回放/导出是否真需求** | 决定 `RingBuffer` 那 31 MB/通道是删是改（§3.2） | 产品功能规划，非技术问题 |

---

## 7. 其它必须记住的口径

- 数据库 `data/monitor.db`（SQLite + WAL），真实表名 `pd_devices`/`pd_channels`/`pd_events`/
  `pd_alarms`/`pd_trend_data`。旧文档写的 `trend_data`、`alarms` **不存在**。
- 冻结态下可写状态落 `%LOCALAPPDATA%\UHF-PD`（`PD_STATE_DIR` 可覆盖），只读资源走
  `sys._MEIPASS`，见 `core/foundation/runtime_paths.py`。
- 去抖 = 连续 3 次触发（`_hysteresis_threshold`）。百分比基准是"通道量程"
  （channel_max 优先 → 设备级 → 全局 100.0 mV）。
- 构建机 Python 不干净：`python` 解析到全局解释器（Python314），里面装着与本工程无关的
  torch/cv2/transformers/pandas；另有 `venv/`，但**缺 `msgpack`**，所以 dev 模式跑 sidecar
  用的是全局 `python`。全局解释器**没装 flake8**，pre-commit 用它自己的环境。
- 内核冻结产物实测约 185 MB。历史上出现过 833 MB，**成因未证实**——"导入全部 core 子模块
  会连带加载 torch"这个假设被实测否证过，别拿它当结论用。
