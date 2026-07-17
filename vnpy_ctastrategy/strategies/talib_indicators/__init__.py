"""多个 CTA 策略共享的 TA-Lib 和技术指标分析模块。"""

from .macd import EmaMacdCalculator, MacdResult, calculate_ema


__all__ = [
    "EmaMacdCalculator",
    "MacdResult",
    "calculate_ema",
]
