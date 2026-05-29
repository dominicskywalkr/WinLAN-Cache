from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .blocked_ips import BlockedIPConfig


@dataclass(slots=True)
class PlatformPolicy:
    name: str
    enabled: bool = True
    dns_rewrite_patterns: list[str] = field(default_factory=list)
    cacheable_http_patterns: list[str] = field(default_factory=list)
    passthrough_patterns: list[str] = field(default_factory=list)
    https_only_patterns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SteamPreloadItem:
    name: str
    app_id: int | None = None
    urls: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SteamCMDConfig:
    executable_path: str = "steamcmd.exe"
    username: str = "anonymous"
    password: str | None = None
    download_root: str = "cache\\steamcmd"
    validate_downloads: bool = False


def default_platform_policies() -> list[PlatformPolicy]:
    return [
        PlatformPolicy(
            name="steam",
            dns_rewrite_patterns=["*.steamcontent.com", "*.steamstatic.com"],
            cacheable_http_patterns=["*.steamcontent.com", "*.steamstatic.com"],
            passthrough_patterns=["*.steampowered.com"],
            https_only_patterns=["store.steampowered.com", "api.steampowered.com"],
        ),
        PlatformPolicy(
            name="epic",
            dns_rewrite_patterns=["*.epicgames-download1.akamaized.net", "*.epicgamescdn.com"],
            cacheable_http_patterns=["*.epicgames-download1.akamaized.net", "*.epicgamescdn.com"],
            passthrough_patterns=["*.epicgames.com"],
            https_only_patterns=["launcher-public-service-prod06.ol.epicgames.com"],
        ),
        PlatformPolicy(
            name="blizzard",
            dns_rewrite_patterns=["*.blizzard.nefficient.co.kr", "*.cdn.blizzard.com"],
            cacheable_http_patterns=["*.blizzard.nefficient.co.kr", "*.cdn.blizzard.com"],
            passthrough_patterns=["*.battle.net", "*.blizzard.com"],
            https_only_patterns=["us.actual.battle.net", "oauth.battle.net"],
        ),
    ]


@dataclass(slots=True)
class DNSConfig:
    listen_host: str = "0.0.0.0"
    listen_port: int = 53
    upstream_host: str = "1.1.1.1"
    upstream_port: int = 53
    response_ttl: int = 60
    cache_ipv4: str = "127.0.0.1"
    cache_ipv6: str | None = None
    log_queries: bool = False


@dataclass(slots=True)
class WindowsDNSConfig:
    enabled: bool = False
    server_host: str = "localhost"
    apply_on_start: bool = False
    script_path: str = "windows-dns-server.ps1"
    replication_scope: str = "Forest"
    zone_file_directory: str | None = None


@dataclass(slots=True)
class ProxyConfig:
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    upstream_scheme: str = "http"
    connect_timeout: float = 15.0
    read_timeout: float = 120.0
    chunk_size: int = 1024 * 1024
    max_cacheable_object_bytes: int = 250 * 1024 * 1024 * 1024
    user_agent: str = "WindowsLanCache/0.1"


@dataclass(slots=True)
class CacheConfig:
    root_dir: str = "cache"
    metadata_db: str = "cache\\metadata.sqlite3"
    max_size_gb: int = 500
    eviction_target_percent: int = 90


@dataclass(slots=True)
class LoggingConfig:
    log_dir: str = "logs"
    level: str = "INFO"
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5


@dataclass(slots=True)
class ServiceConfig:
    service_name: str = "WindowsLanCache"
    display_name: str = "Windows LAN Cache"
    auto_start: bool = True


@dataclass(slots=True)
class SafetyConfig:
    confirm_before_apply: bool = False


@dataclass(slots=True)
class NetworkConfig:
    preferred_interface_alias: str | None = None


@dataclass(slots=True)
class AppConfig:
    dns: DNSConfig = field(default_factory=DNSConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    steamcmd: SteamCMDConfig = field(default_factory=SteamCMDConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    windows_dns: WindowsDNSConfig = field(default_factory=WindowsDNSConfig)
    blocked_ips: BlockedIPConfig = field(default_factory=BlockedIPConfig)
    platform_policies: list[PlatformPolicy] = field(default_factory=default_platform_policies)
    steam_preload_items: list[SteamPreloadItem] = field(default_factory=list)


def _build_steam_preload_items(raw: dict) -> list[SteamPreloadItem]:
    items: list[SteamPreloadItem] = []
    for item in raw.get("steam_preload_items", []):
        items.append(
            SteamPreloadItem(
                name=item.get("name", "Unnamed Steam Item"),
                app_id=item.get("app_id"),
                urls=list(item.get("urls", [])),
            )
        )
    return items


def _build_platform_policies(raw: dict) -> list[PlatformPolicy]:
    policies = raw.get("platform_policies")
    if policies:
        return [PlatformPolicy(**policy) for policy in policies]

    legacy_patterns = raw.get("dns", {}).get("intercept_patterns")
    if legacy_patterns:
        return [
            PlatformPolicy(
                name="legacy",
                dns_rewrite_patterns=list(legacy_patterns),
                cacheable_http_patterns=list(legacy_patterns),
            )
        ]
    return default_platform_policies()


def _build_config(raw: dict) -> AppConfig:
    dns_raw = dict(raw.get("dns", {}))
    dns_raw.pop("intercept_patterns", None)
    cache_config = CacheConfig(**raw.get("cache", {}))
    steamcmd_config = SteamCMDConfig(**raw.get("steamcmd", {}))
    if not Path(steamcmd_config.download_root).is_absolute():
        steamcmd_config.download_root = str(Path(cache_config.root_dir) / "steamcmd")
    return AppConfig(
        dns=DNSConfig(**dns_raw),
        proxy=ProxyConfig(**raw.get("proxy", {})),
        cache=cache_config,
        steamcmd=steamcmd_config,
        logging=LoggingConfig(**raw.get("logging", {})),
        service=ServiceConfig(**raw.get("service", {})),
        safety=SafetyConfig(**raw.get("safety", {})),
        network=NetworkConfig(**raw.get("network", {})),
        windows_dns=WindowsDNSConfig(**raw.get("windows_dns", {})),
        blocked_ips=BlockedIPConfig(**raw.get("blocked_ips", {})),
        platform_policies=_build_platform_policies(raw),
        steam_preload_items=_build_steam_preload_items(raw),
    )


def config_from_dict(raw: dict) -> AppConfig:
    return _build_config(raw)


def config_to_dict(config: AppConfig) -> dict:
    return asdict(config)


def set_cache_root_dir(config: AppConfig, root_dir: str | Path) -> AppConfig:
    cache_root = Path(root_dir)
    if not str(cache_root).strip():
        raise ValueError("Cache directory cannot be empty")
    config.cache.root_dir = str(cache_root)
    config.cache.metadata_db = str(cache_root / "metadata.sqlite3")
    config.steamcmd.download_root = str(cache_root / "steamcmd")
    return config


def load_config(config_path: str | Path = "config.json") -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        config = AppConfig()
        save_config(config, path)
        return config

    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return _build_config(raw)


def save_config(config: AppConfig, config_path: str | Path = "config.json") -> None:
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config_to_dict(config), handle, indent=2)