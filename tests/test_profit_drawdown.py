import unittest
from types import SimpleNamespace

from vnpy_ctastrategy.strategies.signals.profit_drawdown import ProfitDrawdownState


def _strategy(**attrs) -> SimpleNamespace:
    """构造状态机所需策略桩：pos/pos_avg_price + 3 个阈值参数 + UI 变量。"""
    defaults = dict(
        pos=0,
        pos_avg_price=0.0,
        max_profit_drawdown_points=10.0,
        profit_protection_trigger_points=10.0,
        profit_protection_floor_points=5.0,
        profit_drawdown_pending=False,
        peak_profit_points=0.0,
        profit_drawdown_active=False,
        profit_protection_active=False,
    )
    defaults.update(attrs)
    return SimpleNamespace(**defaults)


class ProfitDrawdownStateCheckTest(unittest.TestCase):
    """check 状态机：触发条件、状态更新、UI 回写。"""

    def test_no_position_returns_false_and_resets(self) -> None:
        """pos<=0 时返回 False，且清空内部状态 + UI 回写。"""
        s = _strategy(pos=0)
        state = ProfitDrawdownState(s)
        state.peak_profit_points = 20.0
        state.active = True
        state.protection_active = True

        r = state.check(10.0, s.pos, s.pos_avg_price)

        self.assertFalse(r)
        self.assertEqual(state.peak_profit_points, 0.0)
        self.assertFalse(state.active)
        self.assertFalse(state.protection_active)
        # UI 回写
        self.assertEqual(s.peak_profit_points, 0.0)
        self.assertFalse(s.profit_drawdown_active)
        self.assertFalse(s.profit_protection_active)

    def test_active_returns_true_directly(self) -> None:
        """active 已触发：直接返回 True，不重算。"""
        s = _strategy(pos=1000, pos_avg_price=10.0)
        state = ProfitDrawdownState(s)
        state.active = True
        self.assertTrue(state.check(10.0, s.pos, s.pos_avg_price))

    def test_peak_updates_and_no_trigger(self) -> None:
        """价格上涨：peak 更新，未触发回撤 → False。"""
        s = _strategy(pos=1000, pos_avg_price=10.0)
        state = ProfitDrawdownState(s)
        # 价格 12 → profit=20%
        self.assertFalse(state.check(12.0, s.pos, s.pos_avg_price))
        self.assertEqual(state.peak_profit_points, 20.0)
        self.assertEqual(s.peak_profit_points, 20.0)  # UI 回写

    def test_drawdown_trigger_sets_active(self) -> None:
        """peak 高后回落超阈值 → 触发 active=True，返回 True。"""
        s = _strategy(pos=1000, pos_avg_price=10.0)
        state = ProfitDrawdownState(s)
        state.peak_profit_points = 20.0  # 历史 peak=20%
        # 价格 8 → profit=-20%，回撤 40% > 10 → 触发
        self.assertTrue(state.check(8.0, s.pos, s.pos_avg_price))
        self.assertTrue(state.active)
        self.assertTrue(s.profit_drawdown_active)  # UI 回写

    def test_protection_trigger_then_floor_hit(self) -> None:
        """peak 超 trigger 后 protection_active；回落跌破 floor → 触发。"""
        s = _strategy(
            pos=1000, pos_avg_price=10.0,
            profit_protection_trigger_points=10.0,
            profit_protection_floor_points=5.0,
        )
        state = ProfitDrawdownState(s)
        # 价格 11.5 → profit=15%，peak=15 > trigger=10 → protection_active
        state.check(11.5, s.pos, s.pos_avg_price)
        self.assertTrue(state.protection_active)
        self.assertTrue(s.profit_protection_active)
        # 价格 10.4 → profit=4% < floor=5 → 触发
        self.assertTrue(state.check(10.4, s.pos, s.pos_avg_price))
        self.assertTrue(state.active)

    def test_peak_never_decreases(self) -> None:
        """peak 只增不减：价格回落后 peak 保持历史最高。"""
        s = _strategy(pos=1000, pos_avg_price=10.0)
        state = ProfitDrawdownState(s)
        state.check(12.0, s.pos, s.pos_avg_price)  # peak=20
        state.check(11.0, s.pos, s.pos_avg_price)  # profit=10 < 20，peak 不降
        self.assertEqual(state.peak_profit_points, 20.0)


class ProfitDrawdownStateResetTest(unittest.TestCase):
    """reset_on_open / reset_on_close / reset。"""

    def test_reset_on_open_new_position(self) -> None:
        """新仓位开仓：peak 按成交价重算，active/protection/pending 清空。"""
        s = _strategy(profit_drawdown_pending=True)
        state = ProfitDrawdownState(s)
        state.active = True
        state.protection_active = True
        state.peak_profit_points = 20.0

        # 新仓位：trade_price==pos_avg_price=10 → peak=0
        state.reset_on_open(10.0, 10.0, is_new_position=True)

        self.assertEqual(state.peak_profit_points, 0.0)
        self.assertFalse(state.active)
        self.assertFalse(state.protection_active)
        self.assertFalse(s.profit_drawdown_pending)
        # UI 回写
        self.assertEqual(s.peak_profit_points, 0.0)
        self.assertFalse(s.profit_drawdown_active)

    def test_reset_on_open_add_position_keeps_active(self) -> None:
        """加仓（非新仓位）：peak 重算，active/protection 不清。"""
        s = _strategy(profit_drawdown_pending=False)
        state = ProfitDrawdownState(s)
        state.active = True
        state.protection_active = True

        # 加仓：trade_price=13, pos_avg=12 → peak=(13-12)/12*100≈8.33
        state.reset_on_open(13.0, 12.0, is_new_position=False)

        self.assertAlmostEqual(state.peak_profit_points, 8.33, places=2)
        self.assertTrue(state.active)  # 非新仓位不清
        self.assertTrue(state.protection_active)

    def test_reset_on_close_clears_all(self) -> None:
        """平仓归零：清空全部判定状态 + pending。"""
        s = _strategy(profit_drawdown_pending=True)
        state = ProfitDrawdownState(s)
        state.peak_profit_points = 20.0
        state.active = True
        state.protection_active = True

        state.reset_on_close()

        self.assertEqual(state.peak_profit_points, 0.0)
        self.assertFalse(state.active)
        self.assertFalse(state.protection_active)
        self.assertFalse(s.profit_drawdown_pending)
        self.assertEqual(s.peak_profit_points, 0.0)

    def test_reset_clears_all(self) -> None:
        """reset（on_init）：清空判定状态 + UI 回写。"""
        s = _strategy()
        state = ProfitDrawdownState(s)
        state.peak_profit_points = 20.0
        state.active = True
        state.protection_active = True

        state.reset()

        self.assertEqual(state.peak_profit_points, 0.0)
        self.assertFalse(state.active)
        self.assertFalse(state.protection_active)
        self.assertEqual(s.peak_profit_points, 0.0)


if __name__ == "__main__":
    unittest.main()
