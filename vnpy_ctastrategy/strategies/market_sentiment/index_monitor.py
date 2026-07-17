"""上证、深证等主要市场指数的盘中强弱监控模块。"""

from collections.abc import Mapping
from datetime import datetime

from .models import IndexState, TickSnapshot
from .xtdata_provider import DEFAULT_INDEX_SYMBOLS


class IndexMonitor:
    """从标准化 Tick 中计算主要指数状态。"""

    def __init__(
        self,
        index_symbols: Mapping[str, str] | None = None,
    ) -> None:
        self.index_symbols: dict[str, str] = dict(
            index_symbols or DEFAULT_INDEX_SYMBOLS
        )

    def calculate(
        self,
        ticks: Mapping[str, TickSnapshot],
        fallback_datetime: datetime,
    ) -> dict[str, IndexState]:
        """计算每个配置指数相对前收盘价的涨跌幅。"""
        states: dict[str, IndexState] = {}
        for symbol, name in self.index_symbols.items():
            tick: TickSnapshot | None = ticks.get(symbol)
            if tick is None:
                states[symbol] = IndexState(
                    symbol=symbol,
                    name=name,
                    last_price=0.0,
                    previous_close=0.0,
                    change_percent=0.0,
                    datetime=fallback_datetime,
                    valid=False,
                )
                continue

            states[symbol] = IndexState(
                symbol=symbol,
                name=name,
                last_price=tick.last_price,
                previous_close=tick.previous_close,
                change_percent=tick.change_percent,
                datetime=tick.datetime,
                valid=tick.valid,
            )

        return states
