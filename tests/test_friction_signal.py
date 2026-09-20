import unittest
from types import SimpleNamespace

from vnpy_ctastrategy.strategies.signals import FrictionResult, FrictionSignal


def _make_strategy(**overrides) -> SimpleNamespace:
    """构造最小 mock strategy。"""
    defaults = dict(
        base_size=1000,
        friction_size=500,
        pos=1500,
        pos_avg_price=10.0,
        profit_take_min_points=2.0,
        profit_take_points=3.0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_bar(close_price: float) -> SimpleNamespace:
    return SimpleNamespace(close_price=close_price)


def _accelerating_up(n: int, start: float = 10.0) -> list[float]:
    """加速上涨（涨幅递增），驱动 MACD 柱持续放大并上穿零轴。"""
    return [round(start + (i * i) * 0.05, 2) for i in range(n)]


def _decelerating_then_down(n: int, start: float) -> list[float]:
    """先减速再下跌，驱动 MACD 柱从正转负（下穿零轴）。"""
    prices: list[float] = []
    for i in range(n):
        # 涨幅递减到 0 再转负
        delta = (n / 2 - i) * 0.1
        prices.append(round(start + delta, 2))
    return prices


class FrictionSignalTest(unittest.TestCase):
    """摩擦仓信号测试：喂 close_price，Signal 内部算 MACD 判交叉出目标仓位。"""

    def setUp(self) -> None:
        # 用小周期让 MACD 快速就绪（len >= max(fast, slow) = 5）
        self.strategy = _make_strategy()
        self.signal = FrictionSignal(
            strategy=self.strategy,
            macd_fast=3,
            macd_slow=5,
            macd_signal=3,
        )

    def _feed(self, prices: list[float]) -> list[FrictionResult]:
        """喂入一组收盘价，返回每根 bar 的结果列表。"""
        results: list[FrictionResult] = []
        for price in prices:
            results.append(self.signal.on_bar(_make_bar(price)))
        return results

    # ---- MACD 值回传 ----

    def test_result_carries_macd_values(self) -> None:
        """结果回传 macd_line/signal_line/macd_hist/last_macd_hist 供策略同步 UI。"""
        results = self._feed(_accelerating_up(15))
        last = results[-1]

        self.assertIsInstance(last, FrictionResult)
        # 就绪后 macd_line 应为正（持续上涨）
        self.assertGreater(last.macd_line, 0.0)
        self.assertIsInstance(last.signal_line, float)
        self.assertIsInstance(last.macd_hist, float)
        self.assertIsInstance(last.last_macd_hist, float)

    def test_not_ready_returns_zero_macd(self) -> None:
        """MACD 未就绪（bar 数不足）时回传 0。"""
        result = self.signal.on_bar(_make_bar(10.0))  # 仅 1 根，未就绪

        self.assertEqual(result.macd_line, 0.0)
        self.assertEqual(result.signal_line, 0.0)
        self.assertEqual(result.macd_hist, 0.0)
        self.assertEqual(result.last_macd_hist, 0.0)
        self.assertFalse(result.entered)
        self.assertEqual(result.mark, "")

    def test_last_macd_hist_is_previous_bar_hist(self) -> None:
        """last_macd_hist 是前一根 bar 的 macd_hist。"""
        results = self._feed(_accelerating_up(15))

        # 末根的 last_macd_hist 应等于倒数第二根的 macd_hist
        self.assertEqual(results[-1].last_macd_hist, results[-2].macd_hist)

    # ---- 上穿 ----

    def test_cross_up_buys_friction_position(self) -> None:
        """加速上涨驱动 MACD 上穿：过程中出现目标=底仓+摩擦仓，entered=True。"""
        results = self._feed(_accelerating_up(15))

        entered_results = [r for r in results if r.entered]
        self.assertTrue(len(entered_results) > 0, "应至少出现一次上穿")
        first_cross = entered_results[0]
        self.assertEqual(first_cross.target_pos, 1500)  # 1000 + 500
        self.assertEqual(first_cross.mark, "MACD上穿零轴")

    def test_cross_up_signal_enables_entry_price_recording(self) -> None:
        """上穿时 entered=True，策略据此记录 friction_entry_price（验证标志）。"""
        results = self._feed(_accelerating_up(15))
        entered_results = [r for r in results if r.entered]

        self.assertTrue(len(entered_results) > 0)
        # 策略侧执行：self.friction_entry_price = bar.close_price
        # Signal 本身不写该属性，只返回 entered=True

    # ---- 下穿 ----

    def test_cross_down_reduces_friction_when_above_base(self) -> None:
        """先涨上穿再下跌驱动 MACD 下穿，持仓超底仓：减摩擦仓回底仓。"""
        prices = _accelerating_up(12)  # 先涨：上穿
        prices += _decelerating_then_down(20, start=prices[-1])  # 再跌：下穿
        results = self._feed(prices)

        reduce_results = [
            r for r in results if r.mark == "MACD下穿零轴，减摩擦仓"
        ]
        self.assertTrue(len(reduce_results) > 0, "应至少出现一次下穿减仓")
        first_reduce = reduce_results[0]
        self.assertFalse(first_reduce.entered)
        self.assertEqual(first_reduce.target_pos, 1000)  # max(1000, 1500-500)

    def test_cross_down_floor_respects_base_size(self) -> None:
        """减仓目标不低于底仓：pos-friction < base 时取 base。"""
        prices = _accelerating_up(12)
        prices += _decelerating_then_down(20, start=prices[-1])
        self.strategy.pos = 1200
        self.strategy.base_size = 1000
        self.strategy.friction_size = 500
        results = self._feed(prices)

        reduce_results = [
            r for r in results if r.mark == "MACD下穿零轴，减摩擦仓"
        ]
        self.assertTrue(len(reduce_results) > 0)
        # max(1000, 1200-500=700) = 1000
        self.assertEqual(reduce_results[0].target_pos, 1000)

    def test_cross_down_no_avg_price_holds_position(self) -> None:
        """下穿 + pos_avg_price=0：维持 pos，无减仓 mark。"""
        prices = _accelerating_up(12)
        prices += _decelerating_then_down(20, start=prices[-1])
        self.strategy.pos_avg_price = 0.0
        results = self._feed(prices)

        reduce_results = [
            r for r in results if r.mark == "MACD下穿零轴，减摩擦仓"
        ]
        self.assertEqual(len(reduce_results), 0)

    # ---- 无交叉 ----

    def test_no_cross_holds_position(self) -> None:
        """MACD 就绪但未交叉：维持 pos，无 mark，entered=False。"""
        results = self._feed(_accelerating_up(15))

        # 末根持续上涨，prev 和 hist 都正，未上穿
        last = results[-1]
        self.assertFalse(last.entered)
        self.assertEqual(last.target_pos, self.strategy.pos)
        self.assertEqual(last.mark, "")

    # ---- reset ----

    def test_reset_clears_macd_state(self) -> None:
        """reset() 清空 MACD 计算状态，重置后首根 bar 未就绪回传 0。"""
        self._feed(_accelerating_up(15))
        self.assertGreater(len(self.signal.macd_calculator.close_prices), 0)

        self.signal.reset()

        # reset 后状态已清空
        self.assertEqual(self.signal.macd_calculator.close_prices, [])
        self.assertEqual(self.signal.prev_macd_hist, 0.0)

        # 重置后首根 bar 未就绪，回传 0
        result = self.signal.on_bar(_make_bar(10.0))
        self.assertEqual(result.macd_hist, 0.0)
        self.assertEqual(result.last_macd_hist, 0.0)

    def test_reset_on_trading_day_switch(self) -> None:
        """模拟换交易日 reset：前一日算过的 MACD 状态被清空，新日重新计算。"""
        self._feed(_accelerating_up(15))
        self.assertGreater(len(self.signal.macd_calculator.close_prices), 0)

        self.signal.reset()
        self.assertEqual(len(self.signal.macd_calculator.close_prices), 0)

        # 新交易日重新喂数据，能再次算出 MACD
        results = self._feed(_accelerating_up(15))
        self.assertGreater(results[-1].macd_line, 0.0)


if __name__ == "__main__":
    unittest.main()
