import numpy as np

from vnpy_ctastrategy import (
    CtaTemplate,
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    BarGenerator,
    ArrayManager,
)


class T1DoubleMaStrategy(CtaTemplate):
    """"""

    author = "vn.py"

    fast_window: int = 3
    slow_window: int = 5
    fixed_size: int = 100
    t1: bool = True

    fast_ma: float = 0
    slow_ma: float = 0

    parameters = ["fast_window", "slow_window", "fixed_size"]
    variables = ["fast_ma", "slow_ma"]

    def on_init(self) -> None:
        """"""
        self.write_log("Strategy initialized")

        self.bg: BarGenerator = BarGenerator(self.on_bar)
        self.am: ArrayManager = ArrayManager()

        self.load_bar(10)

    def on_start(self) -> None:
        """"""
        self.write_log("Strategy started")
        self.put_event()

    def on_stop(self) -> None:
        """"""
        self.write_log("Strategy stopped")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """"""
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """"""
        self.cancel_all()

        am: ArrayManager = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        fast_ma: np.ndarray = am.sma(self.fast_window, array=True)
        slow_ma: np.ndarray = am.sma(self.slow_window, array=True)

        self.fast_ma = fast_ma[-1]
        self.slow_ma = slow_ma[-1]

        if self.fast_ma > self.slow_ma:
            if self.pos <= 0:
                self.buy(bar.close_price, self.fixed_size)
        elif self.fast_ma < self.slow_ma:
            available: float = self.yd_pos - self.local_sell_frozen if self.t1 else self.pos
            sell_volume: float = min(self.pos, available)
            sell_volume = max(sell_volume, 0)
            sell_volume = int(sell_volume / 100) * 100

            if sell_volume:
                self.sell(bar.close_price, sell_volume)

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        """"""
        pass

    def on_trade(self, trade: TradeData) -> None:
        """"""
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """"""
        pass
