# -*- mode: python ; coding: utf-8 -*-
"""无界面处理内核 sidecar 的冻结配置。

与 build.spec / PDMonitor.spec 的关键差别：那两个冻结的是 Qt 前端 main.py，必须带上
QtWidgets/QtGui/pyqtgraph/matplotlib；本产物只跑采集 + DSP + WebSocket 推流，Qt 侧只要
QtCore/QtNetwork/QtWebSockets。把 GUI 栈排掉是这件产物大小的分水岭。

    python -m PyInstaller sidecar.spec

产物 dist/uhf-sidecar/ 由 electron-builder 经 extraResources 交付，目标机不需要装 Python。

console=True 不是疏漏：desktop/src/main/sidecar.ts 靠 scrape stdout 拿端口和 token
（token 每次启动随机，不能当参数传），无控制台的窗口化产物会把这两行吞掉。
桌面端用 spawn 的默认 windowsHide=true 拉起，不会闪出黑窗。
"""

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtNetwork",
    "PySide6.QtWebSockets",
    "msgpack",
    "numpy",
    "scipy",
    "scipy.signal",
    "scipy.signal.windows",
    "scipy.fft",
    "scipy.ndimage",
    "scipy.interpolate",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.ext.declarative",
    "sqlite3",
]
# 采集/处理管线里大量 import 写在函数体内，modulegraph 编译字节码时会跟到它们，
# 所以不需要额外整包收集 core。这里曾加过 collect_submodules("core") 兜底，产物从
# 183MB 涨到 833MB，多出来的正是构建机全局解释器里的 torch/cv2/transformers；
# 具体经哪条边被收进去的没有查清（逐个 import core.* 子模块并不会加载 torch），
# 但结论可用：不做整包收集，并对下面这些 ML 包显式排除，体积就锁在 183MB 量级。

# 只读资源：设备配置 JSON 与告警阈值配置。冻结后 config/ 不在 exe 目录而在
# _MEIPASS，取值走 core/foundation/runtime_paths.resource_path()。
datas = [("config", "config")]

excludes = [
    # 只排"确定与内核无关且体积大"的两类：Qt GUI 栈与构建机上的 ML 栈。
    #
    # 曾经这里还排了 h5py/openpyxl/sklearn/pandas/structlog/unittest/test/pytest，
    # 那是误解：PyInstaller 本来就只收可达模块，不做整包收集时这些根本不会进来。
    # 硬排的后果不是省体积而是砸功能——排掉 stdlib 的 unittest 之后
    # scipy.signal -> numpy.testing -> unittest 断链，peak_detector 每次调用都抛
    # ModuleNotFoundError，产物照样打印 "sidecar ready" 但一帧波形都不处理。
    # 所以：**不要排 stdlib，不要排"看着用不到"的第三方。**
    "PySide6.QtWidgets",
    "PySide6.QtGui",
    "PySide6.QtPrintSupport",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVis",
    # 旧前端的绘图栈
    "pyqtgraph",
    "matplotlib",
    "IPython",
    "tkinter",
    # 构建机是共享的全局解释器，装着与本工程无关的 ML 栈。core/ 一个都没 import，
    # 列在这里是为了让"误收"在构建期变成显式失败而不是 500MB 的产物。
    "torch",
    "torchvision",
    "cv2",
    "transformers",
    "tokenizers",
    "huggingface_hub",
    "safetensors",
    "PIL",
    # 只用 SQLite，postgresql 驱动是 SQLAlchemy 顺手收进来的
    "psycopg2",
]

a = Analysis(
    ["sidecar.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="uhf-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="uhf-sidecar",
)
