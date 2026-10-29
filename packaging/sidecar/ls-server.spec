# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent.parent
hiddenimports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + [
        "psycopg",
        "psycopg_pool",
        "sqlglot",
        "sqlparse",
        "jinja2",
        "itsdangerous",
        "multipart",
    ]
)
datas = [
    (str(ROOT / "app" / "templates"), "app/templates"),
    (str(ROOT / "app" / "static"), "app/static"),
    (str(ROOT / "sql"), "sql"),
]

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "hypothesis",
        "tkinter",
        "numpy",
        "pandas",
        "PIL",
        "pptx",
        "setuptools",
        "pip",
        "wheel",
        "test",
        "tests",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ls-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ls-server",
)
