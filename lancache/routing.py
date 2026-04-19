from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

from .config import PlatformPolicy


@dataclass(slots=True)
class DNSRouteDecision:
    action: str
    matched_pattern: str | None
    platform: str
    record_source: str


@dataclass(slots=True)
class ProxyRouteDecision:
    action: str
    matched_pattern: str | None
    platform: str
    upstream_scheme: str


class DomainRouter:
    def __init__(self, policies: list[PlatformPolicy]) -> None:
        self._policies = policies

    def resolve_dns(self, hostname: str) -> DNSRouteDecision:
        normalized = hostname.rstrip(".").lower()
        for policy in self._policies:
            if not policy.enabled:
                continue
            for pattern in self._normalize_patterns(policy.dns_rewrite_patterns):
                if fnmatch(normalized, pattern):
                    return DNSRouteDecision("rewrite", pattern, policy.name, "cache")
            for pattern in self._normalize_patterns(policy.https_only_patterns):
                if fnmatch(normalized, pattern):
                    return DNSRouteDecision("passthrough", pattern, policy.name, "https-only")
            for pattern in self._normalize_patterns(policy.passthrough_patterns):
                if fnmatch(normalized, pattern):
                    return DNSRouteDecision("passthrough", pattern, policy.name, "passthrough")
        return DNSRouteDecision("forward", None, "generic", "upstream")

    def resolve_proxy(self, hostname: str, requested_scheme: str | None) -> ProxyRouteDecision:
        normalized = hostname.rstrip(".").lower()
        normalized_scheme = (requested_scheme or "").lower()
        for policy in self._policies:
            if not policy.enabled:
                continue
            for pattern in self._normalize_patterns(policy.https_only_patterns):
                if fnmatch(normalized, pattern):
                    return ProxyRouteDecision("passthrough", pattern, policy.name, "https")
            for pattern in self._normalize_patterns(policy.cacheable_http_patterns):
                if fnmatch(normalized, pattern):
                    if normalized_scheme == "https":
                        return ProxyRouteDecision("passthrough", pattern, policy.name, "https")
                    return ProxyRouteDecision("cache", pattern, policy.name, "http")
            for pattern in self._normalize_patterns(policy.passthrough_patterns):
                if fnmatch(normalized, pattern):
                    return ProxyRouteDecision(
                        "passthrough",
                        pattern,
                        policy.name,
                        normalized_scheme or "https",
                    )
        return ProxyRouteDecision("passthrough", None, "generic", normalized_scheme or "http")

    @staticmethod
    def _normalize_patterns(patterns: list[str]) -> list[str]:
        return [pattern.lower() for pattern in patterns]