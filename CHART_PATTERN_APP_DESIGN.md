# vn.py 图形形态分析与相似走势搜索 App 设计

## 1. 文档目标

本文档描述一个独立的 vn.py App：`vnpy_chartpattern`。

该 App 面向股票日线技术分析，提供两个相互独立但共享底层算法和数据的核心功能：

1. **单标的形态分析**：选择一个股票，在图表上展示支撑位、压力位、趋势线、三角形、楔形及其识别依据。
2. **形态相似搜索**：用户手绘一段曲线，或者从真实股票走势中选择一段曲线，在全市场搜索相似走势并按分数排序。

该 App 负责分析、展示和查询，不直接负责自动下单。CTA 策略可以通过稳定接口读取分析结果，将其作为交易信号或风控条件。

## 2. 产品边界

### 2.1 建设目标

- 支持从下拉框选择 `vt_symbol` 并加载日线数据。
- 自动识别波峰、波谷、支撑区域、压力区域和趋势线。
- 自动识别对称三角形、上升三角形、下降三角形、上升楔形和下降楔形。
- 在图表中显示识别线条、覆盖区域、确认时间和形态状态。
- 支持手绘价格轨迹并搜索相似股票。
- 支持将某个股票的一段真实走势直接作为搜索模板。
- 返回 `0～100` 的相似度分数和可解释的分项得分。
- 支持多个 CTA 策略共享已经计算和缓存的结果。
- 分析算法能够脱离 GUI 独立测试，并能在回测中逐根 K 线运行。

### 2.2 暂不包含

- App 内直接下单或管理仓位。
- 使用当前实时数据替代历史数据进行回测。
- 第一版使用深度学习训练形态模型。
- 第一版自动解释所有主观技术分析形态。
- 将未经确认的未来波峰、波谷提前用于历史信号。

## 3. 功能一：单标的形态分析

### 3.1 操作流程

1. 用户选择股票、周期和分析区间。
2. App 从本地数据库或数据服务加载历史 K 线。
3. 数据预处理模块处理复权、缺失值和停牌数据。
4. 形态分析器识别波峰、波谷、支撑压力、趋势线和图形形态。
5. 图表展示识别结果，右侧列表展示形态评分和识别依据。
6. 用户点击某条识别结果时，图表高亮对应的时间区间和边界线。

### 3.2 页面布局

```text
┌──────────────────────────────────────────────────────────────┐
│ 标的：[600000.SSE ▼] 周期：[日线 ▼] 区间：[250日 ▼] [分析]  │
├────────────────────────────────────────┬─────────────────────┤
│                                        │ 识别结果            │
│ K线或收盘价曲线                        │                     │
│   · 波峰/波谷                          │ 对称三角形：82分    │
│   · 支撑/压力区域                      │ 压力区域：12.30～12.40│
│   · 趋势线                             │ 支撑区域：11.45～11.55│
│   · 三角形/楔形覆盖区域                │ 状态：接近上轨      │
│   · 突破与失效标记                     │                     │
│                                        │ [以此区间搜索相似]  │
└────────────────────────────────────────┴─────────────────────┘
```

### 3.3 图表表达规则

- 支撑和压力默认显示为价格区域，而不是绝对细线。
- 趋势线显示起点、终点、斜率和延长线。
- 三角形和楔形显示上下边界以及两线之间的半透明区域。
- 波峰、波谷只在获得右侧 K 线确认后显示。
- 未突破、向上突破、向下突破和已经失效使用不同颜色。
- 用户可以分别关闭支撑压力、趋势线、形态和拐点图层。

### 3.4 结果解释

每个识别结果至少包含：

- 形态类型。
- 开始时间和结束时间。
- 实际确认时间。
- 当前状态。
- 上轨和下轨参数。
- 边界触碰次数。
- 拟合误差。
- 突破确认价格。
- 形态失效价格。
- `0～100` 的置信度。

## 4. 功能二：形态相似搜索

### 4.1 搜索模板来源

支持两种模板：

1. **手绘模板**：用户在画板上从左向右绘制一段价格轨迹。
2. **真实走势模板**：用户在单标的图表中框选一段历史走势。

两种模板进入同一套标准化和匹配流程。

### 4.2 页面布局

```text
┌──────────────────────────────┬───────────────────────────────┐
│ 手绘或真实走势模板           │ 搜索结果                      │
│                              │                               │
│          ╱╲                  │ 1. 600001.SSE  94.6分         │
│     ╱╲  ╱  ╲                 │ 2. 000002.SZ   91.2分         │
│ ___╱  ╲╱    ╲___             │ 3. 300003.SZ   87.8分         │
│                              │                               │
│ [撤销] [清空] [开始搜索]     │ 点击后显示模板与实际走势叠加 │
└──────────────────────────────┴───────────────────────────────┘
```

### 4.3 搜索参数

- 模板长度：20、30、60、120 个交易日或自定义。
- 股票范围：沪深 A 股、自选股或指定板块。
- 搜索位置：仅比较每个股票最近窗口，或者搜索指定历史范围。
- 是否忽略绝对涨跌幅。
- 是否允许有限的时间拉伸。
- 是否加入成交量形态。
- 最低返回分数。
- 最大返回数量。

### 4.4 搜索结果

每条结果包含：

- `vt_symbol` 和股票名称。
- 匹配开始、结束日期。
- 总分。
- DTW 距离分。
- 相关性分。
- 拐点匹配分。
- 趋势斜率分。
- 成交量匹配分（启用时）。
- 模板和实际曲线的叠加预览。

## 5. App 总体架构

```text
本地数据库 / vn.py Datafeed / QMT历史数据
                    │
                    ▼
             MarketDataRepository
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
   PatternAnalyzer      SimilaritySearchEngine
          │                   │
          ├──────┬────────────┤
          ▼      ▼            ▼
       结果缓存  事件发布     持久化
          │                   │
          ├─────────┬─────────┤
          ▼         ▼         ▼
      单标的页面  手绘搜索页  CTA策略接口
```

建议将 App 作为与 `vnpy_ctastrategy` 平级的独立 Python 包，而不是放进某个策略目录。

```text
vnpy_chartpattern/
├── pyproject.toml
├── README.md
├── vnpy_chartpattern/
│   ├── __init__.py
│   ├── base.py
│   ├── engine.py
│   ├── models.py
│   ├── analysis/
│   │   ├── pivots.py
│   │   ├── support_resistance.py
│   │   ├── trendlines.py
│   │   ├── triangle.py
│   │   ├── wedge.py
│   │   └── analyzer.py
│   ├── similarity/
│   │   ├── normalize.py
│   │   ├── resample.py
│   │   ├── dtw.py
│   │   ├── scorer.py
│   │   └── search_engine.py
│   ├── data/
│   │   ├── repository.py
│   │   └── cache.py
│   ├── persistence/
│   │   └── database.py
│   └── ui/
│       ├── widget.py
│       ├── symbol_analysis.py
│       ├── sketch_search.py
│       ├── drawing_canvas.py
│       └── chart_items.py
└── tests/
```

## 6. vn.py App 接入

### 6.1 App 描述

```python
from pathlib import Path

from vnpy.trader.app import BaseApp

from .engine import APP_NAME, ChartPatternEngine


class ChartPatternApp(BaseApp):
    app_name = APP_NAME
    app_module = __module__
    app_path = Path(__file__).parent
    display_name = "形态分析"
    engine_class = ChartPatternEngine
    widget_name = "ChartPatternWidget"
    icon_name = str(app_path / "ui" / "chart_pattern.ico")
```

### 6.2 注册方式

```python
from vnpy_chartpattern import ChartPatternApp

main_engine.add_app(ChartPatternApp)
```

### 6.3 事件

```python
EVENT_PATTERN_ANALYSIS = "ePatternAnalysis"
EVENT_PATTERN_SEARCH_PROGRESS = "ePatternSearchProgress"
EVENT_PATTERN_SEARCH_RESULT = "ePatternSearchResult"
EVENT_PATTERN_ERROR = "ePatternError"
```

耗时的数据加载、全市场扫描和相似度计算必须在后台线程或任务池运行。计算结果通过 `EventEngine` 返回 UI，禁止阻塞 Qt 主线程。

## 7. 核心数据模型

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class LevelType(Enum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


class PatternType(Enum):
    SYMMETRICAL_TRIANGLE = "symmetrical_triangle"
    ASCENDING_TRIANGLE = "ascending_triangle"
    DESCENDING_TRIANGLE = "descending_triangle"
    RISING_WEDGE = "rising_wedge"
    FALLING_WEDGE = "falling_wedge"


@dataclass(frozen=True)
class PriceLevel:
    lower_price: float
    upper_price: float
    level_type: LevelType
    strength: float
    touch_count: int
    first_datetime: datetime
    last_datetime: datetime


@dataclass(frozen=True)
class TrendLine:
    start_datetime: datetime
    end_datetime: datetime
    slope: float
    intercept: float
    fit_error: float
    touch_count: int


@dataclass(frozen=True)
class PatternResult:
    pattern_type: PatternType
    start_datetime: datetime
    end_datetime: datetime
    confirmed_datetime: datetime
    confidence: float
    upper_line: TrendLine
    lower_line: TrendLine
    breakout_price: float | None = None
    invalidation_price: float | None = None
    metadata: dict[str, float | int | str] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class SimilarityResult:
    vt_symbol: str
    start_datetime: datetime
    end_datetime: datetime
    score: float
    dtw_score: float
    correlation_score: float
    pivot_score: float
    slope_score: float
    volume_score: float | None = None
```

## 8. Engine 对外接口

```python
class ChartPatternEngine:
    def analyze_symbol(
        self,
        vt_symbol: str,
        window: int = 250,
    ) -> SymbolAnalysis:
        """分析单标的支撑、压力、趋势线和形态。"""

    def get_latest_analysis(
        self,
        vt_symbol: str,
    ) -> SymbolAnalysis | None:
        """读取最近一次缓存结果。"""

    def search_similar(
        self,
        template: list[float],
        window: int,
        universe: list[str],
        limit: int = 50,
    ) -> list[SimilarityResult]:
        """根据手绘或真实走势搜索相似股票。"""

    def search_by_symbol_range(
        self,
        vt_symbol: str,
        start: datetime,
        end: datetime,
        universe: list[str],
    ) -> list[SimilarityResult]:
        """使用真实历史区间作为搜索模板。"""
```

## 9. 形态分析算法

### 9.1 数据预处理

- 默认使用日线前复权数据进行跨股票比较。
- 保留原始价格用于最终支撑压力价格展示。
- 停牌日不应简单填充为正常交易 K 线。
- 算法输入必须按时间升序并去除重复数据。
- 使用 ATR 或收益率波动率统一不同股票的价格容差。

### 9.2 波峰和波谷

第一版采用确认型局部拐点：

- 某根 K 线高点高于其左右各 `N` 根 K 线，则成为候选波峰。
- 某根 K 线低点低于其左右各 `N` 根 K 线，则成为候选波谷。
- 拐点只能在右侧 `N` 根 K 线到达后确认。
- `confirmed_datetime` 必须是实际可使用该拐点的时间。

后续可以增加 ZigZag、ATR 转向阈值和多尺度拐点。

### 9.3 支撑和压力

1. 提取已经确认的波峰和波谷。
2. 使用 ATR 比例作为价格聚类半径。
3. 将相邻价格点聚合为价格区域。
4. 根据触碰次数、持续时间、成交量和最近程度计算强度。
5. 已经被有效突破的区域可以执行支撑压力角色转换。

### 9.4 趋势线

- 对多个波峰和波谷分别拟合候选直线。
- 采用稳健回归或 RANSAC 降低异常点影响。
- 至少需要两个点，默认要求三个有效触碰点才能获得较高置信度。
- 记录斜率、拟合误差、触碰次数和穿越次数。

### 9.5 三角形

- 对称三角形：上轨下降、下轨上升，两线逐渐收敛。
- 上升三角形：上轨接近水平、下轨上升。
- 下降三角形：上轨下降、下轨接近水平。
- 两条边界必须在合理的未来位置相交。
- 形态内部穿越边界的次数必须低于阈值。
- 突破必须基于收盘价、ATR 缓冲和可选成交量确认。

### 9.6 楔形

- 上升楔形：上下轨都上升，但两线逐渐收敛。
- 下降楔形：上下轨都下降，但两线逐渐收敛。
- 需要限制两条线斜率比例、收敛速度、形态长度和有效触碰次数。

## 10. 相似度搜索算法

### 10.1 手绘数据处理

1. 约束或提示用户从左向右绘制。
2. 将鼠标轨迹转换为时间有序的二维点。
3. 根据横坐标进行插值。
4. 重采样为固定长度，例如 60 个点。
5. 翻转画布纵坐标，使向上代表价格上涨。
6. 对曲线进行平滑，但保留主要拐点。

### 10.2 价格标准化

为了忽略股票绝对价格差异，默认使用 Z-Score：

```python
normalized = (prices - prices.mean()) / prices.std()
```

可以提供其他模式：

- 以起点为基准的累计收益率。
- Min-Max 标准化。
- 去趋势后的局部形态。
- 保留真实涨跌幅。

### 10.3 两阶段搜索

全市场直接对所有窗口执行 DTW 成本较高，建议分为两阶段：

1. **粗筛**：使用重采样向量、相关系数、PAA 或欧氏距离筛选候选集。
2. **精排**：仅对候选集执行 DTW、拐点和趋势结构比较。

默认仅比较每个股票的最新窗口时，约 5000 个窗口可以较快完成。搜索多年历史任意窗口时，需要预计算特征并建立索引。

### 10.4 评分

第一版建议：

```text
总分 =
    DTW曲线相似度 × 50%
  + 方向相关性     × 25%
  + 拐点匹配度     × 15%
  + 趋势斜率匹配度 × 10%
```

启用成交量后，可以从上述权重中划分 `10%～20%` 给成交量相似度。

所有分项统一转换为 `0～100`，并返回分项结果，保证排序可解释。

## 11. 数据获取与缓存

### 11.1 数据来源优先级

1. vn.py 本地数据库。
2. 已配置的 Datafeed。
3. QMT 历史行情接口。

App 不应在每次切换标的时重复下载完整历史数据。建议：

- 内存缓存最近访问标的。
- 本地数据库保存日线。
- 按最后交易日增量更新。
- 全市场搜索前先检查数据完整性。

### 11.2 缓存键

```text
(vt_symbol, interval, adjustment, end_date, window, algorithm_version)
```

算法参数或版本变化后必须使旧分析缓存失效。

### 11.3 搜索索引

可按以下维度预计算：

- 股票代码。
- 窗口结束日期。
- 窗口长度。
- 标准化价格向量。
- PAA 向量。
- 拐点位置和方向。
- 趋势斜率。
- 波动率。

## 12. CTA 策略与回测

实盘策略可以从 `MainEngine` 获取 App 引擎：

```python
pattern_engine = main_engine.get_engine("ChartPattern")
analysis = pattern_engine.get_latest_analysis(vt_symbol)
```

但是 `BacktestingEngine` 不会自动加载 `MainEngine` 中的 App。因此必须保持以下分层：

- `analysis/` 和 `similarity/` 是纯算法，不依赖 Qt、MainEngine 或实时网关。
- App Engine 在实盘负责数据加载、缓存、后台任务和事件发布。
- 回测策略直接使用相同的纯算法，并逐根历史 K 线推进。
- 回测只能使用当前回测时间已经确认的拐点和形态。

禁止在历史回测中读取当前 App 缓存、当前 QMT Tick 或未来完整日线序列生成的最终形态。

## 13. 性能与并发

- UI 线程只负责交互和绘图。
- 单标的分析使用后台任务。
- 全市场搜索使用线程池或进程池，具体根据 NumPy、DTW 实现是否释放 GIL 决定。
- 支持取消正在执行的搜索。
- 搜索期间定期发布进度事件。
- 同一搜索请求应生成唯一任务 ID，旧任务结果不得覆盖新任务页面。
- 结果采用分页或限制返回数量，避免一次绘制数千条记录。

第一版建议性能目标：

- 单标的 250 根日线分析：普通电脑小于 300 毫秒。
- 5000 个股票最新 60 日窗口粗筛：小于 3 秒。
- 对前 100 个候选执行精排：小于 2 秒。

## 14. 测试要求

### 14.1 单元测试

- 波峰波谷确认时间测试。
- 支撑压力聚类测试。
- 趋势线斜率和误差测试。
- 三种三角形识别测试。
- 两种楔形识别测试。
- 无形态数据的误报测试。
- 手绘轨迹重采样测试。
- 标准化零方差处理测试。
- DTW 和综合评分排序测试。
- 停牌、缺失值和重复 K 线测试。

### 14.2 回测安全测试

- 在形态确认前查询必须返回未确认。
- 逐根推进结果不能依赖未来 K 线。
- 实盘批量计算和逐根回放在相同截止时间应得到一致结果。

### 14.3 UI 测试

- 标的切换不会显示旧任务结果。
- 搜索任务可取消。
- 图层开关正确。
- 点击结果能够定位并高亮图表区间。
- 手绘曲线清空、撤销和重画正确。

## 15. 分阶段实施

### 阶段一：App 骨架和数据层

- 创建独立 `vnpy_chartpattern` 包。
- 实现 `ChartPatternApp`、`ChartPatternEngine` 和主窗口。
- 实现标的选择、日线加载、缓存和基础 K 线显示。

### 阶段二：支撑压力

- 实现确认型波峰波谷。
- 实现 ATR 价格聚类。
- 在图表显示支撑压力区域。
- 完成未来函数相关测试。

### 阶段三：趋势线和图形形态

- 实现稳健趋势线拟合。
- 实现三角形和楔形识别。
- 实现置信度、突破和失效状态。

### 阶段四：手绘相似搜索

- 实现画板、轨迹采样和标准化。
- 实现粗筛、DTW 精排和结果列表。
- 实现曲线叠加比较。

### 阶段五：真实走势搜索和 CTA 接口

- 支持框选真实走势作为模板。
- 提供稳定的查询接口和事件。
- 编写 CTA 实盘和回测接入示例。

### 阶段六：性能优化

- 预计算全市场特征。
- 建立历史窗口索引。
- 优化并行搜索和缓存淘汰。

## 16. 第一版默认参数建议

| 参数 | 默认值 |
|---|---:|
| 分析周期 | 日线 |
| 单标的分析长度 | 250 根 |
| 拐点左右确认窗口 | 3 根 |
| 支撑压力聚类容差 | 0.5 ATR |
| 最小支撑压力触碰次数 | 2 |
| 高置信趋势线触碰次数 | 3 |
| 手绘模板重采样长度 | 60 点 |
| 粗筛候选数量 | 100 |
| 最终返回数量 | 30 |
| DTW 权重 | 50% |
| 相关性权重 | 25% |
| 拐点权重 | 15% |
| 斜率权重 | 10% |

这些参数必须可配置，并在积累真实样本后通过回测和人工复核调整。

## 17. 验收标准

第一版可以认为完成，需要满足：

- App 能被 `main_engine.add_app(ChartPatternApp)` 正常加载。
- 用户能选择股票并看到日线图。
- 图表能显示可解释的支撑、压力和至少三种三角形。
- 所有识别结果包含真实确认时间，不使用未来数据。
- 用户能手绘曲线并获得按分数排序的股票结果。
- 用户能查看模板与命中走势的叠加图。
- 全市场搜索不会阻塞 UI。
- 核心算法不依赖 UI，可以被 CTA 回测复用。
- 单元测试、Ruff 和 Mypy 检查通过。
