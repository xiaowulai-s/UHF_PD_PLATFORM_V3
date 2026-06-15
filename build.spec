# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置 - 超高频局部放电在线监测系统

用法:
    pyinstaller build.spec

输出:
    dist/UHF-PD-Monitor/UHF-PD-Monitor.exe
"""

import sys
import os

block_cipher = None

a = Analysis(
    ['main.py'],  # PD 系统入口
    pathex=[],
    binaries=[],
    datas=[
        # 配置文件
        ('config/default_config.json', 'config'),

        # UI 样式文件
        ('ui/styles', 'ui/styles'),
    ],
    hiddenimports=[
        # PySide6
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtSvg',

        # 数据库
        'sqlalchemy',
        'sqlalchemy.dialects.sqlite',

        # 数据可视化
        'pyqtgraph',
        'numpy',
        'numpy.random',

        # 项目模块 - PD 系统
        'core',
        'core.communication',
        'core.data',
        'core.foundation',
        'core.processing',
        'core.services',
        'core.utils',
        'ui',
        'ui.pages',
        'ui.widgets',
        'ui.styles',
        'ui.controllers',

        # 其他依赖
        'structlog',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的模块以减小体积
        'tkinter',
        'matplotlib',
        'pandas',
        'IPython',
        'pytest',
        'unittest',
        'doctest',
        'Cython',
        'setuptools',
        'pip',
        'wheel',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='UHF-PD-Monitor',  # 可执行文件名称
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI 模式，不显示控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # TODO: 添加应用图标
)
