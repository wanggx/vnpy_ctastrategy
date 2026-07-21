from datetime import datetime, timedelta
import unittest

from vnpy.trader.constant import Direction, Exchange, Interval, Offset, Status
from vnpy.trader.object import BarData, OrderData
from vnpy.trader.utility import ArrayManager

from vnpy_ctastrategy.strategies.box_trend_swing_strategy import (
    BoxTrendSwingStrategy,
)


class FakeEngine:
    def __init__(self) -> None:
        self.orders: list[tuple] = []
        self.wecom_messages: list[str] = []

    def send_order(
        self,
        strategy: BoxTrendSwingStrategy,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
        stop: bool,
        lock: bool,
        net: bool,
        mark: str,
    ) -> list[str]:
        self.orders.append((direction, offset, price, volume, mark))
        return [f"order_{len(self.orders)}"]

    def cancel_all(self, strategy: BoxTrendSwingStrategy) -> None:
        pass

    def put_strategy_event(self, strategy: BoxTrendSwingStrategy) -> None:
        pass

    def send_wecom(
        self,
        message: str,
        strategy: BoxTrendSwingStrategy,
    ) -> None:
        self.wecom_messages.append(message)


class BoxTrendSwingStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.strategy = BoxTrendSwingStrategy(
            self.engine,
            "box_trend_test",
            "000001.SZSE",
            {},
        )
        self.strategy.am = ArrayManager(size=35)
        self.strategy.trading = True
        self.strategy.t1 = False

    def test_buys_near_box_lower_edge(self) -> None:
        bars = [
            _bar(index, close=10.5, low=10, high=11)
            for index in range(34)
        ]
        bars.append(_bar(34, close=10.1, low=10, high=10.5))

        for bar in bars:
            self.strategy.on_bar(bar)

        self.assertEqual(self.strategy.regime, 1)
        self.assertEqual(self.strategy.buy_price, 10.2)
        self.assertEqual(
            self.engine.orders[-1],
            (Direction.LONG, Offset.OPEN, 10.1, 100, "通道下沿低吸"),
        )

    def test_sells_swing_position_near_uptrend_upper_edge(self) -> None:
        self.strategy.pos = 200
        bars = []
        for index in range(34):
            close = 10 + index * 0.05
            bars.append(
                _bar(index, close=close, low=close - 0.2, high=close + 0.2)
            )
        bars.append(_bar(34, close=11.9, low=11.7, high=12.0))

        for bar in bars:
            self.strategy.on_bar(bar)

        self.assertEqual(self.strategy.regime, 2)
        self.assertEqual(
            self.engine.orders[-1],
            (Direction.SHORT, Offset.CLOSE, 11.9, 100, "通道上沿高抛"),
        )

    def test_exits_when_price_breaks_below_channel(self) -> None:
        self.strategy.pos = 100
        bars = [
            _bar(index, close=10.5, low=10, high=11)
            for index in range(34)
        ]
        bars.append(_bar(34, close=9.7, low=9.6, high=9.8))

        for bar in bars:
            self.strategy.on_bar(bar)

        self.assertEqual(
            self.engine.orders[-1],
            (Direction.SHORT, Offset.CLOSE, 9.7, 100, "跌破通道止损"),
        )

    def test_order_status_change_sends_wecom_message(self) -> None:
        self.strategy.inited = True
        order = OrderData(
            gateway_name="test",
            symbol="000001",
            exchange=Exchange.SZSE,
            orderid="order_1",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            price=10.12,
            volume=100,
            traded=50,
            status=Status.PARTTRADED,
            reference="CtaStrategy_box_trend_test:通道下沿低吸",
        )

        self.strategy.on_order(order)

        self.assertEqual(len(self.engine.wecom_messages), 1)
        message = self.engine.wecom_messages[0]
        self.assertIn("状态=部分成交", message)
        self.assertIn("委托号=test.order_1", message)
        self.assertIn("标注=通道下沿低吸", message)


def _bar(
    index: int,
    *,
    close: float,
    low: float,
    high: float,
) -> BarData:
    return BarData(
        gateway_name="test",
        symbol="000001",
        exchange=Exchange.SZSE,
        datetime=datetime(2026, 7, 21, 9, 30) + timedelta(minutes=index),
        interval=Interval.MINUTE,
        volume=1_000,
        turnover=close * 100_000,
        open_price=close,
        high_price=high,
        low_price=low,
        close_price=close,
    )


if __name__ == "__main__":
    unittest.main()
