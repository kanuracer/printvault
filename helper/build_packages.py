#!/usr/bin/env python3
"""Build portable PrintVault Helper bundles for Linux and Windows.

Both bundles contain the same stdlib-only zipapp and require Python 3.10+ on
that host. They deliberately do not embed configuration or credentials.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "dist"


def _write_launcher(directory: Path, platform: str) -> None:
    if platform == "linux":
        launcher = directory / "printvault-helper"
        launcher.write_text(
            "#!/usr/bin/env sh\n"
            "set -eu\n"
            "DIR=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\n"
            "exec python3 \"$DIR/printvault-helper.pyz\" --config \"$DIR/config.json\" \"$@\"\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    else:
        (directory / "printvault-helper.bat").write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            "py -3 \"%~dp0printvault-helper.pyz\" --config \"%~dp0config.json\" %*\r\n",
            encoding="utf-8",
        )


def _create_zipapp(destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="printvault-helper-") as temp:
        staging = Path(temp)
        shutil.copytree(ROOT / "printvault_helper", staging / "printvault_helper", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (staging / "__main__.py").write_text(
            "from printvault_helper.cli import main\nraise SystemExit(main())\n",
            encoding="utf-8",
        )
        zipapp.create_archive(staging, destination, interpreter="/usr/bin/env python3")


def build(output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    built: list[Path] = []
    for platform in ("linux", "windows"):
        package = output / f"printvault-helper-{platform}"
        shutil.rmtree(package, ignore_errors=True)
        package.mkdir()
        _create_zipapp(package / "printvault-helper.pyz")
        shutil.copy2(ROOT / "config.example.json", package / "config.example.json")
        if platform == "windows":
            shutil.copy2(ROOT / "setup-windows.ps1", package / "setup-windows.ps1")
        _write_launcher(package, platform)
        shutil.make_archive(str(package), "zip", root_dir=package)
        built.append(package.with_suffix(".zip"))
    return built


def main() -> int:
    parser = argparse.ArgumentParser(description="Build portable PrintVault Helper bundles.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    for archive in build(args.output):
        print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
