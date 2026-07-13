"""比较两个底仓摩擦策略的 MACD 计算结果。"""

from pathlib import Path
import sys
from typing import TypeAlias

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy_ctastrategy.strategies.bottom_friction_macd_strategy import (  # noqa: E402
    BottomFrictionMacdStrategy,
)
from vnpy_ctastrategy.strategies.bottom_friction_talib_macd_strategy import (  # noqa: E402
    BottomFrictionTalibMacdStrategy,
)


MacdStrategyType: TypeAlias = type[BottomFrictionMacdStrategy]


class DummyEngine:
    """为直接调用策略指标方法提供空日志实现。"""

    def write_log(self, msg: str, strategy: BottomFrictionMacdStrategy) -> None:
        print(f"{strategy.strategy_name}: {msg}")
        pass


def build_close_prices(count: int = 200) -> np.ndarray:
    """生成包含趋势和周期波动的确定性测试行情。"""
    indexes: np.ndarray = np.arange(count, dtype=np.float64)
    return 10 + indexes * 0.03 + np.sin(indexes / 4) + np.cos(indexes / 11) * 0.4


def calculate_macd(
    strategy_class: MacdStrategyType,
    close_prices: np.ndarray,
) -> np.ndarray:
    """逐根向策略输入收盘价并收集 MACD、Signal 和 Hist。"""
    strategy = strategy_class(DummyEngine(), "macd_compare", "DEMO.LOCAL", {})
    values = [strategy._calc_macd(float(price)) for price in close_prices]
    return np.asarray(values, dtype=np.float64)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    close_prices: np.ndarray = build_close_prices()
    original_values: np.ndarray = calculate_macd(
        BottomFrictionMacdStrategy,
        close_prices,
    )
    talib_values: np.ndarray = calculate_macd(
        BottomFrictionTalibMacdStrategy,
        close_prices,
    )

    # TA-Lib MACD(12, 26, 9) 的首个有效下标为 26 + 9 - 2 = 33。
    first_valid_index: int = (
        BottomFrictionMacdStrategy.macd_slow
        + BottomFrictionMacdStrategy.macd_signal
        - 2
    )
    differences: np.ndarray = np.abs(original_values - talib_values)
    valid_differences: np.ndarray = differences[first_valid_index:]
    tolerance: float = 1e-10
    different_rows: np.ndarray = np.flatnonzero(
        np.any(valid_differences > tolerance, axis=1)
    )

    labels: tuple[str, str, str] = ("MACD", "Signal", "Hist")
    print("比较范围：TA-Lib 指标进入有效期后的数据")
    for column, label in enumerate(labels):
        print(f"{label:>6} 最大绝对差值：{np.max(valid_differences[:, column]):.12f}")

    if different_rows.size:
        first_difference: int = first_valid_index + int(different_rows[0])
        print(f"结论：两个实现存在差异，首次差异下标={first_difference}")
    else:
        print(f"结论：两个实现在容差 {tolerance} 内没有差异")

    print("\n最后 5 根数据：")
    print("index  close       implementation       MACD        Signal      Hist")
    for index in range(len(close_prices) - 5, len(close_prices)):
        original = original_values[index]
        talib_result = talib_values[index]
        print(
            f"{index:>5}  {close_prices[index]:>8.4f}  original       "
            f"{original[0]:>10.6f}  {original[1]:>10.6f}  {original[2]:>10.6f}"
        )
        print(
            f"{'':>5}  {'':>8}  ta-lib        "
            f"{talib_result[0]:>10.6f}  {talib_result[1]:>10.6f}  {talib_result[2]:>10.6f}"
        )


if __name__ == "__main__":
    main()
