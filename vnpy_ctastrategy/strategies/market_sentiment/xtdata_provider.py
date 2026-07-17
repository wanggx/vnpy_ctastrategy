"""基于 xtquant.xtdata 的全市场实时行情数据提供模块。"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from threading import RLock
from types import ModuleType
from typing import Any, Protocol, cast

from .models import TickSnapshot


DEFAULT_INDEX_SYMBOLS: dict[str, str] = {
    "000001.SH": "上证综指",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
}

DEFAULT_EXCLUDED_SECTORS: frozenset[str] = frozenset({
    "沪深A股",
    "沪A",
    "深A",
    "上海市场",
    "深圳市场",
    "全部A股",
})


class XtDataClient(Protocol):
    """本模块实际使用到的 xtdata 接口。"""

    def get_full_tick(
        self,
        code_list: Sequence[str],
    ) -> Mapping[str, Mapping[str, Any]]:
        """批量获取最新 Tick。"""

    def get_stock_list_in_sector(
        self,
        sector_name: str,
        real_timetag: int = -1,
    ) -> Sequence[str]:
        """获取板块成分列表。"""

    def get_sector_list(self) -> Sequence[str]:
        """获取 QMT 板块列表。"""


class XtDataProvider:
    """获取并标准化沪深市场全量实时 Tick。"""

    def __init__(
        self,
        sectors: Sequence[str] = ("沪深A股",),
        index_symbols: Mapping[str, str] | None = None,
        batch_size: int = 1_000,
        sector_names: Sequence[str] | None = None,
        excluded_sectors: Sequence[str] = tuple(DEFAULT_EXCLUDED_SECTORS),
        minimum_sector_size: int = 5,
        maximum_sector_size: int = 800,
        client: XtDataClient | None = None,
    ) -> None:
        if not sectors:
            raise ValueError("sectors 不能为空")
        if batch_size <= 0:
            raise ValueError("batch_size 必须大于0")
        if minimum_sector_size <= 0:
            raise ValueError("minimum_sector_size 必须大于0")
        if maximum_sector_size < minimum_sector_size:
            raise ValueError("maximum_sector_size 不能小于 minimum_sector_size")

        self.sectors: tuple[str, ...] = tuple(sectors)
        self.index_symbols: dict[str, str] = dict(
            index_symbols or DEFAULT_INDEX_SYMBOLS
        )
        self.batch_size: int = batch_size
        self.sector_names: tuple[str, ...] | None = (
            tuple(sector_names) if sector_names is not None else None
        )
        self.excluded_sectors: frozenset[str] = frozenset(excluded_sectors)
        self.minimum_sector_size: int = minimum_sector_size
        self.maximum_sector_size: int = maximum_sector_size
        self._client: XtDataClient | None = client

        self._lock: RLock = RLock()
        self._stock_universe: tuple[str, ...] = ()
        self._universe_updated_on: date | None = None
        self._sector_members: dict[str, tuple[str, ...]] = {}
        self._sectors_updated_on: date | None = None

    def get_stock_universe(self, force: bool = False) -> tuple[str, ...]:
        """获取沪深A股代码，同一个本地日期只访问一次 QMT。"""
        with self._lock:
            today: date = datetime.now().astimezone().date()
            cache_valid: bool = (
                bool(self._stock_universe)
                and self._universe_updated_on == today
            )
            if cache_valid and not force:
                return self._stock_universe

            symbols: set[str] = set()
            client: XtDataClient = self._get_client()
            for sector in self.sectors:
                sector_symbols: Sequence[str] = (
                    client.get_stock_list_in_sector(sector)
                )
                symbols.update(
                    symbol
                    for symbol in sector_symbols
                    if self._is_a_share_symbol(symbol)
                )

            if not symbols:
                raise RuntimeError(
                    f"无法从板块 {self.sectors!r} 获取沪深A股列表"
                )

            self._stock_universe = tuple(sorted(symbols))
            self._universe_updated_on = today
            # 股票池发生刷新后，板块成员必须基于新股票池重新生成。
            self._sectors_updated_on = None
            return self._stock_universe

    def get_full_snapshot(
        self,
    ) -> tuple[
        datetime,
        dict[str, TickSnapshot],
        tuple[str, ...],
        dict[str, tuple[str, ...]],
    ]:
        """获取股票池和指数的最新行情快照。"""
        stock_symbols: tuple[str, ...] = self.get_stock_universe()
        sector_members: dict[str, tuple[str, ...]] = (
            self.get_sector_members(stock_symbols)
        )
        all_symbols: list[str] = list(stock_symbols)
        all_symbols.extend(
            symbol
            for symbol in self.index_symbols
            if symbol not in stock_symbols
        )

        raw_ticks: dict[str, Mapping[str, Any]] = {}
        client: XtDataClient = self._get_client()
        for start in range(0, len(all_symbols), self.batch_size):
            batch: list[str] = all_symbols[start:start + self.batch_size]
            batch_ticks: Mapping[str, Mapping[str, Any]] = (
                client.get_full_tick(batch)
            )
            if batch_ticks:
                raw_ticks.update(batch_ticks)

        now: datetime = datetime.now().astimezone()
        ticks: dict[str, TickSnapshot] = {}
        for symbol, raw_tick in raw_ticks.items():
            if not isinstance(raw_tick, Mapping):
                continue
            ticks[symbol] = self._normalize_tick(symbol, raw_tick, now)

        if ticks:
            snapshot_datetime: datetime = max(
                tick.datetime for tick in ticks.values()
            )
        else:
            snapshot_datetime = now

        return snapshot_datetime, ticks, stock_symbols, sector_members

    def refresh_universe(self) -> tuple[str, ...]:
        """强制刷新股票池。"""
        return self.get_stock_universe(force=True)

    def get_sector_members(
        self,
        stock_universe: Sequence[str] | None = None,
        force: bool = False,
    ) -> dict[str, tuple[str, ...]]:
        """获取 QMT 板块成员；同一股票可以属于多个板块。"""
        with self._lock:
            today: date = datetime.now().astimezone().date()
            cache_valid: bool = (
                self._sectors_updated_on == today
            )
            if cache_valid and not force:
                return dict(self._sector_members)

            universe: set[str] = set(
                stock_universe or self.get_stock_universe()
            )
            client: XtDataClient = self._get_client()
            if self.sector_names is None:
                names: Sequence[str] = client.get_sector_list()
            else:
                names = self.sector_names

            sector_members: dict[str, tuple[str, ...]] = {}
            for name in dict.fromkeys(names):
                if not name or name in self.excluded_sectors:
                    continue

                raw_members: Sequence[str] = (
                    client.get_stock_list_in_sector(name)
                )
                # 先与全市场股票池求交集，再在板块内部去重。
                members: tuple[str, ...] = tuple(sorted(
                    set(raw_members).intersection(universe)
                ))
                if (
                    self.minimum_sector_size
                    <= len(members)
                    <= self.maximum_sector_size
                ):
                    sector_members[name] = members

            self._sector_members = sector_members
            self._sectors_updated_on = today
            return dict(sector_members)

    def refresh_sectors(self) -> dict[str, tuple[str, ...]]:
        """强制刷新 QMT 板块成员关系。"""
        return self.get_sector_members(force=True)

    def _get_client(self) -> XtDataClient:
        """延迟导入 xtdata，保证没有 QMT 时其他模块仍可导入。"""
        if self._client is not None:
            return self._client

        try:
            from xtquant import xtdata
        except ImportError as exc:
            raise RuntimeError(
                "未安装 xtquant，无法获取全市场实时行情"
            ) from exc

        self._client = cast(XtDataClient, cast(ModuleType, xtdata))
        return self._client

    @staticmethod
    def _normalize_tick(
        symbol: str,
        raw_tick: Mapping[str, Any],
        fallback_datetime: datetime,
    ) -> TickSnapshot:
        """把 xtdata 字段转换为稳定的数据模型。"""
        timestamp: float = _as_float(raw_tick.get("time"))
        if timestamp > 0:
            tick_datetime: datetime = datetime.fromtimestamp(
                timestamp / 1_000,
                tz=fallback_datetime.tzinfo,
            )
        else:
            tick_datetime = fallback_datetime

        return TickSnapshot(
            symbol=symbol,
            datetime=tick_datetime,
            last_price=_as_float(raw_tick.get("lastPrice")),
            previous_close=_as_float(raw_tick.get("lastClose")),
            open_price=_as_float(raw_tick.get("open")),
            high_price=_as_float(raw_tick.get("high")),
            low_price=_as_float(raw_tick.get("low")),
            volume=_as_float(raw_tick.get("volume")),
            amount=_as_float(raw_tick.get("amount")),
            status=_as_int(raw_tick.get("stockStatus")),
        )

    @staticmethod
    def _is_a_share_symbol(symbol: str) -> bool:
        """过滤出沪深交易所的六位证券代码。"""
        code, separator, market = symbol.partition(".")
        return (
            bool(separator)
            and len(code) == 6
            and code.isdigit()
            and market in {"SH", "SZ"}
        )


def _as_float(value: Any) -> float:
    """容错转换 xtdata 数值字段。"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    """容错转换 xtdata 整数字段。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
