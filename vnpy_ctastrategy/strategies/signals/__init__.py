"""多个 CTA 策略共享的信号模块：基类 + bar/tick 信号链 + 摩擦/情绪厚信号。

统一两层信号架构（目标仓位制）：
- ``base``：``SignalType`` / ``SignalResult`` / ``SubSignal`` /
  ``PriorityCompositeSubSignal`` / ``SignalAggregator`` / executor 分派键 ``EXECUTOR_*``
- ``bar_signals``：``StopLossSubSignal`` / ``BreakMa10SubSignal`` / ``SentimentRiskSubSignal`` /
  ``MaAlignmentSubSignal`` / ``BelowBaseSubSignal`` / ``FrictionSubSignal`` /
  ``BottomFrictionBarPriority`` / ``BottomFrictionBarSignal``（BottomFriction 的 bar 决策链）
- ``tick_signals``：``ProfitDrawdownSubSignal`` / ``FrictionTakeProfitSubSignal`` /
  ``BottomFrictionTickPriority`` / ``BottomFrictionTickSignal``（BottomFriction 的 tick 保护链）
- ``friction``：``FrictionResult`` / ``FrictionSignal``（MACD 摩擦仓厚信号）
- ``market_sentiment``：``MarketSentimentSignal`` / ``SentimentResult`` / ``EMPTY_RESULT``
  （大盘情绪厚信号）
"""

from .base import (
    EXECUTOR_DRAWDOWN,
    EXECUTOR_FRICTION_TP,
    EXECUTOR_STOP_EXIT,
    EXECUTOR_TARGET,
    PriorityCompositeSubSignal,
    SignalAggregator,
    SignalResult,
    SignalType,
    SubSignal,
)
from .bar_signals import (
    BelowBaseSubSignal,
    BottomFrictionBarPriority,
    BottomFrictionBarSignal,
    BreakMa10SubSignal,
    FrictionSubSignal,
    MaAlignmentSubSignal,
    ProfitDrawdownBarSubSignal,
    SectorStrongStopExitSubSignal,
    SentimentRiskSubSignal,
    StopLossSubSignal,
)
from .tick_signals import (
    BottomFrictionTickPriority,
    BottomFrictionTickSignal,
    FrictionTakeProfitSubSignal,
    ProfitDrawdownSubSignal,
)
from .friction import FrictionResult, FrictionSignal
from .market_sentiment import (
    EMPTY_RESULT,
    SECTOR_STRONG_ENTRY_MARK,
    MarketSentimentSignal,
    SentimentResult,
)
from .profit_drawdown import ProfitDrawdownState

__all__ = [
    # base
    "SignalType",
    "SignalResult",
    "SubSignal",
    "PriorityCompositeSubSignal",
    "SignalAggregator",
    "EXECUTOR_TARGET",
    "EXECUTOR_DRAWDOWN",
    "EXECUTOR_FRICTION_TP",
    "EXECUTOR_STOP_EXIT",
    # bar
    "ProfitDrawdownBarSubSignal",
    "StopLossSubSignal",
    "BreakMa10SubSignal",
    "SentimentRiskSubSignal",
    "MaAlignmentSubSignal",
    "BelowBaseSubSignal",
    "FrictionSubSignal",
    "SectorStrongStopExitSubSignal",
    "BottomFrictionBarPriority",
    "BottomFrictionBarSignal",
    # tick
    "ProfitDrawdownSubSignal",
    "FrictionTakeProfitSubSignal",
    "BottomFrictionTickPriority",
    "BottomFrictionTickSignal",
    # friction
    "FrictionResult",
    "FrictionSignal",
    # market_sentiment
    "EMPTY_RESULT",
    "SECTOR_STRONG_ENTRY_MARK",
    "MarketSentimentSignal",
    "SentimentResult",
    # profit_drawdown
    "ProfitDrawdownState",
]
