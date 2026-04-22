from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
from threading import RLock


@dataclass(slots=True)
class BlockedIPConfig:
    blocked_ips: list[str] = field(default_factory=list)


class BlockedIPRegistry:
    def __init__(self, blocked_ips: list[str] | None = None) -> None:
        self._lock = RLock()
        self._blocked_ips: set[str] = set()
        if blocked_ips:
            self.replace_all(blocked_ips)

    @staticmethod
    def normalize_ip(value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("IP address is required")
        try:
            return str(ip_address(candidate))
        except ValueError as exc:
            raise ValueError(f"Invalid IP address: {value}") from exc

    def is_blocked(self, ip_value: str | None) -> bool:
        if not ip_value:
            return False
        try:
            normalized_ip = self.normalize_ip(ip_value)
        except ValueError:
            return False
        with self._lock:
            return normalized_ip in self._blocked_ips

    def get_all(self) -> list[str]:
        with self._lock:
            return sorted(self._blocked_ips)

    def add(self, ip_value: str) -> str:
        normalized_ip = self.normalize_ip(ip_value)
        with self._lock:
            self._blocked_ips.add(normalized_ip)
        return normalized_ip

    def remove(self, ip_value: str) -> str:
        normalized_ip = self.normalize_ip(ip_value)
        with self._lock:
            self._blocked_ips.remove(normalized_ip)
        return normalized_ip

    def replace_all(self, blocked_ips: list[str]) -> None:
        normalized_ips = {self.normalize_ip(ip_value) for ip_value in blocked_ips}
        with self._lock:
            self._blocked_ips = normalized_ips