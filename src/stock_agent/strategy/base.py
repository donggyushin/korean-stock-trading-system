"""전략 인터페이스 + 시그널 DTO.

책임 범위
- 전략 공용 Protocol (`Strategy`) 정의.
- 진입/청산 시그널 DTO (`EntrySignal`/`ExitSignal`) 정의.

설계 메모
- `MinuteBar` 는 `stock_agent.data` 패키지의 공개 심볼을 그대로 소비. data 는
  strategy 를 모른다(역방향 의존 없음).
- `KST` 상수는 `data/realtime.py` 와 값 동일(`timezone(timedelta(hours=9))`).
  공용 clock 모듈 신설은 현 시점 YAGNI — 두 군데 소비가 전부다.
- 시그널 `price` 는 참고가(분봉 close 또는 마지막 관찰 close). executor 레이어가
  실제 체결가로 덮어쓰는 것을 전제.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, Protocol

from stock_agent.data import MinuteBar

KST = timezone(timedelta(hours=9))

ExitReason = Literal["stop_loss", "take_profit", "session_close"]


@dataclass(frozen=True, slots=True)
class EntrySignal:
    """long 진입 시그널.

    Attributes:
        symbol: 6자리 종목 코드.
        price: 시그널 생성 당시 참고가 (분봉 close).
        ts: 시그널 생성 시각 (KST aware).
        stop_price: 진입가 × (1 - stop_loss_pct).
        take_price: 진입가 × (1 + take_profit_pct).
    """

    symbol: str
    price: Decimal
    ts: datetime
    stop_price: Decimal
    take_price: Decimal


@dataclass(frozen=True, slots=True)
class ExitSignal:
    """청산 시그널. `reason` 으로 청산 사유를 식별.

    Attributes:
        symbol: 6자리 종목 코드.
        price: 시그널 생성 당시 참고가.
        ts: 시그널 생성 시각 (KST aware).
        reason: `"stop_loss" | "take_profit" | "session_close"`.
    """

    symbol: str
    price: Decimal
    ts: datetime
    reason: ExitReason


Signal = EntrySignal | ExitSignal


class Strategy(Protocol):
    """전략 공용 Protocol.

    - `on_bar(bar)`: 분봉 이벤트 진입점. 완성된 분봉 또는 진행 중 분봉 스냅샷을
      받아 이번 이벤트에서 생성된 시그널 리스트를 반환한다.
    - `on_time(now)`: 스케줄러가 거는 시각 이벤트 (강제청산 등) 진입점. 현재
      시각을 받아 이번 이벤트에서 생성된 시그널 리스트를 반환한다.

    두 메서드 모두 생성된 시그널이 없으면 빈 리스트를 반환한다. 예외를 삼키지
    않으며, 잘못된 입력(미정상 symbol, naive datetime 등)은 `RuntimeError` 로
    전파된다.
    """

    def on_bar(self, bar: MinuteBar) -> list[Signal]: ...

    def on_time(self, now: datetime) -> list[Signal]: ...
