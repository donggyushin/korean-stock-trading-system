"""백테스트 리포트 메트릭 — 순수 계산 함수.

책임 범위
- 총수익률, 최대낙폭(MDD), 샤프 비율, 승률, 평균 손익비, 일평균 거래수.
- 모든 함수는 **입력 원시 숫자** (equity 시리즈 / 일일 수익률 / net_pnl 리스트
  / 정수 카운트) 만 받는다. `TradeRecord` 같은 상위 DTO 를 import 하지 않아
  순환 의존을 원천 차단한다.

설계 원칙
- 순수 함수, 외부 I/O 없음, 상태 없음.
- 빈 입력·단일 샘플은 `Decimal("0")` 으로 안전 처리 (분모 0 방어).
- 반환 타입은 모두 `Decimal` 로 통일해 리포트 단계에서 포맷 자유도를 확보.
- 비율 값은 **소수점** (0.15 = 15%). 표현 단위는 렌더러 책임.

계산 정의
- `total_return_pct(start, end) = (end - start) / start`.
- `max_drawdown_pct(equity)` — 러닝 피크 대비 최대 낙폭. 음수(Decimal) 로 반환.
- `sharpe_ratio(daily_returns, N=252)` = `mean(r) / stdev(r) * sqrt(N)`. stdev 는
  모집단 표준편차(`pstdev`) — 일관된 스케일. 표본이 1개 이하거나 stdev=0 이면 0.
- `win_rate(net_pnls)` = `count(pnl > 0) / count(pnl != 0)`. break-even(0)은
  승패 모집단에서 제외.
- `avg_pnl_ratio(net_pnls)` = `mean(winners) / |mean(losers)|`. 승자·패자 중
  한쪽이 없으면 0.
- `trades_per_day(trade_count, sessions)` = `trade_count / sessions`. sessions=0
  이면 0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal
from statistics import mean, pstdev

_ZERO = Decimal("0")


def total_return_pct(starting_equity_krw: int, ending_equity_krw: int) -> Decimal:
    """시작 자본 대비 종료 자본의 수익률 (소수).

    `starting_equity_krw <= 0` 이면 0 (분모 방어 — 정상 경로에서 도달 불가).
    """
    if starting_equity_krw <= 0:
        return _ZERO
    return Decimal(ending_equity_krw - starting_equity_krw) / Decimal(starting_equity_krw)


def max_drawdown_pct(equity_series: Sequence[int]) -> Decimal:
    """러닝 피크 대비 최대 낙폭 (음수 또는 0).

    예: `[100, 120, 90, 130, 80]` → peak 최종 130, drawdown=(80-130)/130≈-0.3846.

    빈 시리즈·단일 포인트 → 0. 모든 값이 단조증가면 0.
    """
    if not equity_series:
        return _ZERO
    peak = equity_series[0]
    max_dd = _ZERO
    for value in equity_series:
        if value > peak:
            peak = value
        if peak <= 0:
            continue
        dd = Decimal(value - peak) / Decimal(peak)
        if dd < max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(
    daily_returns: Sequence[Decimal],
    periods_per_year: int = 252,
) -> Decimal:
    """연환산 샤프 비율. 무위험 수익률 0 가정.

    표본 크기 ≤ 1 또는 표준편차 0 이면 0 반환.
    """
    if len(daily_returns) < 2:
        return _ZERO
    returns_float = [float(r) for r in daily_returns]
    sigma = pstdev(returns_float)
    if sigma == 0:
        return _ZERO
    mu = mean(returns_float)
    sharpe = (mu / sigma) * math.sqrt(periods_per_year)
    return Decimal(str(sharpe))


def win_rate(net_pnls_krw: Sequence[int]) -> Decimal:
    """승률. break-even(0) 은 모집단에서 제외. 유효 모집단이 없으면 0."""
    winners = sum(1 for pnl in net_pnls_krw if pnl > 0)
    losers = sum(1 for pnl in net_pnls_krw if pnl < 0)
    total = winners + losers
    if total == 0:
        return _ZERO
    return Decimal(winners) / Decimal(total)


def avg_pnl_ratio(net_pnls_krw: Sequence[int]) -> Decimal:
    """평균 승자 PnL / |평균 패자 PnL|. 어느 한쪽이 없으면 0."""
    winners = [pnl for pnl in net_pnls_krw if pnl > 0]
    losers = [pnl for pnl in net_pnls_krw if pnl < 0]
    if not winners or not losers:
        return _ZERO
    avg_win = Decimal(sum(winners)) / Decimal(len(winners))
    avg_loss = Decimal(sum(losers)) / Decimal(len(losers))
    return avg_win / abs(avg_loss)


def trades_per_day(trade_count: int, sessions: int) -> Decimal:
    """일평균 거래 수. sessions=0 이면 0 반환 (분모 방어)."""
    if sessions <= 0:
        return _ZERO
    return Decimal(trade_count) / Decimal(sessions)
