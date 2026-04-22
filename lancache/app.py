from __future__ import annotations

import logging
import threading
from pathlib import Path

from .blocked_ips import BlockedIPRegistry
from .cache_storage import CacheStore
from .config import AppConfig, load_config, save_config
from .dns_server import DNSInterceptorServer, DNSServerThread
from .logging_utils import configure_logging
from .metrics import MetricsCollector
from .proxy_server import CacheProxyServer, ProxyServerThread
from .routing import DomainRouter
from .steam_clients import SteamClientTracker
from .windows_dns import WindowsDNSManager


class AppStartupError(RuntimeError):
    pass


class LanCacheApplication:
    def __init__(self, config: AppConfig, config_path: str | Path = "config.json") -> None:
        self.config = config
        self.config_path = Path(config_path)
        self.logger = logging.getLogger(__name__)
        self._initialize_components(config)
        self._state_lock = threading.Lock()
        self._running = False

    def _initialize_components(self, config: AppConfig) -> None:
        self.config = config
        configure_logging(config.logging)
        self.logger = logging.getLogger(__name__)
        self.metrics = MetricsCollector()
        self.router = DomainRouter(config.platform_policies)
        self.cache = CacheStore(config.cache)
        self.blocked_ips = BlockedIPRegistry(config.blocked_ips.blocked_ips)
        self.steam_clients = SteamClientTracker()
        self.windows_dns = WindowsDNSManager(config)
        self.dns_server: DNSInterceptorServer | None = None
        self.proxy_server: CacheProxyServer | None = None
        self.dns_thread: DNSServerThread | None = None
        self.proxy_thread: ProxyServerThread | None = None

    def _build_bind_error(self, service_name: str, host: str, port: int, exc: OSError) -> AppStartupError:
        setting_name = "dns.listen_port" if service_name == "DNS" else "proxy.listen_port"
        message = f"Unable to start the {service_name} server on {host}:{port}: {exc}."

        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 10013:
            message = (
                f"{message} On Windows this usually means the port is already in use or the process does not have permission "
                f"to bind it. Run the app as Administrator, stop the conflicting service, or change {setting_name} in config.json."
            )

        return AppStartupError(message)

    def _build_runtime(self) -> None:
        try:
            dns_server = DNSInterceptorServer(
                self.config.dns,
                self.router,
                self.metrics,
                self.steam_clients,
                self.blocked_ips,
            )
        except OSError as exc:
            raise self._build_bind_error("DNS", self.config.dns.listen_host, self.config.dns.listen_port, exc) from exc

        try:
            proxy_server = CacheProxyServer(
                (self.config.proxy.listen_host, self.config.proxy.listen_port),
                self.config.proxy,
                self.cache,
                self.router,
                self.metrics,
                self.steam_clients,
                self.blocked_ips,
            )
        except OSError as exc:
            dns_server.server_close()
            raise self._build_bind_error("proxy", self.config.proxy.listen_host, self.config.proxy.listen_port, exc) from exc

        self.dns_server = dns_server
        self.proxy_server = proxy_server
        self.dns_thread = DNSServerThread(self.dns_server)
        self.proxy_thread = ProxyServerThread(self.proxy_server)

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    def start(self) -> None:
        with self._state_lock:
            if self._running:
                return
            self._build_runtime()
            self._running = True

        self.logger.info("Starting LAN cache services")
        if self.config.windows_dns.enabled and self.config.windows_dns.apply_on_start:
            result = self.windows_dns.apply()
            if result.returncode != 0:
                self.logger.error("Windows DNS Server configuration failed: %s", result.stderr.strip())
            else:
                self.logger.info(result.stdout.strip())
        self.dns_thread.start()
        self.proxy_thread.start()

    def stop(self) -> None:
        with self._state_lock:
            if not self._running:
                return
            self._running = False

        self.logger.info("Stopping LAN cache services")
        if self.dns_thread and self.dns_server:
            self.dns_thread.stop()
            self.dns_thread.join(timeout=5.0)
        if self.proxy_thread and self.proxy_server:
            self.proxy_thread.stop()
            self.proxy_thread.join(timeout=5.0)
        self.dns_server = None
        self.proxy_server = None
        self.dns_thread = None
        self.proxy_thread = None

    def save_config(self, config: AppConfig) -> None:
        save_config(config, self.config_path)

    def load_config_from_disk(self) -> AppConfig:
        return load_config(self.config_path)

    def apply_config(self, config: AppConfig, save: bool = True) -> None:
        if save:
            self.save_config(config)
        was_running = self.is_running
        if was_running:
            self.stop()
        self._initialize_components(config)
        if was_running:
            self.start()

    @classmethod
    def load_default_config(cls) -> AppConfig:
        return load_config()