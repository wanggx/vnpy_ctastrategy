from datetime import datetime
import unittest

from vnpy.trader.constant import Direction, Exchange, Interval, Offset
from vnpy.trader.object import BarData

from vnpy_ctastrategy.strategies.bottom_friction_strategy import (
    BottomFrictionMacdStrategy,
)
from vnpy_ctastrategy.strategies.market_sentiment import (
    MarketBreadth,
    MarketSentimentSnapshot,
    SectorState,
    SentimentLevel,
)


class FakeEngine:
    def __init__(self) -> None:
        self.orders: list[tuple] = []
        self.logs: list[str] = []

    def send_order(
        self,
        strategy: BottomFrictionMacdStrategy,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
        stop: bool,
        lock: bool,
        net: bool,
        mark: str,
    ) -> list[str]:
        self.orders.append(
            (direction, offset, price, volume, stop, mark)
        )
        return [f"ORDER{len(self.orders)}"]

    def write_log(
        self,
        message: str,
        strategy: BottomFrictionMacdStrategy,
    ) -> None:
        self.logs.append(message)

    def put_strategy_event(
        self,
        strategy: BottomFrictionMacdStrategy,
    ) -> None:
        pass


class FakeSentimentService:
    def __init__(self, snapshot: MarketSentimentSnapshot) -> None:
        self.snapshot: MarketSentimentSnapshot = snapshot

    def get_latest(self) -> MarketSentimentSnapshot:
        return self.snapshot


class BottomFrictionSentimentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.strategy = BottomFrictionMacdStrategy(
            self.engine,
            "sentiment_test",
            "000001.SZSE",
            {},
        )

    def test_thresholds_are_strictly_greater_than(self) -> None:
        at_threshold = _snapshot(
            declining=3_500,
            sector_flags=(True, True, False, False),
        )
        one_third = _snapshot(
            declining=3_501,
            sector_flags=(True, False, False),
        )
        reduce_risk = _snapshot(
            declining=3_501,
            sector_flags=(True, True, False, False),
        )
        exit_risk = _snapshot(
            declining=4_001,
            sector_flags=(),
        )

        self.assertEqual(
            self.strategy._evaluate_market_sentiment(at_threshold),
            0,
        )
        self.assertEqual(
            self.strategy._evaluate_market_sentiment(one_third),
            0,
        )
        self.assertEqual(
            self.strategy._evaluate_market_sentiment(reduce_risk),
            1,
        )
        self.assertEqual(
            self.strategy._evaluate_market_sentiment(exit_risk),
            2,
        )

    def test_reduce_risk_locks_half_position(self) -> None:
        snapshot = _snapshot(
            declining=3_600,
            sector_flags=(True, True, False, False),
        )
        self.strategy.sentiment_service = FakeSentimentService(  # type: ignore[assignment]
            snapshot
        )
        self.strategy.trading = True
        self.strategy.pos = 100

        handled = self.strategy._apply_market_sentiment_risk(_bar())

        self.assertTrue(handled)
        self.assertEqual(self.strategy.sentiment_risk_level, 1)
        self.assertEqual(self.strategy.sentiment_target_pos, 50)
        self.assertEqual(
            self.engine.orders[0][:4],
            (Direction.SHORT, Offset.CLOSE, 10.0, 50),
        )

    def test_exit_risk_clears_position(self) -> None:
        snapshot = _snapshot(
            declining=4_100,
            sector_flags=(),
        )
        self.strategy.sentiment_service = FakeSentimentService(  # type: ignore[assignment]
            snapshot
        )
        self.strategy.trading = True
        self.strategy.pos = 100

        handled = self.strategy._apply_market_sentiment_risk(_bar())

        self.assertTrue(handled)
        self.assertEqual(self.strategy.sentiment_risk_level, 2)
        self.assertEqual(self.strategy.sentiment_target_pos, 0)
        self.assertEqual(
            self.engine.orders[0][:4],
            (Direction.SHORT, Offset.CLOSE, 10.0, 100),
        )


def _snapshot(
    declining: int,
    sector_flags: tuple[bool, ...],
) -> MarketSentimentSnapshot:
    breadth = MarketBreadth(
        universe_size=5_000,
        valid_count=5_000,
        advancing=5_000 - declining,
        declining=declining,
    )
    sectors: dict[str, SectorState] = {}
    for index, sector_declining in enumerate(sector_flags):
        if sector_declining:
            sector_breadth = MarketBreadth(
                universe_size=100,
                valid_count=100,
                advancing=30,
                declining=70,
            )
        else:
            sector_breadth = MarketBreadth(
                universe_size=100,
                valid_count=100,
                advancing=70,
                declining=30,
            )
        sectors[f"sector_{index}"] = SectorState(
            name=f"sector_{index}",
            breadth=sector_breadth,
            score=30 if sector_declining else 70,
            level=(
                SentimentLevel.BEARISH
                if sector_declining
                else SentimentLevel.BULLISH
            ),
        )

    return MarketSentimentSnapshot(
        datetime=datetime.now().astimezone(),
        score=20,
        level=SentimentLevel.BEARISH,
        breadth=breadth,
        sectors=sectors,
    )


def _bar() -> BarData:
    return BarData(
        gateway_name="test",
        symbol="000001",
        exchange=Exchange.SZSE,
        datetime=datetime.now().astimezone(),
        interval=Interval.MINUTE,
        volume=1_000,
        open_price=10,
        high_price=10.1,
        low_price=9.9,
        close_price=10,
    )


if __name__ == "__main__":
    unittest.main()
