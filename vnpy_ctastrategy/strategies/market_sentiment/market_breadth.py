"""上涨、下跌和平盘家数等市场宽度指标计算模块。"""

from collections.abc import Iterable, Mapping
from statistics import fmean, median

from .models import MarketBreadth, TickSnapshot


class MarketBreadthCalculator:
    """根据全市场 Tick 计算涨跌家数和收益分布。"""

    def __init__(
        self,
        unchanged_epsilon: float = 0.001,
        limit_threshold: float = 9.5,
    ) -> None:
        if unchanged_epsilon < 0:
            raise ValueError("unchanged_epsilon 不能小于0")
        if limit_threshold <= 0:
            raise ValueError("limit_threshold 必须大于0")

        self.unchanged_epsilon: float = unchanged_epsilon
        self.limit_threshold: float = limit_threshold

    def calculate(
        self,
        ticks: Mapping[str, TickSnapshot],
        stock_symbols: Iterable[str],
    ) -> MarketBreadth:
        """计算指定股票池的市场宽度。"""
        symbols: tuple[str, ...] = tuple(stock_symbols)
        changes: list[float] = []
        advancing: int = 0
        declining: int = 0
        unchanged: int = 0
        invalid_count: int = 0
        limit_up: int = 0
        limit_down: int = 0

        for symbol in symbols:
            tick: TickSnapshot | None = ticks.get(symbol)
            if tick is None or not tick.valid:
                invalid_count += 1
                continue

            change: float = tick.change_percent
            changes.append(change)

            if change > self.unchanged_epsilon:
                advancing += 1
            elif change < -self.unchanged_epsilon:
                declining += 1
            else:
                unchanged += 1

            if change >= self.limit_threshold:
                limit_up += 1
            elif change <= -self.limit_threshold:
                limit_down += 1

        valid_count: int = len(changes)
        if valid_count:
            advance_ratio: float = advancing / valid_count
            decline_ratio: float = declining / valid_count
            net_advance_ratio: float = (
                advancing - declining
            ) / valid_count
            average_change: float = fmean(changes)
            median_change: float = float(median(changes))
        else:
            advance_ratio = 0.0
            decline_ratio = 0.0
            net_advance_ratio = 0.0
            average_change = 0.0
            median_change = 0.0

        if declining:
            advance_decline_ratio: float = advancing / declining
        elif advancing:
            advance_decline_ratio = float(advancing)
        else:
            advance_decline_ratio = 0.0

        return MarketBreadth(
            universe_size=len(symbols),
            valid_count=valid_count,
            advancing=advancing,
            declining=declining,
            unchanged=unchanged,
            invalid_count=invalid_count,
            limit_up=limit_up,
            limit_down=limit_down,
            advance_ratio=advance_ratio,
            decline_ratio=decline_ratio,
            net_advance_ratio=net_advance_ratio,
            advance_decline_ratio=advance_decline_ratio,
            average_change_percent=average_change,
            median_change_percent=median_change,
        )
