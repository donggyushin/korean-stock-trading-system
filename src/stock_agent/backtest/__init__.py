"""backtest 패키지 공개 심볼.

상위 레이어(scripts/main) 는 이 패키지의 공개 심볼만 사용한다. 비용·메트릭 모듈
(`costs`, `metrics`) 은 엔진 내부 구현 디테일이므로 직접 노출하지 않지만,
필요 시 `stock_agent.backtest.costs` / `stock_agent.backtest.metrics` 로 직접
접근 가능 (테스트·민감도 분석 후속 PR 용도).
"""

from stock_agent.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestMetrics,
    BacktestResult,
    DailyEquity,
    TradeRecord,
)
from stock_agent.backtest.loader import BarLoader, InMemoryBarLoader

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestMetrics",
    "BacktestResult",
    "BarLoader",
    "DailyEquity",
    "InMemoryBarLoader",
    "TradeRecord",
]
