from datetime import datetime
import unittest

from vnpy.trader.constant import Direction, Exchange, Offset, Status
from vnpy.trader.object import OrderData, TickData, TradeData

from vnpy_ctastrategy.strategies.bottom_friction_macd_strategy import (
    BottomFrictionMacdStrategy,
)


class FakeEngine:
    def __init__(self) -> None:
        self.wecom_messages: list[str] = []

    def write_log(self, message: str, strategy: object) -> None:
        pass

    def put_strategy_event(self, strategy: object) -> None:
        pass

    def send_wecom(self, message: str, strategy: object) -> None:
        self.wecom_messages.append(message)


class FakeBarGenerator:
    def __init__(self) -> None:
        self.ticks: list[TickData] = []

    def update_tick(self, tick: TickData) -> None:
        self.ticks.append(tick)


class BottomFrictionAvgPriceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.strategy = BottomFrictionMacdStrategy(
            self.engine,
            "avg_price_test",
            "000001.SZSE",
            {},
        )
        self.strategy.bg = FakeBarGenerator()  # type: ignore[assignment]

    def test_avg_price_uses_current_tick_cumulative_values(self) -> None:
        self.strategy.on_tick(
            _tick(hour=9, minute=30, volume=100, turnover=100_000)
        )
        self.strategy.on_tick(
            _tick(hour=9, minute=31, volume=400, turnover=460_493)
        )

        self.assertEqual(self.strategy.avg_price, 11.51)

    def test_intraday_avg_price_resets_on_new_trading_day(self) -> None:
        first_tick = _tick(
            hour=9,
            minute=30,
            volume=100,
            turnover=100_000,
        )
        next_day_tick = _tick(
            day=22,
            hour=9,
            minute=30,
        )

        self.strategy.on_tick(first_tick)
        self.assertEqual(self.strategy.avg_price, 10)

        self.strategy.on_tick(next_day_tick)
        self.assertEqual(self.strategy.avg_price, 0)

    def test_pos_avg_price_keeps_position_cost(self) -> None:
        self.strategy.pos = 100
        self.strategy.on_trade(_trade(price=10, volume=100))
        self.strategy.pos = 150
        self.strategy.on_trade(_trade(price=13.019, volume=50))

        self.assertEqual(self.strategy.pos_avg_price, 11.01)
        self.assertEqual(self.strategy.avg_price, 0)
        self.assertIn("avg_price", self.strategy.variables)
        self.assertIn("pos_avg_price", self.strategy.variables)
        self.assertIn("shares_per_lot", self.strategy.parameters)

    def test_order_and_trade_send_wecom_messages(self) -> None:
        self.strategy.inited = True
        order = OrderData(
            gateway_name="test",
            symbol="000001",
            exchange=Exchange.SZSE,
            orderid="order",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            price=10,
            volume=100,
            traded=20,
            status=Status.PARTTRADED,
        )

        self.strategy.on_order(order)
        self.strategy.pos = 100
        self.strategy.on_trade(_trade(price=10, volume=100))

        self.assertEqual(len(self.engine.wecom_messages), 2)
        self.assertIn("订单通知：状态=部分成交", self.engine.wecom_messages[0])
        self.assertIn("委托号=test.order", self.engine.wecom_messages[0])
        self.assertIn("成交通知：方向=多", self.engine.wecom_messages[1])


def _tick(
    *,
    day: int = 21,
    hour: int,
    minute: int,
    last_price: float = 10,
    volume: float = 0,
    turnover: float = 0,
) -> TickData:
    return TickData(
        gateway_name="test",
        symbol="000001",
        exchange=Exchange.SZSE,
        datetime=datetime(2026, 7, day, hour, minute),
        last_price=last_price,
        volume=volume,
        turnover=turnover,
    )


def _trade(*, price: float, volume: float) -> TradeData:
    return TradeData(
        gateway_name="test",
        symbol="000001",
        exchange=Exchange.SZSE,
        orderid="order",
        tradeid="trade",
        direction=Direction.LONG,
        offset=Offset.OPEN,
        price=price,
        volume=volume,
    )


if __name__ == "__main__":
    unittest.main()
