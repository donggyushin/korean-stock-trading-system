"""심볼별 CSV 디렉토리를 BarLoader Protocol 로 스트리밍하는 과거 분봉 어댑터.

책임 범위
- `{csv_dir}/{symbol}.csv` 에서 과거 분봉을 시간 정렬 스트림으로 공급
- CSV 포맷 계약 검증 (헤더·정렬·중복·분 경계·OHLC 일관성)
- 여러 심볼의 파일을 `heapq.merge` 로 `(bar_time, symbol)` 순서 병합

범위 제외 (의도적)
- SQLite 캐시: 첫 PR 은 순수 스트리밍. 성능 이슈 실측 후 후속 PR.
- KIS 과거 분봉 API: 30일 롤링 제약으로 2~3년 백테스트 부적합. 별도 PR.
- CSV 자동 생성·수집: 운영자가 외부에서 준비.

에러 정책 (`historical.py` / `realtime.py` 와 동일 기조)
- `RuntimeError` 는 전파 (`start > end` 등 호출자 계약 오류).
- 그 외 `Exception` 은 `MinuteCsvLoadError` 로 래핑 + loguru `exception` 로그.
- 생성자는 디렉토리 경로 검증만 수행. 실제 파일 오픈은 `stream` 호출 시 지연.

CSV 포맷 계약
- 헤더: `bar_time,open,high,low,close,volume` (정확한 순서, 누락·오타 시 에러)
- `bar_time`: naive `YYYY-MM-DD HH:MM:SS` 또는 `YYYY-MM-DD HH:MM` — KST 부여
- 오프셋 포함 (`+09:00` 등) → 에러 (naive 계약 명시적 강제)
- 가격: `Decimal(str)` 파싱 (float 우회 금지)
- 파일 내부 `bar_time` 단조증가 + `(symbol, bar_time)` 중복 금지
- 분 경계 (`second==0, microsecond==0`) 필수
- OHLC: 모두 양수 + `low <= min(open,close) <= max(open,close) <= high`
- 빈 파일(헤더만): 에러 아님 — 해당 심볼 빈 스트림
"""

from __future__ import annotations

import csv
import heapq
import re
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

from loguru import logger

from stock_agent.data.realtime import MinuteBar

KST = timezone(timedelta(hours=9))
_SYMBOL_RE = re.compile(r"^\d{6}$")
_EXPECTED_HEADER: tuple[str, ...] = (
    "bar_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
_BAR_TIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
)


class MinuteCsvLoadError(Exception):
    """CSV 분봉 로드 실패를 공통 표현.

    원본 예외는 `__cause__` 로 보존된다 (`raise ... from e`).
    """


class MinuteCsvBarLoader:
    """`{csv_dir}/{symbol}.csv` 레이아웃의 과거 분봉 어댑터.

    `BarLoader` Protocol (structural) 을 만족한다:
    `stream(start, end, symbols) -> Iterator[MinuteBar]`.

    스트리밍 보장:
    - `start <= bar.bar_time.date() <= end` (경계 포함)
    - `bar.symbol in symbols`
    - 시간 단조증가 (동일 시각 허용, `(bar_time, symbol)` 으로 tie-break)
    - `(symbol, bar_time)` 중복 없음 (파일 내 중복은 에러)

    Raises:
        MinuteCsvLoadError: 생성자에서 `csv_dir` 가 존재하지 않거나 디렉토리가 아닐 때.
    """

    def __init__(self, csv_dir: Path) -> None:
        if not isinstance(csv_dir, Path):
            raise MinuteCsvLoadError(
                f"csv_dir 는 pathlib.Path 이어야 합니다: {type(csv_dir).__name__}"
            )
        if not csv_dir.exists():
            raise MinuteCsvLoadError(f"csv_dir 가 존재하지 않습니다: {csv_dir}")
        if not csv_dir.is_dir():
            raise MinuteCsvLoadError(f"csv_dir 가 디렉토리가 아닙니다: {csv_dir}")
        self._csv_dir: Path = csv_dir

    @property
    def csv_dir(self) -> Path:
        """루트 디렉토리 스냅샷. 테스트·디버깅용."""
        return self._csv_dir

    def stream(
        self,
        start: date,
        end: date,
        symbols: tuple[str, ...],
    ) -> Iterator[MinuteBar]:
        if start > end:
            raise RuntimeError(f"start({start}) 는 end({end}) 이전이어야 합니다.")
        for symbol in symbols:
            if not _SYMBOL_RE.match(symbol):
                raise MinuteCsvLoadError(f"symbol 은 6자리 숫자여야 합니다: {symbol!r}")
        if not symbols:
            return iter(())

        per_symbol_iters = [
            _sorted_bar_iter(self._csv_dir, symbol, start, end) for symbol in symbols
        ]
        return heapq.merge(*per_symbol_iters, key=lambda b: (b.bar_time, b.symbol))


def _sorted_bar_iter(
    csv_dir: Path,
    symbol: str,
    start: date,
    end: date,
) -> Iterator[MinuteBar]:
    """단일 심볼 CSV 를 지연 오픈해 MinuteBar 이터레이터로 변환.

    파일 내부 단조증가·중복 금지 계약은 이 함수에서 강제한다.
    """
    file_path = csv_dir / f"{symbol}.csv"
    if not file_path.exists():
        raise MinuteCsvLoadError(f"심볼 {symbol} 의 CSV 가 없습니다: {file_path}")
    if not file_path.is_file():
        raise MinuteCsvLoadError(f"CSV 경로가 파일이 아닙니다: {file_path}")
    return _iter_symbol_file(file_path, symbol, start, end)


def _iter_symbol_file(
    file_path: Path,
    symbol: str,
    start: date,
    end: date,
) -> Iterator[MinuteBar]:
    handle: TextIO
    try:
        handle = file_path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        logger.exception("CSV 파일 오픈 실패: {}", file_path)
        raise MinuteCsvLoadError(f"CSV 파일 오픈 실패: {file_path}") from exc

    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise MinuteCsvLoadError(f"빈 CSV (헤더 없음): {file_path}") from None
        if tuple(header) != _EXPECTED_HEADER:
            raise MinuteCsvLoadError(
                f"헤더가 {_EXPECTED_HEADER} 와 일치하지 않습니다: {header} ({file_path})"
            )

        last_bar_time: datetime | None = None
        for line_no, row in enumerate(reader, start=2):
            if len(row) != len(_EXPECTED_HEADER):
                raise MinuteCsvLoadError(
                    f"컬럼 수 불일치 (기대 {len(_EXPECTED_HEADER)}, 실제 {len(row)}): "
                    f"{file_path}:{line_no}"
                )
            bar = _parse_row(symbol, row, file_path, line_no)

            if last_bar_time is not None:
                if bar.bar_time < last_bar_time:
                    raise MinuteCsvLoadError(
                        f"bar_time 역행: 이전 {last_bar_time.isoformat()} → "
                        f"현재 {bar.bar_time.isoformat()} ({file_path}:{line_no})"
                    )
                if bar.bar_time == last_bar_time:
                    raise MinuteCsvLoadError(
                        f"bar_time 중복: {bar.bar_time.isoformat()} ({file_path}:{line_no})"
                    )
            last_bar_time = bar.bar_time

            bar_date = bar.bar_time.date()
            if bar_date < start:
                continue
            if bar_date > end:
                return
            yield bar


def _parse_row(
    symbol: str,
    row: list[str],
    file_path: Path,
    line_no: int,
) -> MinuteBar:
    raw_time, raw_open, raw_high, raw_low, raw_close, raw_volume = row

    bar_time = _parse_bar_time(raw_time, file_path, line_no)
    open_ = _parse_price(raw_open, "open", file_path, line_no)
    high = _parse_price(raw_high, "high", file_path, line_no)
    low = _parse_price(raw_low, "low", file_path, line_no)
    close = _parse_price(raw_close, "close", file_path, line_no)
    volume = _parse_volume(raw_volume, file_path, line_no)

    _validate_ohlc(open_, high, low, close, file_path, line_no)

    return MinuteBar(
        symbol=symbol,
        bar_time=bar_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _parse_bar_time(raw: str, file_path: Path, line_no: int) -> datetime:
    text = raw.strip()
    if not text:
        raise MinuteCsvLoadError(f"bar_time 빈 값 ({file_path}:{line_no})")
    if any(marker in text for marker in ("+", "Z")) or _has_timezone_offset(text):
        raise MinuteCsvLoadError(
            f"bar_time 에 타임존 오프셋 포함 — naive 포맷만 허용: {raw!r} ({file_path}:{line_no})"
        )

    parsed: datetime | None = None
    for fmt in _BAR_TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise MinuteCsvLoadError(
            f"bar_time 파싱 실패 (기대 'YYYY-MM-DD HH:MM[:SS]'): {raw!r} ({file_path}:{line_no})"
        )
    if parsed.second != 0 or parsed.microsecond != 0:
        raise MinuteCsvLoadError(f"bar_time 이 분 경계가 아닙니다: {raw!r} ({file_path}:{line_no})")
    return parsed.replace(tzinfo=KST)


def _has_timezone_offset(text: str) -> bool:
    """`1999-12-31 23:59-05:00` 같은 음수 오프셋 탐지 (`+` / `Z` 로 잡히지 않는 경우)."""
    if len(text) < 6:
        return False
    tail = text[-6:]
    return tail[0] == "-" and tail[3] == ":" and tail[1:3].isdigit() and tail[4:6].isdigit()


def _parse_price(raw: str, field: str, file_path: Path, line_no: int) -> Decimal:
    text = raw.strip()
    if not text:
        raise MinuteCsvLoadError(f"{field} 빈 값 ({file_path}:{line_no})")
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise MinuteCsvLoadError(f"{field} 파싱 실패: {raw!r} ({file_path}:{line_no})") from exc
    if not value.is_finite():
        raise MinuteCsvLoadError(f"{field} 가 유한값이 아닙니다: {raw!r} ({file_path}:{line_no})")
    if value <= 0:
        raise MinuteCsvLoadError(f"{field} 는 양수여야 합니다: {raw!r} ({file_path}:{line_no})")
    return value


def _parse_volume(raw: str, file_path: Path, line_no: int) -> int:
    text = raw.strip()
    if not text:
        raise MinuteCsvLoadError(f"volume 빈 값 ({file_path}:{line_no})")
    try:
        decimal_value = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise MinuteCsvLoadError(f"volume 파싱 실패: {raw!r} ({file_path}:{line_no})") from exc
    if not decimal_value.is_finite():
        raise MinuteCsvLoadError(f"volume 이 유한값이 아닙니다: {raw!r} ({file_path}:{line_no})")
    if decimal_value < 0:
        raise MinuteCsvLoadError(f"volume 은 0 이상이어야 합니다: {raw!r} ({file_path}:{line_no})")
    if decimal_value != decimal_value.to_integral_value():
        raise MinuteCsvLoadError(f"volume 은 정수여야 합니다: {raw!r} ({file_path}:{line_no})")
    return int(decimal_value)


def _validate_ohlc(
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    file_path: Path,
    line_no: int,
) -> None:
    if low > high:
        raise MinuteCsvLoadError(f"OHLC 불일치: low({low}) > high({high}) ({file_path}:{line_no})")
    body_low = min(open_, close)
    body_high = max(open_, close)
    if body_low < low:
        raise MinuteCsvLoadError(
            f"OHLC 불일치: min(open,close)={body_low} < low={low} ({file_path}:{line_no})"
        )
    if body_high > high:
        raise MinuteCsvLoadError(
            f"OHLC 불일치: max(open,close)={body_high} > high={high} ({file_path}:{line_no})"
        )
