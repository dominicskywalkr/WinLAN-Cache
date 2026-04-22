from __future__ import annotations

import importlib
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from .app import AppStartupError, LanCacheApplication
from .config import AppConfig, load_config, set_cache_root_dir
from .windows_runtime import ensure_pywin32

if sys.platform.startswith("win"):
    ensure_pywin32()
    pywintypes = importlib.import_module("pywintypes")
    servicemanager = importlib.import_module("servicemanager")
    win32event = importlib.import_module("win32event")
    win32service = importlib.import_module("win32service")
    win32serviceutil = importlib.import_module("win32serviceutil")
else:  # pragma: no cover - service support is Windows-only
    pywintypes = None
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


SERVICE_DESCRIPTION = "Local DNS interceptor and caching proxy for Windows LAN game downloads."
_SERVICE_CONFIG_PATH = Path("config.json").resolve()
_SERVICE_CACHE_DIR: str | None = None


def _require_pywin32() -> None:
    if not sys.platform.startswith("win"):
        raise RuntimeError("Windows service support is only available on Windows")
    ensure_pywin32()


def _load_service_config(config_path: str | Path, cache_dir: str | None = None) -> AppConfig:
    config = load_config(config_path)
    if cache_dir:
        set_cache_root_dir(config, cache_dir)
    return config


def _service_executable() -> Path:
    return Path(sys.executable).resolve()


def _service_commandline(config_path: Path, cache_dir: str | None = None) -> str:
    command = ["service", "run", "--config", str(config_path)]
    if cache_dir:
        command.extend(["--cache-dir", cache_dir])
    return subprocess.list2cmdline(command)


def _configure_service_runtime(config_path: str | Path, cache_dir: str | None = None) -> AppConfig:
    global _SERVICE_CONFIG_PATH, _SERVICE_CACHE_DIR

    resolved_config_path = Path(config_path).resolve()
    _SERVICE_CONFIG_PATH = resolved_config_path
    _SERVICE_CACHE_DIR = cache_dir

    config = _load_service_config(resolved_config_path, cache_dir)
    LanCacheWindowsService._svc_name_ = config.service.service_name
    LanCacheWindowsService._svc_display_name_ = config.service.display_name
    LanCacheWindowsService._svc_description_ = SERVICE_DESCRIPTION
    return config


class ServiceRunner:
    def __init__(self, config: AppConfig, config_path: str | Path = "config.json") -> None:
        self.app = LanCacheApplication(config, config_path)
        self._stop_event = threading.Event()
        self.logger = logging.getLogger(__name__)

    def run_foreground(self) -> None:
        self.app.start()
        self.logger.info("Service runner started in foreground mode")
        try:
            while not self._stop_event.is_set():
                time.sleep(1.0)
        finally:
            self.app.stop()

    def stop(self) -> None:
        self._stop_event.set()


if sys.platform.startswith("win"):
    class LanCacheWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = "WindowsLanCache"
        _svc_display_name_ = "Windows LAN Cache"
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args):
            super().__init__(args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            config = _load_service_config(_SERVICE_CONFIG_PATH, _SERVICE_CACHE_DIR)
            self.runner = ServiceRunner(config, _SERVICE_CONFIG_PATH)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.runner.stop()
            win32event.SetEvent(self.stop_event)

        def SvcDoRun(self):
            servicemanager.LogInfoMsg(f"{self._svc_name_} service starting")
            try:
                self.runner.run_foreground()
            except AppStartupError as exc:
                servicemanager.LogErrorMsg(str(exc))
                raise
else:
    class LanCacheWindowsService:  # pragma: no cover - runtime stub when pywin32 is unavailable
        _svc_name_ = "WindowsLanCache"
        _svc_display_name_ = "Windows LAN Cache"
        _svc_description_ = SERVICE_DESCRIPTION


def install_service(config_path: str | Path = "config.json", cache_dir: str | None = None) -> str:
    _require_pywin32()
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Install the Windows service from the packaged executable so the service points at a stable binary")

    config = _configure_service_runtime(config_path, cache_dir)
    win32serviceutil.InstallService(
        f"{__name__}.LanCacheWindowsService",
        config.service.service_name,
        config.service.display_name,
        startType=win32service.SERVICE_AUTO_START if config.service.auto_start else win32service.SERVICE_DEMAND_START,
        exeName=str(_service_executable()),
        exeArgs=_service_commandline(_SERVICE_CONFIG_PATH, cache_dir),
        description=SERVICE_DESCRIPTION,
    )
    return f"Installed Windows service '{config.service.service_name}'."


def remove_service(config_path: str | Path = "config.json", cache_dir: str | None = None) -> str:
    _require_pywin32()
    config = _configure_service_runtime(config_path, cache_dir)
    win32serviceutil.RemoveService(config.service.service_name)
    return f"Removed Windows service '{config.service.service_name}'."


def start_service(config_path: str | Path = "config.json", cache_dir: str | None = None) -> str:
    _require_pywin32()
    config = _configure_service_runtime(config_path, cache_dir)
    win32serviceutil.StartService(config.service.service_name)
    return f"Started Windows service '{config.service.service_name}'."


def stop_service(config_path: str | Path = "config.json", cache_dir: str | None = None) -> str:
    _require_pywin32()
    config = _configure_service_runtime(config_path, cache_dir)
    win32serviceutil.StopService(config.service.service_name)
    return f"Stopped Windows service '{config.service.service_name}'."


def restart_service(config_path: str | Path = "config.json", cache_dir: str | None = None) -> str:
    stop_service(config_path, cache_dir)
    return start_service(config_path, cache_dir)


def get_service_status(config_path: str | Path = "config.json", cache_dir: str | None = None) -> str:
    _require_pywin32()
    config = _configure_service_runtime(config_path, cache_dir)
    _, current_state, _, _, _, _, _ = win32serviceutil.QueryServiceStatus(config.service.service_name)
    labels = {
        win32service.SERVICE_STOPPED: "stopped",
        win32service.SERVICE_START_PENDING: "start pending",
        win32service.SERVICE_STOP_PENDING: "stop pending",
        win32service.SERVICE_RUNNING: "running",
        win32service.SERVICE_CONTINUE_PENDING: "continue pending",
        win32service.SERVICE_PAUSE_PENDING: "pause pending",
        win32service.SERVICE_PAUSED: "paused",
    }
    status = labels.get(current_state, f"state {current_state}")
    return f"Windows service '{config.service.service_name}' is {status}."


def run_service_host(config_path: str | Path = "config.json", cache_dir: str | None = None) -> None:
    _require_pywin32()
    _configure_service_runtime(config_path, cache_dir)
    try:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(LanCacheWindowsService)
        servicemanager.StartServiceCtrlDispatcher()
    except win32service.error as exc:
        raise RuntimeError("The 'service run' command is reserved for the Windows Service Control Manager") from exc


def handle_service_command(action: str, config_path: str | Path = "config.json", cache_dir: str | None = None) -> str | None:
    if action == "run":
        run_service_host(config_path, cache_dir)
        return None
    if action == "install":
        return install_service(config_path, cache_dir)
    if action == "remove":
        return remove_service(config_path, cache_dir)
    if action == "start":
        return start_service(config_path, cache_dir)
    if action == "stop":
        return stop_service(config_path, cache_dir)
    if action == "restart":
        return restart_service(config_path, cache_dir)
    if action == "status":
        return get_service_status(config_path, cache_dir)
    raise ValueError(f"Unsupported service action: {action}")