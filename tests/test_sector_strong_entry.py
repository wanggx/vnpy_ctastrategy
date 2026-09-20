"""行业极强买入仓位：记录成交均价 + 按成交价 + 止盈点数挂卖出停止单。"""

import unittest
from datetime import date, datetime

from vnpy.trader.constant import Direction, Exchange, Interval, Offset
from vnpy.trader.object import BarData, TradeData

from vnpy_ctastrategy.strategies.bottom_friction_strategy import (
    BottomFrictionStrategy,
)
from vnpy_ctastrategy.strategies.signals import SECTOR_STRONG_ENTRY_MARK


class FakeEngine:
    """记录委托参数的 CTA 引擎桩（stop=True 即本地停止单）。"""

    def __init__(self) -> None:
        self.orders: list[tuple] = []

    def send_order(
        self,
        strategy: object,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
        stop: bool,
        lock: bool,
        net: bool,
        mark: str,
    ) -> list[str]:
        self.orders.append((direction, offset, price, volume, stop, mark))
        return [f"t.{len(self.orders)}"]

    def sync_strategy_data(self, strategy: object) -> None:
        pass

    def load_bar(self, *args: object, **kwargs: object) -> list:
        return []

    def write_log(self, message: str, strategy: object) -> None:
        pass

    def put_strategy_event(self, strategy: object) -> None:
        pass

    def send_wecom(self, message: str, strategy: object) -> None:
        pass


def _bar(close_price: float = 10.0) -> BarData:
    return BarData(
        gateway_name="t",
        symbol="000001",
        exchange=Exchange.SZSE,
        datetime=datetime(2026, 9, 25, 10, 0),
        interval=Interval.MINUTE,
        open_price=close_price,
        high_price=close_price,
        low_price=close_price,
        close_price=close_price,
        volume=1,
        turnover=close_price,
        open_interest=0,
    )


def _trade(
    price: float,
    volume: float,
    orderid: str = "1",
    direction: Direction = Direction.LONG,
    offset: Offset = Offset.OPEN,
) -> TradeData:
    return TradeData(
        gateway_name="t",
        symbol="000001",
        exchange=Exchange.SZSE,
        orderid=orderid,
        tradeid=f"T{orderid}",
        direction=direction,
        offset=offset,
        price=price,
        volume=volume,
        datetime=datetime(2026, 9, 25, 10, 1),
    )


class SectorStrongEntryTest(unittest.TestCase):
    """行业极强入场买入 → 记录成交均价 → 据此挂 +2% 卖出停止单。"""

    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.strategy = BottomFrictionStrategy(
            self.engine,
            "sector_strong_test",
            "000001.SZSE",
            {},
        )
        self.strategy.inited = True
        self.strategy.trading = True
        self.strategy.base_size = 1000
        self.strategy.friction_size = 500
        self.strategy.pos = 1000  # 底仓已满，摩擦位为空
        self.strategy.yd_pos = 1000
        self.strategy.local_sell_frozen = 0
        self.strategy.t1 = True

    # ---- 入场买入与成交均价记录 ----

    def test_strong_entry_buy_registers_orderid(self) -> None:
        """行业极强买入：按目标仓买入摩擦仓量，并登记委托号用于成交价追踪。"""
        self.strategy._set_target_position(_bar(), 1500, SECTOR_STRONG_ENTRY_MARK)

        self.assertEqual(len(self.engine.orders), 1)
        direction, offset, _price, volume, stop, mark = self.engine.orders[0]
        self.assertEqual(direction, Direction.LONG)
        self.assertEqual(offset, Offset.OPEN)
        self.assertEqual(volume, 500)
        self.assertFalse(stop)  # 入场是普通限价买入，不是停止单
        self.assertEqual(mark, SECTOR_STRONG_ENTRY_MARK)
        self.assertEqual(self.strategy.sector_strong_orderids, {"t.1"})

    def test_trade_records_entry_price(self) -> None:
        """我方委托成交 → 记录成交价作为该仓位的入场价。"""
        self.strategy._set_target_position(_bar(), 1500, SECTOR_STRONG_ENTRY_MARK)
        self.strategy.pos = 1500

        self.strategy.on_trade(_trade(price=10.5, volume=500))

        self.assertEqual(self.strategy.sector_strong_entry_price, 10.5)

    def test_partial_fills_use_weighted_average(self) -> None:
        """分笔成交按成交量加权（300@10.0 + 200@11.0 → 10.4）。"""
        self.strategy._set_target_position(_bar(), 1500, SECTOR_STRONG_ENTRY_MARK)
        self.strategy.pos = 1300
        self.strategy.on_trade(_trade(price=10.0, volume=300))

        self.strategy.pos = 1500
        self.strategy.on_trade(_trade(price=11.0, volume=200))

        self.assertEqual(self.strategy.sector_strong_entry_price, 10.4)

    def test_other_mark_fill_not_recorded(self) -> None:
        """非行业极强的买入（如 MACD 上穿）不写入行业极强入场价。"""
        self.strategy._set_target_position(_bar(), 1500, "MACD上穿零轴")
        self.strategy.pos = 1500

        self.strategy.on_trade(_trade(price=10.5, volume=500))

        self.assertEqual(self.strategy.sector_strong_entry_price, 0.0)

    # ---- 一天只做一次 ----

    def test_fill_marks_day_as_done(self) -> None:
        """行业极强买入成交后标记当天已操作。"""
        self.strategy._set_target_position(_bar(), 1500, SECTOR_STRONG_ENTRY_MARK)
        self.strategy.pos = 1500

        self.strategy.on_trade(_trade(price=10.5, volume=500))

        self.assertEqual(self.strategy.sector_strong_day, "2026-09-25")
        self.assertTrue(self.strategy.sector_strong_today)

    def test_flag_resets_on_next_trading_day(self) -> None:
        """同一交易日保持锁定，跨交易日后解锁（可再做一次）。"""
        self.strategy.sector_strong_day = "2026-09-25"

        self.strategy._sync_cleared_trading_day(date(2026, 9, 25))
        self.assertTrue(self.strategy.sector_strong_today)

        self.strategy._sync_cleared_trading_day(date(2026, 9, 26))
        self.assertFalse(self.strategy.sector_strong_today)

    def test_sell_does_not_unlock_same_day(self) -> None:
        """当天买入后又卖回底仓 → 当天仍锁定（避免日内反复操作）。"""
        self.strategy._set_target_position(_bar(), 1500, SECTOR_STRONG_ENTRY_MARK)
        self.strategy.pos = 1500
        self.strategy.on_trade(_trade(price=10.5, volume=500))

        self.strategy.pos = 1000
        self.strategy.yd_pos = 1000
        self.strategy.on_trade(
            _trade(price=10.6, volume=500, direction=Direction.SHORT, offset=Offset.CLOSE)
        )

        self.assertTrue(self.strategy.sector_strong_today)
        self.assertEqual(self.strategy.sector_strong_entry_price, 0.0)  # 仓位已清

    def test_on_init_clears_day_mark(self) -> None:
        """on_init 重置当天标记（进程重启后重新开始计数）。"""
        self.strategy.sector_strong_day = "2026-09-25"
        self.strategy.sector_strong_today = True

        self.strategy.on_init()

        self.assertEqual(self.strategy.sector_strong_day, "")
        self.assertFalse(self.strategy.sector_strong_today)

    # ---- 卖出停止单 ----

    def test_stop_exit_places_sell_stop_order(self) -> None:
        """持有行业极强仓 → 以触发价挂卖出停止单，数量=摩擦仓量。"""
        self.strategy.sector_strong_entry_price = 10.0
        self.strategy.pos = 1500
        self.strategy.yd_pos = 1500

        self.strategy._submit_sector_strong_stop_exit(10.2)

        self.assertEqual(len(self.engine.orders), 1)
        direction, offset, price, volume, stop, mark = self.engine.orders[0]
        self.assertEqual(direction, Direction.SHORT)
        self.assertEqual(offset, Offset.CLOSE)
        self.assertEqual(price, 10.2)
        self.assertEqual(volume, 500)
        self.assertTrue(stop)  # 本地停止单
        self.assertEqual(mark, "行业极强仓止盈")

    def test_stop_exit_limited_by_sellable_position(self) -> None:
        """T+1：当天买入不可卖，挂单量受可卖量（昨仓-已冻结）限制。"""
        self.strategy.sector_strong_entry_price = 10.0
        self.strategy.pos = 1500
        self.strategy.yd_pos = 200

        self.strategy._submit_sector_strong_stop_exit(10.2)

        self.assertEqual(self.engine.orders[0][3], 200)

    def test_stop_exit_skipped_without_friction_position(self) -> None:
        """仓位未超过摩擦位（无该仓位可卖）→ 不挂停止单。"""
        self.strategy.sector_strong_entry_price = 10.0
        self.strategy.pos = 1000
        self.strategy.yd_pos = 1000

        self.strategy._submit_sector_strong_stop_exit(10.2)

        self.assertEqual(self.engine.orders, [])

    def test_position_back_to_base_clears_entry_price(self) -> None:
        """卖出回到底仓后清空入场价，避免后续误挂止盈停止单。"""
        self.strategy.sector_strong_entry_price = 10.0
        self.strategy.sector_strong_filled_volume = 500
        self.strategy.sector_strong_filled_amount = 5000.0
        self.strategy.sector_strong_orderids.add("t.1")
        self.strategy.pos = 1000
        self.strategy.yd_pos = 1000

        self.strategy.on_trade(
            _trade(price=10.2, volume=500, direction=Direction.SHORT, offset=Offset.CLOSE)
        )

        self.assertEqual(self.strategy.sector_strong_entry_price, 0.0)
        self.assertEqual(self.strategy.sector_strong_filled_volume, 0.0)
        self.assertEqual(self.strategy.sector_strong_orderids, set())


if __name__ == "__main__":
    unittest.main()
