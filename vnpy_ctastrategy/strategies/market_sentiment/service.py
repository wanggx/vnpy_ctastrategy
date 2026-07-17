"""线程安全、可由多个 CTA 策略共享的市场情绪服务。"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
import logging
from threading import Event, Lock, RLock, Thread
from time import monotonic
from typing import Any, Protocol, cast

from .index_monitor import IndexMonitor
from .market_breadth import MarketBreadthCalculator
from .models import (
    MarketBreadth,
    MarketSentimentSnapshot,
    SectorState,
    SentimentLevel,
    TickSnapshot,
)
from .sentiment_calculator import SentimentCalculator
from .sector_monitor import SectorMonitor
from .xtdata_provider import XtDataProvider


logger: logging.Logger = logging.getLogger(__name__)


class MarketSnapshotProvider(Protocol):
    """情绪服务使用的数据提供器协议。"""

    index_symbols: Mapping[str, str]

    def get_full_snapshot(
        self,
    ) -> tuple[
        datetime,
        dict[str, TickSnapshot],
        tuple[str, ...],
        dict[str, tuple[str, ...]],
    ]:
        """返回时间、全部行情、股票池和板块成员。"""


class MarketSentimentService:
    """定时刷新并缓存全市场情绪，供多个策略无阻塞读取。"""

    _shared_lock: Lock = Lock()
    _shared_instances: dict[str, "MarketSentimentService"] = {}

    def __init__(
        self,
        provider: MarketSnapshotProvider | None = None,
        breadth_calculator: MarketBreadthCalculator | None = None,
        index_monitor: IndexMonitor | None = None,
        sentiment_calculator: SentimentCalculator | None = None,
        sector_monitor: SectorMonitor | None = None,
        refresh_interval: float = 5.0,
        stale_after: float = 30.0,
    ) -> None:
        if refresh_interval <= 0:
            raise ValueError("refresh_interval 必须大于0")
        if stale_after <= 0:
            raise ValueError("stale_after 必须大于0")

        self.provider: MarketSnapshotProvider = cast(
            MarketSnapshotProvider,
            provider or XtDataProvider(),
        )
        self.breadth_calculator: MarketBreadthCalculator = (
            breadth_calculator or MarketBreadthCalculator()
        )
        self.index_monitor: IndexMonitor = (
            index_monitor or IndexMonitor(self.provider.index_symbols)
        )
        self.sentiment_calculator: SentimentCalculator = (
            sentiment_calculator or SentimentCalculator()
        )
        self.sector_monitor: SectorMonitor = (
            sector_monitor
            or SectorMonitor(self.breadth_calculator)
        )
        self.refresh_interval: float = refresh_interval
        self.stale_after: float = stale_after

        self._state_lock: RLock = RLock()
        self._refresh_lock: Lock = Lock()
        self._stop_event: Event = Event()
        self._thread: Thread | None = None
        self._snapshot: MarketSentimentSnapshot | None = None
        self._last_success_at: float = 0.0
        self._last_attempt_at: float = 0.0
        self._last_error: str = ""

    @classmethod
    def get_shared(
        cls,
        name: str = "default",
        **kwargs: Any,
    ) -> "MarketSentimentService":
        """按名称获得进程内共享实例，避免多个策略重复拉取行情。"""
        with cls._shared_lock:
            service: MarketSentimentService | None = (
                cls._shared_instances.get(name)
            )
            if service is None:
                service = cls(**kwargs)
                cls._shared_instances[name] = service
            return service

    def start(self) -> None:
        """启动后台刷新线程；重复调用是安全的。"""
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return

            self._stop_event.clear()
            self._thread = Thread(
                target=self._run,
                name="MarketSentimentService",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """停止后台刷新线程。共享实例通常在应用退出时调用。"""
        self._stop_event.set()
        thread: Thread | None
        with self._state_lock:
            thread = self._thread

        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))

        with self._state_lock:
            if (
                thread is not None
                and self._thread is thread
                and not thread.is_alive()
            ):
                self._thread = None

    def refresh(self, force: bool = False) -> MarketSentimentSnapshot:
        """同步刷新一次；相邻调用会按刷新间隔合并。"""
        with self._refresh_lock:
            now_monotonic: float = monotonic()
            with self._state_lock:
                recently_attempted: bool = (
                    self._last_attempt_at > 0
                    and now_monotonic - self._last_attempt_at
                    < self.refresh_interval
                )
                if recently_attempted and not force:
                    return self._snapshot_or_unavailable()
                self._last_attempt_at = now_monotonic

            try:
                (
                    snapshot_datetime,
                    ticks,
                    stock_symbols,
                    sector_members,
                ) = (
                    self.provider.get_full_snapshot()
                )
                breadth: MarketBreadth = self.breadth_calculator.calculate(
                    ticks,
                    stock_symbols,
                )
                indices = self.index_monitor.calculate(
                    ticks,
                    snapshot_datetime,
                )
                sectors: dict[str, SectorState]
                stock_sectors: dict[str, tuple[str, ...]]
                sectors, stock_sectors = self.sector_monitor.calculate(
                    ticks,
                    sector_members,
                )
                snapshot: MarketSentimentSnapshot = (
                    self.sentiment_calculator.calculate(
                        snapshot_datetime,
                        breadth,
                        indices,
                        source_count=len(ticks),
                        sectors=sectors,
                        stock_sectors=stock_sectors,
                    )
                )
            except Exception as exc:
                error: str = f"{type(exc).__name__}: {exc}"
                logger.exception("刷新市场情绪失败")
                with self._state_lock:
                    self._last_error = error
                    current: MarketSentimentSnapshot = (
                        self._snapshot_or_unavailable(error)
                    )
                    return replace(
                        current,
                        stale=self._is_stale(),
                        error=error,
                    )

            with self._state_lock:
                self._snapshot = snapshot
                self._last_success_at = monotonic()
                self._last_error = snapshot.error
                return snapshot

    def get_latest(
        self,
        refresh_if_needed: bool = False,
    ) -> MarketSentimentSnapshot:
        """读取缓存；默认不在 CTA 回调线程中执行全市场查询。"""
        if refresh_if_needed:
            self.refresh()

        with self._state_lock:
            snapshot: MarketSentimentSnapshot = (
                self._snapshot_or_unavailable(self._last_error)
            )
            stale: bool = self._is_stale()
            if snapshot.stale == stale:
                return snapshot
            return replace(snapshot, stale=stale)

    def _run(self) -> None:
        """后台刷新循环。"""
        while not self._stop_event.is_set():
            self.refresh(force=True)
            self._stop_event.wait(self.refresh_interval)

    def _is_stale(self) -> bool:
        """调用方持有状态锁时判断缓存是否过期。"""
        cache_stale: bool = (
            self._last_success_at <= 0
            or monotonic() - self._last_success_at > self.stale_after
        )
        if cache_stale or self._snapshot is None:
            return True

        snapshot_datetime: datetime = self._snapshot.datetime
        now: datetime = datetime.now(tz=snapshot_datetime.tzinfo)
        source_age: float = (now - snapshot_datetime).total_seconds()
        return source_age > self.stale_after

    def _snapshot_or_unavailable(
        self,
        error: str = "",
    ) -> MarketSentimentSnapshot:
        """调用方持有状态锁时返回当前或不可用快照。"""
        if self._snapshot is not None:
            return self._snapshot

        return MarketSentimentSnapshot(
            datetime=datetime.now().astimezone(),
            score=0.0,
            level=SentimentLevel.UNAVAILABLE,
            breadth=MarketBreadth(),
            stale=True,
            error=error or "市场情绪尚未完成首次刷新",
        )
