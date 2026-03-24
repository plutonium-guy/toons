from __future__ import annotations

import subprocess
import sys
import os
import platform
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def native_source() -> Path:
    return project_root() / "zig" / "toons.zig"


def library_filename() -> str:
    if sys.platform == "darwin":
        return "libtoons.dylib"
    if sys.platform == "win32":
        return "toons.dll"
    return "libtoons.so"


def build_native(output_dir: Path) -> Path:
    root = project_root()
    source = native_source()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / library_filename()

    env = os.environ.copy()
    if sys.platform == "darwin":
        env.setdefault("MACOSX_DEPLOYMENT_TARGET", "11.0")

    command = [
        "zig",
        "build-lib",
        str(source),
        "-dynamic",
        "-O",
        "ReleaseSafe",
        "-lc",
        f"-femit-bin={output_path}",
    ]
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        arch = {"arm64": "aarch64", "x86_64": "x86_64"}.get(machine, machine)
        command.extend(["-target", f"{arch}-macos.11.0"])
    subprocess.run(command, check=True, cwd=root, env=env)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Zig build did not produce a usable library at {output_path}")
    return output_path


def should_rebuild(output_dir: Path) -> bool:
    output_path = output_dir / library_filename()
    if not output_path.exists() or output_path.stat().st_size == 0:
        return True

    source = native_source()
    if not source.exists():
        return False

    return output_path.stat().st_mtime < source.stat().st_mtime
