from datetime import date, datetime, timedelta
import unittest

from vnpy.trader.constant import Direction, Exchange, Interval, Offset
from vnpy.trader.object import BarData, TickData

from vnpy_ctastrategy.strategies.bottom_friction_strategy import (
    BottomFrictionStrategy,
)


class FakeEngine:
    def __init__(self) -> None:
        self.orders: list[tuple] = []
        self.synced: list[dict] = []
        self.logs: list[str] = []
        self.strategy_data: dict = {}

    def send_order(
        self,
        strategy: BottomFrictionStrategy,
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
        return [f"ORDER{len(self.orders)}"]

    def sync_strategy_data(self, strategy: BottomFrictionStrategy) -> None:
        self.synced.append(
            {
                "cleared_today": strategy.cleared_today,
                "cleared_day": strategy.cleared_day,
            }
        )

    def write_log(self, message: str, strategy: object) -> None:
        self.logs.append(message)

    def put_strategy_event(self, strategy: object) -> None:
        pass

    def send_wecom(self, message: str, strategy: object) -> None:
        pass


class FakeBarGenerator:
    def update_tick(self, tick: TickData) -> None:
        pass


class BottomFrictionClearedTodayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.strategy = BottomFrictionStrategy(
            self.engine,
            "cleared_today_test",
            "000001.SZSE",
            {},
        )
        self.strategy.bg = FakeBarGenerator()  # type: ignore[assignment]
        self.strategy.inited = True
        self.strategy.trading = True

    def test_mark_cleared_today_persists_iso_date(self) -> None:
        trading_day = date(2026, 8, 24)
        self.strategy._sync_cleared_trading_day(trading_day)
        self.strategy._mark_cleared_today()

        self.assertTrue(self.strategy.cleared_today)
        self.assertEqual(self.strategy.cleared_day, "2026-08-24")
        self.assertEqual(self.engine.synced[-1]["cleared_day"], "2026-08-24")
        self.assertIn("cleared_day", self.strategy.variables)

    def test_init_replay_does_not_mark_cleared(self) -> None:
        self.strategy.inited = False
        self.strategy._sync_cleared_trading_day(date(2026, 8, 24))
        self.strategy._mark_cleared_today()

        self.assertFalse(self.strategy.cleared_today)
        self.assertEqual(self.strategy.cleared_day, "")
        self.assertEqual(self.engine.synced, [])

    def test_restart_same_day_still_blocks_buy(self) -> None:
        persisted = self._clear_then_persist(date(2026, 8, 24))
        restarted = self._restore_like_engine(persisted)

        restarted.on_tick(_tick(day=24, hour=11, minute=0))
        restarted._set_target_position(_bar(day=24, hour=11, minute=0), 1_000)

        self.assertTrue(restarted.cleared_today)
        self.assertEqual(restarted.cleared_day, "2026-08-24")
        self.assertEqual(self.engine.orders, [])

    def test_restart_next_day_allows_buy(self) -> None:
        persisted = self._clear_then_persist(date(2026, 8, 24))
        restarted = self._restore_like_engine(persisted)

        restarted.on_tick(_tick(day=25, hour=9, minute=31))
        restarted._set_target_position(_bar(day=25, hour=9, minute=31), 1_000)

        self.assertFalse(restarted.cleared_today)
        self.assertEqual(restarted.cleared_day, "2026-08-24")
        self.assertEqual(len(self.engine.orders), 1)
        self.assertEqual(self.engine.orders[0][0], Direction.LONG)

    def test_on_start_uses_calendar_day_after_restore(self) -> None:
        self.strategy.cleared_day = date.today().isoformat()
        self.strategy.cleared_today = False
        self.strategy.on_start()
        self.assertTrue(self.strategy.cleared_today)

        self.strategy.cleared_day = (date.today() - timedelta(days=1)).isoformat()
        self.strategy.cleared_today = True
        self.strategy.on_start()
        self.assertFalse(self.strategy.cleared_today)

    def test_on_start_loads_cleared_day_from_engine_data(self) -> None:
        today = date.today().isoformat()
        self.engine.strategy_data = {
            self.strategy.strategy_name: {
                "cleared_day": today,
                "cleared_today": True,
            }
        }
        self.strategy.cleared_day = ""
        self.strategy.cleared_today = False

        self.strategy.on_start()

        self.assertEqual(self.strategy.cleared_day, today)
        self.assertTrue(self.strategy.cleared_today)
        self.assertTrue(any("已加载清仓日期" in msg for msg in self.engine.logs))

    def test_on_start_loads_previous_day_and_allows_buy(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.engine.strategy_data = {
            self.strategy.strategy_name: {
                "cleared_day": yesterday,
                "cleared_today": True,
            }
        }
        self.strategy.cleared_day = ""
        self.strategy.cleared_today = False

        self.strategy.on_start()

        self.assertEqual(self.strategy.cleared_day, yesterday)
        self.assertFalse(self.strategy.cleared_today)

    def _clear_then_persist(self, trading_day: date) -> dict:
        self.strategy._sync_cleared_trading_day(trading_day)
        self.strategy._mark_cleared_today()
        return {
            "cleared_today": self.strategy.cleared_today,
            "cleared_day": self.strategy.cleared_day,
        }

    def _restore_like_engine(self, data: dict) -> BottomFrictionStrategy:
        restarted = BottomFrictionStrategy(
            self.engine,
            "cleared_today_restart",
            "000001.SZSE",
            {},
        )
        restarted.bg = FakeBarGenerator()  # type: ignore[assignment]
        restarted.cleared_today = False
        restarted.cleared_day = ""
        restarted.cleared_trading_day = None
        for name, value in data.items():
            if value is not None:
                setattr(restarted, name, value)
        restarted.inited = True
        restarted.trading = True
        return restarted


def _tick(*, day: int, hour: int, minute: int) -> TickData:
    return TickData(
        gateway_name="test",
        symbol="000001",
        exchange=Exchange.SZSE,
        datetime=datetime(2026, 8, day, hour, minute),
        last_price=10,
    )


def _bar(*, day: int, hour: int, minute: int) -> BarData:
    return BarData(
        gateway_name="test",
        symbol="000001",
        exchange=Exchange.SZSE,
        datetime=datetime(2026, 8, day, hour, minute),
        interval=Interval.MINUTE,
        close_price=10,
    )


if __name__ == "__main__":
    unittest.main()
