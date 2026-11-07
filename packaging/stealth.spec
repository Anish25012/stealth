# PyInstaller build for the desktop app. Run through packaging/build.py, not directly.
#
# Onedir, not onefile: onefile unpacks to a temp folder on every launch, which is slower and
# trips more antivirus heuristics.
# ruff: noqa
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
ICON = ROOT / "packaging" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")

datas = (
    collect_data_files("stealth")  # broker list, schema, letter templates, migrations
    + collect_data_files("stealth_app")  # tray icon
    + collect_data_files("tzdata")  # Windows has no system time zone database
    # The scan's browser driver (a small node runtime). Browsers aren't bundled: the scan uses
    # the Edge or Chrome already installed.
    + collect_data_files("playwright", include_py_files=False)
)
hiddenimports = (
    collect_submodules("uvicorn")  # picks its loop and protocol modules at runtime
    + collect_submodules("keyring.backends")  # found through entry points
    + collect_submodules("stealth_app")
    + collect_submodules("stealth")
    + collect_submodules("playwright")  # imported only when the search window opens
)

analysis = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "mypy", "ruff", "IPython"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Stealth",
    icon=str(ICON),
    console=False,
    upx=False,  # UPX-packed binaries are a common antivirus false positive
    codesign_identity=None,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name="Stealth", upx=False)

if sys.platform == "darwin":
    from stealth import __version__

    app = BUNDLE(
        coll,
        name="Stealth.app",
        icon=str(ICON),
        bundle_identifier="io.github.anish25012.stealth",
        version=__version__,
        info_plist={
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
