# GUI 布局体检报告

**被测版本**：UHF_PD_PLATFORM_v3 @ `c574afc`（v0.0.1 发布版）
**体检日期**：2026-10-14
**结论先说**：**布局本身是干净的**——27 个「路由 × 视口」组合，元素重叠、对齐不一致、
响应式失效、文字截断/溢出、内容塌陷**均为 0**。但体检过程中抓到 3 个真实缺陷，
其中 1 个是**会让现场调错参数的功能 bug**。

---

## 1. 检查范围与判定口径

| 项 | 值 |
|---|---|
| 检查方式 | 真机（Electron 33 + 真实内核在线）+ 程序化测量 + 目视复核 |
| 页面 | 9 个（系统概览 / 实时监测 / 趋势分析 / 数据分析 / AE 分析 / 报警管理 / 设备管理 / 系统设置 / 系统日志） |
| 视口 | 1600×960（默认）、1366×768、**1180×720（= 窗口 `minWidth`）** |
| 主题 | 亮色全量；暗色在实时监测页复测 |
| 覆盖字符串 | `document.querySelectorAll('*')` 全域扫描，非抽样 |
| 内核状态 | 自检确认"在线数据源"文字在页（否则测到的是空态，本项必须先验） |

判定口径分四项，全部可复现（脚本与判定式见 §6）：

1. **文字截断/溢出**：叶子文字节点 `scrollWidth > clientWidth + 1` 且未设 `text-overflow: ellipsis`
2. **元素越界**：rect 左缘 `< -1.5px` 或右缘 `> innerWidth + 1.5px`，且祖先不是可滚动容器
3. **内容被裁**：`scrollHeight > clientHeight + 2` 且最近祖先不可滚动、自身 `overflow-y: hidden`
4. **塌陷**：有文字但 rect 宽或高为 0

---

## 2. 体检结果总表

| 检查项 | 判定 | 说明 |
|---|---|---|
| 元素重叠 | ✅ 0 | flex/grid 直接子两两求交，阈值 2px；排除绝对定位层与 canvas 覆盖层 |
| 对齐不一致 | ✅ 未发现 | 同角色兄弟节点内间距比对 + 目视复核 |
| 间距异常 | ⚠️ 2 处 | 见 §4-③，同一页面同式卡片 `p-4` 混用 |
| 响应式适配 | ✅ 0 | 27 组合全部：无页面级横向滚动、无左右越界 |
| 文字截断/溢出 | ✅ 0 | 含 `<table>` 长文本单元格（报警信息列最长 60+ 字符，正常换行） |
| 内容塌陷（0 尺寸却有文字） | ✅ 0 | — |
| 画布 HiDPI 适配 | ✅ 全部正确 | 7 个 canvas 的 `buffer / CSS 尺寸` 全部恰等于 `devicePixelRatio = 1.5` |
| 命中区尺寸 | ⚠️ 1 类 | 见 §4-②，复选框 13×13 px |

---

## 3. 已明确检查"无问题"的范围

以下各项逐条测过、**通过**，不是没测：

- 9 个页面在 **1180×720**（窗口物理最小尺寸）下无横向滚动、无元素越界 → 缩到最小也不破版
- 报警页 **218 行**数据全部渲染后，表格列宽与行高仍稳定，无重叠、无溢出
- 实时监测页 4 个 canvas（波形/FFT/PRPD/PRPS）在 1366 与 1180 下均正常重排
- 暗色主题下布局与亮色完全一致（同一路由复测，差异为 0）
- 趋势页 / 分析页的 uPlot 图表区随视口缩放，无越界

---

## 4. 发现的问题（按严重程度）

### ① 【高】设置页回显的阈值与报警判定用的阈值不是同一套

**位置**：`core/services/alarm_service.py`

**现象**（实测截图取证）：
- 设置页「报警阈值」卡片显示四级：critical **80.0** / high **60.0** / medium **50.0** / low **30.0** mV
- 同一时刻报警管理页的报警详情写：`局放超限 - 幅值 47.0mV (47%), 等级: 中级 / pd_over_limit (47.0 / 40.0 mV)`
- 即**实际判定阈值是 40.0 mV，界面告诉现场是 50.0 mV**

**根因**：两处回退值不一致——
```python
# get_thresholds()（给前端回显用）
percent = self._custom_thresholds or {"critical": 80.0, "high": 60.0, "medium": 50.0, "low": 30.0}

# check_amplitude()（真正做判定的地方）
level_thresholds = self._custom_thresholds or {k: v["threshold_pct"] for k, v in ALARM_LEVELS.items()}
# ALARM_LEVELS 的 medium=40、low=20
```
只有当运维显式下发过 `_custom_thresholds` 时两者才一致；默认状态下界面读数与判定逻辑**差 10 个百分点**。
叠加 `config/default_config.json` 里另有一套（warning 50 / critical 80），实际存在**三套**。

**影响**：现场按界面显示的阈值调参，会得到与预期不符的报警行为；且不可通过界面发现。

**修复建议**：把回退值收敛为单一来源——`get_thresholds()` 改为同样从 `ALARM_LEVELS` 取
（`{k: v["threshold_pct"] for k, v in ALARM_LEVELS.items()}`），并让 `config/default_config.json`
的 `alarm.amplitude_thresholds` 要么删除、要么由代码加载后覆盖同一份常量。
建议同时补一个测试：不加自定义阈值时，`get_thresholds()` 与 `check_amplitude()` 触发的等级必须一致。

---

### ② 【中】复选框命中区 13×13 px，低于无障碍最低要求

**位置**：全站 `<input type="checkbox" class="accent-[var(--color-primary)]">`
（`desktop/src/renderer/views/alarm-view.vue:206`、`:228`，以及实时监测 / 趋势页各 1 处）

**现象**：checkbox 未设尺寸，用浏览器默认值，实测 **13×13 px**。
报警页有 **219 个**（表头全选 + 每行一个），是该页最高频的交互元素。

**严重程度**：中。不影响功能与布局，但：
- 低于 WCAG 2.2 **2.5.8 Target Size (Minimum) AA** 要求的 24×24 CSS px
- 工控机触屏/触板上点击 13px 目标容易失误；报警确认是关键操作，误点代价高

**修复建议**：给一个统一样式类（如 `h-4 w-4` = 16×16），若要满足 AA 则
配 `padding` 或伪元素把命中区扩到 24×24：
```html
<input type="checkbox" class="accent-[var(--color-primary)] h-4 w-4" />
```
命中区扩展可用 `after:*` 或包一层 `p-1` 的 label。建议同时把三个页面的复选框统一到同一个类。

---

### ③ 【低】同式卡片内边距混用

**位置**：`desktop/src/renderer/views/settings-view.vue`、`analysis-view.vue`

**现象**：同一个页面里 class 几乎相同的 `<section>`，一部分带 `p-4`、一部分不带
（不带的那些靠子元素各自 `px-4` 补齐）：

| 页面 | section 数 | 实测内边距 |
|---|---|---|
| 系统设置 | 5 | `0/0/0` 与 `16/16/16` 两种 |
| 数据分析 | 4 | `0/0/0`、`12/12/12`、`8/12/12` **三种** |

**现象后果**：卡片标题与卡内首行内容的左缩进在不同卡片间不一致（8 / 12 / 16 px）。
不是错位，但并排看时显得不齐。

**严重程度**：低。纯视觉一致性问题。

**修复建议**：统一为二选一——
- (a) 所有 section 都不带 `p-4`，由子元素负责内边距（现状多数如此）；或
- (b) 所有 section 都带 `p-4`，子元素去掉各自的 `px-4`。
建议 (a)：改动面小，且标题栏本就需要通栏分隔线，带 `p-4` 反而要做反向抵消。

---

## 5. 附：本次修正的三个误判（如实记录）

初版探测器给出过 3 类错误结论，均已修正后复测，**不构成缺陷**：

1. **"可滚动页面里折叠下方的内容被判成越界"**（初版在设置页报 32/67/74 个）——
   判据写成"rect.bottom > 视口高"，没考虑祖先可滚动。已改为只查横向越界与"被不可滚动容器裁掉"。
2. **"canvas 后备存储与 CSS 尺寸不匹配"**（初版报 7 个）——
   实测 `buffer / CSS = 1.5`，恰等于 `devicePixelRatio`。这正是**正确的 HiDPI 实现**，
   不是缺陷；反而是加分项（说明没有按 1:1 画导致发虚）。
3. **"间距不一致"（初版报 50 处）**——
   原判据比较 flex/grid 的**所有**直接子，把 `<label>` 与 `<input>`、`px-2` 输入框与 `px-3` 按钮
   混在一起比。收紧为"同 tag + 同 class"后只剩 §4-③ 的 2 处。

另外初版还漏查了**左缘**越界（只查了右缘），自检时用注入 `left:-400px` 的元素发现并补齐。

---

## 7. 修复记录（2026-10-14）

三项全部已修并复测通过。

### ① 阈值不一致 —— 已修 ✅

**改法**：新增 `core/services/alarm_service.py::default_percent_thresholds()` 作为唯一来源，
`check_amplitude()` 与 `get_thresholds()` 共用；删掉 `config/default_config.json` 里
那套**无人读取**的 `alarm.amplitude_thresholds`（warning 50 / critical 80）。

**复测**（真机、内核在线）：
```
设置页四级阈值回显：critical=80.0  high=60.0  medium=40.0  low=20.0
报警页详情行       ：局放超限 - 幅值 23.2mV (23%), 等级: 低级 pd_over_limit · 23.2 / 阈值 20.0 mV
```

**回归测试**：新增 `tests/test_alarm_threshold_consistency.py`（10 个用例）。
关键点是**边界值必须取自界面回显**而不是自己用常量算——否则测的只是"常量与常量自洽"，
抓不到"回显与判定脱节"。已做变异验证：把修复退回原状，`medium`/`low` 两档精确报红
（high/critical 数值本就相同，不报是正确行为）。

**顺带修了一个藏得更深的问题**：既有测试
`tests/test_sidecar_protocol.py::test_alarm_service_threshold_percent_to_absolute`
原本断言 `low == 75.0`（250 × 30%）——它把 bug 钉住了。这就是为什么全套 557 passed
发现不了这个缺陷。已改为 50.0 并在注释里写明来历。

### ② 复选框命中区 —— 已修 ✅

**改法**：
- `desktop/src/renderer/style.css` 加全局 `input[type="checkbox"] { width:16px; height:16px; cursor:pointer }`
  （6 处全覆盖）
- 报警页 219 个行内复选框 + 表头全选，外面包 `<label class="flex h-6 w-6 cursor-pointer items-center justify-center">`
  把命中区扩到 24×24（WCAG 2.5.8 AA）

**复测**（报警页 294 个复选框）：
```
input: 16x16    labelHit: 24x24    wrapped: true
```
`fft-chart` / `trend-view` / `device-view` 那三处本就在 `<label>` 里（命中区含文字），全局 16×16 已覆盖。

### ③ 同式卡片内边距 —— 已修 ✅

**改法**：系统设置页「固件与更新」卡原本把 `p-4` 放在 `<section>` 上且**没有标题栏分隔线**，
与该页其余 4 张卡的模式（section 无内边距 + 各自子元素 `p-4` + 标题带 `border-b`）不一致。
已改为既有模式；数据分析页的散点卡同样把 `p-3` 下沉到子包装。

**复测**：`settings 同角色间距不一致：无（已一致）`。

**但要说清一处误判**：分析页仍有 2 个 section 的自身 padding 是 `8px/12px`（筛选工具条与
"本页未实现项"说明框）。它们是**无标题栏的单块面板**，padding 放 section 上是恰当模式，
与带标题栏的卡片本就该不同。实测两者首行文字左缩进均为 **13px**（12px 内边距 + 1px 边框），
与全页所有卡片一致——**视觉上没有不齐**。所以这一项判为"已达一致"，不再改。

### 质量门禁

- 内核全套：**567 passed**（原 557 + 新增 10）
- 前端 `npm run typecheck`：通过
- 变异验证：阈值修复退回后测试精确报红，已执行

---

## 6. 复现方式

探测器脚本与自检测证都在，重跑需满足三个前提（缺一个结论就不成立）：

1. **内核必须在线**。给应用传绝对路径解释器，否则主进程 spawn 的 `python` 会解析到坏的解释器、
   内核起不来，界面跑在离线模式——那时测到的是**空态**布局，一切"0 问题"都不算数：
   ```
   PD_SIDECAR_PYTHON=C:\Users\wo\AppData\Local\Programs\Python\Python314\python.exe
   ```
2. **启动参数**（本机 GPU/渲染沙箱起不来，两个开关仅用于本机验收，不得进装机版）：
   ```
   env -u ELECTRON_RUN_AS_NODE \
     desktop/node_modules/electron/dist/electron.exe . \
     --disable-gpu-sandbox --no-sandbox --remote-debugging-port=<port>
   ```
3. **截图不要走 CDP**：本应用的 `Page.captureScreenshot` 稳定超时（单次就吃满 60s），
   要图请用应用自己的 `PD_CAPTURE` 模式，每个路由单独起一次。

### 判定式（页面侧 JS）

| 检查 | 判定 |
|---|---|
| 文字溢出 | `el.scrollWidth - el.clientWidth > 1` 且 `textOverflow !== 'ellipsis'` 且 `overflowX` 不是 auto/scroll |
| 越界 | `rect.right > innerWidth + 1.5` 或 `rect.left < -1.5`，且向上找不到 `overflow-x: auto/scroll` 且 `scrollWidth > clientWidth` 的祖先 |
| 内容被裁 | `scrollHeight > clientHeight + 2`、自身 `overflow-y: hidden`、最近祖先不可滚动 |
| 塌陷 | `children.length === 0`、有文字、rect 宽或高为 0 |
| 重叠 | flex/grid 容器内 `position: static` 的直接子，交集在两个轴上都 > 2px |
| HiDPI | `canvas.width / canvas.getBoundingClientRect().width` 应 ≈ `devicePixelRatio` |
| 命中区 | 可交互元素 rect 宽 < 16px 或高 < 14px 即记录（WCAG 2.5.8 的线是 24×24） |
| 间距 | flex/grid 容器内**同 tag + 同 class** 的兄弟，`paddingTop/Left/Right` 出现多个不同值 |

### 自检（证明探测器不是静默返回空）

运行后注入 3 个必然有问题的元素，确认全部检出：
```
textOverflow : [{text: "这段文字…", axis: "x", over: 137, cw: 50, sw: 187}]
OOB          : [{text: "left-oob",  side: "left",  over: 399},
                {text: "right-oob", side: "right", over: 1415}]
```
同时打印 `document.querySelectorAll('*').length`（实时监测页 240、文字节点 121、flex/grid 容器 28）
与内核在线状态文字，确认测的不是空页。
