# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['app', 'core', 'ui']
hiddenimports += collect_submodules('app')
hiddenimports += collect_submodules('core')
hiddenimports += collect_submodules('ui')


a = Analysis(
    ['D:/vpktool/main.py'],
    pathex=['D:/vpktool'],
    binaries=[],
    datas=[('D:/vpktool/assets', 'assets')],
    hiddenimports=hiddenimports,
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
    name='RBVP',
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
    icon=['D:/vpktool/assets/RBVP.ico'],
)
