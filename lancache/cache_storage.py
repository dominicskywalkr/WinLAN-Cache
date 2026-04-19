from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import CacheConfig

STEAM_DEPOT_URL_PATTERN = re.compile(r"/depot/(?P<depot_id>\d+)/(chunk|manifest)/", re.IGNORECASE)


@dataclass(slots=True)
class CacheEntry:
    cache_key: str
    url: str
    file_path: str
    size: int
    content_type: str | None
    etag: str | None
    last_modified: str | None
    status: str
    platform: str
    upstream_host: str

    @property
    def is_complete(self) -> bool:
        return self.status == "complete" and Path(self.file_path).exists()


@dataclass(slots=True)
class SteamGameEntry:
    app_id: int
    name: str
    install_path: str
    first_installed_at: float
    last_warmup_at: float
    client_download_count: int


class CacheStore:
    def __init__(self, config: CacheConfig) -> None:
        self.config = config
        self.root_dir = Path(config.root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.root_dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(config.metadata_db)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._db_guard = threading.RLock()
        self._warmup_guard = threading.Lock()
        self._active_warmup: tuple[int, str, str] | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _db_connection(self):
        with self._db_guard:
            with self._connect() as connection:
                yield connection

    def _init_db(self) -> None:
        with self._db_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    content_type TEXT,
                    etag TEXT,
                    last_modified TEXT,
                    status TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    upstream_host TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS steam_games (
                    app_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    install_path TEXT NOT NULL,
                    first_installed_at REAL NOT NULL,
                    last_warmup_at REAL NOT NULL,
                    client_download_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS steam_game_cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    app_id INTEGER NOT NULL,
                    FOREIGN KEY(app_id) REFERENCES steam_games(app_id)
                )
                """
            )

    def cache_key_for(self, url: str) -> str:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()

    def file_path_for(self, cache_key: str) -> Path:
        return self.root_dir / cache_key[:2] / cache_key[2:4] / cache_key

    def temp_path_for(self, cache_key: str) -> Path:
        return self.tmp_dir / f"{cache_key}.part"

    def get_entry(self, url: str, count_client_hit: bool = True) -> CacheEntry | None:
        cache_key = self.cache_key_for(url)
        with self._db_connection() as connection:
            row = connection.execute(
                "SELECT * FROM cache_entries WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE cache_entries SET last_accessed = ?, hit_count = hit_count + 1 WHERE cache_key = ?",
                (time.time(), cache_key),
            )
            if count_client_hit:
                self._ensure_steam_content_association(
                    connection,
                    cache_key,
                    row["url"],
                    row["platform"],
                    row["upstream_host"],
                )
                self._record_game_download(connection, cache_key)
        return self._cache_entry_from_row(row)

    @staticmethod
    def _cache_entry_from_row(row: sqlite3.Row) -> CacheEntry:
        return CacheEntry(
            cache_key=row["cache_key"],
            url=row["url"],
            file_path=row["file_path"],
            size=row["size"],
            content_type=row["content_type"],
            etag=row["etag"],
            last_modified=row["last_modified"],
            status=row["status"],
            platform=row["platform"],
            upstream_host=row["upstream_host"],
        )

    def reserve_temp_file(self, url: str) -> tuple[str, Path]:
        cache_key = self.cache_key_for(url)
        temp_path = self.temp_path_for(cache_key)
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        return cache_key, temp_path

    def commit(
        self,
        url: str,
        temp_path: Path,
        size: int,
        content_type: str | None,
        etag: str | None,
        last_modified: str | None,
        platform: str,
        upstream_host: str,
    ) -> CacheEntry:
        cache_key = self.cache_key_for(url)
        final_path = self.file_path_for(cache_key)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, final_path)

        entry = CacheEntry(
            cache_key=cache_key,
            url=url,
            file_path=str(final_path),
            size=size,
            content_type=content_type,
            etag=etag,
            last_modified=last_modified,
            status="complete",
            platform=platform,
            upstream_host=upstream_host,
        )
        now = time.time()

        with self._db_connection() as connection:
            connection.execute(
                """
                INSERT INTO cache_entries (
                    cache_key, url, file_path, size, content_type, etag,
                    last_modified, status, platform, upstream_host,
                    created_at, last_accessed, hit_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    file_path = excluded.file_path,
                    size = excluded.size,
                    content_type = excluded.content_type,
                    etag = excluded.etag,
                    last_modified = excluded.last_modified,
                    status = excluded.status,
                    platform = excluded.platform,
                    upstream_host = excluded.upstream_host,
                    last_accessed = excluded.last_accessed
                """,
                (
                    entry.cache_key,
                    entry.url,
                    entry.file_path,
                    entry.size,
                    entry.content_type,
                    entry.etag,
                    entry.last_modified,
                    entry.status,
                    entry.platform,
                    entry.upstream_host,
                    now,
                    now,
                    0,
                ),
            )
            self._ensure_steam_content_association(connection, cache_key, url, platform, upstream_host)

        self.evict_if_needed()
        return entry

    def register_steam_game(self, app_id: int, name: str, install_path: str) -> None:
        now = time.time()
        with self._db_connection() as connection:
            connection.execute(
                """
                INSERT INTO steam_games (app_id, name, install_path, first_installed_at, last_warmup_at, client_download_count)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(app_id) DO UPDATE SET
                    name = excluded.name,
                    install_path = excluded.install_path,
                    last_warmup_at = excluded.last_warmup_at
                """,
                (app_id, name, install_path, now, now),
            )

    @contextmanager
    def warmup_session(self, app_id: int, name: str, install_path: str):
        self.register_steam_game(app_id, name, install_path)
        with self._warmup_guard:
            previous_session = self._active_warmup
            self._active_warmup = (app_id, name, install_path)
        try:
            yield
        finally:
            with self._warmup_guard:
                self._active_warmup = previous_session

    def assign_cache_entry_to_active_game(self, cache_key: str) -> None:
        with self._warmup_guard:
            session = self._active_warmup
        if session is None:
            return
        app_id, name, install_path = session
        self.register_steam_game(app_id, name, install_path)
        with self._db_connection() as connection:
            connection.execute(
                """
                INSERT INTO steam_game_cache_entries (cache_key, app_id)
                VALUES (?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    app_id = excluded.app_id
                """,
                (cache_key, app_id),
            )

    def _ensure_steam_content_association(
        self,
        connection: sqlite3.Connection,
        cache_key: str,
        url: str,
        platform: str,
        upstream_host: str,
    ) -> None:
        if platform == "generic":
            return
        existing_mapping = connection.execute(
            "SELECT app_id FROM steam_game_cache_entries WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if existing_mapping is not None:
            return

        depot_id = self._steam_depot_id_from_url(url)
        if depot_id is None:
            return

        now = time.time()
        connection.execute(
            """
            INSERT INTO steam_games (app_id, name, install_path, first_installed_at, last_warmup_at, client_download_count)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(app_id) DO NOTHING
            """,
            (depot_id, f"Depot {depot_id}", upstream_host, now, now),
        )
        connection.execute(
            """
            INSERT INTO steam_game_cache_entries (cache_key, app_id)
            VALUES (?, ?)
            ON CONFLICT(cache_key) DO NOTHING
            """,
            (cache_key, depot_id),
        )

    @staticmethod
    def _steam_depot_id_from_url(url: str) -> int | None:
        match = STEAM_DEPOT_URL_PATTERN.search(url)
        if match is None:
            return None
        try:
            depot_id = int(match.group("depot_id"))
        except (TypeError, ValueError):
            return None
        return depot_id if depot_id > 0 else None

    def list_steam_games(self) -> list[SteamGameEntry]:
        with self._db_connection() as connection:
            rows = connection.execute(
                """
                SELECT app_id, name, install_path, first_installed_at, last_warmup_at, client_download_count
                FROM steam_games
                ORDER BY first_installed_at DESC, name ASC
                """
            ).fetchall()
        return [SteamGameEntry(**dict(row)) for row in rows]

    def _record_game_download(self, connection: sqlite3.Connection, cache_key: str) -> None:
        row = connection.execute(
            "SELECT app_id FROM steam_game_cache_entries WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return
        connection.execute(
            "UPDATE steam_games SET client_download_count = client_download_count + 1 WHERE app_id = ?",
            (row["app_id"],),
        )

    def delete_partial(self, url: str) -> None:
        _, temp_path = self.reserve_temp_file(url)
        temp_path.unlink(missing_ok=True)

    def total_size_bytes(self) -> int:
        with self._db_connection() as connection:
            row = connection.execute("SELECT COALESCE(SUM(size), 0) AS total_size FROM cache_entries").fetchone()
            return int(row["total_size"])

    def evict_if_needed(self) -> None:
        max_size = self.config.max_size_gb * 1024 * 1024 * 1024
        current_size = self.total_size_bytes()
        if current_size <= max_size:
            return

        target_size = int(max_size * (self.config.eviction_target_percent / 100.0))
        with self._db_connection() as connection:
            rows = connection.execute(
                "SELECT cache_key, file_path, size FROM cache_entries ORDER BY last_accessed ASC"
            ).fetchall()
            for row in rows:
                if current_size <= target_size:
                    break
                path = Path(row["file_path"])
                if path.exists():
                    try:
                        size = path.stat().st_size
                        path.unlink()
                        current_size -= size
                    except OSError:
                        continue
                connection.execute(
                    "DELETE FROM cache_entries WHERE cache_key = ?",
                    (row["cache_key"],),
                )

    @contextmanager
    def write_lock(self, url: str):
        cache_key = self.cache_key_for(url)
        with self._locks_guard:
            lock = self._locks.setdefault(cache_key, threading.Lock())
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def clear(self) -> None:
        if self.root_dir.exists():
            shutil.rmtree(self.root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        with self._db_connection() as connection:
            connection.execute("DELETE FROM cache_entries")
            connection.execute("DELETE FROM steam_game_cache_entries")
            connection.execute("DELETE FROM steam_games")