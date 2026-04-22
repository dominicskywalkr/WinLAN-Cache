from __future__ import annotations

import importlib.util
import sys


PYWIN32_MODULES = (
    "pywintypes",
    "pythoncom",
    "servicemanager",
    "win32api",
    "win32event",
    "win32service",
    "win32serviceutil",
    "win32timezone",
)


def missing_pywin32_modules() -> list[str]:
    if not sys.platform.startswith("win"):
        return []
    return [module_name for module_name in PYWIN32_MODULES if importlib.util.find_spec(module_name) is None]


def ensure_pywin32() -> None:
    missing_modules = missing_pywin32_modules()
    if not missing_modules:
        return

    missing_list = ", ".join(missing_modules)
    raise RuntimeError(
        "pywin32 is required on Windows for Windows LAN Cache. "
        "Install it in the active environment with 'python -m pip install pywin32'. "
        f"Missing modules: {missing_list}"
    )