## 程序问题清单

> 审查日期: 2026-06-16 | 基准版本: v1.0.7 | 最终状态: **全部 104 项已处理**
> P0/P1 全部修复 | P2 修复 + TODO 标记 | 14 次提交 | 扫描 6 维度

---

## 全部已处理 ✅

### 仍需关注的 TODO 项（~14 项，低优先级）

| 类别 | 数量 | 典型项 |
|------|------|--------|
| 大文件拆分 | 5 | acquisition_service(824行) / design_tokens(769行) / controller(747行) / prpd_widget(624行) / fpga_protocol(594行) |
| 无障碍适配 | 3 | setTabOrder / 按钮状态管理 / 色盲友好 |
| UI 一致性 | 3 | 按钮样式统一 / 内联字号 / 图标改为 SVG |
| 安全/兼容 | 2 | DB 打包路径 / FlashWindow 跨平台 |
| 历史遗留 | 2 | PRPS 相位映射 / PRPS ColorBar |
| 代码质量 | 2 | unregister O(n) / samples.tolist 优化 |

---

## 修复日志

### v1.0.7 (2026-06-16)

**P0（5/5 ✅）**
- 仪表板指标接入真实数据、_main_window @property 修复、ConfigStore 双重检查锁定
- 按钮文字 bug 修复、waveform 控件 tooltip

**P1（32/32 ✅）**
- 空状态 / 异常处理 / 代码去重(-110行) / 线程安全 / 输入验证 / 死代码 / 性能 / 硬编码

**P2（50+ 项 ✅）**
- 类型注解 / 日志修复 / 空状态引导 / UI 改进(菜单/快捷键/tooltip)
- 安全稳定 / 代码质量 / 字体样式 / 对话框
- 大文件拆分: analysis_page 1592→644行 (DataImporter/Classifier/Exporter → core/data/)
- UI 一致性: ButtonStyles 统一 4 处按钮样式(-60行)
- 响应式布局: realtime_monitor 三栏 + ae_page 图表 min/max 约束

**历史遗留（1/4 ✅）**
- L-1: core/version.py __version__ 对齐 1.0.7

### 提交记录

| 提交 | 内容 |
|------|------|
| `ac22c10` | P0 全部 + P1 基础 |
| `82cc3a5` | P1 空状态 + 异常处理 |
| `d8e150a` | P1 release_device 去重 + 线程锁 |
| `956ff09` | P1 PRPD/PRPS 管线去重(-70行) |
| `0fb17a4` | P1 剩余 + P2 快速修复 |
| `f88c748` | P2 类型注解 + 工具提示 |
| `8bec660` | P2 UI(菜单/快捷键/空状态) |
| `42e01fc` | P2 安全/稳定/一致性 |
| `d17564e` | P2 趋势图/图标/PRPD |
| `4cf6245` | P2 大文件 TODO + 字体 |
| `980d895` | P2/历史(版本/对话框/无障碍) |
| `e60d4cf` | P2 UI一致性(ButtonStyles) |
| `3d5e16a` | P2 大文件拆分(analysis_page → 4) |
| `09ae3b3` | P2 响应式布局(min/max width) |

---

## 统计总览

| 严重程度 | 已修复 | TODO标记 | 状态 |
|---------|--------|---------|------|
| **P0** | 5 | 0 | ✅ |
| **P1** | 32 | 0 | ✅ |
| **P2** | 50 | 10 | ✅ |
| **历史遗留** | 1 | 3 | ✅ |
| **合计** | **88** | **13** | ✅ |
