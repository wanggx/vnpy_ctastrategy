"""收益回撤判定状态机：从策略抽出的纯状态机，bar/tick 两条路径共用一个实例。

``ProfitDrawdownState`` 持有收益回撤判定所需的运行时状态（``peak_profit_points`` /
``active`` / ``protection_active``），``check(price, pos, pos_avg_price) -> bool`` 完成一次
判定并更新状态，同时把最新状态反向写回策略的 UI 变量（``peak_profit_points`` /
``profit_drawdown_active`` / ``profit_protection_active``），供策略 ``variables`` 展示与
``sync_data`` 持久化——策略不再持有判定逻辑，只持有执行状态 ``profit_drawdown_pending``。

成交回调（``on_trade``）经 ``reset_on_open`` / ``reset_on_close`` 在开仓/平仓时重置状态机；
``reset`` 供策略 ``on_init`` 委托。判定阈值（``max_profit_drawdown_points`` /
``profit_protection_trigger_points`` / ``profit_protection_floor_points``）经策略参数读取。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..bottom_friction_strategy import BottomFrictionStrategy


class ProfitDrawdownState:
    """收益回撤判定状态机：bar/tick 共用，check 完成判定 + 状态更新 + UI 回写。

    内部状态：
    - ``peak_profit_points``：开仓后历史最高收益率（点）。
    - ``active``：回撤清仓已触发（触发后持续返回 True，直至 reset）。
    - ``protection_active``：收益保护已激活（peak 超过触发阈值后置 True）。
    策略的 ``profit_drawdown_pending`` 是执行器状态（防双重提交），不在本状态机内。
    """

    def __init__(self, strategy: BottomFrictionStrategy) -> None:
        """构造：绑定策略，初始化内部状态。"""
        self.strategy: BottomFrictionStrategy = strategy
        self.peak_profit_points: float = 0.0
        self.active: bool = False
        self.protection_active: bool = False

    def _sync_ui(self) -> None:
        """把内部状态反向写回策略 UI 变量（供 variables 展示 + sync_data 持久化）。"""
        s = self.strategy
        s.peak_profit_points = self.peak_profit_points
        s.profit_drawdown_active = self.active
        s.profit_protection_active = self.protection_active

    def check(
        self,
        last_price: float,
        pos: float,
        pos_avg_price: float,
    ) -> bool:
        """检查最高收益率是否已回撤到清仓阈值，更新状态并回写 UI。

        与原 ``BottomFrictionStrategy._check_profit_drawdown`` 逻辑一致：
        - 无持仓/无均价/无价：持仓归零时清空状态，返回 False。
        - ``active`` 已触发：直接返回 True。
        - 否则算当前收益、更新 peak、判收益保护激活、判回撤/保底触发。
        """
        if pos <= 0 or pos_avg_price <= 0 or last_price <= 0:
            if pos <= 0:
                self.peak_profit_points = 0.0
                self.active = False
                self.protection_active = False
                self._sync_ui()
            return False

        if self.active:
            return True

        current_profit_points: float = round(
            (last_price - pos_avg_price) / pos_avg_price * 100,
            2,
        )
        self.peak_profit_points = max(self.peak_profit_points, current_profit_points)

        s = self.strategy
        if (
            s.profit_protection_trigger_points > 0
            and self.peak_profit_points > s.profit_protection_trigger_points
        ):
            self.protection_active = True

        protected_profit_hit: bool = (
            self.protection_active
            and current_profit_points <= s.profit_protection_floor_points
        )

        triggered: bool = (
            protected_profit_hit
            or (
                s.max_profit_drawdown_points > 0
                and self.peak_profit_points > 0
                and self.peak_profit_points - current_profit_points
                >= s.max_profit_drawdown_points
            )
        )
        if triggered:
            self.active = True

        self._sync_ui()
        return triggered

    def reset_on_open(
        self,
        trade_price: float,
        pos_avg_price: float,
        is_new_position: bool,
    ) -> None:
        """开仓成交后重置：用成交价相对新均价初始化 peak；新仓位时清空 active/protection。

        与原 ``on_trade`` LONG/OPEN 分支一致：``peak = max(round((trade_price -
        pos_avg_price)/pos_avg_price*100, 2), 0.0)``（新仓位时 trade_price==pos_avg_price→0；
        加仓时 pos_avg_price 是加权均价，trade_price 可能高于它→peak 为正）。
        ``is_new_position``（``pos <= trade.volume``）时清空 active/protection/pending。
        ``profit_drawdown_pending`` 由策略执行器管理，此处一并清（新仓位场景）。
        """
        if pos_avg_price > 0:
            self.peak_profit_points = max(
                round((trade_price - pos_avg_price) / pos_avg_price * 100, 2),
                0.0,
            )
        if is_new_position:
            self.active = False
            self.protection_active = False
            self.strategy.profit_drawdown_pending = False
        self._sync_ui()

    def reset_on_close(self) -> None:
        """平仓成交后（仓位归零）重置：清空全部判定状态。"""
        self.peak_profit_points = 0.0
        self.active = False
        self.protection_active = False
        self.strategy.profit_drawdown_pending = False
        self._sync_ui()

    def reset(self) -> None:
        """全量重置（策略 on_init 委托）。"""
        self.peak_profit_points = 0.0
        self.active = False
        self.protection_active = False
        self._sync_ui()
