from collections.abc import Mapping
from threading import Event
from typing import Any

import pytest

from vnpy_ctastrategy import CtaTemplate
from vnpy_ctastrategy.strategies.base import CtaTemplateService
from vnpy_ctastrategy.strategies.mysql_data import (
    MysqlConnectionConfig,
    MysqlDataService,
)


class FakeBackend:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = [{"value": 1}]
        self.error: Exception | None = None
        self.calls: int = 0
        self.closed: bool = False
        self.entered: Event | None = None
        self.release: Event | None = None

    def query_all(
        self,
        sql: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(timeout=2)
        if self.error is not None:
            raise self.error
        return [dict(row) for row in self.rows]

    def close(self) -> None:
        self.closed = True


class MysqlStrategy(CtaTemplateService):
    mysql_data_service_name: str = "test"

    def on_init(self) -> None:
        pass


def test_config_uses_independent_mysql_data_settings() -> None:
    config = MysqlConnectionConfig.from_vnpy_settings({
        "database.name": "sqlite",
        "mysql_data.host": "db.local",
        "mysql_data.port": 3307,
        "mysql_data.database": "signals",
        "mysql_data.user": "reader",
        "mysql_data.password": "secret",
    })

    assert config.host == "db.local"
    assert config.port == 3307
    assert config.database == "signals"
    assert config.user == "reader"


def test_sync_query_returns_defensive_rows() -> None:
    backend = FakeBackend()
    service = MysqlDataService(backend=backend)

    row = service.query_one("SELECT value FROM config")
    assert row == {"value": 1}
    assert row is not backend.rows[0]

    service.stop()
    assert backend.closed


def test_strategy_gets_engine_shared_service() -> None:
    service = MysqlDataService.get_shared(
        name="test",
        backend=FakeBackend(),
    )
    strategy = MysqlStrategy(
        object(),
        "mysql_test",
        "000001.SZSE",
        {},
    )

    assert strategy.get_mysql_data_service() is service
    assert not hasattr(CtaTemplate, "get_mysql_data_service")
    service.stop()


def test_refresh_merges_in_flight_requests_and_caches_result() -> None:
    backend = FakeBackend()
    backend.entered = Event()
    backend.release = Event()
    service = MysqlDataService(backend=backend)

    first = service.refresh("config", "SELECT value FROM config")
    assert backend.entered.wait(timeout=1)
    second = service.refresh("config", "SELECT value FROM config")
    assert first is second

    backend.release.set()
    snapshot = first.result(timeout=1)
    assert snapshot.rows == ({"value": 1},)
    assert service.get_latest("config") == snapshot
    assert backend.calls == 1

    service.stop()


def test_failed_refresh_preserves_rows_and_marks_snapshot_stale() -> None:
    backend = FakeBackend()
    service = MysqlDataService(backend=backend)

    service.refresh("config", "SELECT value FROM config").result(timeout=1)
    backend.error = RuntimeError("database unavailable")
    failed = service.refresh(
        "config",
        "SELECT value FROM config",
        force=True,
    ).result(timeout=1)

    assert failed.rows == ({"value": 1},)
    assert failed.stale
    assert "database unavailable" in failed.error
    assert backend.calls == 2

    service.stop()


def test_invalid_config_is_rejected_before_connecting() -> None:
    config = MysqlConnectionConfig(
        host="",
        database="",
        user="",
        password="",
    )

    with pytest.raises(ValueError, match="mysql_data.host"):
        config.validate()
