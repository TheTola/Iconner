# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['Gen1.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'), ('Gen2.py', '.'), ('Gen4.py', '.'), ('Gen3.py', '.')],
    hiddenimports=[],
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
    name='IconMaker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Users\\Oluwatola Ayedun\\Desktop\\Iconer\\Icon Images\\Icons\\Ico-Ico.ico'],
)
