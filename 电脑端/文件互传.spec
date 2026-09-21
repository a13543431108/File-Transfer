# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec —— 文件互传
#
# 打包命令（**必须用 .spec 文件**，不能用 文件互传.py）：
#     cd /d "C:\Users\AAAA\Desktop\文件互传\电脑端"
#     pyinstaller 文件互传.spec --clean
#
# 注意：如果运行 `pyinstaller 文件互传.py`，PyInstaller 会自动生成一份
# 默认 spec 并**覆盖本文件**，导致 datas 丢失、图标不被打包。
#
# 关键点：
#   · datas 里包含 icon.ico —— 打包后可通过 sys._MEIPASS/icon.ico 访问，
#     运行时 _apply_icon() 会从那里读取。仅靠 EXE(icon=...) 只设置 exe 文件
#     在资源管理器里的图标，运行时窗口图标仍需 iconbitmap() 加载。
#   · console=False 让 GUI 应用不弹黑框
#   · upx=False 避免某些杀软误报；如果体积敏感可改 True


a = Analysis(
    ['文件互传.py'],
    pathex=[],
    binaries=[],
    # 把 icon.ico 打包到 exe 内部根目录（运行时 sys._MEIPASS/icon.ico）
    datas=[('icon.ico', '.')],
    hiddenimports=[
        # 可选依赖 tkinterdnd2：装了才会被打包；没装 PyInstaller 会忽略
        'tkinterdnd2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='文件互传',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 设置 exe 文件本身的图标（资源管理器显示）
    icon=['icon.ico'],
)
