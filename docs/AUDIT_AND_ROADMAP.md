# 项目检查清单与开发路线图

**审计对象**：UHF_PD_PLATFORM_v3 @ `663cf72`（v0.0.1）
**审计日期**：2026-10-13
**审计方式**：静态取证 + 契约差集 + 实跑测试与覆盖率 + 装机版运行验收
**结论口径**：每项都给出「检查标准 / 验证方法 / 判定结果」三列；判定只有
✅ 通过 / ⚠️ 部分（可用但有缺口）/ ❌ 未通过 / ➖ 不适用 四种。

## 1. 记分卡

| 维度 | 结论 | 一句话 |
|---|---|---|
| 架构分层 | ✅ | 前后端双进程、契约单一来源（`frames.py` ↔ `protocol.ts`），分层清晰 |
| 核心分析功能 | ✅ | 波形 / FFT / PRPD 四模式 / PRPS 双模式 / AE 全部可演示可验收 |
| 持久化与历史 | ⚠️ | 落库本身健全（557 passed），但历史事件缺 phase/polarity，历史 PRPD 散点做不了 |
| 报警闭环 | ❌ | `alarm_cleared` 有 4 处生产发布者，前端未订阅，报警不自动消失 |
| TDOA 声源定位 | ❌ | 算法已实现且 74.4% 覆盖，但生产调用点为 0，链路断开 |
| 契约一致性 | ⚠️ | 31 个 RPC 中 22 个前端可达、9 个悬空；4 个 topic 前端未订阅 |
| 配置体系 | ⚠️ | 5 个偏好"存而不用"；AE 阈值与单通道量程后端有、UI 无 |
| 质量门禁 | ❌ | 覆盖率门禁三处矛盾（实际生效 10%，文档写 80%）；前端零自动化测试 |
| 打包交付 | ✅ | 装机包已出、已发布 Release、进程树 `python.exe` 为 0 |

**总体判断**：工程骨架与可视化分析能力已达可用水平；**主要缺口集中在"链路最后一公里"**——
后端写好了发布者、前端没接；算法写好了、生产没调。这类问题单看测试全绿发现不了。

## 2. 功能完整性检查清单

### 2.1 系统概览（仪表板）

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 在线设备数、局放总数、指标卡数值与内核帧一致 | `stats` 帧每 2s 推送，前端 5 张指标卡取自该帧 | ✅ |
| 三类快照（最新波形/PRPD/事件） | `snapshot-mini.vue` 离屏图渲染 | ✅ |
| 最新告警列表 | 取 `alarm` 帧 | ✅ |
| 内核运行时可观测 | P4b 增加 CPU% / RSS / 累计 CPU 时间面板 | ✅ |
| 跨进程边界吞吐与丢帧分类 | `coalesced`/`throttled`/`dropped` 三项分开记账 | ✅ |

### 2.2 实时监测

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 四视图（波形/FFT/PRPD/PRPS）可切换 | 路由 `/monitor`，模式为显式 UI 状态 | ✅ |
| AE 模式下波形面板显示 2 MHz 包络 | 内核 AE 管线把包络作为 `waveform` 重发 | ✅ |
| PRPD 四种显示模式 | 散点/热力图/密度图/三维（`prpd-3d.vue`） | ✅ |
| 热力图与密度图切换会通知内核换算法 | 切到 density 会发 `config.set_prpd_mode`，并用帧的 `meta.mode` 回校 | ✅ |
| 三维模式不发 RPC | 纯前端渲染同一份 360×256 计数矩阵 | ✅ |
| 响应 20 FPS 且 CPU < 15% | M4 已验收；本次未复跑（见 §7-P3） | ➖ 未复核 |

### 2.3 趋势分析

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 1h/24h/7d/30d 多时间维度 | `storage.query_trend_series` 按等宽时间箱现算 | ✅ |
| 设备候选来自真实出现过的 id | 用 `storage.device_ids`，不用 `device.list`/`stats.sources` | ✅ |
| 未开 `--with-storage` 时优雅降级 | RPC 未注册时退回 `device.list` 并给出提示，不算错误 | ✅ |

### 2.4 数据分析

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 实验数据导入 + 放电类型识别 | `data.import_file` → `classify_by_cycles` 逐周期投票 | ✅ |
| 三格式报告导出 | `report.formats` 自报能力，`report.export` 生成字节，主进程原生对话框落盘 | ✅ |
| Word 报告内嵌图谱 | 从散点容器 canvas 截 PNG 随字节回传 | ✅ |
| CSV 导出带 BOM | 不加 BOM 则 Excel 打开中文列头乱码 | ✅ |
| 统计口径为整份样本 | 不受页面筛选影响（已注明） | ✅ |
| 实时事件的类型标签 | 仅 PRPD 级徽标，`pd_event_detected` 不带类型 | ❌ 见 §5-① |

### 2.5 AE 分析

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 本次会话 / 数据库历史切换 | 顶部切换 + 设备/通道/时窗筛选 | ✅ |
| 特征散点（三组维度对） | `ae-scatter.vue` | ✅ |
| 命中记录表 + CSV 导出 | 真点导出 16 列逐行核对过 | ✅ |
| 跨重启持久化 | `ae_hits` 表 + 三索引，两段换进程重启后仍查回 | ✅ |
| **TDOA 声源定位** | `AELocalizer` 74.4% 覆盖但**生产调用点 0**；`gdop-map.vue` 只算几何 | ❌ 见 §5-② |
| 3D 定位视图 | 从未实现（内核无定位结果通路） | ❌ 见 §5-② |

### 2.6 报警管理

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 报警列表 + 按等级/确认态筛选 | `alarm-view.vue` | ✅ |
| 报警确认 | `alarm.acknowledge`，支持按 id / 按设备 | ✅ |
| 三级报警体系与防抖 | `alarm_service.py` 连续 3 次触发确认 | ✅ |
| 报警持久化 | `pd_alarms` 表 | ✅ |
| **报警自动消除后界面更新** | `alarm_cleared` 在 `alarm_service.py:342/350`、`ae_pipeline.py:96/143` 有 4 处发布者，前端未订阅该 topic | ❌ 见 §5-③ |
| 报警"清除/关闭"人工动作 | 只有确认，无清除 | ❌ 见 §5-③ |

### 2.7 设备管理

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 设备增删查 | `device.list/save/delete` | ✅ |
| 运行时来源面板（启停/移除） | `source.list/start/stop/remove` | ✅ |
| 通道耦合类型配置 | `config.set_channel_coupling` + `device.save` | ✅ |
| 单通道量程上限配置 | 后端 `config.set_channel_max_amplitude` 有实现，UI 无入口 | ❌ 见 §5-④ |
| AE 报警阈值配置 | 后端 `config.set_ae_thresholds` 有实现，UI 无入口 | ❌ 见 §5-④ |
| 文案引用的 RPC 是否存在 | ~~空态文案写 `acquisition.start_simulator`，后端无此方法~~ **本条为误判，已撤销** | ✅ |

### 2.8 系统设置

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 暗色/亮色主题切换并持久化 | `prefs.theme` + `[data-theme]` 覆盖底层令牌 | ✅ |
| PD 幅值阈值下发 | `config.set_amplitude_threshold` + 回读 | ✅ |
| 主题速率（每 topic Hz） | `system.set_topic_rates` | ✅ |
| 日期格式生效 | `prefs.ts:formatStamp` 按所选格式渲染 | ✅ |
| 传感器阵列配置并收敛 | `sanitizeSensors()` 收敛 localStorage 不可信输入 | ✅ |
| 5 个偏好"存而不用" | `systemName`/`language`/`density`/`notifyFrequency`/`timezone` 无消费点 | ❌ 见 §5-⑤ |
| 放电次数阈值 | UI 已禁用并注明"内核暂无写接口" | ➖ 内核确无此 RPC，注释属实 |

### 2.9 系统日志

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| `log` 帧实时展示 + 按级别过滤 | `log-view.vue` | ✅ |
| 日志导出 CSV | 走主进程保存桥 | ✅ |

### 2.10 渲染探针（诊断页）

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 独立探针页可量化渲染帧率 | `/probe`，`runRenderProbe`，不受侧栏状态干扰 | ✅ |
| 现场（软件渲染）复跑 | 目标工控机未复跑 | ➖ 见 §7-P3 |

## 3. 业务流程合理性检查清单

### 3.1 采集 → 处理 → 显示

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 每个 topic 有明确 QoS 归属 | lossy = waveform/fft/prpd/prps/ae_envelope/ae_hit/stats/log；reliable = alarm/alarm_cleared/device_status/acquisition/source | ✅ |
| 无损通道不限流、有损通道可合并 | `coalesced`/`throttled`/`dropped` 三账分开 | ✅ |
| 推流节流可显式配置 | `system.set_topic_rates`，范围 (0,200]，`FLUSH_INTERVAL_MS=10` | ✅ |
| 背压丢弃不静默 | 前端可见 dropped 计数 | ✅ |

### 3.2 事件 → 持久化 → 查询

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 持久化需显式开启且订阅到位 | `--with-storage` 起刷写线程 **且** `PersistenceSubscriber` 订阅 `pd_event_detected`，两者缺一不可 | ✅ 已在注释与文档中写明 |
| 两套队列互不挤压 | `_ae_queue` 独立于 `pd_events`；AE 队列刻意不带指数退避（命中 40+/秒，退避会把队列反复回填放大内存） | ✅ 取舍合理 |
| 库里表名与文档一致 | 真表名 `pd_devices/pd_channels/pd_events/pd_alarms/pd_trend_data` | ✅ |
| **入库字段完整性** | `pd_event_detected` 只带 position 与幅值 → 库里 `phase`/`polarity` 恒 NULL | ❌ 见 §5-⑦ |
| 设备 id 两套命名 | 旧 UHF 事件落 `SIM-001`，现改为与数据源 id 同名；既有库两种都有，取 `storage.device_ids` 兜底 | ⚠️ 历史库需清洗脚本 |

### 3.3 报警 → 确认 → 消除

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 触发有防抖 | 连续 3 次（`_hysteresis_threshold`） | ✅ |
| 百分比基准明确 | 通道量程：通道级 `channel_max` > 设备级 > 全局 100.0 | ⚠️ 口径清楚但与配置不一致 |
| **阈值三处不一致** | 代码 4 级 80/60/40/20；`config/default_config.json` 2 级 warning 50/critical 80；旧文档 30/50 | ❌ 见 §5-⑧ |
| **AE 通道基准矛盾** | `data_source_manager.py:250` 写死 `set_channel_max_amplitude(source_id, 1, 800.0)`；已归档 `ui` 侧曾给 100.0，注释分别称 μV 与 mV | ❌ 见 §5-⑧ |
| 消除事件闭环 | 见 §2.6，前端未订阅 | ❌ |

### 3.4 导入 → 分析 → 导出

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 导入后可按类型分布呈现 | `data.import_file` | ✅ |
| 导入是样本而非全量 | 默认 1000、上限 5000，UI 已标 truncated | ✅ |
| 导出能力由产物自报 | 内核 `report.formats` 自报 json/excel/word，前端据此禁用不可用按钮（不给必失败的按钮） | ✅ |
| 路径由原生对话框决定 | 渲染进程只交字节，不能指定路径 | ✅ 安全 |

### 3.5 配置 → 生效

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 内存态与持久态可分辨 | `device.list` 的 coupling_type 来自库，内核内存态是运行时值，过 `device.save` 才同步 | ⚠️ 已在注释中说明，但用户侧仍可能困惑 |
| 配置改动可回读 | `config.get_thresholds` 回读 | ✅ |
| 偏好设置即时生效并持久化 | `prefs` reactive + localStorage | ✅ |

### 3.6 RPC 异常可用性（条件注册）

| 检查标准 | 验证方法 | 判定 |
|---|---|---|
| 依赖缺失时 RPC 不注册而非抛错 | `write_rpc.runtime_handlers()` / `storage_rpc.available` 按构造情况返回 | ✅ 设计合理 |
| 前端对"未注册"有兜底 | `trend-view.vue` 退回 `device.list`；`analysis-view.vue` 提示需 `--with-storage` | ✅ |
| 未注册 RPC 是否有统一提示 | 仅各页自行处理，无统一"功能不可用"提示层 | ⚠️ |

## 4. 契约接线检查清单

### 4.1 RPC 差集（后端 31 个）

| RPC | 可达性 | 说明 |
|---|---|---|
| `alarm.acknowledge` `config.get_thresholds` `config.set_amplitude_threshold` `config.set_channel_coupling` `config.set_prpd_mode` `data.import_file` `device.delete` `device.list` `device.save` `report.export` `report.formats` `source.list` `source.remove` `source.start` `source.stop` `storage.device_ids` `storage.query_ae_hits` `storage.query_alarms` `storage.query_events` `storage.query_trend_series` `system.diagnostics` `system.set_topic_rates` | ✅ 22 个可达 | — |
| `config.set_ae_thresholds` | ❌ 悬空 | 后端 `write_rpc.py:81` 已实现，UI 无入口 |
| `config.set_channel_max_amplitude` | ❌ 悬空 | 后端 `write_rpc.py:86` 已实现，UI 无入口 |
| `source.set_active` | ❌ 悬空 | 后端 `source_rpc.py:51` 已实现，UI 无入口 |
| `device.get` | ❌ 无消费者 | 后端 `storage_rpc.py:68`，可补 UI 或删除 |
| `storage.stats` | ❌ 无消费者 | 后端 `storage_rpc.py:78`，可补 UI 或删除 |
| `config.get_prpd_mode` | ❌ 仅注释提及 | 前端用帧内 `meta.mode` 回校，故不需要 |
| `system.ping` `system.topics` | ❌ 内部用途 | 探针；前端从 hello 帧拿 topic |
| `storage.query_trends` | ➖ 刻意不用 | `trend-view.vue` 有注释说明：读 `pd_trend_data` 旧口径，改用 `query_trend_series` 现算 |

**判定**：无"前端在调而后端没有"的悬空调用（这类错误会静默失败）。9 个后端悬空中，
**3 个是"功能已写好只差 UI"**（`set_ae_thresholds` / `set_channel_max_amplitude` / `source.set_active`），
2 个是无消费者（可删），其余有正当理由。

### 4.2 topic 订阅差集

| 前端已订阅 | 未订阅 |
|---|---|
| `waveform` `fft` `prpd` `prps` `ae_envelope` `ae_hit` `stats` `log` `alarm`（9 个） | `alarm_cleared` `device_status` `acquisition` `source`（4 个） |

**判定**：`alarm_cleared` 未订阅是**真缺陷**（§5-③）。其余三个中 `source` 与 `acquisition` 的运行态
改由 `stats` 帧承载（`device-view` 注释已说明该取舍），`device_status` 目前无消费者，需确认是否要展示设备掉线。

## 5. 遗漏与未实现功能清单（按根因归类）

### 分类 A：链路最后一公里（后端已具备，前端/生产未接）

| 编号 | 缺项 | 现状证据 | 影响 |
|---|---|---|---|
| ① | 实时事件不带放电类型 | `discharge_type_service` 只发 `pd_type_classified`（PRPD 级）；`pd_event_detected` 不带分类结果 | 历史事件无法按类型检索，类型徽标只对"当前这一帧 PRPD"有效 |
| ② | TDOA 定位未接入生产 | `core/processing/ae_localizer.py` 的 `AELocalizer` 覆盖 74.4%、有测试，但 `grep` 生产代码**零调用**；前端 `gdop-map.vue` 仅 `computeGdop(sensors)` 几何计算 | AE 声源定位功能等于没有，只画了精度地形图 |
| ③ | 报警消除不闭环 | `alarm_cleared` 在 `alarm_service.py:342,350`、`ae_pipeline.py:96,143` 共 4 处 `publish_alarm_cleared`；前端未订阅该 topic | 报警自动恢复后界面不刷新，须手动重查；无人工"清除"动作 |
| ④ | 两项配置后端有、UI 无 | `config.set_ae_thresholds`、`config.set_channel_max_amplitude` 已实现 | AE 报警阈值与单通道量程只能改代码，现场不可调 |
| ⑤ | `source.set_active` 无入口 | `source_rpc.py:51` | 多源场景无法切换活动源，只能启/停 |

### 分类 B：数据字段缺口（要动内核）

| 编号 | 缺项 | 现状证据 | 影响 |
|---|---|---|---|
| ⑥ | 历史事件缺 phase / polarity | `uhf_pipeline` 的 `pd_event_detected` 只带 position 与幅值，`PersistenceSubscriber` 照抄入库 | 库里 `phase`/`polarity` 恒 NULL，历史 PRPD 散点与极性统计做不了 |
| ⑦ | 报警阈值口径三处不一致 | 代码 `alarm_service.py:40-46` 四级 80/60/40/20；`config/default_config.json` 两级 warning 50/critical 80；旧文档 30/50 | 现场改了 config 不改代码行为，极易误判 |
| ⑧ | AE 通道幅值基准矛盾 | `data_source_manager.py:250` 写死 800.0（μV），已归档 `ui` 侧曾给 100.0（mV），相差 8 倍 | AE 报警判定基准不可解释，需领域确认后才能统一 |

### 分类 C：领域口径未定（缺的不是工程量，是定义）

| 编号 | 缺项 | 卡点 |
|---|---|---|
| ⑨ | 幅值单位（计数 ↔ mV） | 全链路无换算，`WaveformData.samples #(mV)` 只是未经实现的文档口径；模拟器 mV、真实数据是原始 int16，相差约百倍 |
| ⑩ | 等效放电量（nC） | 无计数 → 物理量换算，需要现场标定 |
| ⑪ | 最大重复率 | 口径未定义（按时间窗？按事件间隔？） |
| ⑫ | 3D 定位视图 | 依赖 ②；内核需先有定位结果通路 |

### 分类 D：质量基建缺口

| 编号 | 缺项 | 现状证据 |
|---|---|---|
| ⑬ | 覆盖率门禁三处矛盾 | `pytest.ini:23` `--cov-fail-under=10`（生效）、`pyproject.toml:109` `fail_under = 80`（死配置）；实测 66.5%～69.2%，两次跑差 2.7pp，测量非确定 |
| ⑭ | 前端零自动化测试 | `package.json` 无 vitest/jest、无 test script；前端验收全靠 `tools/` 下一次性 CDP 脚本，改回归产全靠人肉 |
| ⑮ | 低覆盖核⼼模块 | `hardware_manager` 0%、`hardware_source` 0%、`sensor_types` 0%、`config_store` 11%、`udp_driver` 17%、`device_repository` 23.9% |
| ⑯ | 无 `.gitattributes` | CRLF/LF 混用的根因；本轮清理时 7 个文件出现"只差行尾"的伪修改只能跳过不提交 |
| ⑰ | 关键路径未接 CI 门禁 | 上面 ⑬⑭ 都未在 CI 生效；`pytest` 带 `--cov=tests`（把测试代码也算进覆盖率，口径可疑） |

### 分类 E：死代码与失效文案（低风险但影响可信度）

| 编号 | 缺项 | 现状证据 |
|---|---|---|
| ⑱ | `under-construction.vue` 无人引用 | 全前端无 import |
| ⑲ | ~~文案引用不存在的 RPC~~ **误判，已撤销** | 当时只 `grep core/` 漏了 `sidecar.py`；`acquisition.start_simulator` 由 `sidecar.py:170` 注册，文案是准确的 |
| ⑳ | 自述清单过期 | `analysis-view.vue` 的"本页未实现的项"第 5 条称"导出报表没有导出 RPC"，但 `report.export` 已交付 JSON/Excel/Word |
| ㉑ | 5 个偏好"存而不用" | `systemName` `language` `density` `notifyFrequency` `timezone` 无消费点（`formatStamp` 用本地时间，不读所选时区） |

## 6. 下一步开发路线图

优先级口径：**P0 = 会给出错误结论或错误体验，不修则不可信**；
**P1 = 功能缺口，影响可用范围**；**P2 = 质量基建，决定后续交付速度**；
**P3 = 依赖外部输入（现场/领域），需先拿口径**。

### P0 阻断性（建议 2 周内，约 8 人日）——✅ 2026-10-15 全部完成

| # | 任务 | 对应缺项 | 预估 | 状态与验证 |
|---|---|---|---|---|
| P0-1 | 报警闭环：前端订阅 `alarm_cleared` | ③ | 1.5 d | ✅ `cca63a6`。`CtrlChannel` 本来就把全部可靠 topic 下发了（它不发 `sub`），帧一直收得到，只是没人处理。加 `clearedAlarms` + 「已消除」筛选/标签 + 概览页取未消除的最新告警。**语义必须是单调的**（某行已消除 ⟺ 消除时刻晚于该行时刻）：第一版写成"收到新 alarm 就删键"，在 AE 秒级"触发→恢复"循环下会把已消除的历史行也抹掉，实测 19 行→0 行抖动；改单调后稳定。端到端验证：筛选后 10 行全为已消除、无混入 |
| P0-2 | 统一覆盖率门禁 | ⑬⑰ | 0.5 d | ✅ `d67841a`。门禁 10 → 60，去掉 `--cov=tests`；删掉 `pyproject.toml` 里被 `.coveragerc` 完全遮蔽的 `[tool.coverage.*]` 两段。两向验证：抬到 99% 立刻 `FAIL`，回到 60% 通过（实测 66.64%） |
| P0-3 | 清理失效文案与死代码 | ⑱⑲⑳ | 0.5 d | ✅ `6c1652a` + `d67841a`。删 `under-construction.vue`；删 `analysis-view` 里已过期的"导出报表无 RPC"条目。**并更正一条误判**：`acquisition.start_simulator` 由 `sidecar.py:170` 注册，体检时只 grep 了 `core/` 才误报 |
| P0-4 | 阈值口径裁决并统一 | ⑦⑧ | 1 d | 🟡 部分完成（`248d2c8`）。回显与判定已统一到 `default_percent_thresholds()`，死配置已删。**仍待领域确认**：AE 通道基准 800.0 vs 100.0、以及"是否要 4 级还是 config 的 2 级" |
| P0-5 | 死设置项处置 | ㉑ | 2 d | ✅ `bd163c4`。`systemName` → 侧栏标题；`timezone` → `formatStamp` 走 `Intl.DateTimeFormat`（实测 Asia/Shanghai 15:23 → America/New_York 03:23，正好 12 小时差）；`language`/`density`/`notifyFrequency` 三项连同控件删除。验证：改 localStorage 后侧栏标题与时间同步变化 |
| P0-6 | 补 `.gitattributes` + 全量归一化 | ⑯ | 1 d | ✅ `5044cc9` + `4a40c30`。243 个已跟踪文件现在**无任何文本含 CRLF 或 NUL**，12 个二进制原样保留。安全检查三项全过（无二进制被改、`--ignore-all-space` 为空） |

**P0 实际投入约 6 人日。** P0-4 剩余部分依赖领域口径，转入 P3。

#### P0-6 收尾时额外发现并修复的一处数据损坏

`.workbuddy/memory/2026-03-23.md` 是全仓唯一没被归一化的"文本"文件——它含 23 个
NUL 字节，git 据此判它是二进制，`text=auto` 就不做行尾转换（`--renormalize` 对它是
no-op，不是配置漏了）。

根因是**混合编码**：文件主体是 UTF-8，但末尾 58 字节是原样写进 UTF-8 文件的
UTF-16LE 字节。按 UTF-16LE 解码正是 `修复完成时间: 2026-03-23 13:45:04`——
不是乱码丢失而是编码混写，所以原文可无损还原（修复脚本带 assert 校验才写入）。
修复后 3328 → 3223 字节、NUL 23 → 0、CRLF 88 → 0，文件变回可 diff 的纯文本；
原始字节仍留在 git 历史里。


### P1 功能缺口（建议 1–1.5 月，约 22 人日）

| # | 任务 | 对应缺项 | 预估 | 预期目标 |
|---|---|---|---|---|
| P1-1 | 设置页补 AE 报警阈值（`signal_loss_frames`/`noise_threshold_mv`/`noise_clear_mv`/`noise_frame_count`）与单通道量程，后端 RPC 已就绪 | ④ | 2 d | 现场可调 AE 参数，无需改代码 |
| P1-2 | `source.set_active` 接入运行时来源面板 | ④⑤ | 0.5 d | 多源可切换活动源 |
| P1-3 | 历史事件补 phase/polarity：`uhf_pipeline` 按 position 换算相位、补极性字段，`PersistenceSubscriber` 落库；分析页解锁历史 PRPD 散点 | ⑥ | 4 d | 历史 PRPD 散点与极性统计可用 |
| P1-4 | 放电类型落库：`pd_event_detected` 携带分类结果（或在 `DischargeTypeService` 侧同帧写库） | ① | 3 d | 历史事件可按类型检索 |
| P1-5 | TDOA 定位接入生产：选定挂点（AE hit 帧或 `ae_hit_detected`）调 `AELocalizer`，结果经 RPC/帧下发，前端 AE 页显示声源坐标与不确定度 | ②⑫ | 6 d | AE 从"只画地形图"变成"真定位" |
| P1-6 | `device.get` / `storage.stats` 处置：补设备详情面板 / 存储统计卡，或从契约删除 | §4.1 | 2 d | RPC 无悬空 |
| P1-7 | 补低覆盖模块测试：优先 `udp_driver`、`device_repository`、`config_store`、`hardware_manager`/`hardware_source`/`sensor_types`（后三个可评估改删除） | ⑮ | 4 d | 覆盖率达 P0-2 定的目标 |

**P1 合计约 22 人日。P1-5 是关键项，它把 AE 的核心卖点补上。**

### P2 质量基建（可与 P1 并行，约 10 人日）

| # | 任务 | 对应缺项 | 预估 | 预期目标 |
|---|---|---|---|---|
| P2-1 | 引入 vitest + @vue/test-utils，给 `lib/`（prefs/colormap/ae-stats/gdop/sidecar-client）写单测 | ⑭ | 3 d | 纯逻辑改动有回归保护 |
| P2-2 | 把 P0–P5 的一日性 CDP 验收脚本固化为可复跑的 `npm run test:e2e` | ⑭ | 3 d | 界面改动不再"改完不敢发" |
| P2-3 | CI 补前端 job：`typecheck` + `build` + `test:e2e` | ⑰ | 1 d | 前后端同门禁 |
| P2-4 | 统一 RPC 未注册的前端兜底层（"此功能需 --with-storage/未启用"） | §3.6 | 1.5 d | 降级提示一致 |
| P2-5 | 建立覆盖率非确定性的排查（两次跑差 2.7pp，定位不稳定来源并固定） | ⑬ | 1.5 d | 覆盖率数字可信 |

**P2 合计约 10 人日。**

### P3 领域口径与外场验证（依赖外部输入，工作量另计）

| # | 任务 | 对应缺项 | 前置条件 |
|---|---|---|---|
| P3-1 | 幅值单位换算与现场标定 | ⑨ | 需现场标定数据 / 硬件手册 |
| P3-2 | 等效放电量（nC）口径与实现 | ⑩ | 需 P3-1 |
| P3-3 | 最大重复率定义与实现 | ⑪ | 需产品/现场定义 |
| P3-4 | M7.1 渲染探针现场复跑（软件渲染） | §2.10 | 需目标工控机 |
| P3-5 | M7.2 24h 长稳（用户已决定跳过，如需恢复现场可信度再做） | — | 需连续 24h |

### 建议推进顺序

**第 1 周**：P0-1 → P0-2 → P0-3 → P0-4（口径评审并行）→ P0-5 → P0-6。
**第 2–4 周**：P1-1 → P1-2 → P1-3 → P1-4 → P1-6 → P1-7，P2-1 ~ P2-5 全程并行。
**第 5–6 周**：P1-5（TDOA 定位，最复杂，放最后集中攻）。
**P3** 一旦拿到口径即插入；M7.1 建议在 P1-5 之后、下一次出包之前跑。

**两条硬约束写进路线图**：① 每完成一项都要跑「变异验证」——把新加的断言对应的行为注掉，确认测试确实变红；
② 装机版验收必须确认进程树里 `python.exe` 为 0、内核取自 `resources/uhf-sidecar/`。

## 7. 附录：证据与复现方式

| 结论 | 复现方式 |
|---|---|
| RPC 差集 | `grep -rhoE '"[a-z_]+\.[a-z_]+"' core/protocol/*.py \| sort -u`，再对每个名字在 `desktop/src` 全文检索 |
| topic 差集 | `sidecar-client.ts` 的 `StreamChannel(topics)` 入参列表 vs `core/protocol/frames.py` 的 topic 定义 |
| `AELocalizer` 无生产调用 | `grep -rn "AELocalizer\|ae_localizer" --include=*.py core/ sidecar.py`，仅剩 `processing/__init__.py` 的再导出；唯一使用者在 `tests/test_ae_e2e_integration.py` |
| `alarm_cleared` 未订阅 | `grep -rn "alarm_cleared" desktop/src` 无结果；`grep -rn "publish_alarm_cleared" core/` 有 4 处 |
| 覆盖率与门禁 | `pytest -q --cov=core --cov-report=term`；`grep -rn fail_under pytest.ini pyproject.toml` |
| 装机版内核取自冻结产物 | 启动后进程树扫描 `uhf-sidecar.exe` 在、`python.exe` 计数为 0 |
| 死代码/死设置 | 对每个组件/字段做 import 检索；`grep -rn "prefs\\.<字段>"` 排除 `lib/prefs.ts` 与设置页自身 |
| 前端无测试运行器 | `desktop/package.json` 的 `scripts` 无 test 类命令，`devDependencies` 无 vitest/jest |

## 8. 本清单的边界

- 本文**不重新验证** 20 FPS、CPU < 15%、启动时间、多通道线性度等性能指标——这些属 M7，
  上次实测结论见 `docs/ELECTRON_REWRITE_PLAN.md`，本次只在 §2.10 / §7-P3 标注"未复核"。
- 报警页"清除"动作是否有业务必要**未与现场确认**：本文按"报警自动消除后界面应一致"这条通用经验判 ❌，
  若现场流程是"报警只进不出、由运维定期归档"，则 P0-1 应降级为"仅订阅 `alarm_cleared` 做视觉区分"。
- 工作量估算基于对 `core/` 与 `desktop/` 的静态阅读，未含现场联调与回归测试时间。
