import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import MagicMock

from vnpy.trader.object import BarData, TickData

from vnpy_ctastrategy.strategies.signals.base import (
    PriorityCompositeSubSignal,
    SignalAggregator,
    SignalResult,
    SignalType,
    SubSignal,
)


VT_SYMBOL = "000001.SZSE"


# ---- 固定输出的 mock 子信号，用于测试组合短路 ----


class FixedSubSignal(SubSignal):
    """返回预设结果的 mock 子信号，记录是否被调用及收到的 prev。"""

    def __init__(self, vt_symbol: str, strategy) -> None:
        super().__init__(vt_symbol, strategy)
        self.fixed: SignalResult = SignalResult()
        self.tick_called: int = 0
        self.bar_called: int = 0
        self.last_prev: SignalResult = SignalResult()

    def set_fixed(self, result: SignalResult) -> None:
        self.fixed = result

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        self.tick_called += 1
        self.last_prev = prev
        return self.fixed

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        self.bar_called += 1
        self.last_prev = prev
        return self.fixed

    def signal_result(self) -> SignalResult:
        return self.fixed


def _make_sub_factory(result: SignalResult):
    """构造一个产出固定 result 的子信号工厂（闭包绑定 result）。"""
    def factory(vt_symbol: str, strategy) -> FixedSubSignal:
        sub = FixedSubSignal(vt_symbol, strategy)
        sub.set_fixed(result)
        return sub
    return factory


def _strategy_stub() -> SimpleNamespace:
    """最小策略桩，仅用于 SubSignal 绑定。"""
    return SimpleNamespace()


class SignalResultTest(unittest.TestCase):
    """SignalResult 目标仓位制：默认值与不可变。"""

    def test_defaults(self) -> None:
        r = SignalResult()
        self.assertEqual(r.type, SignalType.NONE)
        self.assertEqual(r.target_pos, 0)
        self.assertEqual(r.reason, "")
        self.assertEqual(r.executor, "target")
        self.assertEqual(r.price, 0.0)

    def test_frozen_immutable(self) -> None:
        r = SignalResult(type=SignalType.BUY, target_pos=1000, reason="补仓")
        with self.assertRaises(FrozenInstanceError):
            r.target_pos = 2000  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        a = SignalResult(type=SignalType.CLEAR, target_pos=0, reason="止损")
        b = SignalResult(type=SignalType.CLEAR, target_pos=0, reason="止损")
        self.assertEqual(a, b)

    def test_executor_custom(self) -> None:
        r = SignalResult(type=SignalType.CLEAR, target_pos=0, reason="回撤", executor="drawdown")
        self.assertEqual(r.executor, "drawdown")


class PriorityCompositeSubSignalTest(unittest.TestCase):
    """优先级组合：on_tick 与 on_bar 都短路、同 prev、首非 NONE 胜出、全 NONE 返 NONE。"""

    def _make_priority(self, factories) -> PriorityCompositeSubSignal:
        cls = type(
            "TestPriority",
            (PriorityCompositeSubSignal,),
            {"sub_factories": list(factories)},
        )
        return cls(VT_SYMBOL, _strategy_stub())

    def test_on_tick_short_circuits_on_first_non_none(self) -> None:
        a = _make_sub_factory(SignalResult(type=SignalType.BUY, target_pos=1, reason="a"))
        b = _make_sub_factory(SignalResult(type=SignalType.SELL, target_pos=2, reason="b"))
        priority = self._make_priority([a, b])

        r = priority.on_tick(MagicMock(TickData), SignalResult())

        self.assertEqual(r.type, SignalType.BUY)
        self.assertEqual(r.reason, "a")
        # b 不应被调用（短路）
        self.assertEqual(priority._subs[1].tick_called, 0)

    def test_on_tick_same_prev_passed_to_each(self) -> None:
        seed = SignalResult(type=SignalType.BUY, target_pos=99, reason="seed")
        a = _make_sub_factory(SignalResult())
        b = _make_sub_factory(SignalResult(type=SignalType.SELL, target_pos=1, reason="b"))
        priority = self._make_priority([a, b])

        priority.on_tick(MagicMock(TickData), seed)

        self.assertIs(priority._subs[0].last_prev, seed)
        self.assertIs(priority._subs[1].last_prev, seed)

    def test_on_tick_all_none_returns_none(self) -> None:
        a = _make_sub_factory(SignalResult())
        b = _make_sub_factory(SignalResult())
        priority = self._make_priority([a, b])

        r = priority.on_tick(MagicMock(TickData), SignalResult())

        self.assertEqual(r.type, SignalType.NONE)
        self.assertEqual(priority._subs[0].tick_called, 1)
        self.assertEqual(priority._subs[1].tick_called, 1)

    def test_on_bar_short_circuits_same_as_on_tick(self) -> None:
        """CTA 适配：on_bar 也短路（参考组合策略是 pass-through，CTA 版必须短路）。"""
        a = _make_sub_factory(SignalResult(type=SignalType.CLEAR, target_pos=0, reason="a"))
        b = _make_sub_factory(SignalResult(type=SignalType.SELL, target_pos=2, reason="b"))
        priority = self._make_priority([a, b])

        r = priority.on_bar(MagicMock(BarData), SignalResult())

        self.assertEqual(r.type, SignalType.CLEAR)
        self.assertEqual(priority._subs[1].bar_called, 0)

    def test_on_bar_all_none_returns_none(self) -> None:
        a = _make_sub_factory(SignalResult())
        b = _make_sub_factory(SignalResult())
        priority = self._make_priority([a, b])

        r = priority.on_bar(MagicMock(BarData), SignalResult())

        self.assertEqual(r.type, SignalType.NONE)

    def test_signal_result_returns_last_cached(self) -> None:
        a = _make_sub_factory(SignalResult(type=SignalType.BUY, target_pos=1, reason="a"))
        priority = self._make_priority([a])

        priority.on_tick(MagicMock(TickData), SignalResult())
        self.assertEqual(priority.signal_result().reason, "a")

        priority.on_bar(MagicMock(BarData), SignalResult())
        # on_bar 全 NONE（a 返回 BUY？不，a 固定 BUY）→ 缓存更新为 BUY
        self.assertEqual(priority.signal_result().type, SignalType.BUY)


class SignalAggregatorTest(unittest.TestCase):
    """总信号：懒创建子信号、缓存 signal_result、单 vt_symbol 行为。"""

    def _make_aggregator(self, factories) -> SignalAggregator:
        cls = type(
            "TestAggregator",
            (SignalAggregator,),
            {"sub_factories": list(factories)},
        )
        return cls(_strategy_stub())

    def test_signal_result_empty_before_first_on(self) -> None:
        agg = self._make_aggregator([_make_sub_factory(SignalResult())])
        self.assertEqual(
            agg.signal_result(VT_SYMBOL).type, SignalType.NONE
        )

    def test_lazy_creation_then_cache(self) -> None:
        a = _make_sub_factory(SignalResult(type=SignalType.BUY, target_pos=1, reason="a"))
        agg = self._make_aggregator([a])

        # 首次 on_tick 懒创建子信号并缓存结果
        tick = MagicMock(TickData)
        tick.vt_symbol = VT_SYMBOL
        agg.on_tick(tick)
        self.assertIn(VT_SYMBOL, agg._subs)
        self.assertEqual(agg.signal_result(VT_SYMBOL).reason, "a")

    def test_on_bar_uses_bar_vt_symbol(self) -> None:
        a = _make_sub_factory(SignalResult(type=SignalType.BUY, target_pos=1, reason="a"))
        agg = self._make_aggregator([a])

        bar = MagicMock(BarData)
        bar.vt_symbol = VT_SYMBOL
        agg.on_bar(bar)
        self.assertEqual(agg.signal_result(VT_SYMBOL).reason, "a")

    def test_remove_clears_symbol(self) -> None:
        a = _make_sub_factory(SignalResult(type=SignalType.BUY, target_pos=1, reason="a"))
        agg = self._make_aggregator([a])

        tick = MagicMock(TickData)
        tick.vt_symbol = VT_SYMBOL
        agg.on_tick(tick)
        agg.remove(VT_SYMBOL)

        self.assertNotIn(VT_SYMBOL, agg._subs)
        self.assertNotIn(VT_SYMBOL, agg._results)
        self.assertEqual(agg.signal_result(VT_SYMBOL).type, SignalType.NONE)

    def test_execute_is_noop_by_default(self) -> None:
        """基类 _execute 默认不分派：策略桩无执行器方法也不会被调用（不抛异常）。"""
        agg = self._make_aggregator([_make_sub_factory(SignalResult())])

        self.assertFalse(
            agg._execute(
                SignalResult(type=SignalType.BUY, target_pos=1),
                MagicMock(TickData),
            )
        )

    def test_on_tick_of_base_aggregator_does_not_touch_strategy(self) -> None:
        """基类聚合器跑完链只缓存结果，不调策略执行器（分派由子类覆写 _execute）。"""
        a = _make_sub_factory(SignalResult(type=SignalType.BUY, target_pos=1, reason="a"))
        agg = self._make_aggregator([a])
        tick = MagicMock(TickData)
        tick.vt_symbol = VT_SYMBOL

        agg.on_tick(tick)

        self.assertEqual(agg.signal_result(VT_SYMBOL).reason, "a")


if __name__ == "__main__":
    unittest.main()
