# 贡献指南

本文档描述 UHF_PD_PLATFORM_Python 项目的开发规范、代码风格和提交流程。

## 目录

- [开发环境](#开发环境)
- [代码风格](#代码风格)
- [提交规范](#提交规范)
- [测试规范](#测试规范)
- [分支策略](#分支策略)
- [审查流程](#审查流程)

## 开发环境

### 1. Python 版本

- **Python 3.10+** (推荐 3.12)
- Windows / Linux / macOS 均可

### 2. 依赖安装

```bash
# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. IDE 配置

推荐使用 **Visual Studio Code** + **Pyright** 语言服务器。

**推荐扩展:**
- Python (Microsoft)
- Pylance (Microsoft)
- pytest (Microsoft)
- GitLens

**VS Code Settings:**
```json
{
    "python.languageServer": "Pylance",
    "python.testing.pytestEnabled": true,
    "python.testing.autoTestDiscoveryOnSaveEnabled": true,
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.organizeImports": true
    }
}
```

### 4. 运行测试

```bash
# 运行所有测试
pytest

# 运行特定模块
pytest tests/test_ae_e2e_integration.py

# 查看覆盖率
pytest --cov=core --cov=ui
```

## 代码风格

### 1. 通用规范

- **PEP 8** - Python 代码风格指南
- **PEP 257** - Docstring 规范
- **行宽** - 100 字符 (Black 默认 88，但我们使用 100)
- **缩进** - 4 个空格 (不用 Tab)

### 2. Import 顺序

按以下顺序组织 import:

```python
# 1. 标准库
import os
import sys
from pathlib import Path

# 2. 第三方库
import numpy as np
import pytest
from PySide6.QtCore import QObject

# 3. 项目内部
from core.foundation.signal_bus import PDDataBus
from core.processing.fft_processor import FFTProcessor
```

**工具:** 使用 `isort` 自动整理

```bash
isort core/ ui/ tests/
```

### 3. 类型注解

- **所有公共 API 必须添加类型注解**
- **使用 `from __future__ import annotations`** (支持 Python 3.10 及以下)
- 复杂类型使用 `typing` 模块 (List, Dict, Optional, Union, ...)

**示例:**
```python
from __future__ import annotations

def process_data(
    device_id: str,
    channel_id: int,
    data: WaveformData,
) -> ProcessingResult:
    ...
```

### 4. 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 模块 | snake_case | `fft_processor.py` |
| 类 | PascalCase | `FFTProcessor` |
| 函数/方法 | snake_case | `compute_fft()` |
| 变量 | snake_case | `sample_rate` |
| 常量 | UPPER_SNAKE_CASE | `MAX_AMPLITUDE` |
| 私有成员 | _前缀 | `_internal_cache` |

### 5. Docstring 规范

使用 **Google Style** docstring:

```python
def process_waveform(waveform: np.ndarray, sample_rate: int) -> ProcessingResult:
    """
    处理波形数据 (提取 PRPD 特征)

    Args:
        waveform: 输入波形数组
        sample_rate: 采样率 (Hz)

    Returns:
        ProcessingResult 对象

    Raises:
        ValueError: 当波形长度不足时

    Example:
        >>> result = process_waveform(np.random.randn(4096), 100_000_000)
        >>> print(result.features)
    """
```

### 6. 日志规范

使用 `logging` 模块:

```python
import logging

logger = logging.getLogger(__name__)

class MyClass:
    def process(self):
        logger.debug("处理数据: %s", data)
        logger.info("处理完成: %d 条记录", count)
        logger.warning("数据异常: %s", reason)
        logger.error("处理失败: %s", error, exc_info=True)
```

**日志级别:**
- `DEBUG` - 详细调试信息
- `INFO` - 一般运行时信息
- `WARNING` - 警告信息 (可恢复)
- `ERROR` - 错误信息 (需要关注)

### 7. 错误处理

- **捕获具体异常**，不用 `except Exception`
- **提供有意义的错误消息**
- **使用 `logger.exception()` 记录堆栈**

```python
try:
    result = risky_operation()
except ValueError as e:
    logger.error("参数错误: %s", e)
    raise
except IOError as e:
    logger.exception("IO 错误")
    raise RuntimeError("无法读取文件") from e
```

### 8. 性能优化

#### 推荐做法

- **避免在循环中创建临时对象**
- **使用局部变量 (比全局查找快)**
- **批量操作 (避免循环 append)**

#### 避免做法

- **不要在热路径中使用 `try/except`** (影响 JIT)
- **避免频繁的 `numpy` 数组拷贝**
- **不要阻塞主线程** (长时间计算使用 Worker)

## 提交规范

### 1. Commit Message 格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Type (必填):**
- `feat` - 新功能
- `fix` - Bug 修复
- `refactor` - 代码重构
- `perf` - 性能优化
- `test` - 测试相关
- `docs` - 文档更新
- `style` - 代码格式 (不影响功能)
- `chore` - 构建/工具相关

**Scope (可选):**
- `acquisition` - 采集服务
- `alarm` - 报警服务
- `storage` - 存储服务
- `processing` - 数据处理
- `ui` - UI 相关
- `test` - 测试

**示例:**
```
feat(processing): 添加 AE 多通道独立处理支持

- 支持多个 AE 通道并行处理
- 每个通道独立的环形缓冲区
- 优化处理器池管理

Closes #123
```

### 2. 提交前检查

```bash
# 1. 确保测试通过
pytest

# 2. 代码格式化
black core/ ui/ tests/
isort core/ ui/ tests/

# 3. Lint 检查 (可选)
flake8 core/ ui/ tests/
mypy core/ ui/
```

## 测试规范

### 1. 测试文件组织

```
tests/
├── test_ae_e2e_integration.py   # AE 端到端集成测试
├── test_controller_integration.py # 控制器集成测试
├── test_acquisition_pipeline.py  # 采集管线测试
├── test_fft_processor.py         # FFT 处理器测试
├── test_prpd_processor.py        # PRPD 处理器测试
├── ...
```

### 2. 测试命名

```python
# 类: Test + 被测试类名
class TestFFTProcessor:
    pass

# 方法: test_ + 场景描述
def test_compute_with_valid_input(self):
    pass

def test_compute_raises_error_on_empty_input(self):
    pass
```

### 3. 使用 Fixture

```python
@pytest.fixture
def mock_db():
    return MagicMock()

@pytest.fixture
def ctrl(mock_db):
    controller = PDSystemController(db_manager=mock_db)
    controller.initialize()
    return controller
```

### 4. Mock 策略

- **Mock 外部依赖** (数据库、网络、硬件)
- **不 Mock 被测试的核心逻辑**
- **使用 `MagicMock` 简化 mock**

```python
from unittest.mock import MagicMock, patch

# Mock 简单依赖
mock_waveform = MagicMock()
mock_waveform.samples = np.random.randn(4096)

# Mock 并验证调用
with patch.object(ctrl._storage, 'store_pd_event') as mock_store:
    ctrl._on_pd_event_for_storage(...)
    mock_store.assert_called_once()
```

### 5. 测试覆盖率目标

- **核心模块 ≥ 80%**
- **新增代码必须有测试**
- **Bug 修复必须添加回归测试**

```bash
# 查看覆盖率
pytest --cov=core --cov=ui --cov-report=html
# 在 browser 中打开 htmlcov/index.html
```

## 分支策略

### 1. 主分支

- **`main`** - 稳定分支，随时可发布
- **`develop`** - 开发分支（可选）

### 2. 功能分支

```bash
# 从 main 创建功能分支
git checkout -b feat/ae-multi-channel main

# 开发完成后合并到 main
git checkout main
git merge feat/ae-multi-channel
git branch -d feat/ae-multi-channel
```

### 3. 分支命名

- `feat/<feature-name>` - 新功能
- `fix/<issue-description>` - Bug 修复
- `refactor/<module>` - 重构
- `perf/<optimization>` - 性能优化
- `test/<module>` - 测试相关

### 4. 合并策略

- **功能分支 → main** 使用 Squash Merge
- **保持 main 线性历史**

## 审查流程

### 1. 自检清单

提交 PR 前检查:

- [ ] 代码符合 PEP 8 规范
- [ ] 添加类型注解
- [ ] 添加/更新 Docstring
- [ ] 添加单元测试
- [ ] 测试通过 (`pytest`)
- [ ] 代码格式化 (`black`, `isort`)
- [ ] Commit Message 符合规范

### 2. 代码审查要点

**功能性:**
- 是否实现了需求？
- 边界条件是否处理？
- 错误处理是否完善？

**性能:**
- 是否有性能回归？
- 是否有内存泄漏？
- 是否阻塞主线程？

**可维护性:**
- 代码是否易读？
- 命名是否清晰？
- 是否有重复代码？

**安全性:**
- 是否有注入风险？
- 是否处理了异常输入？
- 是否有敏感信息泄露？

### 3. 性能回归检查

```bash
# 运行基准测试
python tools/benchmarks/benchmark_prpd.py
python tools/benchmarks/benchmark_fft.py
python tools/benchmarks/benchmark_databus.py
```

**性能目标:**
- PRPD: ≥ 500K events/s
- FFT (4K): ≥ 1K FFT/s
- DataBus p99: < 10ms

## 常见问题

### Q: 如何处理类型检查错误?

A: 使用 `# type: ignore` 注释:

```python
result = risky_operation()  # type: ignore[return-value]
```

仅在必要时使用，并添加说明。

### Q: 如何调试 Qt 信号?

A: 在信号连接时添加调试打印:

```python
self._data_bus.waveform_received.connect(
    lambda dev, ch, st, wf: logger.debug("waveform: %s", dev)
)
```

或使用 Qt 调试器。

### Q: 如何处理跨平台兼容性?

A:
- 使用 `pathlib.Path` 而非 `os.path`
- 避免硬编码路径分隔符
- 使用 `os.name` 或 `sys.platform` 判断平台

### Q: 如何添加新的传感器类型?

A:
1. 在 `core/foundation/sensor_types.py` 添加类型常量
2. 在 `AcquisitionService` 添加处理路由
3. 创建新的 Pipeline Handler (可选)
4. 更新 UI 页面支持新类型

## 附录

### 工具脚本

- `tools/prpd_analyzer.py` - PRPD 数据分析工具
- `tools/benchmarks/benchmark_prpd.py` - PRPD 性能基准
- `tools/benchmarks/benchmark_fft.py` - FFT 性能基准
- `tools/benchmarks/benchmark_databus.py` - DataBus 延迟基准

### 相关文档

- [API 文档](api.md)
- [架构文档](architecture.md)
- [Update.md](Update.md) - 项目更新日志

### 联系方式

- **Issue Tracker:** GitHub Issues
- **Discussions:** GitHub Discussions
