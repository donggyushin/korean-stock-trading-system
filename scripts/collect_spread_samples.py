"""호가 스프레드 샘플 수집 CLI — Step B (Issue #75, ADR-0019).

ADR-0019 Phase 2 복구 로드맵 Step B 인프라. 평일 장중 KOSPI 200 호가창을
주기적 스냅샷해 JSONL 로 적재한다. 후속 단계에서 `data/spread_analysis.md`
산출 → ADR-0006 슬리피지 0.1% 가정 재검정.

사용 예시:

```
uv run python scripts/collect_spread_samples.py \
  --interval-s 30 --duration-h 6.5 \
  --output-dir data/spread_samples
```

JSONL 라인 포맷:

```json
{"symbol":"005930","ts":"2026-04-27T10:30:00+09:00","bid1":"71500","ask1":"71600","bid_qty1":1234,"ask_qty1":890,"spread_pct":"0.139860"}
```

`Decimal` 은 `str` 직렬화 (정밀도 보존), `ts` 는 isoformat (KST aware).

exit code (`scripts/backfill_minute_bars.py` 와 정합)
- 0: 정상 완료 (failed=0).
- 1: 부분 실패 (`failed > 0`, `SpreadSampleCollectorError` 누적).
- 2: 입력·설정 오류 (`interval_s<1.0`, `duration_h<=0`, no live keys 등) — 재시도 무의미.
- 3: I/O 오류 (디스크 권한 등) — 재시도 가치 있음.

제약
- 실전 KIS API 호출. IP 화이트리스트 등록 선행 필수.
- 단일 프로세스 전용 — `RealtimeDataStore`/`KisMinuteBarLoader` 와 동시 기동 가능
  (서로 다른 PyKis 인스턴스 + 별도 read-only 가드).
- 본 스크립트는 수집 전용 — 분석 (`data/spread_analysis.md`) 은 후속 PR.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from stock_agent.config import get_settings
from stock_agent.data import load_kospi200_universe
from stock_agent.data.spread_samples import (
    SpreadSample,
    SpreadSampleCollector,
    SpreadSampleCollectorError,
)

_EXIT_OK = 0
_EXIT_PARTIAL_FAILURE = 1
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

KST = timezone(timedelta(hours=9))
_DEFAULT_OUTPUT_DIR = Path("data/spread_samples")
_MARKET_OPEN_MIN = 9 * 60 + 0
_MARKET_CLOSE_MIN = 15 * 60 + 30


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KIS 호가 스프레드 샘플 수집 (Step B, ADR-0019)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="쉼표 구분 종목 코드 (미지정 시 config/universe.yaml 전체).",
    )
    parser.add_argument(
        "--interval-s",
        type=float,
        default=30.0,
        help="sweep 사이 sleep 초. >= 1.0 (그 미만은 KIS rate limit 위험).",
    )
    parser.add_argument(
        "--duration-h",
        type=float,
        default=6.5,
        help="총 수집 시간 (시간). 양수.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="JSONL 출력 디렉토리. 자동 생성.",
    )
    parser.add_argument(
        "--http-timeout-s",
        type=float,
        default=10.0,
        help="KIS HTTP timeout (초).",
    )
    parser.add_argument(
        "--no-skip-outside-market",
        dest="skip_outside_market",
        action="store_false",
        help="장외 시간대(09:00 이전 / 15:30 이후)도 수집 (기본: skip).",
    )
    parser.set_defaults(skip_outside_market=True)
    return parser.parse_args(argv)


def _resolve_symbols(raw: str) -> tuple[str, ...]:
    """`--symbols` 인자 해석 — 빈 값이면 universe.yaml 전체.

    `scripts/backfill_minute_bars.py` 와 동일 계약.
    """
    if raw.strip():
        return tuple(s.strip() for s in raw.split(",") if s.strip())
    universe = load_kospi200_universe()
    if not universe.tickers:
        raise RuntimeError(
            "config/universe.yaml 이 비어있습니다 — --symbols 로 명시하거나 "
            "유니버스 YAML 을 갱신하세요."
        )
    return universe.tickers


def _jsonl_path(output_dir: Path, session_date: date) -> Path:
    """`{output_dir}/{YYYY-MM-DD}.jsonl` — 세션 날짜 단위 파일. 디렉토리 자동 생성."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{session_date.isoformat()}.jsonl"


def _within_market_hours(now: datetime) -> bool:
    """KST aware now 가 09:00~15:30 (포함) 사이인가."""
    cur = now.hour * 60 + now.minute
    return _MARKET_OPEN_MIN <= cur <= _MARKET_CLOSE_MIN


def _serialize_sample(sample: SpreadSample) -> str:
    """`SpreadSample` → JSONL 한 줄. `Decimal` 은 str, ts 는 isoformat."""
    return json.dumps(
        {
            "symbol": sample.symbol,
            "ts": sample.ts.isoformat(),
            "bid1": str(sample.bid1),
            "ask1": str(sample.ask1),
            "bid_qty1": int(sample.bid_qty1),
            "ask_qty1": int(sample.ask_qty1),
            "spread_pct": str(sample.spread_pct),
        },
        ensure_ascii=False,
    )


def _append_jsonl(output_dir: Path, sample: SpreadSample) -> None:
    """샘플 한 줄을 세션 날짜 JSONL 에 append. 호출자가 OSError 처리."""
    path = _jsonl_path(output_dir, sample.ts.date())
    line = _serialize_sample(sample)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _run_pipeline(
    args: argparse.Namespace,
    *,
    collector: SpreadSampleCollector,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> tuple[int, int]:
    """심볼별 sweep 루프. `(samples_written, failed)` 반환.

    `SpreadSampleCollectorError` 는 심볼 단위로 격리 — 한 심볼 실패가 다음 심볼·
    다음 sweep 을 죽이지 않는다. `OSError` (디스크 권한 등) 는 전파 → `main` 에서
    exit 3 으로 매핑.
    """
    symbols = _resolve_symbols(args.symbols)
    output_dir = Path(args.output_dir)
    written = 0
    failed = 0

    now = clock()
    end = now + timedelta(hours=args.duration_h)
    logger.info(
        "spread.start symbols={n} interval_s={i} duration_h={d} skip_outside={s}",
        n=len(symbols),
        i=args.interval_s,
        d=args.duration_h,
        s=args.skip_outside_market,
    )

    try:
        while now < end:
            if not args.skip_outside_market or _within_market_hours(now):
                for sym in symbols:
                    try:
                        sample = collector.snapshot(sym)
                    except SpreadSampleCollectorError as exc:
                        failed += 1
                        logger.error("spread.snapshot_failed symbol={s} err={e}", s=sym, e=exc)
                        continue
                    if sample is None:
                        continue
                    _append_jsonl(output_dir, sample)
                    written += 1
            sleep(args.interval_s)
            now = clock()
    finally:
        try:
            collector.close()
        except Exception as exc:  # noqa: BLE001 — close 부수 정보
            logger.warning("collector.close 중 예외 (무시): {e}", e=exc)

    logger.info("spread.done written={w} failed={f}", w=written, f=failed)
    return written, failed


def main(argv: list[str] | None = None) -> int:
    """CLI 엔트리포인트 — 예외 → exit code 매핑."""
    args = _parse_args(argv)

    if args.interval_s < 1.0:
        logger.error(
            f"--interval-s 는 1.0 이상이어야 합니다 (got={args.interval_s}). KIS rate limit 위험."
        )
        return _EXIT_INPUT_ERROR
    if args.duration_h <= 0:
        logger.error(f"--duration-h 는 양수여야 합니다 (got={args.duration_h}).")
        return _EXIT_INPUT_ERROR
    if args.http_timeout_s < 0:
        logger.error(f"--http-timeout-s 는 0 이상이어야 합니다 (got={args.http_timeout_s}).")
        return _EXIT_INPUT_ERROR

    try:
        settings = get_settings()
    except (RuntimeError, OSError) as exc:
        logger.error(f"설정 로드 실패: {exc}")
        return _EXIT_INPUT_ERROR

    try:
        collector = SpreadSampleCollector(
            settings,
            http_timeout_s=args.http_timeout_s,
        )
    except SpreadSampleCollectorError as exc:
        logger.error(f"SpreadSampleCollector 생성 실패: {exc}")
        return _EXIT_INPUT_ERROR
    except RuntimeError as exc:
        logger.error(f"입력 오류: {exc}")
        return _EXIT_INPUT_ERROR

    try:
        _, failed = _run_pipeline(
            args,
            collector=collector,
            clock=lambda: datetime.now(KST),
            sleep=time.sleep,
        )
    except SpreadSampleCollectorError as exc:
        logger.error(f"수집 중 오류: {exc}")
        return _EXIT_INPUT_ERROR
    except RuntimeError as exc:
        logger.error(f"실행 오류: {exc}")
        return _EXIT_INPUT_ERROR
    except OSError as exc:
        logger.exception(f"I/O 오류 (재시도 가능): {exc}")
        return _EXIT_IO_ERROR

    if failed > 0:
        return _EXIT_PARTIAL_FAILURE
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
