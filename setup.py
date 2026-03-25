from __future__ import annotations

import importlib.util
from pathlib import Path

from setuptools import Distribution, setup
from setuptools.command.build_py import build_py as _build_py


def _load_build_module():
    root = Path(__file__).resolve().parent
    module_path = root / "src" / "toonz" / "_build_zig.py"
    spec = importlib.util.spec_from_file_location("toonz_build_zig", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Zig build helper from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class build_py(_build_py):
    def run(self) -> None:
        build_module = _load_build_module()
        target_dir = Path(self.build_lib) / "toonz" / "_native"
        build_module.build_native(target_dir)
        super().run()


class BinaryDistribution(Distribution):
    """Mark the distribution as platform-specific (non-pure)."""

    def has_ext_modules(self) -> bool:
        return True


setup(cmdclass={"build_py": build_py}, distclass=BinaryDistribution)
