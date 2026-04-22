from __future__ import annotations

import logging
import socket
import socketserver
import threading

from .blocked_ips import BlockedIPRegistry
from .config import DNSConfig
from .dns_protocol import TYPE_A, TYPE_AAAA, build_address_response, parse_query
from .metrics import MetricsCollector
from .routing import DomainRouter
from .steam_clients import SteamClientTracker


class _DNSHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        payload, sock = self.request
        server: DNSInterceptorServer = self.server  # type: ignore[assignment]
        sock.sendto(server.handle_request(payload, self.client_address), self.client_address)


class DNSInterceptorServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True

    def __init__(
        self,
        config: DNSConfig,
        router: DomainRouter,
        metrics: MetricsCollector,
        steam_clients: SteamClientTracker,
        blocked_ips: BlockedIPRegistry,
    ) -> None:
        self.config = config
        self.router = router
        self.metrics = metrics
        self.steam_clients = steam_clients
        self.blocked_ips = blocked_ips
        self.logger = logging.getLogger(__name__)
        super().__init__((config.listen_host, config.listen_port), _DNSHandler)

    def verify_request(self, request, client_address) -> bool:
        del request
        client_ip = client_address[0] if client_address else None
        return not self.blocked_ips.is_blocked(client_ip)

    def handle_request(self, payload: bytes, client_address: tuple[str, int] | None = None) -> bytes:
        try:
            query = parse_query(payload)
        except Exception as exc:
            self.logger.warning("Dropping malformed DNS query: %s", exc)
            return b""

        decision = self.router.resolve_dns(query.hostname)
        if decision.platform != "generic" and client_address is not None:
            self.steam_clients.record_activity(client_address[0], query.hostname, source=f"dns/{decision.platform}")
        if decision.action == "rewrite" and query.qtype in {TYPE_A, TYPE_AAAA}:
            target_ip = self.config.cache_ipv4 if query.qtype == TYPE_A else self.config.cache_ipv6
            if target_ip:
                self.metrics.increment("dns_rewrites")
                self.logger.info(
                    "Rewriting DNS response for %s using pattern %s on platform %s",
                    query.hostname,
                    decision.matched_pattern,
                    decision.platform,
                )
                return build_address_response(query, target_ip, self.config.response_ttl)

        response = self._forward_to_upstream(payload)
        if decision.action == "passthrough":
            self.metrics.increment("dns_passthroughs")
        self.metrics.increment("dns_forwards")
        return response

    def _forward_to_upstream(self, payload: bytes) -> bytes:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream_socket:
            upstream_socket.settimeout(3.0)
            upstream_socket.sendto(
                payload,
                (self.config.upstream_host, self.config.upstream_port),
            )
            response, _ = upstream_socket.recvfrom(65535)
            return response


class DNSServerThread(threading.Thread):
    def __init__(self, server: DNSInterceptorServer) -> None:
        super().__init__(name="dns-server", daemon=True)
        self.server = server

    def run(self) -> None:
        self.server.serve_forever(poll_interval=0.5)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()