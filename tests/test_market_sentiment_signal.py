import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

from vnpy_ctastrategy.strategies.signals import (
    SECTOR_STRONG_ENTRY_MARK,
    MarketSentimentSignal,
    SentimentResult,
)
from vnpy_ctastrategy.strategies.signals.market_sentiment import _sector_symbol


# ---- mock snapshot 基础设施（不依赖 vnpy_marketsentiment 包） ----


@dataclass
class FakeBreadth:
    declining: int
    advancing: int


@dataclass
class FakeSectorState:
    breadth: FakeBreadth
    score: float = 50.0
    name: str = ""


@dataclass
class FakeSnapshot:
    available: bool
    score: float
    breadth: FakeBreadth
    sectors: dict[str, FakeSectorState]
    stock_sectors: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def get_stock_sectors(self, symbol: str) -> tuple[str, ...]:
        return self.stock_sectors.get(symbol, ())

    def get_stock_sector(self, symbol: str) -> FakeSectorState | None:
        for name in self.get_stock_sectors(symbol):
            state: FakeSectorState | None = self.sectors.get(name)
            if state is not None:
                return state
        return None


def _make_snapshot(
    *,
    declining: int = 0,
    sectors: dict[str, tuple[int, int]] | None = None,
    sector_scores: dict[str, float] | None = None,
    stock_sectors: dict[str, str] | None = None,
    score: float = 50.0,
) -> FakeSnapshot:
    """构造 mock snapshot。

    ``sectors`` 值为 (declining, advancing) 元组；``sector_scores`` 为 板块名→情绪分；
    ``stock_sectors`` 为 QMT 代码→板块名（模拟申万二级映射）。
    """
    scores: dict[str, float] = sector_scores or {}
    sector_states: dict[str, FakeSectorState] = {}
    for name, (dec, adv) in (sectors or {}).items():
        sector_states[name] = FakeSectorState(
            breadth=FakeBreadth(declining=dec, advancing=adv),
            score=scores.get(name, 50.0),
            name=name,
        )
    return FakeSnapshot(
        available=True,
        score=score,
        breadth=FakeBreadth(declining=declining, advancing=5000 - declining),
        sectors=sector_states,
        stock_sectors={
            symbol: (name,) for symbol, name in (stock_sectors or {}).items()
        },
    )


def _make_strategy(**overrides) -> SimpleNamespace:
    """构造最小 mock strategy。"""
    defaults = dict(
        market_sentiment_enabled=True,
        cta_engine=SimpleNamespace(main_engine=object()),
        pos=1000,
        base_size=1000,
        friction_size=500,
        sector_strong_today=False,
        vt_symbol="000001.SZSE",
        write_log=lambda msg: None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ============================================================
# 纯计算测试：直接调 _calc，不需 mock 引擎
# ============================================================


class MarketSentimentCalcTest(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = _make_strategy(pos=0)
        self.signal = MarketSentimentSignal(
            strategy=self.strategy,
            decline_reduce_threshold=3500,
            decline_exit_threshold=4000,
            sector_decline_ratio_threshold=0.33,
        )

    def test_normal_market_returns_level_0(self) -> None:
        snap = _make_snapshot(
            declining=1000,
            sectors={
                "s1": (10, 90),
                "s2": (20, 80),
                "s3": (30, 70),
            },
        )
        result = self.signal._calc(snap)

        self.assertIsInstance(result, SentimentResult)
        self.assertEqual(result.risk_level, 0)
        self.assertEqual(result.score, 50.0)
        self.assertEqual(result.declining_count, 1000)
        self.assertEqual(result.valid_sector_count, 3)
        self.assertEqual(result.declining_sector_count, 0)
        self.assertEqual(result.declining_sector_ratio, 0.0)
        # 占位值
        self.assertEqual(result.target_pos, -1)
        self.assertEqual(result.mark, "")
        self.assertFalse(result.should_block)

    def test_exit_threshold_returns_level_2(self) -> None:
        snap = _make_snapshot(
            declining=4001,
            sectors={"s1": (10, 90)},
        )
        result = self.signal._calc(snap)

        self.assertEqual(result.risk_level, 2)

    def test_reduce_threshold_with_weak_sectors_returns_level_1(self) -> None:
        snap = _make_snapshot(
            declining=3501,
            sectors={
                "s1": (80, 20),  # 弱势
                "s2": (80, 20),  # 弱势
                "s3": (20, 80),  # 强势
            },
        )
        result = self.signal._calc(snap)

        self.assertEqual(result.risk_level, 1)
        self.assertEqual(result.declining_sector_count, 2)
        self.assertEqual(result.valid_sector_count, 3)
        self.assertAlmostEqual(result.declining_sector_ratio, 0.67, places=2)

    def test_reduce_threshold_but_strong_sectors_returns_level_0(self) -> None:
        snap = _make_snapshot(
            declining=3501,
            sectors={
                "s1": (10, 90),
                "s2": (20, 80),
                "s3": (30, 70),
            },
        )
        result = self.signal._calc(snap)

        self.assertEqual(result.risk_level, 0)

    def test_no_sectors_high_decline_returns_level_0(self) -> None:
        """无板块时 ratio=0.0，即使下跌家数超过减仓阈值也不触发减仓。"""
        snap = _make_snapshot(declining=3501, sectors={})
        result = self.signal._calc(snap)

        self.assertEqual(result.risk_level, 0)
        self.assertEqual(result.valid_sector_count, 0)
        self.assertEqual(result.declining_sector_ratio, 0.0)

    def test_no_sectors_exit_threshold_still_returns_level_2(self) -> None:
        """清仓阈值不依赖板块 ratio，无板块也触发。"""
        snap = _make_snapshot(declining=4001, sectors={})
        result = self.signal._calc(snap)

        self.assertEqual(result.risk_level, 2)

    def test_score_rounded_to_two_decimals(self) -> None:
        snap = _make_snapshot(score=73.456789, declining=0)
        result = self.signal._calc(snap)

        self.assertEqual(result.score, 73.46)

    def test_calc_carries_stock_sector(self) -> None:
        """_calc 顺带给出标的所属行业名与行业情绪分（vt_symbol → QMT 代码）。"""
        self.strategy.vt_symbol = "600000.SSE"
        snap = _make_snapshot(
            declining=0,
            sectors={"汽车零部件": (10, 90)},
            sector_scores={"汽车零部件": 31.256},
            stock_sectors={"600000.SH": "汽车零部件"},
        )
        result = self.signal._calc(snap)

        self.assertEqual(result.sector_name, "汽车零部件")
        self.assertEqual(result.sector_score, 31.26)

    def test_calc_without_mapping_returns_empty_sector(self) -> None:
        snap = _make_snapshot(declining=0, sectors={"汽车零部件": (10, 90)})
        result = self.signal._calc(snap)

        self.assertEqual(result.sector_name, "")
        self.assertEqual(result.sector_score, 0.0)

    def test_default_thresholds_match_strategy_defaults(self) -> None:
        """不传参时默认阈值与 BottomFrictionStrategy 类默认一致。"""
        signal = MarketSentimentSignal(strategy=self.strategy)

        self.assertEqual(signal.decline_reduce_threshold, 3500)
        self.assertEqual(signal.decline_exit_threshold, 4000)
        self.assertAlmostEqual(
            signal.sector_decline_ratio_threshold, 0.33, places=2
        )
        self.assertEqual(signal.sector_score_exit_threshold, 40.0)
        self.assertEqual(signal.sector_score_entry_threshold, 80.0)


class SectorSymbolTest(unittest.TestCase):
    """vt_symbol → 情绪 App（QMT 代码）的交易所后缀映射。"""

    def test_exchange_suffixes(self) -> None:
        self.assertEqual(_sector_symbol("600000.SSE"), "600000.SH")
        self.assertEqual(_sector_symbol("000001.SZSE"), "000001.SZ")
        self.assertEqual(_sector_symbol("430047.BSE"), "430047.BJ")

    def test_unknown_exchange_passed_through(self) -> None:
        self.assertEqual(_sector_symbol("600000.UNKNOWN"), "600000.UNKNOWN")
        self.assertEqual(_sector_symbol("600000"), "600000")


# ============================================================
# 厚信号 on_bar 测试：mock 引擎 + 快照 + strategy.pos，验证状态机和降级
# ============================================================


class MarketSentimentOnBarTest(unittest.TestCase):
    """厚信号 on_bar 测试：mock 引擎 + 快照 + strategy.pos，验证状态机和降级。"""

    def setUp(self) -> None:
        # 快照持有在引擎上，每测试设置不同快照
        self._snapshot: FakeSnapshot | None = None
        self.fake_engine = SimpleNamespace(
            started=True,
            get_latest=lambda: self._snapshot,
        )
        self.strategy = _make_strategy(pos=1000)
        # patch 模块级 get_sentiment_engine，使 _get_engine 返回 fake_engine
        self._patch = patch(
            "vnpy_ctastrategy.strategies.signals.market_sentiment.get_sentiment_engine",
            return_value=self.fake_engine,
        )
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def _make_signal(self, **overrides) -> MarketSentimentSignal:
        params: dict = dict(
            strategy=self.strategy,
            decline_reduce_threshold=3500,
            decline_exit_threshold=4000,
            sector_decline_ratio_threshold=0.33,
        )
        params.update(overrides)
        return MarketSentimentSignal(**params)

    # ---- 降级路径 ----

    def test_no_main_engine_returns_empty_result(self) -> None:
        """回测环境：cta_engine 无 main_engine -> 降级。"""
        self.strategy.cta_engine = SimpleNamespace()  # 无 main_engine
        signal = self._make_signal()
        result = signal.on_bar(None)

        self.assertFalse(result.should_block)
        self.assertEqual(result.risk_level, 0)
        self.assertEqual(result.target_pos, -1)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(signal.risk_level, 0)
        self.assertEqual(signal.target_pos, -1)

    def test_snapshot_unavailable_resets_state(self) -> None:
        """快照不可用 -> 重置状态机 -> 降级。"""
        self._snapshot = FakeSnapshot(
            available=False, score=0, breadth=FakeBreadth(0, 0), sectors={}
        )
        signal = self._make_signal()
        signal.risk_level = 1   # 模拟之前有风控
        signal.target_pos = 500
        result = signal.on_bar(None)

        self.assertFalse(result.should_block)
        self.assertEqual(result.risk_level, 0)
        self.assertEqual(result.target_pos, -1)
        self.assertEqual(signal.risk_level, 0)
        self.assertEqual(signal.target_pos, -1)

    # ---- 风控路径 ----

    def test_risk_level_2_clears_position(self) -> None:
        """下跌 >4000 -> risk_level=2 -> target_pos=0 -> should_block=True。"""
        self._snapshot = _make_snapshot(declining=4001, sectors={"s1": (10, 90)})
        signal = self._make_signal()
        result = signal.on_bar(None)

        self.assertTrue(result.should_block)
        self.assertEqual(result.risk_level, 2)
        self.assertEqual(result.target_pos, 0)
        self.assertEqual(result.mark, "市场超过4000家下跌，清仓")
        self.assertEqual(signal.risk_level, 2)
        self.assertEqual(signal.target_pos, 0)

    def test_risk_level_1_halves_position(self) -> None:
        """下跌 >3500 且板块弱 >1/3 -> risk_level=1 -> target_pos=int(pos/2)。"""
        self._snapshot = _make_snapshot(
            declining=3501,
            sectors={"s1": (80, 20), "s2": (80, 20), "s3": (20, 80)},
        )
        self.strategy.pos = 1000
        signal = self._make_signal()
        result = signal.on_bar(None)

        self.assertTrue(result.should_block)
        self.assertEqual(result.risk_level, 1)
        self.assertEqual(result.target_pos, 500)  # int(1000/2)
        self.assertEqual(result.mark, "市场和板块偏弱，仓位减半")

    def test_risk_level_unchanged_maintains_target_pos(self) -> None:
        """risk_level 未变化 -> 不重算 target_pos -> 维持上次值。"""
        self._snapshot = _make_snapshot(declining=4001, sectors={"s1": (10, 90)})
        signal = self._make_signal()
        signal.risk_level = 2    # 已在 level 2
        signal.target_pos = 0    # 上次已清仓
        result = signal.on_bar(None)

        self.assertTrue(result.should_block)
        self.assertEqual(result.risk_level, 2)
        self.assertEqual(result.target_pos, 0)   # 未重算
        self.assertEqual(signal.target_pos, 0)   # 内部也未变

    def test_risk_level_0_returns_observable_fields(self) -> None:
        """risk_level=0 -> should_block=False 但 5 个可观测字段有效，状态机重置。"""
        self._snapshot = _make_snapshot(
            declining=1000,
            sectors={"s1": (10, 90), "s2": (20, 80)},
            score=65.0,
        )
        signal = self._make_signal()
        signal.risk_level = 1  # 之前有风控
        result = signal.on_bar(None)

        self.assertFalse(result.should_block)
        self.assertEqual(result.risk_level, 0)
        self.assertEqual(result.target_pos, -1)
        self.assertEqual(result.score, 65.0)          # 可观测字段有效
        self.assertEqual(result.declining_count, 1000)
        self.assertEqual(result.valid_sector_count, 2)
        self.assertEqual(signal.risk_level, 0)         # 状态机已重置
        self.assertEqual(signal.target_pos, -1)

    def test_transition_from_level_1_to_level_2(self) -> None:
        """risk_level 从 1 升到 2 -> target_pos 从 pos/2 变为 0。"""
        self._snapshot = _make_snapshot(declining=4001, sectors={"s1": (10, 90)})
        signal = self._make_signal()
        signal.risk_level = 1
        signal.target_pos = 500
        result = signal.on_bar(None)

        self.assertTrue(result.should_block)
        self.assertEqual(result.risk_level, 2)
        self.assertEqual(result.target_pos, 0)  # 清仓
        self.assertEqual(signal.target_pos, 0)

    def test_reset_clears_state_machine(self) -> None:
        """reset() 重置状态机内部状态。"""
        signal = self._make_signal()
        signal.risk_level = 2
        signal.target_pos = 0
        signal.reset()

        self.assertEqual(signal.risk_level, 0)
        self.assertEqual(signal.target_pos, -1)

    # ---- 行业情绪风控路径（risk_level==0 时判定） ----

    def _weak_sector_snapshot(self, sector_score: float) -> FakeSnapshot:
        return _make_snapshot(
            declining=1000,
            sectors={"汽车零部件": (10, 90)},
            sector_scores={"汽车零部件": sector_score},
            stock_sectors={"000001.SZ": "汽车零部件"},
        )

    def test_weak_sector_sells_friction_position(self) -> None:
        """行业情绪分 < 40（偏弱档）且有摩擦仓 → SELL(底仓)。"""
        self._snapshot = self._weak_sector_snapshot(35.0)
        self.strategy.pos = 1500
        self.strategy.base_size = 1000
        signal = self._make_signal()
        result = signal.on_bar(None)

        self.assertTrue(result.should_block)
        self.assertEqual(result.risk_level, 0)
        self.assertEqual(result.target_pos, 1000)  # base_size
        self.assertEqual(result.mark, "汽车零部件情绪偏弱，减摩擦仓")
        self.assertEqual(result.sector_score, 35.0)
        self.assertEqual(signal.target_pos, -1)  # 大盘状态机未激活

    def test_neutral_sector_does_not_sell(self) -> None:
        """中性档（40 <= score < 60）不触发减摩擦仓。"""
        self._snapshot = self._weak_sector_snapshot(45.0)
        self.strategy.pos = 1500
        signal = self._make_signal()
        result = signal.on_bar(None)

        self.assertFalse(result.should_block)
        self.assertEqual(result.declining_count, 1000)

    def test_weak_sector_without_friction_position_no_action(self) -> None:
        """无摩擦仓（pos == base_size）时不动作，避免反而给出补仓目标。"""
        self._snapshot = self._weak_sector_snapshot(20.0)
        self.strategy.pos = 1000
        self.strategy.base_size = 1000
        result = self._make_signal().on_bar(None)

        self.assertFalse(result.should_block)
        self.assertEqual(result.target_pos, -1)

    def test_weak_sector_with_unmapped_symbol_no_action(self) -> None:
        """标的未映射到行业时不动作（sector_score 为 0 也不误触发）。"""
        self._snapshot = _make_snapshot(
            declining=1000,
            sectors={"汽车零部件": (10, 90)},
            sector_scores={"汽车零部件": 10.0},
        )
        self.strategy.pos = 1500
        result = self._make_signal().on_bar(None)

        self.assertFalse(result.should_block)
        self.assertEqual(result.sector_name, "")

    def test_market_risk_takes_priority_over_weak_sector(self) -> None:
        """大盘清仓（risk_level=2）优先于行业减摩擦仓。"""
        self._snapshot = _make_snapshot(
            declining=4001,
            sectors={"汽车零部件": (10, 90)},
            sector_scores={"汽车零部件": 10.0},
            stock_sectors={"000001.SZ": "汽车零部件"},
        )
        self.strategy.pos = 1500
        signal = self._make_signal()
        result = signal.on_bar(None)

        self.assertTrue(result.should_block)
        self.assertEqual(result.risk_level, 2)
        self.assertEqual(result.target_pos, 0)
        self.assertEqual(result.mark, "市场超过4000家下跌，清仓")

    def test_sector_rule_disabled_by_zero_threshold(self) -> None:
        """阈值 <= 0 关闭行业减摩擦仓规则。"""
        self._snapshot = self._weak_sector_snapshot(10.0)
        self.strategy.pos = 1500
        result = self._make_signal(sector_score_exit_threshold=0).on_bar(None)

        self.assertFalse(result.should_block)

    def test_sector_threshold_is_configurable(self) -> None:
        """阈值调到 60 后，中性档（45 分）也会减摩擦仓。"""
        self._snapshot = self._weak_sector_snapshot(45.0)
        self.strategy.pos = 1500
        result = self._make_signal(sector_score_exit_threshold=60.0).on_bar(None)

        self.assertTrue(result.should_block)
        self.assertEqual(result.target_pos, 1000)

    def test_engine_unavailable_does_not_sell_on_sector(self) -> None:
        """引擎不可用（获取不到快照）时不动作。"""
        self.strategy.cta_engine = SimpleNamespace()  # 无 main_engine
        self.strategy.pos = 1500
        result = self._make_signal().on_bar(None)

        self.assertFalse(result.should_block)

    # ---- 行业情绪极强入场路径（risk_level==0 且摩擦位为空） ----

    def test_strong_sector_buys_friction_position(self) -> None:
        """行业情绪分 >= 80（极强）且摩擦位为空 → BUY(底仓+摩擦仓)。"""
        self._snapshot = self._weak_sector_snapshot(85.0)
        self.strategy.pos = 1000
        self.strategy.base_size = 1000
        self.strategy.friction_size = 500
        result = self._make_signal().on_bar(None)

        self.assertTrue(result.should_block)
        self.assertEqual(result.risk_level, 0)
        self.assertEqual(result.target_pos, 1500)  # base + friction
        self.assertEqual(result.mark, SECTOR_STRONG_ENTRY_MARK)

    def test_strong_sector_with_friction_position_no_action(self) -> None:
        """已有摩擦仓（pos > base_size）时不重复买入（也不减仓）。"""
        self._snapshot = self._weak_sector_snapshot(85.0)
        self.strategy.pos = 1500
        result = self._make_signal().on_bar(None)

        self.assertFalse(result.should_block)
        self.assertEqual(result.target_pos, -1)

    def test_strong_sector_threshold_boundary(self) -> None:
        """边界：== 80 触发，79.99 不触发。"""
        self._snapshot = self._weak_sector_snapshot(79.99)
        self.strategy.pos = 1000
        self.assertFalse(self._make_signal().on_bar(None).should_block)

        self._snapshot = self._weak_sector_snapshot(80.0)
        self.assertTrue(self._make_signal().on_bar(None).should_block)

    def test_market_risk_takes_priority_over_strong_sector(self) -> None:
        """大盘清仓（risk_level=2）优先：即便行业极强也不买入。"""
        self._snapshot = _make_snapshot(
            declining=4001,
            sectors={"汽车零部件": (10, 90)},
            sector_scores={"汽车零部件": 95.0},
            stock_sectors={"000001.SZ": "汽车零部件"},
        )
        self.strategy.pos = 1000
        result = self._make_signal().on_bar(None)

        self.assertEqual(result.risk_level, 2)
        self.assertEqual(result.target_pos, 0)
        self.assertEqual(result.mark, "市场超过4000家下跌，清仓")

    def test_strong_sector_skipped_after_done_today(self) -> None:
        """当天已操作过一次（成交过）→ 当天不再买入，即使行业仍极强。"""
        self._snapshot = self._weak_sector_snapshot(85.0)
        self.strategy.pos = 1000
        self.strategy.sector_strong_today = True
        result = self._make_signal().on_bar(None)

        self.assertFalse(result.should_block)
        self.assertEqual(result.target_pos, -1)

    def test_strong_sector_allowed_again_next_day(self) -> None:
        """跨交易日后标记解锁 → 可以再次买入。"""
        self._snapshot = self._weak_sector_snapshot(85.0)
        self.strategy.pos = 1000
        self.strategy.sector_strong_today = False  # 新的一天
        result = self._make_signal().on_bar(None)

        self.assertTrue(result.should_block)
        self.assertEqual(result.target_pos, 1500)

    def test_strong_sector_with_unmapped_symbol_no_action(self) -> None:
        """标的未映射到行业时不买入（sector_score 为 0 也不会误触发减仓）。"""
        self._snapshot = _make_snapshot(
            declining=1000,
            sectors={"汽车零部件": (10, 90)},
            sector_scores={"汽车零部件": 95.0},
        )
        self.strategy.pos = 1000
        result = self._make_signal().on_bar(None)

        self.assertFalse(result.should_block)
        self.assertEqual(result.sector_name, "")


if __name__ == "__main__":
    unittest.main()
