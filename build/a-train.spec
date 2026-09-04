# PyInstaller spec for the standalone `a-train` executable (GitHub Actions
# build workflow). Onefile console binary bundling the app package, its
# dependencies, and the static web/ client.
#
# Build locally with:  uv run pyinstaller build/a-train.spec

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent  # repo root; this spec lives in build/

datas = [(str(ROOT / "web"), "web")]
binaries = []
hiddenimports = []

# uvicorn[standard] pulls optional accelerators in via guarded imports that
# static analysis misses; bundle whichever are installed on this platform.
for package in ("uvicorn", "httptools", "websockets", "watchfiles", "dotenv", "uvloop"):
    if importlib.util.find_spec(package) is None:
        continue
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    [str(ROOT / "build" / "frozen_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="a-train",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
