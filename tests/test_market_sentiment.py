from datetime import datetime
import unittest

from vnpy_ctastrategy.strategies.market_sentiment import (
    IndexMonitor,
    MarketBreadth,
    MarketBreadthCalculator,
    MarketSentimentService,
    SectorMonitor,
    SectorState,
    SentimentCalculator,
    SentimentLevel,
    TickSnapshot,
    XtDataProvider,
)


class FakeXtDataClient:
    """不连接 QMT 的 xtdata 模拟对象。"""

    def __init__(self) -> None:
        self.sector_calls: int = 0
        self.tick_calls: int = 0

    def get_stock_list_in_sector(
        self,
        sector_name: str,
        real_timetag: int = -1,
    ) -> list[str]:
        self.sector_calls += 1
        if sector_name == "银行":
            return ["600000.SH", "600000.SH", "000001.SZ"]
        return ["600000.SH", "000001.SZ", "INVALID"]

    def get_sector_list(self) -> list[str]:
        return ["银行", "沪深A股"]

    def get_full_tick(
        self,
        code_list: list[str],
    ) -> dict[str, dict]:
        self.tick_calls += 1
        raw: dict[str, dict] = {
            "600000.SH": _raw_tick(10.2, 10.0),
            "000001.SZ": _raw_tick(9.8, 10.0),
            "000001.SH": _raw_tick(3_030, 3_000),
            "399001.SZ": _raw_tick(10_100, 10_000),
            "399006.SZ": _raw_tick(2_020, 2_000),
        }
        return {
            symbol: raw[symbol]
            for symbol in code_list
            if symbol in raw
        }


class FakeSnapshotProvider:
    """情绪服务使用的稳定快照数据源。"""

    index_symbols: dict[str, str] = {"000001.SH": "上证综指"}

    def __init__(self) -> None:
        self.calls: int = 0

    def get_full_snapshot(
        self,
    ) -> tuple[
        datetime,
        dict[str, TickSnapshot],
        tuple[str, ...],
        dict[str, tuple[str, ...]],
    ]:
        self.calls += 1
        now: datetime = datetime.now().astimezone()
        ticks: dict[str, TickSnapshot] = {
            "600000.SH": _tick("600000.SH", 10.2, 10.0, now),
            "000001.SZ": _tick("000001.SZ", 10.1, 10.0, now),
            "000001.SH": _tick("000001.SH", 3_030, 3_000, now),
        }
        return (
            now,
            ticks,
            ("600000.SH", "000001.SZ"),
            {"银行": ("600000.SH", "000001.SZ")},
        )


class MarketSentimentTest(unittest.TestCase):
    def test_xtdata_provider_normalizes_and_caches(self) -> None:
        client = FakeXtDataClient()
        provider = XtDataProvider(
            client=client,
            batch_size=2,
            universe_cache_seconds=3_600,
            minimum_sector_size=1,
        )

        timestamp, ticks, stock_symbols, sector_members = (
            provider.get_full_snapshot()
        )

        self.assertIsInstance(timestamp, datetime)
        self.assertEqual(stock_symbols, ("000001.SZ", "600000.SH"))
        self.assertEqual(client.sector_calls, 2)
        self.assertEqual(client.tick_calls, 3)
        self.assertAlmostEqual(ticks["600000.SH"].change_percent, 2.0)
        self.assertEqual(
            sector_members["银行"],
            ("000001.SZ", "600000.SH"),
        )

        provider.get_full_snapshot()
        self.assertEqual(client.sector_calls, 2)

    def test_breadth_index_and_sentiment(self) -> None:
        now = datetime.now().astimezone()
        ticks: dict[str, TickSnapshot] = {
            "A.SZ": _tick("A.SZ", 10.2, 10.0, now),
            "B.SZ": _tick("B.SZ", 10.1, 10.0, now),
            "C.SH": _tick("C.SH", 9.9, 10.0, now),
            "D.SH": _tick("D.SH", 10.0, 10.0, now),
            "000001.SH": _tick("000001.SH", 3_030, 3_000, now),
        }

        breadth = MarketBreadthCalculator().calculate(
            ticks,
            ("A.SZ", "B.SZ", "C.SH", "D.SH"),
        )
        indices = IndexMonitor(
            {"000001.SH": "上证综指"}
        ).calculate(ticks, now)
        sentiment = SentimentCalculator().calculate(
            now,
            breadth,
            indices,
            source_count=len(ticks),
        )

        self.assertEqual(breadth.advancing, 2)
        self.assertEqual(breadth.declining, 1)
        self.assertEqual(breadth.unchanged, 1)
        self.assertAlmostEqual(breadth.net_advance_ratio, 0.25)
        self.assertTrue(indices["000001.SH"].valid)
        self.assertGreater(sentiment.score, 50)
        self.assertIn(
            sentiment.level,
            {SentimentLevel.NEUTRAL, SentimentLevel.BULLISH},
        )

    def test_overlapping_sector_members_are_kept_separately(self) -> None:
        now = datetime.now().astimezone()
        ticks: dict[str, TickSnapshot] = {
            "A.SZ": _tick("A.SZ", 10.5, 10.0, now),
            "B.SZ": _tick("B.SZ", 10.3, 10.0, now),
            "C.SH": _tick("C.SH", 10.2, 10.0, now),
            "D.SH": _tick("D.SH", 9.8, 10.0, now),
        }
        sector_members = {
            "人工智能": ("A.SZ", "A.SZ", "B.SZ", "C.SH"),
            "金融科技": ("A.SZ", "D.SH"),
        }

        sectors, stock_sectors = SectorMonitor(
            minimum_valid_count=2
        ).calculate(ticks, sector_members)

        self.assertEqual(
            sectors["人工智能"].breadth.universe_size,
            3,
        )
        self.assertEqual(
            sectors["金融科技"].breadth.universe_size,
            2,
        )
        self.assertEqual(
            stock_sectors["A.SZ"],
            ("人工智能", "金融科技"),
        )
        self.assertGreater(sectors["人工智能"].score, 60)

    def test_strong_sector_does_not_hide_weak_whole_market(self) -> None:
        now = datetime.now().astimezone()
        market_breadth = MarketBreadth(
            universe_size=4_100,
            valid_count=4_100,
            advancing=80,
            declining=4_000,
            unchanged=20,
            advance_ratio=80 / 4_100,
            decline_ratio=4_000 / 4_100,
            net_advance_ratio=(80 - 4_000) / 4_100,
            average_change_percent=-2.5,
        )
        strong_breadth = MarketBreadth(
            universe_size=20,
            valid_count=20,
            advancing=20,
            net_advance_ratio=1.0,
            average_change_percent=3.0,
        )
        sectors = {
            "机器人": SectorState(
                name="机器人",
                breadth=strong_breadth,
                score=100.0,
                level=SentimentLevel.EXTREME_BULLISH,
            )
        }

        snapshot = SentimentCalculator().calculate(
            now,
            market_breadth,
            {},
            source_count=4_100,
            sectors=sectors,
            stock_sectors={"A.SZ": ("机器人", "人工智能")},
        )

        self.assertIn(
            snapshot.level,
            {
                SentimentLevel.EXTREME_BEARISH,
                SentimentLevel.BEARISH,
            },
        )
        self.assertEqual(
            snapshot.get_strong_sectors()[0].name,
            "机器人",
        )
        self.assertEqual(
            snapshot.get_stock_sectors("A.SZ"),
            ("机器人", "人工智能"),
        )

    def test_service_reuses_recent_snapshot(self) -> None:
        provider = FakeSnapshotProvider()
        service = MarketSentimentService(
            provider=provider,
            sector_monitor=SectorMonitor(minimum_valid_count=2),
            refresh_interval=60,
            stale_after=120,
        )

        first = service.refresh()
        second = service.refresh()
        latest = service.get_latest()

        self.assertEqual(provider.calls, 1)
        self.assertIs(first, second)
        self.assertIs(first, latest)
        self.assertTrue(latest.available)
        self.assertIn("银行", latest.sectors)
        self.assertEqual(
            latest.get_stock_sectors("600000.SH"),
            ("银行",),
        )

    def test_old_source_timestamp_is_stale(self) -> None:
        provider = FakeSnapshotProvider()
        service = MarketSentimentService(
            provider=provider,
            refresh_interval=60,
            stale_after=1,
        )
        snapshot = service.refresh()

        old_snapshot = snapshot.__class__(
            datetime=datetime.fromtimestamp(
                1_600_000_000,
                tz=snapshot.datetime.tzinfo,
            ),
            score=snapshot.score,
            level=snapshot.level,
            breadth=snapshot.breadth,
            indices=snapshot.indices,
            source_count=snapshot.source_count,
        )
        service._snapshot = old_snapshot

        self.assertTrue(service.get_latest().stale)
        self.assertFalse(service.get_latest().available)


def _raw_tick(last_price: float, previous_close: float) -> dict:
    return {
        "time": 1_750_000_000_000,
        "lastPrice": last_price,
        "lastClose": previous_close,
        "open": previous_close,
        "high": max(last_price, previous_close),
        "low": min(last_price, previous_close),
        "volume": 1_000,
        "amount": last_price * 1_000,
        "stockStatus": 3,
    }


def _tick(
    symbol: str,
    last_price: float,
    previous_close: float,
    timestamp: datetime,
) -> TickSnapshot:
    return TickSnapshot(
        symbol=symbol,
        datetime=timestamp,
        last_price=last_price,
        previous_close=previous_close,
        volume=1_000,
    )


if __name__ == "__main__":
    unittest.main()
