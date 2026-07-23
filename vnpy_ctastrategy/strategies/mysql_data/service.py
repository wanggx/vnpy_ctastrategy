"""Thread-safe shared MySQL query service for CTA strategies."""

from __future__ import annotations

from atexit import register
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from time import monotonic
from types import MappingProxyType
from typing import Any

from .models import (
    MysqlConnectionConfig,
    MysqlQuerySnapshot,
    QueryParameters,
)
from .provider import MysqlQueryBackend, SqlAlchemyMysqlBackend


class MysqlDataService:
    """Business-agnostic MySQL service shared by CTA strategies."""

    _shared_lock: RLock = RLock()
    _shared_instances: dict[str, "MysqlDataService"] = {}

    def __init__(
        self,
        config: MysqlConnectionConfig | None = None,
        backend: MysqlQueryBackend | None = None,
        max_workers: int = 2,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers必须大于0")

        self.config: MysqlConnectionConfig = (
            config or MysqlConnectionConfig.from_vnpy_settings()
        )
        self.max_workers: int = max_workers
        self._backend: MysqlQueryBackend | None = backend
        self._executor: ThreadPoolExecutor | None = None
        self._lock: RLock = RLock()
        self._snapshots: dict[str, MysqlQuerySnapshot] = {}
        self._last_success_monotonic: dict[str, float] = {}
        self._last_attempt_monotonic: dict[str, float] = {}
        self._futures: dict[str, Future[MysqlQuerySnapshot]] = {}
        self._closed: bool = False

    @classmethod
    def get_shared(
        cls,
        name: str = "default",
        **kwargs: Any,
    ) -> MysqlDataService:
        """Return a named process-wide service instance."""
        with cls._shared_lock:
            service: MysqlDataService | None = cls._shared_instances.get(name)
            if service is None:
                service = cls(**kwargs)
                cls._shared_instances[name] = service
            return service

    @classmethod
    def stop_all_shared(cls) -> None:
        """Stop and remove all process-wide service instances."""
        with cls._shared_lock:
            services: list[MysqlDataService] = list(
                cls._shared_instances.values()
            )
            cls._shared_instances.clear()

        for service in services:
            service.stop()

    def start(self) -> None:
        """Start query workers; repeated calls are safe."""
        with self._lock:
            if self._closed:
                raise RuntimeError("MysqlDataService已经关闭")
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.max_workers,
                    thread_name_prefix="MysqlDataService",
                )

    def stop(self, wait: bool = True) -> None:
        """Stop workers and close the connection pool."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            executor: ThreadPoolExecutor | None = self._executor
            backend: MysqlQueryBackend | None = self._backend
            self._executor = None
            self._backend = None

        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=True)
        if backend is not None:
            backend.close()

        with self._shared_lock:
            for name, service in list(self._shared_instances.items()):
                if service is self:
                    self._shared_instances.pop(name)

    def query_all(
        self,
        sql: str,
        parameters: QueryParameters = None,
    ) -> list[dict[str, Any]]:
        """Synchronously query rows; use only outside market-data callbacks."""
        rows: list[dict[str, Any]] = self._get_backend().query_all(
            sql,
            parameters,
        )
        return [dict(row) for row in rows]

    def query_one(
        self,
        sql: str,
        parameters: QueryParameters = None,
    ) -> dict[str, Any] | None:
        """Synchronously return the first row, if any."""
        rows: list[dict[str, Any]] = self.query_all(sql, parameters)
        return rows[0] if rows else None

    def refresh(
        self,
        cache_key: str,
        sql: str,
        parameters: QueryParameters = None,
        min_interval: float = 0.0,
        force: bool = False,
    ) -> Future[MysqlQuerySnapshot]:
        """Refresh a cache key asynchronously and merge duplicate requests."""
        if not cache_key:
            raise ValueError("cache_key不能为空")
        if min_interval < 0:
            raise ValueError("min_interval不能小于0")

        self.start()
        now: float = monotonic()
        with self._lock:
            current: Future[MysqlQuerySnapshot] | None = self._futures.get(cache_key)
            if current is not None and not current.done():
                return current

            last_attempt: float = self._last_attempt_monotonic.get(cache_key, 0.0)
            if (
                not force
                and current is not None
                and now - last_attempt < min_interval
            ):
                return current

            self._last_attempt_monotonic[cache_key] = now
            executor: ThreadPoolExecutor | None = self._executor
            if executor is None:
                raise RuntimeError("MysqlDataService线程池未启动")
            future: Future[MysqlQuerySnapshot] = executor.submit(
                self._refresh,
                cache_key,
                sql,
                dict(parameters or {}),
            )
            self._futures[cache_key] = future
            return future

    def get_latest(
        self,
        cache_key: str,
        stale_after: float | None = None,
    ) -> MysqlQuerySnapshot | None:
        """Return a defensive copy of the latest cached snapshot."""
        if stale_after is not None and stale_after <= 0:
            raise ValueError("stale_after必须大于0")

        with self._lock:
            snapshot: MysqlQuerySnapshot | None = self._snapshots.get(cache_key)
            if snapshot is None:
                return None

            stale: bool = bool(snapshot.error)
            if stale_after is not None:
                last_success: float = self._last_success_monotonic.get(
                    cache_key,
                    0.0,
                )
                stale = stale or not last_success or (
                    monotonic() - last_success > stale_after
                )

            return replace(
                snapshot,
                rows=tuple(dict(row) for row in snapshot.rows),
                stale=stale,
            )

    def invalidate(self, cache_key: str) -> None:
        """Remove cached data and throttling state for a key."""
        with self._lock:
            self._snapshots.pop(cache_key, None)
            self._last_success_monotonic.pop(cache_key, None)
            self._last_attempt_monotonic.pop(cache_key, None)
            self._futures.pop(cache_key, None)

    def _get_backend(self) -> MysqlQueryBackend:
        """Lazily create the SQL backend."""
        with self._lock:
            if self._closed:
                raise RuntimeError("MysqlDataService已经关闭")
            if self._backend is None:
                self._backend = SqlAlchemyMysqlBackend(self.config)
            return self._backend

    def _refresh(
        self,
        cache_key: str,
        sql: str,
        parameters: QueryParameters,
    ) -> MysqlQuerySnapshot:
        """Worker implementation that converts failures into stale snapshots."""
        attempted_at: datetime = datetime.now(timezone.utc)
        try:
            rows: list[dict[str, Any]] = self.query_all(sql, parameters)
        except Exception as exc:
            with self._lock:
                previous: MysqlQuerySnapshot | None = self._snapshots.get(cache_key)
                snapshot = MysqlQuerySnapshot(
                    cache_key=cache_key,
                    rows=previous.rows if previous else (),
                    updated_at=previous.updated_at if previous else None,
                    attempted_at=attempted_at,
                    stale=True,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._snapshots[cache_key] = snapshot
                return snapshot

        snapshot = MysqlQuerySnapshot(
            cache_key=cache_key,
            rows=tuple(MappingProxyType(dict(row)) for row in rows),
            updated_at=attempted_at,
            attempted_at=attempted_at,
        )
        with self._lock:
            self._snapshots[cache_key] = snapshot
            self._last_success_monotonic[cache_key] = monotonic()
        return snapshot


register(MysqlDataService.stop_all_shared)
