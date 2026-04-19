from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(slots=True)
class SteamClientEntry:
    ip_address: str
    first_seen_at: float
    last_seen_at: float
    active_for_seconds: int
    idle_for_seconds: int
    request_count: int
    last_hostname: str | None
    sources: tuple[str, ...]


class SteamClientTracker:
    def __init__(self, active_window_seconds: int = 300) -> None:
        self.active_window_seconds = max(30, int(active_window_seconds))
        self._lock = threading.Lock()
        self._clients: dict[str, dict[str, object]] = {}

    def record_activity(self, ip_address: str, hostname: str | None = None, source: str = "proxy") -> None:
        normalized_ip = (ip_address or "").strip()
        if not normalized_ip:
            return

        now = time.time()
        normalized_source = source.strip().lower() or "proxy"
        with self._lock:
            record = self._clients.get(normalized_ip)
            if record is None or now - float(record["last_seen_at"]) > self.active_window_seconds:
                record = {
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "request_count": 0,
                    "last_hostname": None,
                    "sources": set(),
                }
                self._clients[normalized_ip] = record

            record["last_seen_at"] = now
            record["request_count"] = int(record["request_count"]) + 1
            if hostname:
                record["last_hostname"] = hostname
            sources = record["sources"]
            if isinstance(sources, set):
                sources.add(normalized_source)

    def list_active_clients(self, now: float | None = None) -> list[SteamClientEntry]:
        snapshot_time = now or time.time()
        active_clients: list[SteamClientEntry] = []
        with self._lock:
            stale_ips = [
                ip_address
                for ip_address, record in self._clients.items()
                if snapshot_time - float(record["last_seen_at"]) > self.active_window_seconds
            ]
            for ip_address in stale_ips:
                self._clients.pop(ip_address, None)

            for ip_address, record in self._clients.items():
                first_seen_at = float(record["first_seen_at"])
                last_seen_at = float(record["last_seen_at"])
                active_clients.append(
                    SteamClientEntry(
                        ip_address=ip_address,
                        first_seen_at=first_seen_at,
                        last_seen_at=last_seen_at,
                        active_for_seconds=max(0, int(snapshot_time - first_seen_at)),
                        idle_for_seconds=max(0, int(snapshot_time - last_seen_at)),
                        request_count=int(record["request_count"]),
                        last_hostname=record["last_hostname"] if isinstance(record["last_hostname"], str) else None,
                        sources=tuple(sorted(record["sources"])) if isinstance(record["sources"], set) else tuple(),
                    )
                )

        return sorted(active_clients, key=lambda item: item.last_seen_at, reverse=True)