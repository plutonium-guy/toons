from __future__ import annotations

import shutil
import subprocess
import sys
import os
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
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / library_filename()

    env = os.environ.copy()
    if sys.platform == "darwin":
        env.setdefault("MACOSX_DEPLOYMENT_TARGET", "11.0")

    command = [
        *_zig_command(),
        "build",
        "--prefix", str(output_dir),
        "-Doptimize=ReleaseSafe",
    ]
    subprocess.run(command, check=True, cwd=root, env=env)

    # zig build installs to <prefix>/lib/<filename>
    installed = output_dir / "lib" / library_filename()
    if installed.exists():
        shutil.move(str(installed), str(output_path))
        lib_dir = output_dir / "lib"
        if lib_dir.is_dir() and not any(lib_dir.iterdir()):
            lib_dir.rmdir()

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
    # Also check build.zig
    build_zig = project_root() / "build.zig"
    if build_zig.exists() and lib_mtime < build_zig.stat().st_mtime:
        return True
    return False
