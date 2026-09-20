"""大盘情绪厚信号：持有 strategy 引用，on_bar 内部取引擎+计算+状态机，输出完整 SentimentResult。

除大盘风控（下跌家数 → 减半/清仓）外，还有两级**行业级**判定：

- 行业情绪分 < ``sector_score_exit_threshold``（默认 40，即"偏弱/极弱"档）→ 卖出摩擦仓回到底仓。
- 行业情绪分 >= ``sector_score_entry_threshold``（默认 80，即"极强"档）且摩擦位为空 →
  买入摩擦仓（``SECTOR_STRONG_ENTRY_MARK`` 标注），由策略按成交价挂 +2% 卖出停止单止盈。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

try:
    from vnpy_marketsentiment import get_sentiment_engine
except ImportError:  # noqa: BLE001 - 情绪 App 未安装时降级
    get_sentiment_engine = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from vnpy.trader.object import BarData
    from vnpy_marketsentiment import MarketSentimentSnapshot


# 行业情绪极强入场买入的下单标注：策略据此识别"我方委托"并在成交后记录入场价
SECTOR_STRONG_ENTRY_MARK: str = "行业极强，买入摩擦仓"

# CTA 的 vt_symbol 后缀 → 情绪 App（QMT 代码）的交易所后缀
_SECTOR_SYMBOL_SUFFIX: dict[str, str] = {
    "SSE": "SH",
    "SZSE": "SZ",
    "BSE": "BJ",
}


def _sector_symbol(vt_symbol: str) -> str:
    """把策略的 vt_symbol 转成情绪快照使用的 QMT 代码：``600000.SSE`` → ``600000.SH``。

    未知交易所后缀时原样返回（引擎查不到映射，按"无行业"处理）。
    """
    symbol, separator, exchange = vt_symbol.partition(".")
    suffix: str | None = _SECTOR_SYMBOL_SUFFIX.get(exchange.upper())
    if not separator or not symbol or suffix is None:
        return vt_symbol
    return f"{symbol}.{suffix}"


@dataclass(frozen=True, slots=True)
class SentimentResult:
    """单次大盘情绪评估结果。"""

    risk_level: int                # 0正常、1减半、2清仓
    score: float                   # 情绪分 0-100
    declining_count: int           # 全市场下跌家数
    declining_sector_count: int    # 弱势板块数
    valid_sector_count: int        # 有效板块总数
    declining_sector_ratio: float  # 弱势板块占比
    target_pos: int                # 状态机目标仓位（-1=未激活）
    mark: str                      # 下单标注（空串=不下单）
    should_block: bool             # 是否阻断后续策略信号
    sector_name: str = ""          # 标的所属行业名（""=未映射）
    sector_score: float = 0.0      # 标的所属行业情绪分 0-100（未映射时 0.0）


# 降级空结果：引擎不可用/快照不可用时复用。frozen=True 保证不可变，模块级单例安全。
EMPTY_RESULT: SentimentResult = SentimentResult(
    risk_level=0,
    score=0.0,
    declining_count=0,
    declining_sector_count=0,
    valid_sector_count=0,
    declining_sector_ratio=0.0,
    target_pos=-1,
    mark="",
    should_block=False,
)


class MarketSentimentSignal:
    """大盘情绪厚信号 + 行业情绪风控。

    持有 strategy 引用，on_bar(bar) 内部完成取引擎→取 snapshot→计算→状态机，
    返回完整 SentimentResult（含 target_pos/mark/should_block + 可观测字段 + risk_level）。
    策略 on_bar 调 signal.on_bar(bar) 拿结果后做 UI 变量同步 + 下单。

    两级风控 + 一级机会，大盘优先：
    1. **大盘**（状态机）：下跌家数超阈值 → 减半/清仓（``risk_level`` 1/2）。
    2. **行业减仓**（级别触发）：标的所属行业情绪分 < ``sector_score_exit_threshold`` 且有摩擦仓
       → 回到底仓（SELL(base_size)）。
    3. **行业加仓**（级别触发，仅摩擦位为空时）：行业情绪分 >= ``sector_score_entry_threshold``
       → BUY(base_size + friction_size)，标注 ``SECTOR_STRONG_ENTRY_MARK``。
    2/3 只在 ``risk_level == 0`` 时判定（大盘有动作时不开新仓、也不与减半目标冲突）。
    状态机内部状态（risk_level/target_pos）由 Signal 持有，不下单、不改 strategy 状态。
    """

    def __init__(
        self,
        strategy: Any,
        decline_reduce_threshold: int = 3500,
        decline_exit_threshold: int = 4000,
        sector_decline_ratio_threshold: float = round(1 / 3, 2),
        sector_score_exit_threshold: float = 40.0,
        sector_score_entry_threshold: float = 80.0,
    ) -> None:
        self.strategy = strategy
        self.decline_reduce_threshold: int = decline_reduce_threshold
        self.decline_exit_threshold: int = decline_exit_threshold
        self.sector_decline_ratio_threshold: float = sector_decline_ratio_threshold
        # 行业情绪分低于该值时卖出摩擦仓；<=0 表示关闭该规则（40 = "偏弱"档起点）
        self.sector_score_exit_threshold: float = sector_score_exit_threshold
        # 行业情绪分不低于该值时买入摩擦仓；<=0 表示关闭该规则（80 = "极强"档起点）
        self.sector_score_entry_threshold: float = sector_score_entry_threshold
        # 状态机内部状态（从策略移入）
        self.risk_level: int = 0
        self.target_pos: int = -1

    def _get_engine(self):
        """取市场情绪引擎；未启用/未安装/无 main_engine 时返回 None。"""
        if not self.strategy.market_sentiment_enabled or get_sentiment_engine is None:
            return None
        main_engine = getattr(self.strategy.cta_engine, "main_engine", None)
        if main_engine is None:
            return None
        return get_sentiment_engine(main_engine)

    def _calc(self, snapshot: MarketSentimentSnapshot) -> SentimentResult:
        """纯计算：从 snapshot 算 risk_level + 可观测字段 + 标的行业情绪。

        不触及状态机；target_pos/mark/should_block 为占位值，由 on_bar 用 replace 覆写。
        """
        score: float = round(float(snapshot.score), 2)
        declining_count: int = snapshot.breadth.declining

        sector_states = list(snapshot.sectors.values())
        valid_sector_count: int = len(sector_states)
        declining_sector_count: int = sum(
            state.breadth.declining > state.breadth.advancing
            for state in sector_states
        )
        if valid_sector_count:
            declining_sector_ratio: float = round(
                declining_sector_count / valid_sector_count, 2
            )
        else:
            declining_sector_ratio = 0.0

        if declining_count > self.decline_exit_threshold:
            risk_level: int = 2
        elif (
            declining_count > self.decline_reduce_threshold
            and declining_sector_ratio > self.sector_decline_ratio_threshold
        ):
            risk_level = 1
        else:
            risk_level = 0

        sector_name, sector_score = self._stock_sector(snapshot)

        return SentimentResult(
            risk_level=risk_level,
            score=score,
            declining_count=declining_count,
            declining_sector_count=declining_sector_count,
            valid_sector_count=valid_sector_count,
            declining_sector_ratio=declining_sector_ratio,
            target_pos=-1,
            mark="",
            should_block=False,
            sector_name=sector_name,
            sector_score=sector_score,
        )

    def _stock_sector(self, snapshot: MarketSentimentSnapshot) -> tuple[str, float]:
        """取标的所属行业名与行业情绪分；未映射/行业被丢掉时返回 ("", 0.0)。"""
        symbol: str = _sector_symbol(str(getattr(self.strategy, "vt_symbol", "")))
        state = snapshot.get_stock_sector(symbol) if symbol else None
        if state is None:
            return "", 0.0
        return str(state.name), round(float(state.score), 2)

    def _sector_friction_exit(self, result: SentimentResult) -> SentimentResult | None:
        """行业情绪偏弱且有摩擦仓 → 回到底仓（SELL(base_size)）。

        触发条件：阈值 > 0 且行业已映射 且 ``sector_score < sector_score_exit_threshold``
        且 ``pos > base_size``（无摩擦仓时不动作，避免反而给出"补仓"目标）。命中返回改写后的
        结果（target_pos=base_size、mark、should_block=True），否则 None 表示不干预。

        级别触发（非一次性）：行业持续偏弱期间每根 bar 都给出底仓目标，由
        ``_set_target_position`` 按目标仓位收敛；行业回暖（分 >= 阈值）后自动停止。
        """
        if self.sector_score_exit_threshold <= 0:
            return None
        if not result.sector_name:
            return None
        if result.sector_score >= self.sector_score_exit_threshold:
            return None

        s = self.strategy
        if s.pos <= s.base_size:
            return None

        return replace(
            result,
            target_pos=int(s.base_size),
            mark=f"{result.sector_name}情绪偏弱，减摩擦仓",
            should_block=True,
        )

    def _sector_strong_entry(self, result: SentimentResult) -> SentimentResult | None:
        """行业情绪极强且摩擦位为空 → 买入摩擦仓（BUY(底仓+摩擦仓)）。

        触发条件：阈值 > 0 且行业已映射 且 ``sector_score >= sector_score_entry_threshold``
        且 ``pos <= base_size``（已有摩擦仓或在途时不重复买入）且当天尚未操作过
        （``strategy.sector_strong_today``，由策略在成交后标记，一天最多买一次）。
        命中返回改写后的结果（target_pos=base+friction、mark=``SECTOR_STRONG_ENTRY_MARK``、
        should_block=True）。

        级别触发：行业持续极强且摩擦位为空时每根 bar 都会给出目标仓，由
        ``_set_target_position`` 按目标仓位收敛（在途委托已计入，不会重复下单）。
        """
        if self.sector_score_entry_threshold <= 0:
            return None
        if not result.sector_name:
            return None
        if result.sector_score < self.sector_score_entry_threshold:
            return None

        s = self.strategy
        if s.pos > s.base_size:
            return None
        # 一天只做一次：当天已成交过就不再买入（当天被卖回底仓也不重复）
        if getattr(s, "sector_strong_today", False):
            return None

        return replace(
            result,
            target_pos=int(s.base_size) + int(s.friction_size),
            mark=SECTOR_STRONG_ENTRY_MARK,
            should_block=True,
        )

    def on_bar(self, bar: BarData) -> SentimentResult:
        """厚信号入口：取引擎→取 snapshot→计算→状态机→返回完整 Result。

        降级（引擎 None/快照不可用）：重置内部状态，返回 should_block=False。
        大盘风控（risk_level 1/2）：仅 risk_level 变化时重算 target_pos，返回 should_block=True。
        行业风控（仅 risk_level==0 时判定）：行业偏弱且有摩擦仓 → SELL(base_size)；
        行业极强且摩擦位为空 → BUY(base_size+friction_size)。
        bar 保留以对齐 CtaSignal.on_bar 标准签名，情绪数据来自 snapshot 而非 bar。
        """
        engine = self._get_engine()
        if engine is None:
            self.risk_level = 0
            self.target_pos = -1
            return EMPTY_RESULT

        snapshot: MarketSentimentSnapshot = engine.get_latest()
        if not snapshot.available:
            self.risk_level = 0
            self.target_pos = -1
            return EMPTY_RESULT

        result: SentimentResult = self._calc(snapshot)

        if result.risk_level == 0:
            self.risk_level = 0
            self.target_pos = -1
            # 大盘无风控时才判行业，避免与减半/清仓目标冲突
            friction_exit: SentimentResult | None = self._sector_friction_exit(result)
            if friction_exit is not None:
                return friction_exit
            strong_entry: SentimentResult | None = self._sector_strong_entry(result)
            if strong_entry is not None:
                return strong_entry
            return result  # 占位值恰好正确：target_pos=-1, mark="", should_block=False

        if result.risk_level != self.risk_level:
            if result.risk_level == 2:
                self.target_pos = 0
            else:
                self.target_pos = max(0, int(self.strategy.pos / 2))
            self.risk_level = result.risk_level

        mark: str = (
            "市场超过4000家下跌，清仓"
            if result.risk_level == 2
            else "市场和板块偏弱，仓位减半"
        )
        return replace(
            result,
            target_pos=self.target_pos,
            mark=mark,
            should_block=True,
        )

    def check_health(self) -> None:
        """启动时健康检查，通过 strategy.write_log 告警。"""
        if not self.strategy.market_sentiment_enabled:
            return
        if get_sentiment_engine is None:
            self.strategy.write_log("未安装 vnpy_marketsentiment，大盘情绪风控不可用")
            return
        engine = self._get_engine()
        if engine is None:
            self.strategy.write_log(
                "市场情绪引擎未加载，大盘情绪风控不可用；"
                "请在启动脚本中于 CTA App 之前 add_app(MarketSentimentApp)"
            )
        elif not engine.started:
            self.strategy.write_log(
                "市场情绪引擎未启动，大盘情绪风控不可用；"
                "请启动情绪引擎或设置 marketsentiment.auto_start=true"
            )

    def reset(self) -> None:
        """重置状态机内部状态。策略 on_init 调用。"""
        self.risk_level = 0
        self.target_pos = -1
