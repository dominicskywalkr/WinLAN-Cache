from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import AppConfig


class WindowsDNSManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logger = logging.getLogger(__name__)

    def export_script(self, script_path: str | Path | None = None) -> Path:
        path = Path(script_path or self.config.windows_dns.script_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._build_script(), encoding="utf-8")
        return path

    def apply(self, script_path: str | Path | None = None) -> subprocess.CompletedProcess[str]:
        path = self.export_script(script_path)
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(path),
            "-DnsServer",
            self.config.windows_dns.server_host,
        ]
        self.logger.info("Applying Windows DNS Server configuration using %s", path)
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def _build_script(self) -> str:
        ttl = int(self.config.dns.response_ttl)
        cache_ipv4 = self.config.dns.cache_ipv4
        cache_ipv6 = self.config.dns.cache_ipv6
        replication_scope = self.config.windows_dns.replication_scope
        zone_file_directory = self.config.windows_dns.zone_file_directory
        zone_entries = self._collect_zone_entries()

        zone_lines = []
        for zone_name, platform_name in zone_entries:
            zone_lines.append(
                "    @{ Zone = '" + zone_name + "'; Platform = '" + platform_name + "' }"
            )
        zones_block = "@(\n" + "\n".join(zone_lines) + "\n)" if zone_lines else "@()"

        script_lines = [
            "param(",
            "    [string]$DnsServer = 'localhost'",
            ")",
            "$ErrorActionPreference = 'Stop'",
            "Import-Module DnsServer",
            "$ttl = [TimeSpan]::FromSeconds(" + str(ttl) + ")",
            "$cacheIpv4 = '" + cache_ipv4 + "'",
            "$cacheIpv6 = " + ("'" + cache_ipv6 + "'" if cache_ipv6 else "$null"),
            "$zones = " + zones_block,
            "foreach ($zone in $zones) {",
            "    if (-not (Get-DnsServerZone -ComputerName $DnsServer -Name $zone.Zone -ErrorAction SilentlyContinue)) {",
        ]

        if zone_file_directory:
            script_lines.extend(
                [
                    "        Add-DnsServerPrimaryZone -ComputerName $DnsServer -Name $zone.Zone -ZoneFile ('" + zone_file_directory.replace("'", "''") + "\\' + $zone.Zone + '.dns') | Out-Null",
                ]
            )
        else:
            script_lines.extend(
                [
                    "        Add-DnsServerPrimaryZone -ComputerName $DnsServer -Name $zone.Zone -ReplicationScope '" + replication_scope + "' | Out-Null",
                ]
            )

        script_lines.extend(
            [
                "    }",
                "    $existingA = Get-DnsServerResourceRecord -ComputerName $DnsServer -ZoneName $zone.Zone -Name '*' -RRType A -ErrorAction SilentlyContinue",
                "    if ($existingA) { $existingA | Remove-DnsServerResourceRecord -ComputerName $DnsServer -ZoneName $zone.Zone -Force }",
                "    Add-DnsServerResourceRecordA -ComputerName $DnsServer -ZoneName $zone.Zone -Name '*' -IPv4Address $cacheIpv4 -TimeToLive $ttl | Out-Null",
                "    if ($cacheIpv6) {",
                "        $existingAAAA = Get-DnsServerResourceRecord -ComputerName $DnsServer -ZoneName $zone.Zone -Name '*' -RRType AAAA -ErrorAction SilentlyContinue",
                "        if ($existingAAAA) { $existingAAAA | Remove-DnsServerResourceRecord -ComputerName $DnsServer -ZoneName $zone.Zone -Force }",
                "        Add-DnsServerResourceRecordAAAA -ComputerName $DnsServer -ZoneName $zone.Zone -Name '*' -IPv6Address $cacheIpv6 -TimeToLive $ttl | Out-Null",
                "    }",
                "}",
                "Write-Host ('Configured ' + $zones.Count + ' Windows DNS zones for LAN cache redirection.')",
            ]
        )
        return "\n".join(script_lines) + "\n"

    def _collect_zone_entries(self) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for policy in self.config.platform_policies:
            if not policy.enabled:
                continue
            for pattern in policy.dns_rewrite_patterns:
                zone_name = self._zone_name_from_pattern(pattern)
                if zone_name in seen:
                    continue
                seen.add(zone_name)
                entries.append((zone_name, policy.name))
        return entries

    @staticmethod
    def _zone_name_from_pattern(pattern: str) -> str:
        normalized = pattern.strip().lower()
        if normalized.startswith("*."):
            return normalized[2:]
        return normalized