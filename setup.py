from __future__ import annotations

import importlib.util
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

try:
    from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
except ImportError:  # pragma: no cover
    _bdist_wheel = None


def _load_build_module():
    root = Path(__file__).resolve().parent
    module_path = root / "src" / "toons" / "_build_zig.py"
    spec = importlib.util.spec_from_file_location("toons_build_zig", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Zig build helper from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class build_py(_build_py):
    def run(self) -> None:
        build_module = _load_build_module()
        target_dir = Path(self.build_lib) / "toons" / "_native"
        build_module.build_native(target_dir)
        super().run()


cmdclass = {"build_py": build_py}

if _bdist_wheel is not None:
    class bdist_wheel(_bdist_wheel):
        def finalize_options(self) -> None:
            super().finalize_options()
            self.root_is_pure = False

    cmdclass["bdist_wheel"] = bdist_wheel


setup(cmdclass=cmdclass)
