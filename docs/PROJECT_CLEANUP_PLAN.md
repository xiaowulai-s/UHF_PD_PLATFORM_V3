# 项目清理计划

**创建时间**: 2026-07-01
**目标**: 整理项目文件夹结构，删除冗余过期文件
**预计释放空间**: 200K - 1.1G（取决于执行方案）

---

## 📊 项目结构分析

### 当前磁盘使用情况

| 目录/文件 | 大小 | 说明 |
|----------|------|------|
| venv/ | 1.1G | Python虚拟环境 |
| generated-images/ | 14M | AI生成的UI原型图 |
| outputs/ | 15M | 主题方案和页面截图 |
| logs/ | 624K | 日志文件 |
| data/ | 4.6M | 数据库文件 |
| __pycache__/ | 28K | Python缓存 |
| *.csv, *.json, *.xlsx | 66K | 临时测试数据 |
| 总占用 | **~1.13G** | - |

---

## 🗑️ 可清理项目清单

### 类别1：Python缓存文件（安全清理）

| 项目 | 大小 | 风险 | 说明 |
|------|------|------|------|
| `__pycache__/` | 28K | ✅ 无风险 | Python运行时缓存 |
| `*/__pycache__/` (多个) | ~30K | ✅ 无风险 | 各模块缓存 |
| `.pytest_cache/` | 很小 | ✅ 无风险 | pytest缓存 |

**小计**: ~58K
**建议**: **立即清理**

---

### 类别2：临时测试数据（安全清理）

| 文件 | 大小 | 风险 | 说明 |
|------|------|------|------|
| `alarms_20260630_143714.csv` | 57B | ✅ 无风险 | 测试报警数据 |
| `classification_20260630_143521.csv` | 388B | ✅ 无风险 | 测试分类数据 |
| `prpd_report.json` | 60K | ✅ 无风险 | 测试报告JSON |
| `prpd_report.xlsx` | 5.3K | ✅ 无风险 | 测试报告Excel |

**小计**: ~66K
**建议**: **立即清理**

---

### 类别3：占位符脚本（安全清理）

| 文件 | 大小 | 说明 |
|------|------|------|
| `run_gen.py` | 11B | 占位符，仅包含"placeholder" |
| `generate_all_schemes.py` | 11B | 占位符 |
| `generate_designs.py` | 11B | 占位符 |

**小计**: 33B
**建议**: **立即清理**

---

### 类别4：空目录（安全清理）

| 目录 | 大小 | 说明 |
|------|------|------|
| `prpd_output/` | 0 | 空输出目录 |

**建议**: **立即清理**

---

### 类别5：生成的UI设计图（需要评估）

#### 5.1 generated-images/ (14M)

**内容**: AI生成的UI原型图和线框图
**文件数**: 12+ 个PNG文件
**创建时间**: 2026-06-25
**说明**: 项目初期UI设计阶段生成的参考图

**问题**:
- ❓ 是否还需要这些设计参考图？
- ❓ 是否已经进入开发阶段，不再需要原型图？

**建议选项**:
- **选项A**: 移动到归档 `design-assets/generated-images-2026-06/`
- **选项B**: 删除（如果不再需要）
- **选项C**: 保留在原位

#### 5.2 outputs/ (15M)

**内容**:
- 主题方案HTML (`B_theme_component_spec.html`)
- 页面截图 (`H1_仪表板.png`, `H2_实时监测.png`等)
- 暗色主题设计图

**问题**:
- ❓ 这些输出文件是否已经过时？
- ❓ 是否还需要用于文档或演示？

**建议选项**:
- **选项A**: 移动到归档 `design-assets/outputs-2026-06/`
- **选项B**: 删除（如果不再需要）
- **选项C**: 保留在原位

**小计**: 29M
**风险**: ⚠️ 需要确认

---

### 类别6：验证脚本（需要评估）

#### 6.1 Phase 1/2 验证脚本

| 文件 | 行数 | 说明 |
|------|------|------|
| `scripts/phase1_verification.py` | 264行 | Phase 1验证脚本 |
| `scripts/refactoring_comparison.py` | 109行 | 重构对比脚本 |
| `scripts/test_theme_system.py` | 166行 | 主题系统测试 |

**问题**:
- ❓ Phase 1/2 已完成，这些脚本是否还需要？
- ❓ 是否应集成到CI/CD流程中？

**建议选项**:
- **选项A**: 移动到归档 `scripts/archive/`
- **选项B**: 删除（如果验证已完成）
- **选项C**: 保留（作为历史参考）

#### 6.2 其他脚本（建议保留）

| 文件 | 说明 | 建议 |
|------|------|------|
| `scripts/build.py` | 构建脚本 | ✅ 保留 |
| `scripts/final_verification.py` | 最终验证 | ✅ 保留 |
| `scripts/migrate_database.py` | 数据库迁移 | ✅ 保留 |
| `scripts/ui_lint.py` | UI代码检查 | ✅ 保留 |

**小计**: ~539行脚本
**风险**: ⚠️ 需要确认

---

### 类别7：文档整理

#### 当前文档结构

`docs/` 目录包含大量阶段性文档：

| 文档 | 类型 | 建议 |
|------|------|------|
| `PHASE1_FINAL_REPORT.md` | 完成报告 | 📦 归档 |
| `PHASE1_IMPLEMENTATION_REPORT.md` | 实施报告 | 📦 归档 |
| `PHASE2_COMPLETION_REPORT.md` | 完成报告 | 📦 归档 |
| `PHASE2_FINAL_REPORT.md` | 最终报告 | 📋 保留（最终版） |
| `PHASE2_PROGRESS.md` | 进度跟踪 | 📦 归档 |
| `PHASE2_SUMMARY.txt` | 总结 | 📦 归档 |
| `PHASE2_VALIDATION_REPORT.md` | 验证报告 | 📦 归档 |
| `PHASE3_PLAN.md` | 计划 | 📋 保留（进行中） |
| `PHASE3_COMPLETION_REPORT.md` | 完成报告 | 📋 保留（最新） |
| `THEME_REFACTORING_REPORT.md` | 专题报告 | 📦 归档 |
| `UI_BASE_CLASSES_REPORT.md` | 专题报告 | 📦 归档 |
| `EXCEPTION_HANDLING_GUIDE.md` | 指南 | 📋 保留 |
| `EXCEPTION_HANDLING_PROGRESS.md` | 进度 | 📦 归档 |
| `ASYNC_REFACTORING_REPORT.md` | 专题报告 | 📦 归档 |
| `TECHNICAL_AUDIT_REPORT.md` | 审计报告 | 📋 保留（重要） |

**归档建议**:
- 移动到 `docs/archive/` 而不是删除
- 保留最终报告和当前计划
- 保留重要参考文档（审计报告、指南）

**预期节省**: ~50-100K

---

### 类别8：其他项目目录（谨慎处理）

| 目录 | 说明 | 建议 |
|------|------|------|
| `next-app/` | 可能是其他项目 | 🔍 需要确认 |
| `migrate_test_m990ekar/` | 测试迁移目录 | 🔍 需要确认 |
| `.charts/` | PRPD图表缓存 | 🔍 需要确认 |

**风险**: 🔴 可能包含重要数据
**建议**: 先检查内容，再决定是否删除

---

### 类别9：虚拟环境（可选）

| 目录 | 大小 | 风险 | 说明 |
|------|------|------|------|
| `venv/` | 1.1G | 🔴 高风险 | Python虚拟环境 |

**问题**:
- ❓ 项目是否还使用这个venv？
- ❓ 是否可以重新创建？

**建议**:
- **如果不再使用**: 删除可释放1.1G空间
- **如果需要保留**: 保留在原位
- **折中方案**: 移动到备份目录

**风险**: ⚠️ 删除后需要重新安装所有依赖

---

## 🎯 清理方案

### 方案A：保守清理（推荐）✅

**适用场景**: 首次清理，只删除明确无用的文件

**执行步骤**:
1. ✅ 清理所有Python缓存（58K）
2. ✅ 清理临时测试数据（66K）
3. ✅ 清理占位符脚本（33B）
4. ✅ 清理空目录（0）
5. 📦 归档旧文档到 `docs/archive/`
6. 📦 移动设计图到 `design-assets/`（归档而非删除）

**预期释放**: ~200K + 文档整理
**风险**: ✅ 无风险
**可逆性**: ✅ 完全可逆

---

### 方案B：中度清理

**适用场景**: 确认不再需要设计图和旧脚本

**执行步骤**:
1. ✅ 方案A的所有步骤
2. 🗑️ 删除 `generated-images/`（14M）
3. 🗑️ 删除 `outputs/`（15M）
4. 📦 归档验证脚本到 `scripts/archive/`

**预期释放**: 29M + 200K
**风险**: ⚠️ 中等（需要确认不再需要设计图）
**可逆性**: ❌ 不可逆（除非从git恢复）

---

### 方案C：深度清理

**适用场景**: 彻底清理，最大化释放空间

**执行步骤**:
1. ✅ 方案B的所有步骤
2. 🗑️ 删除 `venv/`（1.1G）⚠️
3. 🔍 检查并清理 `next-app/`, `migrate_test_m990ekar/`
4. 🔍 检查并清理 `.charts/`

**预期释放**: 1.1G + 29M + 200K
**风险**: 🔴 高风险（需要确认venv不再使用）
**可逆性**: ❌ 不可逆（需要重新安装依赖）

---

## 📋 执行清单

### ✅ 立即可执行（方案A）

#### 1. 清理Python缓存

```bash
# 清理所有 __pycache__ 目录
find . -type d -name "__pycache__" -exec rm -rf {} +

# 清理 pytest 缓存
rm -rf .pytest_cache/
```

**预期释放**: 58K

#### 2. 清理临时测试数据

```bash
# 删除测试数据文件
rm -f alarms_*.csv classification_*.csv

# 删除测试报告
rm -f prpd_report.json prpd_report.xlsx
```

**预期释放**: 66K

#### 3. 清理占位符脚本

```bash
# 删除占位符
rm -f run_gen.py generate_all_schemes.py generate_designs.py
```

**预期释放**: 33B

#### 4. 清理空目录

```bash
# 删除空输出目录
rm -rf prpd_output/
```

**预期释放**: 0

#### 5. 归档旧文档

```bash
# 创建归档目录
mkdir -p docs/archive

# 移动 Phase 1 文档
mv docs/PHASE1_*.md docs/archive/

# 移动 Phase 2 进度文档
mv docs/PHASE2_PROGRESS.md docs/PHASE2_SUMMARY.txt docs/archive/
mv docs/EXCEPTION_HANDLING_PROGRESS.md docs/archive/

# 移动其他专题报告（保留最终报告）
mv docs/THEME_REFACTORING_REPORT.md docs/UI_BASE_CLASSES_REPORT.md docs/archive/
mv docs/ASYNC_REFACTORING_REPORT.md docs/EXCEPTION_HANDLING_GUIDE.md docs/archive/ 2>/dev/null || true
```

**预期节省**: ~50-100K

#### 6. 归档设计图（可选）

```bash
# 创建设计资产归档目录
mkdir -p design-assets

# 移动生成的设计图（归档而非删除）
mv generated-images design-assets/generated-images-2026-06
mv outputs design-assets/outputs-2026-06
```

**预期释放**: 0（归档不删除，但整理目录结构）

---

### ⚠️ 需要确认后执行（方案B）

#### 7. 删除设计图（确认不再需要）

```bash
# ⚠️ 此操作不可逆！
rm -rf generated-images/ outputs/
```

**预期释放**: 29M
**风险**: ⚠️ 需要确认

#### 8. 归档验证脚本

```bash
# 创建脚本归档目录
mkdir -p scripts/archive

# 移动 Phase 1/2 验证脚本
mv scripts/phase1_verification.py scripts/archive/
mv scripts/refactoring_comparison.py scripts/archive/
mv scripts/test_theme_system.py scripts/archive/
```

**预期释放**: 0（归档）
**风险**: ✅ 低风险

---

### 🔍 深度清理（方案C）

#### 9. 删除虚拟环境（谨慎！）

```bash
# ⚠️ 此操作不可逆！
# 删除前确保：
# 1. 项目不再使用此venv
# 2. 可以重新创建（需要重新安装所有依赖）
# 3. 已备份requirements.txt

rm -rf venv/
```

**预期释放**: 1.1G
**风险**: 🔴 高风险

#### 10. 检查其他目录

```bash
# 检查 next-app 目录
ls -la next-app/

# 检查 migrate_test_m990ekar 目录
ls -la migrate_test_m990ekar/

# 检查 .charts 目录
ls -la .charts/
```

**根据检查结果决定是否删除**

---

## 📊 清理效果预期

### 方案A（保守）

```
清理前: 1.13G
清理后: ~1.13G（整理归档）
释放:   200K（实际删除）
节省:   文档整理 + 目录结构优化
风险:   ✅ 无风险
```

### 方案B（中度）

```
清理前: 1.13G
清理后: ~1.1G
释放:   29M + 200K
节省:   删除不需要的设计图
风险:   ⚠️ 中等
```

### 方案C（深度）

```
清理前: 1.13G
清理后: ~30M
释放:   1.1G + 29M + 200K
节省:   最大程度清理
风险:   🔴 高风险
```

---

## 🎯 推荐执行顺序

### 第1步：立即执行（方案A）

- ✅ Python缓存清理
- ✅ 临时文件清理
- ✅ 占位符脚本清理
- ✅ 空目录清理
- ✅ 文档归档

**耗时**: 5分钟
**释放**: 200K
**风险**: 无

### 第2步：评估后执行（方案B）

- 检查设计图是否还需要
- 确认后删除或归档
- 归档验证脚本

**耗时**: 10分钟
**释放**: 29M
**风险**: 低-中

### 第3步：谨慎评估（方案C）

- 确认venv是否还需要
- 检查其他项目目录
- 深度清理

**耗时**: 30分钟
**释放**: 1.1G
**风险**: 高

---

## 📝 注意事项

### 清理前

1. **备份重要数据**（如有疑问）
2. **确认当前git状态**（所有修改已提交）
3. **记录清理前的大小**（用于对比）

### 清理后

1. **验证应用仍然可以启动**
   ```bash
   python main.py --help
   ```

2. **检查语法**
   ```bash
   python -m py_compile core/**/*.py ui/**/*.py
   ```

3. **更新.gitignore**（如有必要）

### 恢复方法

如果清理出现问题，可以从git恢复：

```bash
# 恢复误删的文件
git checkout -- <file>

# 恢复整个目录
git checkout -- docs/ scripts/

# 如果已提交删除，可以回滚
git revert HEAD
```

---

## 📅 执行记录

**创建时间**: 2026-07-01
**状态**: 📋 待执行
**最后更新**: 2026-07-01

### 执行日志

| 日期 | 执行的方案 | 释放空间 | 备注 |
|------|-----------|---------|------|
| 2026-09-29 | A+B 删除，B′+C 归档 | 约 92M 从工作区移除 | 详见下方"2026-09-29 执行记录" |

---

## 🧹 2026-09-29 执行记录

本文档 2026-07-01 写就后**从未执行**。本次按现状重新核对了每一项（本文档写于旧 UI
还在开发、Electron 重写尚未开始的时期，所以有几处判断需要修正，见"与原计划的差异"）。

### 删除（可再生成 + 一次性导出，共约 92M）

| 项 | 依据 |
|---|---|
| `build/`(60M)、`htmlcov/`(15M)、`.coverage`、`coverage.xml`、`.pytest_cache/`、21 个 `__pycache__/` | 全部 git 已忽略，工具自动重建 |
| 空目录 `migrate_test_m990ekar/`、`prpd_output/`、`.uploads/` | 0 项 |
| `alarms_*.csv`、`classification_*.csv`、`prpd_report.json`、`prpd_report.xlsx`、`test_output.txt`、`test_results.txt`、`rate.log`、`gen-chart-tmp.js` | 6/30~7/8 的一次性导出 |
| `logs/system/monitor.log`(888K，7/30 之后未再写) | 旧日志；`logs/sidecar.log` 保留（当前内核在写） |
| `alembic.ini` + `migrations/` | **全仓库零引用**：存储层走 `storage_service` 自建 SQLite 表，不经 alembic。经用户确认直接删除而非归档 |

### 归档到 `_archive/`（保留可回捞，已加入 .gitignore）

| 目录 | 内容 |
|---|---|
| `editor-tmp-20260929/` | 23 个 `*.tmp.<pid>.<hash>` 编辑器原子保存残留，**保留原相对路径**。其中 16 个与正本内容不同（是中间快照），删掉不可恢复，故归档而非删除 |
| `legacy-ui-assets-20260929/` | `outputs/`(15M 主题方案与页面截图)、`generated-images/`(14M AI 原型图)、`screenshots/`、`generate_html_schemes.py`（只往 `outputs/ui_design_proposals.html` 写，全仓库零引用，与它产出的资产同批归档） |
| `legacy-docs-20260929/` | `P0_P1_FIX_SUMMARY.md`、`P2_P3_FIX_SUMMARY.md`、`UI_FIX_PLAN.md`、`UI_REVIEW_REPORT.md`、`UI_REVIEW_SUPPLEMENT.md`、`UI设计师.md` |
| `placeholder-scripts-20260929/` | `run_gen.py`、`generate_all_schemes.py`、`generate_designs.py`（内容各只有 11 字节 `placeholder`） |
| `legacy-root-tests-20260929/` | `test_dark_ui.py`(33K，散在根目录而非 `tests/`) |

### 与原计划的差异（不要照原计划继续执行）

1. **`dist/` 不能删**。原计划把它当构建垃圾。现在 `dist/uhf-sidecar/`(185M) 是
   **PyInstaller 冻结内核产物**，M8 出包与 #11 验证都依赖它，删了要重跑构建。
2. **`build.py` / `PDMonitor.spec` / `installer.iss` 本次保留**。三者是一个自洽的旧
   PySide6 打包链，且 `AGENTS.md` 仍把 `python build.py` 列为现行构建命令；M8（
   electron-builder 接管）之前归档会打断文档化的工作流。
   ⚠️ **顺带发现一个真实地雷**：`build.py:27` 是 `for spec_file in Path('.').glob('*.spec'): spec_file.unlink()`，
   它会**无差别删掉根目录所有 `.spec`**，包括新的 `sidecar.spec`。在 M8 之前运行
   `python build.py` 会静默毁掉内核构建配置。已记入待修。
3. **`plot_PRPS.py` 保留**。`ui/widgets/prpd_3d_renderer.py` 的注释以它为 3D 视角
   （elev=36/azim=-122）与 Y 轴归一化的基准来源，删了会丢掉这个依据。
4. **`main_v2.py`、`ui/pages/*_v2.py` 不在本次动**。`docs/ELECTRON_REWRITE_PLAN.md`
   的 M8 明确写着"旧 `main.py`/`main_v2.py` 下线，`ui/` 归档删除"，属于 M8 的范围。

### 仍需用户拍板的两项 git 操作（本次未做）

- `~` 目录（pre-commit 因 HOME 解析错误写进仓库的缓存，**已被提交进版本控制**）
  需要 `git rm -r '~'` 才清得掉，单纯删文件会留下"已删除"的脏状态。
- 工作区里有 3 个**已删除但未 stage** 的受控文件：
  `core/foundation/plugin_registry.py`、`ui/styles/qss/light.qss`、`工作日志_2026-06-10.md`。
  不是本次清理所为，需确认是否有意删除。

### 验证

清理后全套 `pytest`：**529 passed / 2 skipped / 0 failed（EXIT=0，71.8 秒）**。
`desktop/` 构建链与 `dist/uhf-sidecar` 未受影响。

---

## 🧹 2026-09-29 第二轮：全仓深度盘点（不只根目录）

第一轮只扫了根目录，本轮覆盖 `desktop/`、`docs/`、`ui/`、`data/`。

### 删除

| 项 | 体积 | 依据 |
|---|---|---|
| `desktop/release2/` | 481M | 第二次出包尝试，`npm run package:dir` 可重生成 |
| `desktop/release/`（部分） | 约 268M | 同上。**剩 202M 删不掉**：`win-unpacked/resources/app.asar` 与 `win-unpacked.tmp/resources/default_app.asar` 报 `Device or resource busy`，而进程表里没有任何进程从 `release/` 运行（按 `ExecutablePath` 核过），推断为 Defender/索引服务持句柄。**重启后即可删除，未强杀未知进程** |
| `data/m33_verify.db` | 4.8M | M3.3 验证时建的临时库，gitignored |
| `ui/app_styles.py` | 3.6K | 全仓 **0 引用**的死模块。但它带 80/78 行的未提交真实改动（忽略空白后仍有差异，不是行尾噪音），所以**先复制副本到 `_archive/dead-code-with-local-edits/` 再 `git rm -f`**，可一条命令还原 |

### 归档

| 目录 | 内容 |
|---|---|
| `_archive/m4-captures-20260929/` | 28 个 M4 验收取证文件（26 张 `capture-*.png` + 构建/验收日志）。这是 M4 各面板唯一的视觉证据，值得留 |
| `_archive/legacy-docs-20260703/` | **31 份**旧 PySide6 时期的阶段/重构报告：`PHASE1/2/3_*`、`ARCHITECTURE_REVIEW`、`ARCHITECTURE_FIX_PROGRESS`、`ASYNC_REFACTORING_REPORT`、`THEME_REFACTORING_REPORT`、`TECHNICAL_AUDIT_REPORT`、`UI_BASE_CLASSES_REPORT`、`PROJECT_COMPLETION_REPORT`、`EXCEPTION_HANDLING_*`、`ui_optimization_summary`、`ui_crowding_fix_report`、`ui_implementation_checklist`、`README_UI`、`ui_tools_quick_reference`、`REFACTORING_STATUS_20260703`、`VERSION_UPDATE_1.0.8`、`development_summary`、`phase7/8_*`、`phase_progress` 等 |

`docs/` 从 40 项降到 8 项。删掉 10 个空的目录树（`ai/ architecture/ core/ meta/ project/ reports/ superpowers/ ui/`、`core/*`、`superpowers/plans`）。

### 明确保留的 docs（有入站引用，不是垃圾）

- `architecture.md`、`contributing.md` ← `AGENTS.md:498-499`、`CLAUDE.md:498-499` 的"Related Documentation"
- `DISCHARGE_TYPE_ANALYSIS.md` ← `README.md:258,313` 与 `ui/changelog.py:22`，是放电类型识别的规范来源
- `api.md`、`build_guide.md`（`build.py` 本轮保留，文档要跟着留）、`design/data-source-integration.md`
- `ELECTRON_REWRITE_PLAN.md`、`PROJECT_CLEANUP_PLAN.md`（活文档）

### 盘点出但**本轮未动**的（M8 范围）

`ui/` 旧样式系统四套并行：`theme_dark_monitoring.py`/`theme_pure_white.py`/`theme_teal_cyan.py` 各仅 3 处引用（基本只是 `theme_manager`/`theme_engine` 的注册表）、`apply_teal_cyan.py` 1 处、`ui/pages/analysis_page_v2.py` 与 `dashboard_page_v2.py` 与正本并存。按计划 M8"旧 `main.py`/`main_v2.py` 下线、`ui/` 归档删除"统一处理，避免现在零敲碎打。

### 仍需处理

- 重启后删除 `desktop/release/` 残留的 202M（两个被占用的 asar）。
- `build.py:27` 的 `glob('*.spec').unlink()` 地雷（会删 `sidecar.spec`）。
- `assets/icons`、`assets/sounds` 均为**空目录**，而 `ui/window_managers.py` 会引用 `assets/` → 窗口图标静默加载失败。这是缺陷不是清理项。
- `CLAUDE.md` 与 `AGENTS.md` 实质重复（去掉首行只差 4 行），前者受控、后者未跟踪，建议合并成一份再让另一份引用。

### 验证

两轮清理后全套 `pytest`：**529 passed / 2 skipped / 0 failed**。

---

**文档创建**: 2026-07-01
**最近执行**: 2026-09-29 两轮（根目录 + 全仓深度盘点）
**状态**: ✅ 已执行；剩余项见"仍需处理"
**建议**: 先执行方案A（保守清理），评估后再决定是否继续

---

## 🔄 定期清理建议

**建议执行频率**: 每月一次

**定期清理项目**:
1. Python缓存（`__pycache__/`, `.pytest_cache/`）
2. 日志文件（`logs/`，保留最近7天）
3. 临时数据文件（`*.csv`, `*.json`, `*.xlsx`）

**按需清理项目**:
1. 生成的报告文件
2. 设计资产归档
3. 旧文档归档

---

**文档创建**: 2026-07-01
**状态**: 📋 **待执行**
**建议**: 先执行方案A（保守清理），评估后再决定是否继续

---

# 第 3 轮清理（2026-09-29，Electron 重写期间）

前两轮（2026-07-01）的正文已大幅过期：它列的 `generated-images/`、`outputs/`、
`run_gen.py`、`generate_all_schemes.py`、`alarms_*.csv`、`prpd_report.{json,xlsx}`、
`prpd_output/` 等目标**现在都已不存在**（被归档进 `_archive/` 各带日期的子目录）。
文末那句"状态：待执行"是残留，与正文"已执行"矛盾，本轮一并更正。

## 已执行（无需确认的安全动作）

1. **清掉本轮开发自己产生的临时物**（约 964 MB）：`desktop/release-verify/`、
   `desktop/release-probe/` 两个验证用出包目录，以及 `desktop/*.log`、
   `desktop/capture-*.png`、`desktop/probe-*.png`。
2. **修掉 `build.py` 的破坏性地雷**。原 `clean_build()` 做三件事：
   `rmtree('build')`、`rmtree('dist')`、`Path('.').glob('*.spec')` 逐个 `unlink()`。
   而现役链路正好依赖被它波及的东西——`sidecar.spec` 是
   `desktop/package.json` 的 `build:kernel` 入口，`dist/uhf-sidecar/` 是
   `electron-builder.json` 的 `extraResources` 来源，`build/` 是那条 PyInstaller
   的 workpath。**跑一次旧打包脚本就会把现役内核的 spec 与产物一起删掉。**
   现改为只清 `__pycache__` 与两个旧桌面版 spec（`build.spec`、`PDMonitor.spec`）。
   已在临时目录做过等价演练：`sidecar.spec`、`dist/`、`build/` 全部保住。

## 待你确认后才动的项

按"未跟踪=删了不可恢复"分级，未跟踪的一律**移入 `_archive/<名字>-YYYYMMDD/`**
而不是删除（`_archive/` 已被 `.gitignore:84` 忽略，文件留在磁盘上可随时取回）。

### A. 建议归档（未跟踪，零调用者）

| 文件 | 证据 |
|---|---|
| `scripts/final_verification.py` | 断言旧重构文件存在；无调用者，CI 不跑 |
| `scripts/phase1_verification.py` | Phase-1 导入/实例化检查；无调用者 |
| `scripts/refactoring_comparison.py` | 重构前后指标对比；无调用者 |
| `scripts/test_theme_system.py` | 名字像测试但在 `testpaths = tests` 之外，pytest 永不收集 |
| `ui/apply_teal_cyan.py` | 一次性改色脚本，`:157` 自带 `app.exec()`，零入向引用 |

### B. 建议还原（tracked，改动是纯行尾噪音，不是真内容）

- `data/ui_preferences.json` —— 读写方随 MCGS 在 `377c940` 移除而消失，全仓库
  `ui_preferences` 零命中；`git diff --ignore-cr-at-eol` 为空，即"已修改"只是 CRLF。
- `.charts/prpd_chart.png` —— 全仓库无任何写入者（没有 `.charts` 字符串、
  也没有对应的 `savefig`），不是测试夹具；当前 9.9K→368K 的变化来源不明。
  建议先 `git checkout --` 还原，再决定是否从库里彻底移除。

### C. 保留到 M8（现在动反而添乱）

`main.py`、`main_v2.py`、`build.spec`、`PDMonitor.spec`、`installer.iss`、
`plot_PRPS.py`、`Update.md` —— 都仍被 README / AGENTS.md / docs/build_guide.md /
spec 文件引用；且 `docs/ELECTRON_REWRITE_PLAN.md` 的 M8 明确要求 `main.py`
保持可运行作为回滚路径。`plot_PRPS.py` 还被 `ui/widgets/prpd_3d_renderer.py`
的注释当基线引用，删了会让注释指向不存在的东西。

### D. 明确不要碰

`venv/`（1.1G）、两个历史 `git stash`（`WIP on main: 211d38b` / `5169ee2`）、
`desktop/release/win-unpacked/`（旧装机版，此前被系统级占用锁住，需你确认后再删）。

## 顺带查出的仓库卫生缺陷（属"要修"，不是"要删"）

1. `assets/` 是**空目录**，但 `ui/window_managers.py:476-478` 引用
   `assets/sounds/alarm_critical.wav`、`alarm_warning.wav` —— 报警声音实际不存在。
2. `AGENTS.md` 与 `CLAUDE.md` **只差 2 行**（标题行 + 一句 Codex/Claude 措辞），
   且两个都未跟踪。前一轮文档说"只差 4 行、CLAUDE.md 已跟踪"，两点都不对。
   合并与否需要你定，因为它们是 agent 指令来源。
3. 全仓库 CRLF/LF 混用：`git diff` 里大量文件显示整文件重写，
   真实内容差异要加 `--ignore-cr-at-eol` 才看得见。根治办法是加 `.gitattributes`。
4. `build.py:123` 用了 `iscc_ppath`，而 `:110` 定义的是 `iscc_path` → NameError，
   Inno Setup 那条路径从来没走通过。
5. `scripts/build.py`（已跟踪）是给**另一个产品**抄的样板：驱动 `build.spec`，
   还要拷贝 `新架构说明_v2.md` 和 `docs/用户文档.md`，两个文件都不存在。
   它与根目录 `build.py` 不是重复关系（结构零重叠），别合并。
6. 空目录：`.design/uhf-ae-pd-redesign/{assets,pages,partials}`、
   `assets/{icons,sounds}`、`config/profiles`。

## 一处对既往说法的更正

提交 `9e0ee29` 的说明把 `trend_predictor` 与 `lazy_loader` 和"被已跟踪代码引用却
从未提交"的模块并列陈述，措辞过头。逐条复核后：那批 9 个模块里
`data_source`/`data_source_config`/`hardware_source`/`protocol_data_structures`/
`result_cache`/`uhf_pipeline`/`ae_pipeline` 确有引用，而
**`trend_predictor`、`lazy_loader` 是零引用孤儿**，只是为了让仓库完整才一并入库。
M8 若要瘦身，这两个是第一批候选。

> 2026-09-30 追记：上面这句"`result_cache` 确有引用"已经作废。它确实曾被
> `PRPDProcessor`/`FFTProcessor` 引用，但实测证明那层缓存在流式路径下**恒零命中**
> （键取累积事件流/波形字节的 md5，每帧必变），却让 sidecar 常驻 200 MB LRU 上限，
> 已整层删除、模块连同引用一起移除。详见 `ELECTRON_REWRITE_PLAN.md` 的 M5.1 长稳一节。

---

**文档创建**: 2026-07-01
**最近更新**: 2026-09-29（第 3 轮）
**状态**: 第 1、2、3 轮的部分项已执行；A/B 两组与指令文件合并已于 2026-09-29 执行

## 第 3 轮执行记录（2026-09-29，经确认后执行）

- **A 组已归档** → `_archive/scripts-20260929/`（保留相对路径）：
  `scripts/{final_verification,phase1_verification,refactoring_comparison,test_theme_system}.py`、
  `ui/apply_teal_cyan.py`。归档后复查零悬挂引用——`ui/theme_manager.py:170` 的
  `apply_teal_cyan` 是**同名方法**而非对该文件的引用，属误报。
- **B 组已还原**：`data/ui_preferences.json`（确认纯 CRLF 噪音）、
  `.charts/prpd_chart.png`。后者**不是噪音**——复核发现它是真实的图片内容变化
  （9.9K→368K，981×1223 有效 PNG），因此先备份到
  `_archive/noise-revert-20260929/` 再还原，没有按"噪音"名义丢弃真东西。
- **指令文件已合并**：`CLAUDE.md` 从 16517 字节缩为 426 字节的指针文件，
  唯一来源是 `AGENTS.md`；原始完整副本存 `_archive/docs-20260929/CLAUDE.md.full`。
  实测两者只差 2 处（标题行 + 一句工具名措辞），共 4 行 diff。
- 全套测试 544 passed / 2 skipped；现役资产
  （`sidecar.spec`、`dist/uhf-sidecar/`、`build-resources/`、`electron-builder.json`）
  逐项确认未被波及。

## 归档后暴露出的最大一处过期：AGENTS.md 本身

`AGENTS.md`（16.5 KB，2026-07-27）仍把 PySide6 当作**现役**架构来写：
开头就是 `python main.py` 运行方式、`ui/pages/*` 模块表、`PDSystemController`
单例、PyInstaller `build.py` 打包、v1.0.7 版本历史。而仓库现在处于
Electron + sidecar 重写中途（`docs/ELECTRON_REWRITE_PLAN.md` 才是当前事实）。

这份文件是后续每一次 agent 会话的默认输入，写错方向的代价比普通文档高，
建议单独立项更新（保留其中仍然成立的 DSP/内核部分：传感器规格、报警阈值、
数据流、性能目标）。本轮未改，因为重写它属于文档工作而非文件清理。
