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
    """全市场上涨、下跌和收益分布统计。

    家数类字段的涨跌判定由 MarketBreadthCalculator 的阈值决定，默认：
    平盘 epsilon = 0.001%（涨幅绝对值 <= 该阈值视为平盘），
    涨跌停阈值 = 9.5%（涨幅 >= 视为涨停，<= -阈值视为跌停）。
    """

    universe_size: int = 0  # 股票池标的总数，含无有效行情的标的
    valid_count: int = 0  # 有有效 Tick、可统计涨跌幅的标的数
    advancing: int = 0  # 上涨家数（涨幅 > 0.001%）
    declining: int = 0  # 下跌家数（涨幅 < -0.001%）
    unchanged: int = 0  # 平盘家数（涨幅落在 ±0.001% 之间）
    invalid_count: int = 0  # Tick 缺失或价格无效的标的数
    limit_up: int = 0  # 涨停家数（涨幅 >= 9.5%）
    limit_down: int = 0  # 跌停家数（涨幅 <= -9.5%）
    advance_ratio: float = 0.0  # 上涨占比 = advancing / valid_count
    decline_ratio: float = 0.0  # 下跌占比 = declining / valid_count
    net_advance_ratio: float = 0.0  # 净涨跌占比 = (advancing - declining) / valid_count，取值 [-1, 1]
    advance_decline_ratio: float = 0.0  # 涨跌家数比 = advancing / declining；无下跌时退化为 advancing 裸值，非比值
    average_change_percent: float = 0.0  # 有效标的涨跌幅算术平均，单位 %
    median_change_percent: float = 0.0  # 有效标的涨跌幅中位数，单位 %


@dataclass(frozen=True, slots=True)
class SectorState:
    """单个 QMT 板块的盘中宽度和强弱状态。"""

    name: str
    breadth: MarketBreadth
    score: float
    level: SentimentLevel


@dataclass(frozen=True, slots=True)
class MarketSentimentSnapshot:
    """供多个 CTA 策略共享读取的市场情绪快照。

    score 由 SentimentCalculator 合成：宽度(权重0.55)、指数(0.30)、板块(0.15)
    加权（权重归一，缺项按可用权重降级）；宽度分=净涨跌占比分*0.75+平均涨幅分*0.25，
    指数分=有效指数平均涨幅映射，板块分=最强3个板块均值。level 由 score 经 classify
    映射：<20 极弱 / <40 偏弱 / <60 中性 / <80 偏强 / >=80 极强，有效标的不足时为
    不可用。stale/error 反映缓存新鲜度与降级/异常状态。
    """

    datetime: datetime  # 快照所基于的行情时间（数据提供器返回），非策略读取时刻
    score: float  # 0~100 综合情绪分，见类说明；有效标的不足时为 0.0
    level: SentimentLevel  # 情绪分级，由 score 经 classify 映射；有效标的不足时为 UNAVAILABLE
    breadth: MarketBreadth  # 全市场宽度统计（涨跌家数、涨跌停、涨跌幅分布等）
    indices: dict[str, IndexState] = field(default_factory=dict)  # 主要指数状态，键为指数代码，含缺失/无效项(valid=False)
    sectors: dict[str, SectorState] = field(default_factory=dict)  # 板块状态，键为板块名，仅含有效标的数达阈值(默认5)的板块
    stock_sectors: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 股票到板块的反向多对多映射，键为股票代码，值为所属板块名元组(已排序)
    sector_score: float = 0.0  # 参与总分计算的板块分=最强3个板块 score 的均值；无板块时为 0.0
    source_count: int = 0  # 数据提供器返回的 Tick 总数，反映本次采样覆盖面
    stale: bool = False  # 缓存是否过期（距上次成功刷新或行情时间超过 stale_after，默认30s），策略据此判断是否信任本快照
    error: str = ""  # 降级/异常提示：正常为空；缺指数或板块时降级提示；刷新抛异常时为异常信息；首次刷新前为占位提示

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
