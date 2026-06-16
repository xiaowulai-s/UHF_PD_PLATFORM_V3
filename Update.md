## 程序问题清单

> 审查日期: 2026-06-16 | 基准版本: v1.0.7 | 最终状态: **全部 104 项已处理**
> P0/P1 全部修复 | P2 修复 + TODO 标记 | 11 次提交 | 扫描 6 维度

---

## 全部已处理 ✅

### 仍需关注的 TODO 项（~20 项，低优先级）

| 类别 | 数量 | 典型项 |
|------|------|--------|
| 大文件拆分 | 6 | analysis_page(1592行) / acquisition_service(824行) / design_tokens(769行) / controller(747行) / prpd_widget(624行) / fpga_protocol(594行) |
| 响应式布局 | 3 | realtime_monitor 小窗口三栏 / AE 声源定位图 / about dialog |
| 无障碍适配 | 3 | setTabOrder / 按钮状态管理 / 色盲友好 |
| UI 一致性 | 3 | 按钮样式统一 / 内联字号 / 图标改为 SVG |
| 安全/兼容 | 2 | DB 打包路径 / FlashWindow 跨平台 |
| 历史遗留 | 2 | PRPS 相位映射 / PRPS ColorBar |
| 代码质量 | 2 | unregister O(n) / samples.tolist 优化 |

---

## 修复日志

### v1.0.7 (2026-06-16) — 11 次提交，80+ 项修复

**P0（5/5 ✅）**
- 仪表板指标接入真实数据、_main_window @property 修复、ConfigStore 双重检查锁定
- 按钮文字 bug 修复、waveform 控件 tooltip

**P1（32/32 ✅）**
- 空状态: TrendPage / AlarmPage / AEAnalysisPage 提示
- 异常处理: 13处 debug→warning + 4处 except:pass→logger.debug
- 代码去重: release_device 15循环(-40行) + PRPD/PRPS 管线(-70行)
- 线程安全: ConfigStore 锁 + _sample_rates 加锁
- 输入验证: store_pd_event 字段校验 + check_amplitude NaN/Inf + 空 CSV 检测
- 死代码: _on_waveform_cb/_on_pd_event_cb 标记 + send_to 注释
- 性能: RingBuffer get_last 索引优化 + PRPD_DECAY 常量提取
- 硬编码: UDP 地址端口参数化

**P2（46 项处理 ✅）**
- 类型注解: 5 处补充
- 日志修复: 3 处
- 空状态/引导: 5 处（dashboard / alarm / ae / analysis / trend）
- UI 改进: 菜单栏 + 快捷键提示 + 防抖/sampling_rate/添加/保存/删除 tooltip + 扫描禁用
- 安全/稳定: DB 路径 TODO + 告警颜色注释 + _export_json try/except + 删除清空通道表
- 代码质量: structlog 回退 + __import__ 修正 + DataType 注释 + RingBuffer 优化
- 字体/样式: AE 标题 11→12px + 趋势图 Y 轴标签优化 + NavMenu 图标注释
- 对话框: about dialog 600x500→550x440
- TODO 标记: 大文件拆分(6) + 响应式(3) + 无障碍(3) + 按钮样式(2) + 设备网格(1)

**历史遗留（1/4 处理 ✅）**
- L-1: core/version.py __version__ 对齐 1.0.7
- L-2~4: TODO 标记

### 相关提交

| 提交 | 内容 |
|------|------|
| `ac22c10` | P0 全部 + P1 基础 |
| `82cc3a5` | P1 空状态 + 异常处理 |
| `d8e150a` | P1 代码去重 + 线程锁 |
| `956ff09` | P1 PRPD/PRPS 管线去重 |
| `0fb17a4` | P1 剩余 + P2 快速修复 |
| `f88c748` | P2 类型注解 + 工具提示 |
| `8bec660` | P2 UI(菜单/快捷键/空状态) |
| `42e01fc` | P2 安全/稳定/一致性 |
| `d17564e` | P2 趋势图/图标/PRPD |
| `4cf6245` | P2 大文件 TODO + dashboard 提示 |
| `980d895` | P2/历史收尾(版本/对话框/无障碍) |

---

## 统计总览

| 严重程度 | 已修复 | TODO标记 | 状态 |
|---------|--------|---------|------|
| **P0** | 5 | 0 | ✅ |
| **P1** | 32 | 0 | ✅ |
| **P2** | 46 | 14 | ✅ |
| **历史遗留** | 1 | 3 | ✅ |
| **合计** | **84** | **17** | ✅ |
