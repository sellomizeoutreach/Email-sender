# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import copy_metadata, collect_data_files, collect_submodules

block_cipher = None

datas = []
# Collect Streamlit core assets
datas += copy_metadata('streamlit')
datas += collect_data_files('streamlit')

# Collect LiteLLM definitions
datas += copy_metadata('litellm')
datas += collect_data_files('litellm')

# Collect streamlit-quill rich text component
try:
    datas += copy_metadata('streamlit_quill')
    datas += collect_data_files('streamlit_quill')
except Exception:
    pass

# Collect tiktoken definitions
datas += copy_metadata('tiktoken')
datas += collect_data_files('tiktoken')
try:
    datas += copy_metadata('tiktoken_ext')
    datas += collect_data_files('tiktoken_ext')
except Exception:
    pass

# Project application files
datas += [
    ('app.py', '.'),
    ('database.py', '.'),
    ('llm_engine.py', '.'),
    ('scheduler.py', '.'),
    ('contacts_handler.py', '.'),
    ('.streamlit/config.toml', '.streamlit'),
    ('assets/logo.jpg', 'assets'),
    ('assets/sellomize.ico', 'assets'),
    ('tiktoken_cache', 'tiktoken_cache'),
]

hiddenimports = [
    'streamlit',
    'streamlit.web.cli',
    'litellm',
    'tiktoken',
    'tiktoken_ext',
    'tiktoken_ext.openai_public',
    'win32com',
    'win32com.client',
    'pythoncom',
    'pywintypes',
    'schedule',
    'apscheduler',
    'streamlit_quill',
    'pandas',
    'bs4',
    'PIL',
    'sqlite3',
    'email.mime.text',
    'email.mime.multipart',
]
hiddenimports += collect_submodules('streamlit')
hiddenimports += collect_submodules('streamlit_quill')
hiddenimports += collect_submodules('tiktoken')
hiddenimports += collect_submodules('tiktoken_ext')

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'notebook'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SellomizeReach',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/sellomize.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SellomizeReach',
)