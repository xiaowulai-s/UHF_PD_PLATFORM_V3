# 打包部署指南

## 概述

本文档介绍如何打包 PD 监测系统为可分发的可执行文件或安装包。

## 前置要求

### 1. 安装 PyInstaller

```bash
pip install pyinstaller
```

### 2. （可选）安装 Inno Setup

用于创建 Windows 安装程序：

1. 下载 Inno Setup 6: https://jrsoftware.org/isdl.php
2. 安装到默认路径: `C:\Program Files (x86)\Inno Setup 6`

## 打包方式

### 方式 1: 使用构建脚本（推荐）

```bash
# 查看所有选项
python build.py --help

# 清理旧的构建文件
python build.py --clean

# 构建多文件目录（默认）
python build.py

# 构建单个可执行文件
python build.py --onefile

# 构建调试版本（显示控制台窗口）
python build.py --debug

# 构建并创建安装程序
python build.py --installer

# 构建并创建便携版压缩包
python build.py --portable

# 组合使用
python build.py --onefile --installer --version 1.0.0
```

### 方式 2: 使用 PyInstaller 直接打包

```bash
# 使用 spec 文件打包
pyinstaller PDMonitor.spec

# 或直接命令行打包
pyinstaller --name PDMonitor --windowed --clean --noconfirm main.py
```

## 输出文件

构建完成后，可在以下位置找到输出文件：

- **多文件模式**: `dist/PDMonitor/`
- **单文件模式**: `dist/PDMonitor.exe`
- **安装程序**: `installer/PDMonitor_v{version}_Setup.exe`
- **便携版压缩包**: `PDMonitor_Portable_{timestamp}.zip`

## PyInstaller 配置说明

### 配置文件

`PDMonitor.spec` 文件包含完整的打包配置：

- **Analysis**: 分析依赖关系
  - 添加数据文件（config, ui/styles, docs 等）
  - 包含隐藏导入（PySide6, numpy, sklearn 等）
  - 排除不必要的模块（测试、tkinter 等）

- **EXE**: 可执行文件配置
  - 应用名称: `PDMonitor`
  - 图标: `data/icon.ico`
  - 窗口模式: GUI（无控制台窗口）

- **COLLECT**: 多文件分发包

### 常见问题

#### 1. 缺少 DLL 文件

如果运行时缺少 DLL，使用以下命令查找：

```bash
pyinstaller --clean main.py
```

检查生成的 `build/PDMonitor/warn-*.txt` 文件。

#### 2. 文件过大

单文件模式通常较大（100-300 MB），这是正常的。可以使用 UPX 压缩：

```bash
pip install upx-binary
pyinstaller --upx-dir /path/to/upx ...
```

#### 3. 杀毒软件误报

某些杀毒软件可能误报 PyInstaller 打包的应用。解决方案：

1. 提交误报给杀毒软件厂商
2. 使用代码签名证书签名（需要购买证书）

## Inno Setup 安装程序

### 配置说明

`installer.iss` 脚本包含安装程序配置：

- **安装路径**: `C:\Program Files\PD 监测系统`
- **开始菜单快捷方式**: 自动创建
- **桌面快捷方式**: 可选
- **卸载程序**: 自动生成

### 自定义

修改 `installer.iss` 中的以下常量：

```iss
#define MyAppName "PD 监测系统"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "PD Monitor Team"
```

### 编译

使用 Inno Setup 编译器：

1. 打开 Inno Setup
2. 打开 `installer.iss`
3. 点击 "Build" → "Compile"

或使用命令行：

```bash
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss /DMyAppVersion=1.0.0
```

## 发布检查清单

### 构建前

- [ ] 更新版本号 (`version.py`)
- [ ] 更新 CHANGELOG
- [ ] 确保所有测试通过 (`pytest tests/`)
- [ ] 检查所有资源文件存在（图标、样式表等）

### 构建时

- [ ] 清理旧的构建文件 (`python build.py --clean`)
- [ ] 构建发布版本（不使用 `--debug`）
- [ ] 验证可执行文件运行正常
- [ ] 创建安装程序（可选）

### 发布后

- [ ] 测试安装程序
- [ ] 验证安装目录结构
- [ ] 测试卸载功能
- [ ] 上传到发布平台
- [ ] 更新文档

## 多架构支持

### x64（推荐）

当前默认配置仅支持 64 位 Windows：

```iss
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
```

### x86（不推荐）

如需支持 32 位系统，修改 `PDMonitor.spec`：

```python
exe = EXE(
    ...
    target_arch='x86',  # 添加此参数
)
```

## 代码签名（可选）

为了获得用户信任和避免杀毒软件误报，建议对可执行文件进行代码签名：

### 1. 获取代码签名证书

从证书颁发机构（CA）购买代码签名证书，如：

- DigiCert
- Sectigo
- GlobalSign

### 2. 签名可执行文件

```bash
signtool sign /f certificate.pfx /p password /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist\PDMonitor.exe
```

### 3. 签名安装程序

```bash
signtool sign /f certificate.pfx /p password /tr http://timestamp.digicert.com /td sha256 /fd sha256 installer\PDMonitor_v1.0.0_Setup.exe
```

## 自动化发布（CI/CD）

### GitHub Actions

在 `.github/workflows/release.yml` 中添加：

```yaml
name: Build and Release

on:
  push:
    tags:
      - 'v*'

jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt pyinstaller

      - name: Build executable
        run: python build.py --onefile

      - name: Create installer
        run: python build.py --installer --version ${{ github.ref_name }}

      - name: Upload artifacts
        uses: actions/upload-artifact@v3
        with:
          name: PDMonitor
          path: |
            dist\PDMonitor.exe
            installer\*.exe
```

## 性能优化

### 减小文件大小

1. **排除不必要的依赖**:
   ```python
   excludedimports = [
       'tkinter',
       'unittest',
       'test',
       'tests',
       'matplotlib.tests',
       'numpy.testing',
   ]
   ```

2. **使用 UPX 压缩**:
   ```bash
   pip install upx-binary
   pyinstaller --upx-dir C:\upx ...
   ```

3. **删除不必要的 .py 文件**:
   在打包后，检查 `dist/PDMonitor/` 目录，删除不需要的测试文件和文档。

### 提高启动速度

1. **使用多文件模式**:
   - 启动速度更快
   - 文件更大

2. **优化导入**:
   - 延迟导入大型库
   - 使用 `__all__` 限制导出

## 故障排除

### PyInstaller 错误

#### ModuleNotFoundError

添加缺失的模块到 `hiddenimports`:

```python
hiddenimports = [
    ...
    'missing_module',
]
```

#### Data files not found

确保数据文件路径正确：

```python
datas = [
    ('config', 'config'),  # 源目录, 目标目录
]
```

### 运行时错误

#### DLL load failed

安装 Visual C++ Redistributable:

https://support.microsoft.com/en-us/help/2977003/the-latest-supported-visual-c-downloads

#### Qt platform plugin error

添加 Qt 插件：

```python
datas += collect_data_files('PySide6')
```

## 参考资料

- [PyInstaller 文档](https://pyinstaller.org/)
- [Inno Setup 文档](https://jrsoftware.org/isinfo/)
- [PyInstaller 钩子](https://pyinstaller.org/en/stable/hooks.html)
