"""摩擦仓信号：内聚 MACD 计算，上穿加摩擦仓、下穿减摩擦仓回底仓，输出目标仓位与 MACD 值。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..talib_indicators import EmaMacdCalculator, MacdResult

if TYPE_CHECKING:
    from vnpy.trader.object import BarData


@dataclass(frozen=True, slots=True)
class FrictionResult:
    """单次摩擦仓信号评估结果。"""

    target_pos: int      # 目标仓位
    mark: str            # 下单标注（空串=维持当前仓位）
    entered: bool        # 是否本次上穿买入摩擦仓（策略据此记录 friction_entry_price）
    macd_line: float     # 最新 MACD 线（供策略同步 UI 变量）
    signal_line: float    # 最新信号线
    macd_hist: float      # 最新 MACD 柱
    last_macd_hist: float  # 前一根 MACD 柱


class FrictionSignal:
    """摩擦仓信号，内聚 MACD 计算。

    内部持有 EmaMacdCalculator 与 prev_macd_hist 状态，on_bar(bar) 自行算 MACD、
    判上下穿、决定摩擦仓目标仓位，并回传 MACD 值供策略同步 UI 变量。
    不下单、不写策略状态；策略调 on_bar 后自行下单并记录入场价。
    """

    def __init__(
        self,
        strategy: Any,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
    ) -> None:
        self.strategy = strategy
        self.macd_calculator: EmaMacdCalculator = EmaMacdCalculator(
            fast_period=macd_fast,
            slow_period=macd_slow,
            signal_period=macd_signal,
        )
        # 前一根 MACD 柱，用于判定上穿/下穿
        self.prev_macd_hist: float = 0.0

    def reset(self) -> None:
        """重置 MACD 计算状态，例如 on_init 或切换交易日时调用。"""
        self.macd_calculator.reset()
        self.prev_macd_hist = 0.0

    def _calc_macd(self, close_price: float) -> tuple[float, float, float]:
        """通过共享流式组件计算最新 1 分钟 MACD。"""
        result: MacdResult = self.macd_calculator.update(close_price)
        if not result.ready:
            return 0.0, 0.0, 0.0
        macd, signal, histogram = result.as_tuple()
        return round(macd, 2), round(signal, 2), round(histogram, 2)

    def on_bar(self, bar: BarData) -> FrictionResult:
        """计算 MACD、判定交叉、决定摩擦仓目标仓位。

        - 上穿（prev_hist<=0 且 hist>0）：目标 = 底仓 + 摩擦仓，entered=True
        - 下穿（prev_hist>=0 且 hist<0）且持仓超底仓：减摩擦仓回底仓
        - 其余：维持当前仓位
        MACD 值随结果回传，供策略同步 UI 变量。
        """
        macd_line, signal_line, macd_hist = self._calc_macd(bar.close_price)
        last_macd_hist: float = self.prev_macd_hist
        self.prev_macd_hist = macd_hist

        macd_cross_up: bool = last_macd_hist <= 0 and macd_hist > 0
        macd_cross_down: bool = last_macd_hist >= 0 and macd_hist < 0

        strategy = self.strategy

        if macd_cross_up:
            return FrictionResult(
                target_pos=strategy.base_size + strategy.friction_size,
                mark="MACD上穿零轴",
                entered=True,
                macd_line=macd_line,
                signal_line=signal_line,
                macd_hist=macd_hist,
                last_macd_hist=last_macd_hist,
            )

        if macd_cross_down:
            if strategy.pos > strategy.base_size and strategy.pos_avg_price > 0:
                current_profit: float = round(
                    (bar.close_price - strategy.pos_avg_price)
                    / strategy.pos_avg_price
                    * 100,
                    2,
                )
                # 既有逻辑：or True 使 should_reduce 恒为 True，下穿永远减仓。
                # 保留原样（逻辑不变原则）；profit_take 阈值检查为死代码。
                should_reduce: bool = (
                    strategy.profit_take_min_points
                    <= current_profit
                    <= strategy.profit_take_points
                    or True
                )
                if should_reduce:
                    return FrictionResult(
                        target_pos=max(
                            strategy.base_size,
                            strategy.pos - strategy.friction_size,
                        ),
                        mark="MACD下穿零轴，减摩擦仓",
                        entered=False,
                        macd_line=macd_line,
                        signal_line=signal_line,
                        macd_hist=macd_hist,
                        last_macd_hist=last_macd_hist,
                    )
                return FrictionResult(
                    target_pos=strategy.pos,
                    mark="",
                    entered=False,
                    macd_line=macd_line,
                    signal_line=signal_line,
                    macd_hist=macd_hist,
                    last_macd_hist=last_macd_hist,
                )
            return FrictionResult(
                target_pos=strategy.pos,
                mark="",
                entered=False,
                macd_line=macd_line,
                signal_line=signal_line,
                macd_hist=macd_hist,
                last_macd_hist=last_macd_hist,
            )

        return FrictionResult(
            target_pos=strategy.pos,
            mark="",
            entered=False,
            macd_line=macd_line,
            signal_line=signal_line,
            macd_hist=macd_hist,
            last_macd_hist=last_macd_hist,
        )
