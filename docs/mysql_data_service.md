# MysqlDataService

`MysqlDataService` 用于多个实盘 CTA 策略共享访问自定义 MySQL 表。它不包含业务 SQL，也不替代 VeighNa 用于 Bar/Tick 的 `BaseDatabase`。

## 安装和配置

```bash
pip install "vnpy_ctastrategy[mysql]"
```

在 `.vntrader/vt_setting.json` 中增加独立配置：

```json
{
    "mysql_data.host": "127.0.0.1",
    "mysql_data.port": 3306,
    "mysql_data.database": "strategy_data",
    "mysql_data.user": "cta_reader",
    "mysql_data.password": "password",
    "mysql_data.charset": "utf8mb4",
    "mysql_data.pool_size": 5,
    "mysql_data.max_overflow": 5,
    "mysql_data.pool_recycle": 1800,
    "mysql_data.connect_timeout": 5,
    "mysql_data.read_timeout": 10,
    "mysql_data.write_timeout": 10
}
```

如果没有配置 `mysql_data.*`，并且 `database.name` 为 `mysql`，服务会回退使用现有的 `database.*` 连接配置。

## 策略中使用

需要访问 MySQL 的策略继承项目扩展基类，不修改 VeighNa 的核心
`CtaTemplate`：

```python
from vnpy_ctastrategy.strategies.base import CtaTemplateService


class MyStrategy(CtaTemplateService):
    pass
```

初始化阶段允许同步查询：

```python
def on_init(self) -> None:
    mysql = self.get_mysql_data_service()
    row = mysql.query_one(
        "SELECT value FROM strategy_config WHERE name = :name",
        {"name": "risk_limit"},
    )
```

不要在 `on_tick` 或 `on_bar` 中同步查询。使用异步刷新和缓存：

```python
def on_start(self) -> None:
    self.mysql = self.get_mysql_data_service()
    self.mysql.refresh(
        cache_key=f"stock_pool:{self.vt_symbol}",
        sql="""
            SELECT symbol, enabled
            FROM stock_pool
            WHERE strategy = :strategy
        """,
        parameters={"strategy": self.strategy_name},
        min_interval=60,
    )

def on_bar(self, bar: BarData) -> None:
    self.mysql.refresh(
        cache_key=f"stock_pool:{self.vt_symbol}",
        sql="""
            SELECT symbol, enabled
            FROM stock_pool
            WHERE strategy = :strategy
        """,
        parameters={"strategy": self.strategy_name},
        min_interval=60,
    )
    snapshot = self.mysql.get_latest(
        f"stock_pool:{self.vt_symbol}",
        stale_after=120,
    )
    if snapshot is None or snapshot.stale:
        return

    rows = snapshot.rows
```

SQL 使用 SQLAlchemy 的命名参数格式（例如 `:strategy`），不能通过字符串拼接参数。相同 `cache_key` 的并发刷新会合并，失败时保留最后一次成功数据并将快照标记为 `stale`。

同名服务由多个策略共享。单个策略不应调用 `stop()`；进程退出时会统一关闭
共享线程池和数据库连接池。应用需要提前释放时可以调用：

```python
MysqlDataService.stop_all_shared()
```
