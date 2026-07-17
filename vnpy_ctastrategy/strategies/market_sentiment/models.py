"""市场行情快照和情绪计算结果的数据模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite


class SentimentLevel(Enum):
    """市场情绪分级。"""

    EXTREME_BEARISH = "极弱"
    BEARISH = "偏弱"
    NEUTRAL = "中性"
    BULLISH = "偏强"
    EXTREME_BULLISH = "极强"
    UNAVAILABLE = "不可用"


@dataclass(frozen=True, slots=True)
class TickSnapshot:
    """从 xtdata 全量 Tick 中标准化出的单证券行情。"""

    symbol: str
    datetime: datetime
    last_price: float
    previous_close: float
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    status: int = 0

    @property
    def valid(self) -> bool:
        """价格是否足以参与涨跌统计。"""
        return (
            isfinite(self.last_price)
            and isfinite(self.previous_close)
            and self.last_price > 0
            and self.previous_close > 0
        )

    @property
    def change_percent(self) -> float:
        """相对前收盘价的涨跌幅，单位为百分比。"""
        if not self.valid:
            return 0.0
        return (self.last_price / self.previous_close - 1) * 100


@dataclass(frozen=True, slots=True)
class IndexState:
    """单个市场指数的盘中状态。"""

    symbol: str
    name: str
    last_price: float
    previous_close: float
    change_percent: float
    datetime: datetime
    valid: bool


@dataclass(frozen=True, slots=True)
class MarketBreadth:
    """全市场上涨、下跌和收益分布统计。"""

    universe_size: int = 0
    valid_count: int = 0
    advancing: int = 0
    declining: int = 0
    unchanged: int = 0
    invalid_count: int = 0
    limit_up: int = 0
    limit_down: int = 0
    advance_ratio: float = 0.0
    decline_ratio: float = 0.0
    net_advance_ratio: float = 0.0
    advance_decline_ratio: float = 0.0
    average_change_percent: float = 0.0
    median_change_percent: float = 0.0


@dataclass(frozen=True, slots=True)
class SectorState:
    """单个 QMT 板块的盘中宽度和强弱状态。"""

    name: str
    breadth: MarketBreadth
    score: float
    level: SentimentLevel


@dataclass(frozen=True, slots=True)
class MarketSentimentSnapshot:
    """供多个 CTA 策略共享读取的市场情绪快照。"""

    datetime: datetime
    score: float
    level: SentimentLevel
    breadth: MarketBreadth
    indices: dict[str, IndexState] = field(default_factory=dict)
    sectors: dict[str, SectorState] = field(default_factory=dict)
    stock_sectors: dict[str, tuple[str, ...]] = field(default_factory=dict)
    sector_score: float = 0.0
    source_count: int = 0
    stale: bool = False
    error: str = ""

    @property
    def available(self) -> bool:
        """当前快照是否可用于交易判断。"""
        return (
            self.level != SentimentLevel.UNAVAILABLE
            and self.breadth.valid_count > 0
            and not self.stale
        )

    @property
    def risk_off(self) -> bool:
        """是否处于偏弱或极弱的风险规避状态。"""
        return self.level in {
            SentimentLevel.EXTREME_BEARISH,
            SentimentLevel.BEARISH,
        }

    def get_stock_sectors(self, symbol: str) -> tuple[str, ...]:
        """返回某个标的所属的全部板块。"""
        return self.stock_sectors.get(symbol, ())

    def get_strong_sectors(
        self,
        limit: int = 10,
        minimum_score: float = 60,
    ) -> list[SectorState]:
        """按分数从高到低返回强势板块。"""
        states: list[SectorState] = [
            state
            for state in self.sectors.values()
            if state.score >= minimum_score
        ]
        states.sort(key=lambda state: state.score, reverse=True)
        return states[:max(0, limit)]
