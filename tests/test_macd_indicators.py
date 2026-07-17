import unittest

from vnpy_ctastrategy.strategies.talib_indicators import (
    EmaMacdCalculator,
    calculate_ema,
)


class MacdIndicatorTest(unittest.TestCase):
    def test_streaming_results_match_original_algorithm(self) -> None:
        prices: list[float] = [
            10 + index * 0.03 + (index % 7) * 0.1
            for index in range(80)
        ]
        calculator = EmaMacdCalculator(12, 26, 9)

        expected: list[tuple[float, float, float]] = (
            _original_macd(prices, 12, 26, 9)
        )
        actual: list[tuple[float, float, float]] = [
            calculator.update(price).as_tuple()
            for price in prices
        ]

        for actual_row, expected_row in zip(
            actual,
            expected,
            strict=True,
        ):
            for actual_value, expected_value in zip(
                actual_row,
                expected_row,
                strict=True,
            ):
                self.assertAlmostEqual(
                    actual_value,
                    expected_value,
                    places=14,
                )

    def test_reset_clears_all_streaming_state(self) -> None:
        calculator = EmaMacdCalculator(3, 5, 2)
        for price in [10, 11, 12, 13, 14, 15]:
            calculator.update(price)

        self.assertTrue(calculator.close_prices)
        self.assertTrue(calculator.macd_values)

        calculator.reset()

        self.assertEqual(calculator.close_prices, [])
        self.assertEqual(calculator.macd_values, [])
        self.assertFalse(calculator.update(10).ready)

    def test_invalid_periods_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EmaMacdCalculator(fast_period=0)
        with self.assertRaises(ValueError):
            calculate_ema([1.0], 0)


def _original_macd(
    prices: list[float],
    fast_period: int,
    slow_period: int,
    signal_period: int,
) -> list[tuple[float, float, float]]:
    close_prices: list[float] = []
    macd_values: list[float] = []
    results: list[tuple[float, float, float]] = []

    for close_price in prices:
        close_prices.append(close_price)
        if len(close_prices) < max(fast_period, slow_period):
            results.append((0.0, 0.0, 0.0))
            continue

        macd_line: float = (
            _original_ema(close_prices, fast_period)
            - _original_ema(close_prices, slow_period)
        )
        macd_values.append(macd_line)
        if len(macd_values) < signal_period:
            signal_line: float = 0.0
        else:
            signal_line = _original_ema(
                macd_values[-signal_period:],
                signal_period,
            )
        results.append(
            (
                macd_line,
                signal_line,
                macd_line - signal_line,
            )
        )

    return results


def _original_ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0

    alpha: float = 2.0 / (period + 1)
    ema: float = float(values[0])
    for value in values[1:]:
        ema = alpha * value + (1 - alpha) * ema
    return ema


if __name__ == "__main__":
    unittest.main()
