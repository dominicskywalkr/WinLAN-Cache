from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .cache_storage import CacheStore
from .config import ProxyConfig, SteamCMDConfig, SteamPreloadItem


@dataclass(slots=True)
class PreloadResult:
    item_name: str
    app_id: int | None
    status: str
    bytes_downloaded: int
    message: str


class CachePreloader:
    def __init__(self, cache: CacheStore, steamcmd_config: SteamCMDConfig, proxy_config: ProxyConfig) -> None:
        self.cache = cache
        self.steamcmd_config = steamcmd_config
        self.proxy_config = proxy_config

    def preload_items(self, items: list[SteamPreloadItem]) -> list[PreloadResult]:
        results: list[PreloadResult] = []
        for item in items:
            try:
                results.append(self.preload_item(item))
            except Exception as exc:
                results.append(
                    PreloadResult(
                        item_name=item.name,
                        app_id=item.app_id,
                        status="failed",
                        bytes_downloaded=0,
                        message=str(exc),
                    )
                )
        return results

    def preload_item(self, item: SteamPreloadItem, output_callback: Callable[[str], None] | None = None) -> PreloadResult:
        if item.app_id is None or item.app_id <= 0:
            raise ValueError("Steam app ID must be a positive integer")

        steamcmd_path, runtime_dir = self._prepare_runtime_executable()
        install_dir = Path(self.steamcmd_config.download_root) / str(item.app_id)
        install_dir.mkdir(parents=True, exist_ok=True)

        command = [
            steamcmd_path,
            "+@ShutdownOnFailedCommand",
            "1",
            "+@NoPromptForPassword",
            "1",
            "+force_install_dir",
            str(install_dir),
            "+login",
            self.steamcmd_config.username,
        ]
        if self.steamcmd_config.username != "anonymous":
            command.append(self.steamcmd_config.password or "")
        app_update_value = str(item.app_id)
        if self.steamcmd_config.validate_downloads:
            app_update_value = f"{app_update_value} validate"
        command.extend(["+app_update", app_update_value, "+quit"])

        env = self._build_environment()
        self._emit(output_callback, f"cwd> {runtime_dir}")
        self._emit(output_callback, f"cmd> {' '.join(command)}")

        output_lines: list[str] = []
        with self.cache.warmup_session(item.app_id, item.name, str(install_dir)):
            with subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                cwd=runtime_dir,
            ) as process:
                if process.stdout is not None:
                    for raw_line in process.stdout:
                        line = raw_line.rstrip()
                        output_lines.append(line)
                        self._emit(output_callback, line)
                return_code = process.wait()

        combined_output = "\n".join(line for line in output_lines if line).strip()
        self._emit(output_callback, f"exit> {return_code}")
        if return_code != 0:
            raise ValueError(combined_output or f"SteamCMD exited with code {return_code}")

        bytes_downloaded = self._directory_size(install_dir)
        message = combined_output.splitlines()[-1] if combined_output else "SteamCMD completed successfully"
        return PreloadResult(
            item_name=item.name,
            app_id=item.app_id,
            status="downloaded",
            bytes_downloaded=bytes_downloaded,
            message=message,
        )

    def _resolve_executable(self) -> Path:
        executable_path = self.steamcmd_config.executable_path.strip()
        if not executable_path:
            raise ValueError("steamcmd.executable_path is empty")
        resolved_path = shutil.which(executable_path) if not Path(executable_path).is_file() else executable_path
        if not resolved_path:
            raise ValueError(f"SteamCMD executable not found: {executable_path}")
        return Path(resolved_path).resolve()

    def _prepare_runtime_executable(self) -> tuple[str, str]:
        source_path = self._resolve_executable()
        runtime_dir = Path(self.steamcmd_config.download_root) / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        runtime_path = runtime_dir / source_path.name
        if source_path != runtime_path:
            needs_copy = True
            if runtime_path.exists():
                source_stat = source_path.stat()
                runtime_stat = runtime_path.stat()
                needs_copy = (
                    source_stat.st_size != runtime_stat.st_size
                    or int(source_stat.st_mtime) != int(runtime_stat.st_mtime)
                )
            if needs_copy:
                shutil.copy2(source_path, runtime_path)

        return str(runtime_path), str(runtime_dir)

    def _build_environment(self) -> dict[str, str]:
        proxy_host = self.proxy_config.listen_host
        if proxy_host in {"0.0.0.0", "::"}:
            proxy_host = "127.0.0.1"
        proxy_url = f"http://{proxy_host}:{self.proxy_config.listen_port}"
        environment = os.environ.copy()
        environment["HTTP_PROXY"] = proxy_url
        environment["HTTPS_PROXY"] = proxy_url
        environment["ALL_PROXY"] = proxy_url
        environment["http_proxy"] = proxy_url
        environment["https_proxy"] = proxy_url
        environment["all_proxy"] = proxy_url
        return environment

    def _directory_size(self, path: Path) -> int:
        total = 0
        for child in path.rglob("*"):
            if child.is_file():
                total += child.stat().st_size
        return total

    def _emit(self, output_callback: Callable[[str], None] | None, message: str) -> None:
        if output_callback is not None:
            output_callback(message)