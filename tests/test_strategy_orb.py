"""ORBStrategy / StrategyConfig / 시그널 DTO 공개 계약 단위 테스트.

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. 목킹 불필요.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.data import MinuteBar
from stock_agent.strategy import (
    EntrySignal,
    ExitSignal,
    ORBStrategy,
    StrategyConfig,
)

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_SYMBOL = "005930"
_DATE = date(2026, 4, 20)


def _bar(
    symbol: str,
    h: int,
    m: int,
    open_: int | str | Decimal,
    high: int | str | Decimal,
    low: int | str | Decimal,
    close: int | str | Decimal,
    *,
    date_: date = _DATE,
    volume: int = 0,
) -> MinuteBar:
    """MinuteBar 생성 헬퍼. h/m 은 KST 시·분."""
    return MinuteBar(
        symbol=symbol,
        bar_time=datetime(date_.year, date_.month, date_.day, h, m, tzinfo=KST),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


def _now(h: int, m: int, *, date_: date = _DATE) -> datetime:
    """KST aware datetime 헬퍼."""
    return datetime(date_.year, date_.month, date_.day, h, m, tzinfo=KST)


# ---------------------------------------------------------------------------
# 1. OR 누적 (09:00 ~ 09:29)
# ---------------------------------------------------------------------------


def test_or_구간_30개_bar_후_고저_정확():
    """09:00~09:29 bar 30개 주입 후 or_high/or_low 가 실제 max/min 과 일치."""
    strategy = ORBStrategy()
    highs = list(range(70100, 70130))  # 70100 ~ 70129
    lows = list(range(69900, 69930))

    for m in range(30):
        strategy.on_bar(_bar(_SYMBOL, 9, m, 70000, highs[m], lows[m], 70000))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.or_high == Decimal(str(max(highs)))
    assert state.or_low == Decimal(str(min(lows)))


def test_or_09시_이전_bar_무시():
    """08:59 bar 는 OR 누적에 포함되지 않는다."""
    strategy = ORBStrategy()
    # 08:59 bar — 무시돼야 한다
    strategy.on_bar(_bar(_SYMBOL, 8, 59, 70000, 71000, 69000, 70500))
    # 09:00 bar 하나만 넣기
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70200, 69800, 70100))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    # 08:59 의 high(71000)/low(69000) 가 반영되지 않아야 한다
    assert state.or_high == Decimal("70200")
    assert state.or_low == Decimal("69800")


def test_or_첫_bar_09시05분_지각_케이스():
    """첫 bar 가 09:05 여도 이후 bar 만 누적된다 (09:00~09:04 빠짐)."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 70000, 70300, 69700, 70100))
    strategy.on_bar(_bar(_SYMBOL, 9, 15, 70100, 70400, 69900, 70200))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.or_high == Decimal("70400")
    assert state.or_low == Decimal("69700")


def test_or_bar_없는_상태로_확정_전이_후_진입_없음():
    """OR 구간에 bar 가 하나도 없으면 or_high 가 None → 돌파 bar 가 와도 진입 없음."""
    strategy = ORBStrategy()
    # 09:30 이후 bar 를 OR bar 없이 바로 주입
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70000, 71000, 70000, 71000))
    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.or_high is None


# ---------------------------------------------------------------------------
# 2. 진입 시그널
# ---------------------------------------------------------------------------


def test_진입_OR확정_후_close_초과_시_EntrySignal():
    """OR 확정 + bar.close > or_high 이면 EntrySignal 1건 반환."""
    strategy = ORBStrategy()
    # OR 구간 누적
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    # 09:30 bar — close(71000) > or_high(70500) → 진입
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000))

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, EntrySignal)
    assert sig.symbol == _SYMBOL
    assert sig.price == Decimal("71000")


def test_진입_stop_take_price_정확():
    """EntrySignal 의 stop_price/take_price 가 Decimal 연산 기대값과 일치."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000))
    sig = result[0]
    assert isinstance(sig, EntrySignal)

    entry = Decimal("71000")
    expected_stop = entry * (Decimal("1") - Decimal("0.015"))
    expected_take = entry * (Decimal("1") + Decimal("0.030"))
    assert sig.stop_price == pytest.approx(expected_stop, rel=Decimal("1e-10"))
    assert sig.take_price == pytest.approx(expected_take, rel=Decimal("1e-10"))


def test_진입_close_or_high_동일_터치_진입없음():
    """bar.close == or_high (엄밀 초과 아님) → EntrySignal 없음."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    # close == or_high
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 70800, 70100, 70500))
    assert result == []


def test_진입_OR_구간_bar_진입없음():
    """09:29 bar 는 OR 구간이므로 close > or_high 여도 진입 없음."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    # 09:29 는 아직 OR 구간 — 누적만, 진입 없음
    result = strategy.on_bar(_bar(_SYMBOL, 9, 29, 70200, 71000, 70100, 71000))
    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"


def test_진입_후_상태_long_동일_bar_추가_시그널_없음():
    """진입 bar 에서 EntrySignal 1건만 반환하고 상태가 long 으로 전이된다."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000))
    assert len(result) == 1
    assert isinstance(result[0], EntrySignal)

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "long"


# ---------------------------------------------------------------------------
# 3. 청산 시그널
# ---------------------------------------------------------------------------


def _setup_long(strategy: ORBStrategy, entry_close: int = 71000) -> Decimal:
    """OR 누적 후 진입까지 세팅하고 entry_price 반환."""
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))
    strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, entry_close))
    return Decimal(str(entry_close))


def test_청산_손절_low_leq_stop_price():
    """bar.low <= stop_price → ExitSignal(stop_loss), price=stop_price, closed."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))

    # low 가 stop_price 이하
    result = strategy.on_bar(_bar(_SYMBOL, 9, 31, 70500, 70800, stop - Decimal("1"), 70600))
    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "stop_loss"
    assert sig.price == stop
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_청산_익절_high_geq_take_price():
    """bar.high >= take_price → ExitSignal(take_profit), price=take_price, closed."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    take = entry * (Decimal("1") + Decimal("0.030"))

    # high 가 take_price 이상
    result = strategy.on_bar(_bar(_SYMBOL, 9, 31, 71100, take + Decimal("1"), 70900, 71200))
    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "take_profit"
    assert sig.price == take
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_청산_동일_bar_손절_익절_동시_손절_우선():
    """같은 bar 에서 stop·take 모두 성립 → stop_loss 우선."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))
    take = entry * (Decimal("1") + Decimal("0.030"))

    # low <= stop AND high >= take
    result = strategy.on_bar(
        _bar(_SYMBOL, 9, 31, 71000, take + Decimal("10"), stop - Decimal("1"), 71000)
    )
    assert len(result) == 1
    assert result[0].reason == "stop_loss"  # type: ignore[union-attr]


def test_청산_후_closed_재진입_없음():
    """청산(closed) 상태에서 돌파 bar 를 주입해도 재진입 없음."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))

    # 손절로 closed
    strategy.on_bar(_bar(_SYMBOL, 9, 31, 70500, 70800, stop - Decimal("1"), 70600))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    # 또 다른 돌파 bar
    result = strategy.on_bar(_bar(_SYMBOL, 9, 35, 70600, 72000, 70500, 72000))
    assert result == []
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 4. 강제청산 (on_time)
# ---------------------------------------------------------------------------


def test_강제청산_15시_long_심볼_session_close():
    """on_time(15:00) 호출 시 long 심볼 → ExitSignal(session_close), price=last_close."""
    strategy = ORBStrategy()
    _setup_long(strategy, 71000)
    # 15:00 이전 분봉으로 last_close 갱신
    strategy.on_bar(_bar(_SYMBOL, 14, 55, 71000, 71200, 70900, 71100))

    signals = strategy.on_time(_now(15, 0))
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "session_close"
    assert sig.price == Decimal("71100")  # last_close
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_강제청산_flat_심볼_시그널_없음():
    """flat 상태 심볼은 on_time(15:00) 에서 시그널 없음."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))
    # 돌파 없음 — flat 유지
    strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 70400, 69900, 70300))

    signals = strategy.on_time(_now(15, 0))
    assert signals == []


def test_강제청산_closed_심볼_시그널_없음():
    """closed 상태 심볼은 on_time(15:00) 에서 시그널 없음."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))
    strategy.on_bar(_bar(_SYMBOL, 9, 31, 70500, 70800, stop - Decimal("1"), 70600))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    signals = strategy.on_time(_now(15, 0))
    assert signals == []


def test_강제청산_force_close_at_커스터마이즈():
    """force_close_at=14:30 커스텀 config → 14:30 on_time 에서 강제청산."""
    from datetime import time as dtime

    cfg = StrategyConfig(force_close_at=dtime(14, 30))
    strategy = ORBStrategy(config=cfg)
    _setup_long(strategy, 71000)

    # 14:30 미만 → 빈 리스트
    assert strategy.on_time(_now(14, 29)) == []

    # 14:30 이상 → session_close
    signals = strategy.on_time(_now(14, 30))
    assert len(signals) == 1
    assert signals[0].reason == "session_close"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 5. 세션 전환 (날짜 변경)
# ---------------------------------------------------------------------------


def test_세션_전환_상태_리셋_후_새_OR_누적():
    """날짜 변경 bar 주입 시 이전 상태가 리셋되고 새 OR 누적 시작."""
    strategy = ORBStrategy()
    # 4월 20일 OR 누적
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))
    state_before = strategy.get_state(_SYMBOL)
    assert state_before is not None
    assert state_before.or_high == Decimal("70500")

    # 4월 21일 첫 bar (날짜 변경)
    next_day = date(2026, 4, 21)
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 71000, 71200, 70800, 71000, date_=next_day))

    state_after = strategy.get_state(_SYMBOL)
    assert state_after is not None
    assert state_after.session_date == next_day
    assert state_after.or_high == Decimal("71200")
    assert state_after.or_low == Decimal("70800")
    assert state_after.position_state == "flat"


def test_세션_전환_closed_후_새_세션_재진입_가능():
    """전날 closed 상태여도 새 세션(날짜 변경)에서 재진입 가능."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))
    strategy.on_bar(_bar(_SYMBOL, 9, 31, 70500, 70800, stop - Decimal("1"), 70600))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    # 새 세션 — OR 누적 후 진입
    next_day = date(2026, 4, 21)
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200, date_=next_day))
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000, date_=next_day))

    assert len(result) == 1
    assert isinstance(result[0], EntrySignal)


# ---------------------------------------------------------------------------
# 6. 복수 심볼 독립
# ---------------------------------------------------------------------------


def test_복수_심볼_상태_격리():
    """심볼 A 진입해도 심볼 B 는 flat 유지 — 상태 격리."""
    strategy = ORBStrategy()
    sym_a = "005930"
    sym_b = "000660"

    # 두 심볼 OR 누적
    strategy.on_bar(_bar(sym_a, 9, 0, 70000, 70500, 69800, 70200))
    strategy.on_bar(_bar(sym_b, 9, 0, 80000, 80500, 79800, 80200))

    # A 만 돌파
    strategy.on_bar(_bar(sym_a, 9, 30, 70200, 71500, 70100, 71000))
    # B 는 돌파 없음
    strategy.on_bar(_bar(sym_b, 9, 30, 80200, 80400, 79900, 80300))

    state_a = strategy.get_state(sym_a)
    state_b = strategy.get_state(sym_b)
    assert state_a is not None and state_a.position_state == "long"
    assert state_b is not None and state_b.position_state == "flat"


def test_복수_심볼_on_time_대상_심볼만_청산():
    """on_time(15:00) 시 long 심볼만 청산 — flat 심볼은 시그널 없음."""
    strategy = ORBStrategy()
    sym_a = "005930"
    sym_b = "000660"

    # A 진입
    strategy.on_bar(_bar(sym_a, 9, 0, 70000, 70500, 69800, 70200))
    strategy.on_bar(_bar(sym_a, 9, 30, 70200, 71500, 70100, 71000))
    # B OR 누적만, 돌파 없음
    strategy.on_bar(_bar(sym_b, 9, 0, 80000, 80500, 79800, 80200))
    strategy.on_bar(_bar(sym_b, 9, 30, 80200, 80400, 79900, 80300))

    signals = strategy.on_time(_now(15, 0))
    assert len(signals) == 1
    assert signals[0].symbol == sym_a  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 7. 입력 검증·에러
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    ["00593", "AAPL", "", "12345a", "0059300"],
    ids=["5자리", "영문", "빈문자열", "영문혼용", "7자리"],
)
def test_잘못된_symbol_RuntimeError(symbol: str):
    """유효하지 않은 symbol 로 on_bar 호출 → RuntimeError."""
    strategy = ORBStrategy()
    bar = MinuteBar(
        symbol=symbol,
        bar_time=_now(9, 30),
        open=Decimal("70000"),
        high=Decimal("70500"),
        low=Decimal("69800"),
        close=Decimal("70200"),
        volume=0,
    )
    with pytest.raises(RuntimeError, match="6자리 숫자"):
        strategy.on_bar(bar)


def test_naive_bar_time_RuntimeError():
    """bar.bar_time 이 naive datetime → RuntimeError (tz-aware 요구)."""
    strategy = ORBStrategy()
    bar = MinuteBar(
        symbol=_SYMBOL,
        bar_time=datetime(2026, 4, 20, 9, 30),  # naive
        open=Decimal("70000"),
        high=Decimal("70500"),
        low=Decimal("69800"),
        close=Decimal("70200"),
        volume=0,
    )
    with pytest.raises(RuntimeError, match="tz-aware"):
        strategy.on_bar(bar)


def test_naive_now_on_time_RuntimeError():
    """on_time 에 naive datetime → RuntimeError."""
    strategy = ORBStrategy()
    with pytest.raises(RuntimeError, match="tz-aware"):
        strategy.on_time(datetime(2026, 4, 20, 15, 0))  # naive


def test_bar_time_역행_RuntimeError():
    """같은 심볼에서 시간이 역행하는 bar → RuntimeError."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 70000, 70500, 69800, 70200))
    # 9:05 → 9:04 역행
    with pytest.raises(RuntimeError, match="역행"):
        strategy.on_bar(_bar(_SYMBOL, 9, 4, 70000, 70500, 69800, 70200))


# ---------------------------------------------------------------------------
# 8. StrategyConfig 검증
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"stop_loss_pct": Decimal("0")}, "stop_loss_pct"),
        ({"stop_loss_pct": Decimal("-0.01")}, "stop_loss_pct"),
        ({"take_profit_pct": Decimal("0")}, "take_profit_pct"),
        ({"take_profit_pct": Decimal("-0.05")}, "take_profit_pct"),
    ],
    ids=["stop_loss_zero", "stop_loss_negative", "take_profit_zero", "take_profit_negative"],
)
def test_config_비정상_pct_RuntimeError(kwargs: dict, match: str):
    """stop_loss_pct/take_profit_pct 가 0 이하이면 RuntimeError."""
    with pytest.raises(RuntimeError, match=match):
        StrategyConfig(**kwargs)


def test_config_or_start_geq_or_end_RuntimeError():
    """or_start >= or_end → RuntimeError."""
    from datetime import time as dtime

    with pytest.raises(RuntimeError, match="or_start"):
        StrategyConfig(or_start=dtime(9, 30), or_end=dtime(9, 0))


def test_config_or_end_geq_force_close_at_RuntimeError():
    """or_end >= force_close_at → RuntimeError."""
    from datetime import time as dtime

    with pytest.raises(RuntimeError, match="or_end"):
        StrategyConfig(or_end=dtime(15, 0), force_close_at=dtime(9, 30))


def test_config_기본값_검증():
    """기본 StrategyConfig 는 예외 없이 생성되고 승인된 리스크 한도를 유지한다."""
    cfg = StrategyConfig()
    assert cfg.stop_loss_pct == Decimal("0.015")
    assert cfg.take_profit_pct == Decimal("0.030")
