"""可供多个 CTA 策略复用的流式 MACD 计算组件。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MacdResult:
    """单次 MACD 更新结果。"""

    macd: float
    signal: float
    histogram: float
    ready: bool

    def as_tuple(self) -> tuple[float, float, float]:
        """以策略常用的三元组形式返回指标值。"""
        return self.macd, self.signal, self.histogram


class EmaMacdCalculator:
    """
    保持 BottomFrictionMacdStrategy 原有算法的流式计算器。

    该实现使用自定义 EMA 初始值和信号线预热规则，目的是保证重构
    前后的实盘信号一致；它与 TA-Lib MACD 的预热结果可能不同。
    """

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> None:
        if fast_period <= 0:
            raise ValueError("fast_period 必须大于0")
        if slow_period <= 0:
            raise ValueError("slow_period 必须大于0")
        if signal_period <= 0:
            raise ValueError("signal_period 必须大于0")

        self.fast_period: int = fast_period
        self.slow_period: int = slow_period
        self.signal_period: int = signal_period
        self.close_prices: list[float] = []
        self.macd_values: list[float] = []

    def update(self, close_price: float) -> MacdResult:
        """输入一根已完成 K 线的收盘价并返回最新 MACD。"""
        self.close_prices.append(float(close_price))

        if len(self.close_prices) < max(
            self.fast_period,
            self.slow_period,
        ):
            return MacdResult(0.0, 0.0, 0.0, ready=False)

        ema_fast: float = calculate_ema(
            self.close_prices,
            self.fast_period,
        )
        ema_slow: float = calculate_ema(
            self.close_prices,
            self.slow_period,
        )
        macd_line: float = ema_fast - ema_slow

        self.macd_values.append(macd_line)
        if len(self.macd_values) < self.signal_period:
            signal_line: float = 0.0
        else:
            signal_line = calculate_ema(
                self.macd_values[-self.signal_period:],
                self.signal_period,
            )

        return MacdResult(
            macd=macd_line,
            signal=signal_line,
            histogram=macd_line - signal_line,
            ready=True,
        )

    def reset(self) -> None:
        """清空价格与指标状态，例如用于切换交易日。"""
        self.close_prices.clear()
        self.macd_values.clear()


def calculate_ema(values: list[float], period: int) -> float:
    """使用首个输入值作为初始值计算指数移动平均。"""
    if period <= 0:
        raise ValueError("period 必须大于0")
    if not values:
        return 0.0

    alpha: float = 2.0 / (period + 1)
    ema: float = float(values[0])
    for value in values[1:]:
        ema = alpha * value + (1 - alpha) * ema
    return ema
