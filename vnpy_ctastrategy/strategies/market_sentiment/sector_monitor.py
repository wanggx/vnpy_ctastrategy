"""QMT 多对多板块成员关系和板块强弱计算。"""

from collections.abc import Mapping, Sequence

from .market_breadth import MarketBreadthCalculator
from .models import SectorState, TickSnapshot
from .sentiment_calculator import SentimentCalculator


class SectorMonitor:
    """按板块分别计算宽度，同时保留股票所属的多个板块。"""

    def __init__(
        self,
        breadth_calculator: MarketBreadthCalculator | None = None,
        minimum_valid_count: int = 5,
        stock_change_scale: float = 2.0,
    ) -> None:
        if minimum_valid_count <= 0:
            raise ValueError("minimum_valid_count 必须大于0")
        if stock_change_scale <= 0:
            raise ValueError("stock_change_scale 必须大于0")

        self.breadth_calculator: MarketBreadthCalculator = (
            breadth_calculator or MarketBreadthCalculator()
        )
        self.minimum_valid_count: int = minimum_valid_count
        self.stock_change_scale: float = stock_change_scale

    def calculate(
        self,
        ticks: Mapping[str, TickSnapshot],
        sector_members: Mapping[str, Sequence[str]],
    ) -> tuple[dict[str, SectorState], dict[str, tuple[str, ...]]]:
        """计算板块状态与股票到板块的反向多对多映射。"""
        sectors: dict[str, SectorState] = {}
        reverse_members: dict[str, list[str]] = {}

        for sector_name, raw_members in sector_members.items():
            # 板块内部去重，但不跨板块去重。
            members: tuple[str, ...] = tuple(dict.fromkeys(raw_members))
            breadth = self.breadth_calculator.calculate(ticks, members)
            if breadth.valid_count < self.minimum_valid_count:
                continue

            count_score: float = _clamp(
                50 + breadth.net_advance_ratio * 50
            )
            return_score: float = _clamp(
                50
                + breadth.average_change_percent
                / self.stock_change_scale
                * 50
            )
            score: float = count_score * 0.75 + return_score * 0.25
            sectors[sector_name] = SectorState(
                name=sector_name,
                breadth=breadth,
                score=score,
                level=SentimentCalculator.classify(score),
            )

            for symbol in members:
                reverse_members.setdefault(symbol, []).append(sector_name)

        stock_sectors: dict[str, tuple[str, ...]] = {
            symbol: tuple(sorted(names))
            for symbol, names in reverse_members.items()
        }
        return sectors, stock_sectors


def _clamp(value: float) -> float:
    """把分数限制在0到100。"""
    return max(0.0, min(100.0, float(value)))
