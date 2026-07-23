"""Pluggable query backends for :mod:`mysql_data`."""

from __future__ import annotations

from typing import Any, Protocol

from .models import MysqlConnectionConfig, QueryParameters


class MysqlQueryBackend(Protocol):
    """Backend contract used by MysqlDataService."""

    def query_all(
        self,
        sql: str,
        parameters: QueryParameters = None,
    ) -> list[dict[str, Any]]:
        """Execute a query and return rows as dictionaries."""

    def close(self) -> None:
        """Release backend resources."""


class SqlAlchemyMysqlBackend:
    """SQLAlchemy/PyMySQL backend with connection pooling and reconnect."""

    def __init__(self, config: MysqlConnectionConfig) -> None:
        config.validate()

        try:
            from sqlalchemy import URL, create_engine
        except ImportError as exc:
            raise RuntimeError(
                "使用MysqlDataService需要安装SQLAlchemy和PyMySQL，"
                "请执行：pip install 'vnpy_ctastrategy[mysql]'"
            ) from exc

        url: Any = URL.create(
            drivername="mysql+pymysql",
            username=config.user,
            password=config.password,
            host=config.host,
            port=config.port,
            database=config.database,
            query={"charset": config.charset},
        )
        self.engine: Any = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_recycle=config.pool_recycle,
            connect_args={
                "connect_timeout": config.connect_timeout,
                "read_timeout": config.read_timeout,
                "write_timeout": config.write_timeout,
            },
        )

    def query_all(
        self,
        sql: str,
        parameters: QueryParameters = None,
    ) -> list[dict[str, Any]]:
        """Execute a SQLAlchemy text query."""
        from sqlalchemy import text

        with self.engine.connect() as connection:
            result: Any = connection.execute(text(sql), dict(parameters or {}))
            if not result.returns_rows:
                raise ValueError("MysqlDataService只支持返回数据行的查询")
            return [dict(row._mapping) for row in result]

    def close(self) -> None:
        """Dispose all pooled connections."""
        self.engine.dispose()
