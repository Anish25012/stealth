"""Build the desktop app and its installer for the current OS.

    python packaging/build.py              # app folder plus installer
    python packaging/build.py --app-only   # skip the installer step

Outputs go to dist/. The installer's path is also written to dist/artifact.txt for CI.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT / "src"))

from stealth import __version__  # noqa: E402


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=ROOT)  # noqa: S603


def build_app() -> Path:
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD),
            "packaging/stealth.spec",
        ]
    )
    if sys.platform == "darwin":
        return DIST / "Stealth.app"
    return DIST / "Stealth"


def find_iscc() -> str:
    candidates = [
        shutil.which("iscc"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("Inno Setup 6 isn't installed. Get it from https://jrsoftware.org/isdl.php")


def windows_installer(app: Path) -> Path:
    run(
        [
            find_iscc(),
            f"/DAppVersion={__version__}",
            f"/DSourceDir={app}",
            str(ROOT / "packaging" / "installer.iss"),
        ]
    )
    return DIST / f"Stealth-{__version__}-windows-setup.exe"


def mac_dmg(app: Path) -> Path:
    staging = BUILD / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    # symlinks=True keeps the bundle's framework links intact, which code signing relies on.
    shutil.copytree(app, staging / app.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    arch = platform.machine()
    out = DIST / f"Stealth-{__version__}-macos-{arch}.dmg"
    run(
        [
            "hdiutil",
            "create",
            "-volname",
            "Stealth",
            "-srcfolder",
            str(staging),
            "-ov",
            "-format",
            "UDZO",
            str(out),
        ]
    )
    return out


def linux_tarball(app: Path) -> Path:
    # Best effort until an AppImage is worth the effort: see the plan's packaging notes.
    out = DIST / f"Stealth-{__version__}-linux-{platform.machine()}.tar.gz"
    with tarfile.open(out, "w:gz") as archive:
        archive.add(app, arcname="Stealth")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-only", action="store_true")
    args = parser.parse_args()

    app = build_app()
    if args.app_only:
        print(f"Built {app}")
        return
    if sys.platform == "win32":
        artifact = windows_installer(app)
    elif sys.platform == "darwin":
        artifact = mac_dmg(app)
    else:
        artifact = linux_tarball(app)
    (DIST / "artifact.txt").write_text(str(artifact), "utf-8")
    print(f"Built {artifact}")


if __name__ == "__main__":
    main()
