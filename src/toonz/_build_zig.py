from __future__ import annotations

import shutil
import subprocess
import sys
import os
import platform
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def native_sources() -> list[Path]:
    zig_dir = project_root() / "zig"
    return [zig_dir / "toonz.zig", zig_dir / "text_format.zig"]


def _zig_command() -> list[str]:
    if shutil.which("zig"):
        return ["zig"]
    return [sys.executable, "-m", "ziglang"]


def library_filename() -> str:
    if sys.platform == "darwin":
        return "libtoonz.dylib"
    if sys.platform == "win32":
        return "toonz.dll"
    return "libtoonz.so"


def build_native(output_dir: Path) -> Path:
    root = project_root()
    sources = native_sources()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / library_filename()

    env = os.environ.copy()
    if sys.platform == "darwin":
        env.setdefault("MACOSX_DEPLOYMENT_TARGET", "11.0")

    command = [
        *_zig_command(),
        "build-lib",
        str(sources[0]),
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
    elif sys.platform == "linux":
        machine = platform.machine().lower()
        arch = {"aarch64": "aarch64", "x86_64": "x86_64"}.get(machine, machine)
        command.extend(["-target", f"{arch}-linux-gnu.2.28"])
    subprocess.run(command, check=True, cwd=root, env=env)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Zig build did not produce a usable library at {output_path}")
    return output_path


def should_rebuild(output_dir: Path) -> bool:
    output_path = output_dir / library_filename()
    if not output_path.exists() or output_path.stat().st_size == 0:
        return True

    lib_mtime = output_path.stat().st_mtime
    for source in native_sources():
        if source.exists() and lib_mtime < source.stat().st_mtime:
            return True
    return False
