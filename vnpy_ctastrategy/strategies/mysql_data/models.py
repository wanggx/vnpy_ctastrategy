"""Configuration and query result models for shared MySQL access."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vnpy.trader.setting import SETTINGS


QueryParameters = Mapping[str, Any] | None
QueryRow = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MysqlConnectionConfig:
    """Connection and pool settings for a MySQL backend."""

    host: str
    database: str
    user: str
    password: str
    port: int = 3306
    charset: str = "utf8mb4"
    pool_size: int = 5
    max_overflow: int = 5
    pool_recycle: int = 1800
    connect_timeout: int = 5
    read_timeout: int = 10
    write_timeout: int = 10

    @classmethod
    def from_vnpy_settings(
        cls,
        settings: Mapping[str, Any] | None = None,
        prefix: str = "mysql_data",
    ) -> MysqlConnectionConfig:
        """Build config from custom settings, with vn.py MySQL fallback."""
        values: Mapping[str, Any] = SETTINGS if settings is None else settings
        use_database_fallback: bool = values.get("database.name") == "mysql"

        def get_value(name: str, default: Any) -> Any:
            custom_key: str = f"{prefix}.{name}"
            if custom_key in values:
                return values[custom_key]
            if use_database_fallback:
                return values.get(f"database.{name}", default)
            return default

        return cls(
            host=str(get_value("host", "")),
            port=int(get_value("port", 3306)),
            database=str(get_value("database", "")),
            user=str(get_value("user", "")),
            password=str(get_value("password", "")),
            charset=str(get_value("charset", "utf8mb4")),
            pool_size=int(get_value("pool_size", 5)),
            max_overflow=int(get_value("max_overflow", 5)),
            pool_recycle=int(get_value("pool_recycle", 1800)),
            connect_timeout=int(get_value("connect_timeout", 5)),
            read_timeout=int(get_value("read_timeout", 10)),
            write_timeout=int(get_value("write_timeout", 10)),
        )

    def validate(self) -> None:
        """Validate required connection and pool values."""
        missing: list[str] = [
            name
            for name in ("host", "database", "user")
            if not getattr(self, name)
        ]
        if missing:
            names: str = ", ".join(f"mysql_data.{name}" for name in missing)
            raise ValueError(f"MySQL配置缺少必填项：{names}")
        if self.port <= 0:
            raise ValueError("mysql_data.port必须大于0")
        if self.pool_size <= 0:
            raise ValueError("mysql_data.pool_size必须大于0")
        if self.max_overflow < 0:
            raise ValueError("mysql_data.max_overflow不能小于0")


@dataclass(frozen=True, slots=True)
class MysqlQuerySnapshot:
    """Immutable metadata and rows produced by an asynchronous refresh."""

    cache_key: str
    rows: tuple[QueryRow, ...]
    updated_at: datetime | None
    attempted_at: datetime
    stale: bool = False
    error: str = ""
