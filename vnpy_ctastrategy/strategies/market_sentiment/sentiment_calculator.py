"""根据指数表现和市场宽度合成市场情绪信号。"""

from collections.abc import Mapping
from datetime import datetime
from statistics import fmean

from .models import (
    IndexState,
    MarketBreadth,
    MarketSentimentSnapshot,
    SectorState,
    SentimentLevel,
)


class SentimentCalculator:
    """把市场宽度与指数强弱合成为0到100的情绪分数。"""

    def __init__(
        self,
        breadth_weight: float = 0.55,
        index_weight: float = 0.30,
        sector_weight: float = 0.15,
        stock_change_scale: float = 2.0,
        index_change_scale: float = 2.0,
    ) -> None:
        if (
            breadth_weight < 0
            or index_weight < 0
            or sector_weight < 0
        ):
            raise ValueError("情绪权重不能小于0")
        if breadth_weight + index_weight + sector_weight <= 0:
            raise ValueError("情绪权重之和必须大于0")
        if stock_change_scale <= 0 or index_change_scale <= 0:
            raise ValueError("涨跌幅缩放参数必须大于0")

        total_weight: float = (
            breadth_weight + index_weight + sector_weight
        )
        self.breadth_weight: float = breadth_weight / total_weight
        self.index_weight: float = index_weight / total_weight
        self.sector_weight: float = sector_weight / total_weight
        self.stock_change_scale: float = stock_change_scale
        self.index_change_scale: float = index_change_scale

    def calculate(
        self,
        snapshot_datetime: datetime,
        breadth: MarketBreadth,
        indices: Mapping[str, IndexState],
        source_count: int,
        sectors: Mapping[str, SectorState] | None = None,
        stock_sectors: Mapping[str, tuple[str, ...]] | None = None,
    ) -> MarketSentimentSnapshot:
        """生成综合市场情绪快照。"""
        if breadth.valid_count <= 0:
            return MarketSentimentSnapshot(
                datetime=snapshot_datetime,
                score=0.0,
                level=SentimentLevel.UNAVAILABLE,
                breadth=breadth,
                indices=dict(indices),
                sectors=dict(sectors or {}),
                stock_sectors=dict(stock_sectors or {}),
                source_count=source_count,
                error="没有足够的有效股票行情",
            )

        # 净上涨家数比例天然位于[-1, 1]，映射至[0, 100]。
        breadth_count_score: float = _clamp(
            50 + breadth.net_advance_ratio * 50
        )
        breadth_return_score: float = _change_to_score(
            breadth.average_change_percent,
            self.stock_change_scale,
        )
        breadth_score: float = (
            breadth_count_score * 0.75
            + breadth_return_score * 0.25
        )

        valid_indices: list[IndexState] = [
            state for state in indices.values() if state.valid
        ]
        component_scores: list[tuple[float, float]] = [
            (breadth_score, self.breadth_weight)
        ]
        if valid_indices:
            index_change: float = fmean(
                state.change_percent for state in valid_indices
            )
            index_score: float = _change_to_score(
                index_change,
                self.index_change_scale,
            )
            component_scores.append((index_score, self.index_weight))

        sector_states: list[SectorState] = list(
            (sectors or {}).values()
        )
        if sector_states:
            # 强板块只能小幅修正总分，不能覆盖全市场宽度结论。
            strongest: list[SectorState] = sorted(
                sector_states,
                key=lambda state: state.score,
                reverse=True,
            )[:3]
            sector_score: float = fmean(
                state.score for state in strongest
            )
            component_scores.append((sector_score, self.sector_weight))
        else:
            sector_score = 0.0

        available_weight: float = sum(
            weight for _, weight in component_scores
        )
        score: float = sum(
            component_score * weight
            for component_score, weight in component_scores
        ) / available_weight

        score = _clamp(score)
        missing_sources: list[str] = []
        if not valid_indices:
            missing_sources.append("指数行情")
        if not sector_states:
            missing_sources.append("板块行情")
        error: str = ""
        if missing_sources:
            error = f"{'、'.join(missing_sources)}不可用，已降级计算"

        return MarketSentimentSnapshot(
            datetime=snapshot_datetime,
            score=score,
            level=self.classify(score),
            breadth=breadth,
            indices=dict(indices),
            sectors=dict(sectors or {}),
            stock_sectors=dict(stock_sectors or {}),
            sector_score=sector_score,
            source_count=source_count,
            error=error,
        )

    @staticmethod
    def classify(score: float) -> SentimentLevel:
        """将连续分数映射为情绪等级。"""
        if score < 20:
            return SentimentLevel.EXTREME_BEARISH
        if score < 40:
            return SentimentLevel.BEARISH
        if score < 60:
            return SentimentLevel.NEUTRAL
        if score < 80:
            return SentimentLevel.BULLISH
        return SentimentLevel.EXTREME_BULLISH


def _change_to_score(change_percent: float, scale: float) -> float:
    """将[-scale, scale]附近的涨跌幅线性映射至[0, 100]。"""
    return _clamp(50 + change_percent / scale * 50)


def _clamp(value: float) -> float:
    """把分数限制在0到100。"""
    return max(0.0, min(100.0, float(value)))
