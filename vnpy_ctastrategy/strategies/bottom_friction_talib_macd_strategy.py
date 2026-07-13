import numpy as np
import talib

from . import bottom_friction_macd_strategy


class BottomFrictionTalibMacdStrategy(
    bottom_friction_macd_strategy.BottomFrictionMacdStrategy
):
    """
    使用 TA-Lib 计算 MACD 的底仓 + 摩擦仓位盘中做 T 策略。

    交易、仓位上限和活动买单预占逻辑继承自
    BottomFrictionMacdStrategy，仅替换 MACD 的计算实现。
    """

    author = "Copilot"

    def _calc_macd(self, close_price: float) -> tuple[float, float, float]:
        """使用 TA-Lib 基于最新 1 分钟收盘价序列计算 MACD。"""
        self.close_prices.append(float(close_price))
        close_prices: np.ndarray = np.asarray(self.close_prices, dtype=np.float64)

        macd_lines, signal_lines, macd_hists = talib.MACD(
            close_prices,
            fastperiod=self.macd_fast,
            slowperiod=self.macd_slow,
            signalperiod=self.macd_signal,
        )

        macd_line: float = float(macd_lines[-1])
        signal_line: float = float(signal_lines[-1])
        macd_hist: float = float(macd_hists[-1])

        if np.isnan(macd_line) or np.isnan(signal_line) or np.isnan(macd_hist):
            self.write_log(
                f"MACD计算来源=TA-Lib，收盘价={close_price:.2f}，"
                "MACD=0.000000，Signal=0.000000，Hist=0.000000（数据不足）"
            )
            return 0.0, 0.0, 0.0

        self.write_log(
            f"MACD计算来源=TA-Lib，收盘价={close_price:.2f}，"
            f"MACD={macd_line:.6f}，Signal={signal_line:.6f}，Hist={macd_hist:.6f}"
        )
        return macd_line, signal_line, macd_hist
