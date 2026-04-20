"""backtest — ORB 전략 단일 런 백테스트 CLI.

사용 예시:

```
uv run python scripts/backtest.py \
  --csv-dir data/minute_csv \
  --from 2023-01-01 --to 2025-12-31 \
  --symbols 005930,000660,035420 \
  --starting-capital 1000000 \
  --output-markdown data/backtest_report.md \
  --output-csv data/backtest_metrics.csv \
  --output-trades-csv data/backtest_trades.csv
```

동작
- `--csv-dir` 하위의 `{symbol}.csv` 를 `MinuteCsvBarLoader` 로 읽어 분봉 스트림
  공급.
- `--symbols` 미지정 시 `config/universe.yaml` 의 KOSPI 200 전체 사용.
- `BacktestEngine` 1회 실행. `StrategyConfig`/`RiskConfig`/비용률은 코드 기본값
  사용 (plan.md Phase 2 운영 기본값과 동일). 파라미터를 바꿀 경우
  `scripts/sensitivity.py` 를 먼저 돌려 sanity check 를 한다.
- 리포트 3종 출력: Markdown (육안), metrics CSV, trades CSV (운영자 재검증 용).

PASS 판정
- 리포트 상단에 `max_drawdown_pct < -0.15` (더 작은 음수) 이면 PASS, 아니면
  FAIL 라벨을 기록한다. **exit code 에는 반영하지 않는다** — Phase 2 PASS
  선언은 운영자가 walk-forward·데이터 편향·슬리피지 실측 괴리까지 수동
  검토하는 영역이다 (CI 자동화 금지).

제약
- 외부 네트워크·KIS·pykis 접촉 없음 — 순수 CSV + 엔진.
- 기본 출력 경로 `data/*` 는 `.gitignore` 대상. 실데이터 산출물을 커밋하지
  않는다.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from loguru import logger

from stock_agent.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestMetrics,
    BacktestResult,
    TradeRecord,
)
from stock_agent.data import MinuteCsvBarLoader, MinuteCsvLoadError, load_kospi200_universe

# exit code 규약 (scripts/sensitivity.py 와 동일): 2 = 입력·설정 오류 (재시도
# 무의미), 3 = I/O 오류 (재시도 가치 있음).
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

# Phase 2 PASS 임계값 — plan.md Verification 섹션.
_MDD_PASS_THRESHOLD: Decimal = Decimal("-0.15")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ORB 단일 런 백테스트",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        required=True,
        help="분봉 CSV 디렉토리 ({symbol}.csv 레이아웃).",
    )
    parser.add_argument(
        "--from",
        dest="start",
        type=date.fromisoformat,
        required=True,
        help="구간 시작 (YYYY-MM-DD, 경계 포함).",
    )
    parser.add_argument(
        "--to",
        dest="end",
        type=date.fromisoformat,
        required=True,
        help="구간 종료 (YYYY-MM-DD, 경계 포함).",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="쉼표 구분 종목 코드 (미지정 시 config/universe.yaml 전체 사용).",
    )
    parser.add_argument(
        "--starting-capital",
        type=int,
        default=1_000_000,
        help="시작 자본 (KRW).",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=Path("data/backtest_report.md"),
        help="Markdown 리포트 출력 경로.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/backtest_metrics.csv"),
        help="메트릭 CSV 출력 경로.",
    )
    parser.add_argument(
        "--output-trades-csv",
        type=Path,
        default=Path("data/backtest_trades.csv"),
        help="체결 기록 CSV 출력 경로 (TradeRecord 전체 필드).",
    )
    return parser.parse_args(argv)


def _resolve_symbols(raw: str) -> tuple[str, ...]:
    """`--symbols` 인자 해석 — 빈 값이면 유니버스 YAML 전체.

    `scripts/sensitivity.py:_resolve_symbols` 와 동일 계약. 공용 헬퍼로
    승격은 YAGNI (현재 소비자 2개).
    """
    if raw.strip():
        parts = tuple(s.strip() for s in raw.split(",") if s.strip())
        return parts
    universe = load_kospi200_universe()
    if not universe.tickers:
        raise RuntimeError(
            "config/universe.yaml 이 비어있습니다 — --symbols 로 명시하거나 "
            "유니버스 YAML 을 갱신하세요."
        )
    return universe.tickers


@dataclass(frozen=True, slots=True)
class _ReportContext:
    """Markdown 렌더링에 필요한 런타임 컨텍스트 (엔진 밖 정보)."""

    start: date
    end: date
    symbols: tuple[str, ...]
    starting_capital_krw: int


def _run_pipeline(args: argparse.Namespace) -> None:
    """실제 파이프라인 — 호출자가 예외 분기를 책임진다.

    엔진·로더 공개 API 만 호출. 경계를 single-purpose 로 분리해 `main()` 은
    예외 → exit code 매핑에 집중한다 (sensitivity 와 동일 기조).
    """
    symbols = _resolve_symbols(args.symbols)
    loader = MinuteCsvBarLoader(args.csv_dir)
    config = BacktestConfig(starting_capital_krw=args.starting_capital)
    engine = BacktestEngine(config)

    logger.info(
        "backtest.start from={s} to={e} symbols={n} capital={c}",
        s=args.start,
        e=args.end,
        n=len(symbols),
        c=args.starting_capital,
    )
    result = engine.run(loader.stream(args.start, args.end, symbols))

    context = _ReportContext(
        start=args.start,
        end=args.end,
        symbols=symbols,
        starting_capital_krw=args.starting_capital,
    )

    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    args.output_trades_csv.parent.mkdir(parents=True, exist_ok=True)

    args.output_markdown.write_text(_render_markdown(result, context), encoding="utf-8")
    _write_metrics_csv(result.metrics, args.output_csv)
    _write_trades_csv(result.trades, args.output_trades_csv)

    logger.info(
        "backtest.done trades={t} rejected={r} post_slippage={p} mdd={m} verdict={v}",
        t=len(result.trades),
        r=sum(result.rejected_counts.values()),
        p=result.post_slippage_rejections,
        m=_format_pct(result.metrics.max_drawdown_pct),
        v=_verdict_label(result.metrics.max_drawdown_pct),
    )


def _render_markdown(result: BacktestResult, context: _ReportContext) -> str:
    """`BacktestResult` → 사람이 읽는 Markdown 리포트."""
    metrics = result.metrics
    verdict = _verdict_label(metrics.max_drawdown_pct)
    lines: list[str] = []

    lines.append("# ORB 백테스트 리포트")
    lines.append("")
    lines.append(f"- 기간: `{context.start.isoformat()}` ~ `{context.end.isoformat()}`")
    lines.append(f"- 종목 수: {len(context.symbols)}")
    lines.append(f"- 시작 자본: {context.starting_capital_krw:,} KRW")
    lines.append(f"- 거래 수: {len(result.trades)}")
    lines.append("")
    lines.append(f"## Phase 2 PASS 판정: **{verdict}**")
    lines.append("")
    lines.append(
        f"- 기준: `max_drawdown_pct < {_format_pct(_MDD_PASS_THRESHOLD)}` "
        f"(plan.md Verification — Phase 2)"
    )
    lines.append(f"- 실측: `{_format_pct(metrics.max_drawdown_pct)}`")
    lines.append("")
    lines.append("## 메트릭")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    lines.append(f"| 총수익률 | {_format_pct(metrics.total_return_pct)} |")
    lines.append(f"| 최대 낙폭 (MDD) | {_format_pct(metrics.max_drawdown_pct)} |")
    lines.append(f"| 샤프 비율 (연환산) | {_format_decimal(metrics.sharpe_ratio, 4)} |")
    lines.append(f"| 승률 | {_format_pct(metrics.win_rate)} |")
    lines.append(f"| 평균 손익비 | {_format_decimal(metrics.avg_pnl_ratio, 4)} |")
    lines.append(f"| 일평균 거래 수 | {_format_decimal(metrics.trades_per_day, 3)} |")
    lines.append(f"| 순손익 (KRW) | {metrics.net_pnl_krw:,} |")
    lines.append("")
    lines.append("## 일일 자본 요약")
    lines.append("")
    if result.daily_equity:
        equities = [row.equity_krw for row in result.daily_equity]
        first = result.daily_equity[0]
        last = result.daily_equity[-1]
        trough = min(result.daily_equity, key=lambda r: r.equity_krw)
        lines.append(f"- 세션 수: {len(result.daily_equity)}")
        lines.append(f"- 시작: `{first.session_date.isoformat()}` {first.equity_krw:,} KRW")
        lines.append(f"- 종료: `{last.session_date.isoformat()}` {last.equity_krw:,} KRW")
        lines.append(f"- 최저점: `{trough.session_date.isoformat()}` {trough.equity_krw:,} KRW")
        lines.append(f"- 최고점 자본: {max(equities):,} KRW")
    else:
        lines.append("- 세션 없음 (입력 분봉이 비어있거나 날짜 필터 결과가 0건)")
    lines.append("")
    lines.append("## 거부 카운트")
    lines.append("")
    if result.rejected_counts:
        lines.append("| 사유 | 카운트 |")
        lines.append("|---|---|")
        for reason in sorted(result.rejected_counts):
            lines.append(f"| `{reason}` | {result.rejected_counts[reason]} |")
    else:
        lines.append("- RiskManager 사전 거부 0건")
    lines.append("")
    lines.append(f"- 사후 슬리피지 거부: {result.post_slippage_rejections}건")
    lines.append("")
    lines.append("## 주의")
    lines.append("")
    lines.append(
        "- 이 리포트의 `PASS` 라벨은 단일 구간 MDD 만 본다. "
        "실전 전환은 walk-forward 검증(Phase 5 후보) + 모의투자 2주 무사고(Phase 3) "
        "선행을 전제한다."
    )
    lines.append(
        "- 슬리피지·수수료·거래세는 백테스트 기본값이며 실전 괴리는 Phase 4 주간 회고로 "
        "측정한다 (plan.md Phase 4)."
    )
    lines.append("")
    return "\n".join(lines)


def _write_metrics_csv(metrics: BacktestMetrics, path: Path) -> None:
    """`metric,value` 2열 CSV — 프로그래매틱 후처리 용."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("metric", "value"))
        writer.writerow(("total_return_pct", str(metrics.total_return_pct)))
        writer.writerow(("max_drawdown_pct", str(metrics.max_drawdown_pct)))
        writer.writerow(("sharpe_ratio", str(metrics.sharpe_ratio)))
        writer.writerow(("win_rate", str(metrics.win_rate)))
        writer.writerow(("avg_pnl_ratio", str(metrics.avg_pnl_ratio)))
        writer.writerow(("trades_per_day", str(metrics.trades_per_day)))
        writer.writerow(("net_pnl_krw", str(metrics.net_pnl_krw)))


def _write_trades_csv(trades: tuple[TradeRecord, ...], path: Path) -> None:
    """체결 1쌍(entry~exit) 단위 전체 덤프 — 운영자 재검증 용."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            (
                "symbol",
                "entry_ts",
                "entry_price",
                "exit_ts",
                "exit_price",
                "qty",
                "exit_reason",
                "gross_pnl_krw",
                "commission_krw",
                "tax_krw",
                "net_pnl_krw",
            )
        )
        for trade in trades:
            writer.writerow(
                (
                    trade.symbol,
                    trade.entry_ts.isoformat(),
                    str(trade.entry_price),
                    trade.exit_ts.isoformat(),
                    str(trade.exit_price),
                    trade.qty,
                    trade.exit_reason,
                    trade.gross_pnl_krw,
                    trade.commission_krw,
                    trade.tax_krw,
                    trade.net_pnl_krw,
                )
            )


def _verdict_label(mdd: Decimal) -> str:
    """MDD 가 임계값보다 더 작은 음수(낙폭 15% 이하) 이면 PASS."""
    return "PASS" if mdd < _MDD_PASS_THRESHOLD else "FAIL"


def _format_pct(value: Decimal) -> str:
    """`Decimal(0.1234)` → `"12.34%"`. float 경유 — 리포트 용 2자리."""
    return f"{float(value) * 100:.2f}%"


def _format_decimal(value: Decimal, digits: int) -> str:
    """`Decimal` → 고정 소수점 문자열. 샤프·손익비·일평균 거래 수 공용."""
    return f"{float(value):.{digits}f}"


def main(argv: list[str] | None = None) -> int:
    """CLI 엔트리포인트 — 예외 → exit code 매핑만 책임진다.

    예외 분류 (프로젝트 가드레일 "generic except Exception 금지" 기조 준수):

    - `MinuteCsvLoadError` · `RuntimeError` → exit 2 (입력·설정 오류).
    - `OSError` → exit 3 (I/O 오류, 재시도 가치 있음).
    - 그 외는 버그로 간주해 Python traceback 그대로 종료.
    """
    args = _parse_args(argv)

    if args.start > args.end:
        logger.error(f"--from({args.start}) 는 --to({args.end}) 이전이어야 합니다.")
        return _EXIT_INPUT_ERROR
    if args.starting_capital <= 0:
        logger.error(f"--starting-capital 은 양수여야 합니다 (got={args.starting_capital}).")
        return _EXIT_INPUT_ERROR

    try:
        _run_pipeline(args)
    except MinuteCsvLoadError as e:
        logger.error(f"CSV 입력 오류: {e}")
        return _EXIT_INPUT_ERROR
    except RuntimeError as e:
        logger.error(f"설정·검증 오류: {e}")
        return _EXIT_INPUT_ERROR
    except OSError as e:
        logger.exception(f"I/O 오류 (재시도 가능): {e}")
        return _EXIT_IO_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main())
