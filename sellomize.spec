# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import copy_metadata, collect_data_files, collect_submodules

block_cipher = None

datas = []
# Collect Streamlit core assets
datas += copy_metadata('streamlit')
datas += collect_data_files('streamlit')

# Collect streamlit-quill rich text component
try:
    datas += copy_metadata('streamlit_quill')
    datas += collect_data_files('streamlit_quill')
except Exception:
    pass

# Project application files
datas += [
    ('app.py', '.'),
    ('database.py', '.'),
    ('template_engine.py', '.'),
    ('scheduler.py', '.'),
    ('smtp_dispatcher.py', '.'),
    ('contacts_handler.py', '.'),
    ('tracker.py', '.'),
    ('mx_checker.py', '.'),
    ('ui', 'ui'),
    ('.streamlit/config.toml', '.streamlit'),
    ('.streamlit/secrets.toml', '.streamlit'),
    ('assets/logo.jpg', 'assets'),
    ('assets/sellomize.ico', 'assets'),
]

hiddenimports = [
    'streamlit',
    'streamlit.web.cli',
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
    'smtplib',
    'ssl',
    'imaplib',
    'email',
    'email.mime.text',
    'email.mime.multipart',
    'database',
    'contacts_handler',
    'scheduler',
    'template_engine',
    'smtp_dispatcher',
    'tracker',
    'mx_checker',
    'ui',
    'ui.theme',
    'ui.components',
    'ui.sidebar',
    'ui.tabs',
    'ui.tabs.crm',
    'ui.tabs.studio',
    'ui.tabs.signature',
    'ui.tabs.campaigns',
    'ui.tabs.review',
    'ui.tabs.analytics',
    'dns',
    'dns.resolver',
    'cryptography',
    'cryptography.fernet',
    'keyring',
    'keyring.backends',
    'keyring.backends.Windows',
    'nh3',
]
hiddenimports += collect_submodules('streamlit')
hiddenimports += collect_submodules('streamlit_quill')
hiddenimports += collect_submodules('cryptography')
hiddenimports += collect_submodules('keyring')
hiddenimports += collect_submodules('nh3')

try:
    datas += copy_metadata('keyring')
    datas += copy_metadata('cryptography')
    datas += copy_metadata('nh3')
except Exception:
    pass


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