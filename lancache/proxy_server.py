from __future__ import annotations

import http.client
import http.server
import logging
import select
import socket
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .cache_storage import CacheEntry, CacheStore
from .config import ProxyConfig
from .metrics import MetricsCollector
from .routing import DomainRouter
from .steam_clients import SteamClientTracker

HEALTH_CHECK_PATH = "/__lancache__/health"

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class CacheProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self.server.handle_http_method(self, cacheable=True)  # type: ignore[attr-defined]

    def do_HEAD(self) -> None:
        self.server.handle_http_method(self, cacheable=False)  # type: ignore[attr-defined]

    def do_CONNECT(self) -> None:
        self.server.handle_connect(self)  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:
        logging.getLogger(__name__).info("%s - %s", self.address_string(), format % args)


class CacheProxyServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        listen_address: tuple[str, int],
        config: ProxyConfig,
        cache: CacheStore,
        router: DomainRouter,
        metrics: MetricsCollector,
        steam_clients: SteamClientTracker,
    ) -> None:
        self.config = config
        self.cache = cache
        self.router = router
        self.metrics = metrics
        self.steam_clients = steam_clients
        self.logger = logging.getLogger(__name__)
        super().__init__(listen_address, CacheProxyHandler)

    def handle_http_method(self, handler: CacheProxyHandler, cacheable: bool) -> None:
        if self._is_health_check_request(handler):
            self._serve_health_check(handler)
            return

        target_url, target_host = self._build_target(handler)
        if not target_url or not target_host:
            handler.send_error(400, "Host header is required")
            return

        requested_scheme = urlsplit(target_url).scheme or None
        route = self.router.resolve_proxy(target_host, requested_scheme)
        self._record_client_activity(handler.client_address[0], target_host, route.platform, "proxy")
        if route.action == "cache" and cacheable and handler.command == "GET":
            entry = self.cache.get_entry(target_url, count_client_hit=not self._is_local_request(handler))
            if entry and entry.is_complete:
                self._serve_cached(handler, entry, route.platform)
                return

        if route.action == "cache":
            self.metrics.record_miss(route.platform)
        else:
            self.metrics.increment("proxy_passthrough_requests")
        self._proxy_to_upstream(handler, target_url, target_host, route, cacheable)

    def handle_connect(self, handler: CacheProxyHandler) -> None:
        target_host, _, port_text = handler.path.partition(":")
        port = int(port_text or "443")
        route = self.router.resolve_proxy(target_host, "https")
        self._record_client_activity(handler.client_address[0], target_host, route.platform, "connect")
        if route.action == "cache":
            handler.send_error(421, "CONNECT is not allowed for cache-routed hosts")
            return

        try:
            upstream = socket.create_connection((target_host, port), timeout=self.config.connect_timeout)
        except OSError as exc:
            handler.send_error(502, f"CONNECT failed: {exc}")
            return

        handler.send_response(200, "Connection established")
        handler.end_headers()
        self.metrics.increment("proxy_connect_tunnels")

        sockets = [handler.connection, upstream]
        try:
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 1.0)
                if exceptional:
                    break
                for source in readable:
                    data = source.recv(self.config.chunk_size)
                    if not data:
                        return
                    target = upstream if source is handler.connection else handler.connection
                    target.sendall(data)
        finally:
            upstream.close()

    def _build_target(self, handler: CacheProxyHandler) -> tuple[str | None, str | None]:
        host_header = handler.headers.get("Host")
        if not host_header:
            return None, None
        host = host_header.split(":", 1)[0]
        parsed = urlsplit(handler.path)
        if parsed.scheme and parsed.netloc:
            return handler.path, parsed.hostname
        route = self.router.resolve_proxy(host, None)
        return f"{route.upstream_scheme}://{host}{handler.path}", host

    @staticmethod
    def _is_health_check_request(handler: CacheProxyHandler) -> bool:
        parsed = urlsplit(handler.path)
        request_path = parsed.path or handler.path
        return request_path == HEALTH_CHECK_PATH

    def _serve_health_check(self, handler: CacheProxyHandler) -> None:
        payload = b"ok\n"
        handler.send_response(200, "OK")
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(payload)

    def _serve_cached(self, handler: CacheProxyHandler, entry: CacheEntry, platform: str) -> None:
        file_path = Path(entry.file_path)
        file_size = file_path.stat().st_size
        range_header = handler.headers.get("Range")
        start = 0
        end = file_size - 1
        status_code = 200

        if range_header:
            range_value = range_header.replace("bytes=", "", 1)
            start_text, _, end_text = range_value.partition("-")
            if start_text:
                start = int(start_text)
            if end_text:
                end = min(int(end_text), file_size - 1)
            status_code = 206

        content_length = end - start + 1
        handler.send_response(status_code)
        handler.send_header("Content-Type", entry.content_type or "application/octet-stream")
        handler.send_header("Content-Length", str(content_length))
        handler.send_header("Accept-Ranges", "bytes")
        if status_code == 206:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        handler.end_headers()

        if handler.command == "HEAD":
            return

        with file_path.open("rb") as cached_file:
            cached_file.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = cached_file.read(min(self.config.chunk_size, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)

        self.metrics.record_hit(platform, file_size)
        self.metrics.record_bytes_served(content_length)

    def _proxy_to_upstream(
        self,
        handler: CacheProxyHandler,
        target_url: str,
        target_host: str,
        route,
        cacheable: bool,
    ) -> None:
        parsed = urlsplit(target_url)
        connection_class = (
            http.client.HTTPSConnection if parsed.scheme.lower() == "https" else http.client.HTTPConnection
        )
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        connection = connection_class(
            parsed.hostname,
            parsed.port or default_port,
            timeout=self.config.read_timeout,
        )
        try:
            upstream_headers = self._build_upstream_headers(handler)
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"

            connection.request(handler.command, path, headers=upstream_headers)
            response = connection.getresponse()
            should_cache = route.action == "cache" and self._should_cache(handler, response, cacheable)
            if should_cache:
                self._stream_and_cache(handler, response, target_url, target_host, route.platform)
            else:
                self._stream_passthrough(handler, response)
        except (OSError, http.client.HTTPException, socket.timeout) as exc:
            self.logger.exception("Proxy request failed for %s", target_url)
            handler.send_error(502, f"Upstream request failed: {exc}")
        finally:
            connection.close()

    def _build_upstream_headers(self, handler: CacheProxyHandler) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in handler.headers.items():
            if key.lower() in HOP_BY_HOP_HEADERS:
                continue
            headers[key] = value
        headers["Connection"] = "close"
        headers.setdefault("User-Agent", self.config.user_agent)
        return headers

    def _should_cache(
        self,
        handler: CacheProxyHandler,
        response: http.client.HTTPResponse,
        cacheable: bool,
    ) -> bool:
        if not cacheable or handler.command != "GET":
            return False
        if response.status != 200:
            return False
        cache_control = response.headers.get("Cache-Control", "").lower()
        if "no-store" in cache_control:
            return False
        length_header = response.headers.get("Content-Length")
        if not length_header:
            return False
        return int(length_header) <= self.config.max_cacheable_object_bytes

    def _stream_passthrough(self, handler: CacheProxyHandler, response: http.client.HTTPResponse) -> None:
        handler.send_response(response.status, response.reason)
        for key, value in response.headers.items():
            if key.lower() in HOP_BY_HOP_HEADERS:
                continue
            handler.send_header(key, value)
        handler.end_headers()

        if handler.command == "HEAD":
            return

        total = 0
        while True:
            chunk = response.read(self.config.chunk_size)
            if not chunk:
                break
            handler.wfile.write(chunk)
            total += len(chunk)
        self.metrics.record_bytes_served(total)

    def _stream_and_cache(
        self,
        handler: CacheProxyHandler,
        response: http.client.HTTPResponse,
        target_url: str,
        target_host: str,
        platform: str,
    ) -> None:
        with self.cache.write_lock(target_url):
            existing_entry = self.cache.get_entry(target_url)
            if existing_entry and existing_entry.is_complete:
                self._serve_cached(handler, existing_entry, platform)
                return

            cache_key, temp_path = self.cache.reserve_temp_file(target_url)
            total = 0
            try:
                handler.send_response(response.status, response.reason)
                for key, value in response.headers.items():
                    if key.lower() in HOP_BY_HOP_HEADERS:
                        continue
                    handler.send_header(key, value)
                handler.end_headers()

                with temp_path.open("wb") as cache_file:
                    while True:
                        chunk = response.read(self.config.chunk_size)
                        if not chunk:
                            break
                        cache_file.write(chunk)
                        handler.wfile.write(chunk)
                        total += len(chunk)
                self.cache.commit(
                    target_url,
                    temp_path,
                    total,
                    response.headers.get("Content-Type"),
                    response.headers.get("ETag"),
                    response.headers.get("Last-Modified"),
                    platform,
                    target_host,
                )
                if self._is_local_request(handler):
                    self.cache.assign_cache_entry_to_active_game(cache_key)
                self.logger.info("Cached %s as %s (%s bytes)", target_url, cache_key, total)
            except BrokenPipeError:
                self.logger.warning("Client disconnected during cache fill for %s", target_url)
                self.cache.delete_partial(target_url)
            except OSError:
                self.cache.delete_partial(target_url)
                raise
            finally:
                self.metrics.record_bytes_served(total)

    @staticmethod
    def _is_local_request(handler: CacheProxyHandler) -> bool:
        host = handler.client_address[0]
        return host in {"127.0.0.1", "::1", "localhost"}

    def _record_client_activity(self, client_ip: str, hostname: str | None, platform: str, source: str) -> None:
        if platform == "generic":
            return
        if client_ip in {"127.0.0.1", "::1", "localhost"}:
            return
        self.steam_clients.record_activity(client_ip, hostname, f"{source}/{platform}")


class ProxyServerThread(threading.Thread):
    def __init__(self, server: CacheProxyServer) -> None:
        super().__init__(name="proxy-server", daemon=True)
        self.server = server

    def run(self) -> None:
        self.server.serve_forever(poll_interval=0.5)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()