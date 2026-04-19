"""Opening Range Breakout 전략 구현.

책임 범위
- OR 구간(09:00~09:30 KST) 분봉에서 고가(OR-High)·저가(OR-Low) 확정.
- OR 확정 이후 분봉 close 가 OR-High 상향 돌파 시 long 진입 시그널 생성.
- 진입 이후 손절(-1.5%) · 익절(+3.0%) · 강제청산(15:00) 중 먼저 성립하는 쪽으로
  청산 시그널 생성. 동일 분봉에서 손절·익절이 함께 성립하면 **손절 우선**
  (보수적 — 슬리피지 과소평가 방지).
- per-symbol 상태 머신. 세션 경계(`bar.bar_time.date()`) 는 자동 전환.
- 1일 1심볼 최대 1회 진입 — 청산(`closed`) 이후 당일 재진입 금지.

범위 제외 (의도적)
- 포지션 사이징·자금 관리 — `risk/manager.py` 책임.
- 주문 실행·체결 추적 — `execution/executor.py` (Phase 3).
- 거래대금/유동성 필터 — `MinuteBar.volume=0 고정` 제약으로 본 모듈 범위 밖.
- 틱 기반 진입(`on_tick`) — 필요 시 Phase 3 에서 `Strategy` Protocol 확장.

에러 정책 (broker/data 와 동일 기조)
- `RuntimeError` 는 전파 — 잘못된 symbol, naive datetime, 시간 역행, 설정 위반.
- 그 외 `Exception` 은 `StrategyError` 로 래핑 + loguru `exception` 로그.
  원본 예외는 `__cause__` 로 보존.

스레드 모델
- 단일 프로세스 전용. `on_bar`/`on_time` 은 동일 호출자 스레드에서 순차 호출을
  가정. 동시 호출이 필요해지면 Phase 5 재설계 범위.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from loguru import logger

from stock_agent.data import MinuteBar
from stock_agent.strategy.base import EntrySignal, ExitSignal, Signal

_SYMBOL_RE = re.compile(r"^\d{6}$")

_DEFAULT_OR_START = time(9, 0)
_DEFAULT_OR_END = time(9, 30)
_DEFAULT_FORCE_CLOSE_AT = time(15, 0)
_DEFAULT_STOP_LOSS_PCT = Decimal("0.015")
_DEFAULT_TAKE_PROFIT_PCT = Decimal("0.030")

PositionState = Literal["flat", "long", "closed"]


class StrategyError(Exception):
    """ORB 상태 머신 처리 중 발생한 예기치 못한 오류.

    사용자 수정이 필요한 입력 오류(`RuntimeError`) 와 구분. 원본 예외는
    `__cause__` 로 보존된다 (`raise ... from e`).
    """


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """ORB 파라미터. 모든 값은 생성자 인자로 재정의 가능.

    `or_start`/`or_end`/`force_close_at` 은 naive `datetime.time` 이다
    (KST 기준 암묵적 해석). `MinuteBar.bar_time` 은 KST aware datetime 이지만
    `.time()` 은 tzinfo 미포함 naive time 을 반환하므로 naive 끼리 비교가
    안전·단순하다.

    Raises:
        RuntimeError: `stop_loss_pct ≤ 0`, `take_profit_pct ≤ 0`,
            `or_start ≥ or_end`, `or_end ≥ force_close_at` 일 때.
    """

    or_start: time = _DEFAULT_OR_START
    or_end: time = _DEFAULT_OR_END
    force_close_at: time = _DEFAULT_FORCE_CLOSE_AT
    stop_loss_pct: Decimal = _DEFAULT_STOP_LOSS_PCT
    take_profit_pct: Decimal = _DEFAULT_TAKE_PROFIT_PCT

    def __post_init__(self) -> None:
        if self.stop_loss_pct <= 0:
            raise RuntimeError(f"stop_loss_pct 는 양수여야 합니다 (got={self.stop_loss_pct})")
        if self.take_profit_pct <= 0:
            raise RuntimeError(f"take_profit_pct 는 양수여야 합니다 (got={self.take_profit_pct})")
        if self.or_start >= self.or_end:
            raise RuntimeError(
                f"or_start({self.or_start}) 는 or_end({self.or_end}) 보다 이전이어야 합니다."
            )
        if self.or_end >= self.force_close_at:
            raise RuntimeError(
                f"or_end({self.or_end}) 는 force_close_at({self.force_close_at}) "
                "보다 이전이어야 합니다."
            )


@dataclass
class _SymbolState:
    """심볼별 상태. 세션 단위로 `reset()` 된다."""

    session_date: date | None = None
    or_high: Decimal | None = None
    or_low: Decimal | None = None
    or_confirmed: bool = False
    position_state: PositionState = "flat"
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_price: Decimal | None = None
    last_bar_time: datetime | None = None
    last_close: Decimal | None = None

    def reset(self, session_date: date) -> None:
        self.session_date = session_date
        self.or_high = None
        self.or_low = None
        self.or_confirmed = False
        self.position_state = "flat"
        self.entry_price = None
        self.stop_price = None
        self.take_price = None
        self.last_bar_time = None
        self.last_close = None


class ORBStrategy:
    """Opening Range Breakout 규칙 엔진. `Strategy` Protocol 구현체.

    공개 API: `on_bar`, `on_time`, `config` (프로퍼티), `get_state` (디버깅용).

    동일 호출자 스레드에서 `on_bar` → `on_bar` → `on_time` 형태로 순차 호출하는
    것을 가정한다. 동시 호출은 지원하지 않는다.
    """

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self._config = config or StrategyConfig()
        self._states: dict[str, _SymbolState] = {}

    @property
    def config(self) -> StrategyConfig:
        return self._config

    def get_state(self, symbol: str) -> _SymbolState | None:
        """테스트·디버깅용 상태 스냅샷. 반환 객체는 내부 상태와 공유되므로
        호출자는 수정하지 않는다."""
        return self._states.get(symbol)

    # ---- on_bar --------------------------------------------------------

    def on_bar(self, bar: MinuteBar) -> list[Signal]:
        """분봉 이벤트를 소비하고 발생한 시그널 리스트를 반환."""
        try:
            self._validate_symbol(bar.symbol)
            self._require_aware(bar.bar_time, "bar.bar_time")

            state = self._states.setdefault(bar.symbol, _SymbolState())
            session = bar.bar_time.date()

            if state.session_date is None or state.session_date != session:
                state.reset(session)

            if state.last_bar_time is not None and bar.bar_time < state.last_bar_time:
                raise RuntimeError(
                    f"bar.bar_time 역행 감지 ({bar.symbol}): "
                    f"last={state.last_bar_time.isoformat()}, "
                    f"now={bar.bar_time.isoformat()}"
                )
            state.last_bar_time = bar.bar_time
            state.last_close = bar.close

            return self._dispatch_bar(state, bar)
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001 — 예기치 못한 예외는 StrategyError 로 래핑
            logger.exception(f"ORB on_bar 실패 ({bar.symbol}): {e.__class__.__name__}: {e}")
            raise StrategyError(f"ORB on_bar 실패 ({bar.symbol}): {e}") from e

    def _dispatch_bar(self, state: _SymbolState, bar: MinuteBar) -> list[Signal]:
        cfg = self._config
        bar_t = bar.bar_time.time()

        if bar_t < cfg.or_start:
            # 장 시작 전 데이터 — 무시 (로그 생략, 정상 케이스).
            return []

        if bar_t < cfg.or_end:
            self._accumulate_or(state, bar)
            return []

        # OR 확정 이후.
        if not state.or_confirmed:
            state.or_confirmed = True

        if state.position_state == "flat":
            if bar_t >= cfg.force_close_at:
                # 장 마감 30분 이내에는 신규 진입 금지.
                return []
            if state.or_high is None:
                # OR 구간에 bar 가 단 하나도 없었던 극단 케이스 — 당일 포기.
                return []
            if bar.close > state.or_high:
                return [self._enter_long(state, bar)]
            return []

        if state.position_state == "long":
            exit_signal = self._check_exit(state, bar)
            return [exit_signal] if exit_signal is not None else []

        # "closed" — 당일 재진입 금지.
        return []

    def _accumulate_or(self, state: _SymbolState, bar: MinuteBar) -> None:
        state.or_high = bar.high if state.or_high is None else max(state.or_high, bar.high)
        state.or_low = bar.low if state.or_low is None else min(state.or_low, bar.low)

    def _enter_long(self, state: _SymbolState, bar: MinuteBar) -> EntrySignal:
        cfg = self._config
        entry = bar.close
        stop = entry * (Decimal("1") - cfg.stop_loss_pct)
        take = entry * (Decimal("1") + cfg.take_profit_pct)

        state.position_state = "long"
        state.entry_price = entry
        state.stop_price = stop
        state.take_price = take

        logger.info(
            f"ORB 진입: {bar.symbol} @ {entry} "
            f"(or_high={state.or_high}, stop={stop}, take={take}, "
            f"ts={bar.bar_time.isoformat()})"
        )
        return EntrySignal(
            symbol=bar.symbol,
            price=entry,
            ts=bar.bar_time,
            stop_price=stop,
            take_price=take,
        )

    def _check_exit(self, state: _SymbolState, bar: MinuteBar) -> ExitSignal | None:
        # long 상태에서는 stop/take 가 모두 세팅되어 있다.
        assert state.stop_price is not None
        assert state.take_price is not None

        if bar.low <= state.stop_price:
            state.position_state = "closed"
            logger.info(
                f"ORB 손절: {bar.symbol} @ {state.stop_price} "
                f"(low={bar.low}, stop={state.stop_price}, "
                f"ts={bar.bar_time.isoformat()})"
            )
            return ExitSignal(
                symbol=bar.symbol,
                price=state.stop_price,
                ts=bar.bar_time,
                reason="stop_loss",
            )

        if bar.high >= state.take_price:
            state.position_state = "closed"
            logger.info(
                f"ORB 익절: {bar.symbol} @ {state.take_price} "
                f"(high={bar.high}, take={state.take_price}, "
                f"ts={bar.bar_time.isoformat()})"
            )
            return ExitSignal(
                symbol=bar.symbol,
                price=state.take_price,
                ts=bar.bar_time,
                reason="take_profit",
            )

        return None

    # ---- on_time -------------------------------------------------------

    def on_time(self, now: datetime) -> list[Signal]:
        """시각 이벤트 진입점. 현재는 `force_close_at` 이후 강제청산만 발생.

        `long` 상태인 모든 심볼에 대해 `ExitSignal(reason="session_close")` 를
        생성하고 상태를 `closed` 로 전이한다. 가격은 마지막 관찰 분봉 close
        (없으면 entry_price) — executor 가 실제 체결가로 덮어쓰는 것을 전제.
        """
        try:
            self._require_aware(now, "now")
            cfg = self._config
            if now.time() < cfg.force_close_at:
                return []

            signals: list[Signal] = []
            for symbol, state in self._states.items():
                if state.position_state != "long":
                    continue
                price = state.last_close if state.last_close is not None else state.entry_price
                # long 상태면 _enter_long 에서 entry_price 가 세팅되었으므로 price 는 항상 존재.
                assert price is not None
                state.position_state = "closed"
                logger.info(f"ORB 강제청산: {symbol} @ {price} (ts={now.isoformat()})")
                signals.append(
                    ExitSignal(
                        symbol=symbol,
                        price=price,
                        ts=now,
                        reason="session_close",
                    )
                )
            return signals
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception(f"ORB on_time 실패: {e.__class__.__name__}: {e}")
            raise StrategyError(f"ORB on_time 실패: {e}") from e

    # ---- 공통 가드 -----------------------------------------------------

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if not symbol or not _SYMBOL_RE.match(symbol):
            raise RuntimeError(f"symbol 은 6자리 숫자 문자열이어야 합니다 (got={symbol!r})")

    @staticmethod
    def _require_aware(ts: datetime, name: str) -> None:
        if ts.tzinfo is None:
            raise RuntimeError(
                f"{name} 은 tz-aware datetime 이어야 합니다 (got naive {ts.isoformat()})"
            )
