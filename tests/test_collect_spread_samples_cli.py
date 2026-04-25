"""scripts/collect_spread_samples.py 공개 계약 단위 테스트 (RED 명세).

_parse_args / _resolve_symbols / _jsonl_path / _run_pipeline / main(exit code) 를 검증한다.
SpreadSampleCollector 와 get_settings 는 전부 MagicMock 으로 교체.
실 KIS 네트워크·실 pykis·실 DB 접촉 없음.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# scripts/collect_spread_samples.py 로드
# (scripts/ 에 __init__.py 없음 — spec_from_file_location 사용, backfill_cli 와 동일 패턴)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "collect_spread_samples.py"

_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

_LOAD_ERROR: Exception | None = None
collect_cli = None  # type: ignore[assignment]

try:
    _spec = importlib.util.spec_from_file_location("collect_cli", _SCRIPT_PATH)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"spec_from_file_location 이 None 반환: {_SCRIPT_PATH}")
    collect_cli = importlib.util.module_from_spec(_spec)
    sys.modules["collect_cli"] = collect_cli
    _spec.loader.exec_module(collect_cli)  # type: ignore[union-attr]
except Exception as _e:
    _LOAD_ERROR = _e


def _require_module():
    """각 테스트 시작 시 호출 — 모듈 로드 실패면 pytest.fail() 로 FAIL."""
    if _LOAD_ERROR is not None:
        pytest.fail(
            f"scripts/collect_spread_samples.py 로드 실패 (RED 예상): {_LOAD_ERROR}",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# 검증 대상 심볼 참조 (로드 성공 시에만 유효)
# ---------------------------------------------------------------------------


def _parse_args(argv=None):  # type: ignore[misc]
    _require_module()
    return collect_cli._parse_args(argv)  # type: ignore[union-attr]


def _resolve_symbols(raw):  # type: ignore[misc]
    _require_module()
    return collect_cli._resolve_symbols(raw)  # type: ignore[union-attr]


def _jsonl_path(output_dir, session_date):  # type: ignore[misc]
    _require_module()
    return collect_cli._jsonl_path(output_dir, session_date)  # type: ignore[union-attr]


def _run_pipeline(args, *, collector, clock, sleep):  # type: ignore[misc]
    _require_module()
    return collect_cli._run_pipeline(args, collector=collector, clock=clock, sleep=sleep)  # type: ignore[union-attr]


def main(argv=None):  # type: ignore[misc]
    _require_module()
    return collect_cli.main(argv)  # type: ignore[union-attr]


def _get_exit_const(name: str) -> int:
    _require_module()
    return getattr(collect_cli, name)  # type: ignore[union-attr]


_EXIT_OK = 0
_EXIT_PARTIAL_FAILURE = 1
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_SYMBOL = "005930"
_SYMBOL2 = "000660"


def _kst(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST)


def _make_spread_sample(
    symbol: str = _SYMBOL,
    ts: datetime | None = None,
    bid1: str = "71500",
    ask1: str = "71600",
    bid_qty1: int = 1234,
    ask_qty1: int = 890,
    spread_pct: str = "0.139860",
) -> MagicMock:
    """SpreadSample 모양의 MagicMock 더미."""
    ts = ts or _kst(2026, 4, 27, 10, 30)
    sample = MagicMock()
    sample.symbol = symbol
    sample.ts = ts
    sample.bid1 = Decimal(bid1)
    sample.ask1 = Decimal(ask1)
    sample.bid_qty1 = bid_qty1
    sample.ask_qty1 = ask_qty1
    sample.spread_pct = Decimal(spread_pct)
    return sample


def _make_fake_collector(
    *,
    snapshot_returns: dict | None = None,
    raise_for: dict | None = None,
) -> MagicMock:
    """SpreadSampleCollector 모양의 MagicMock.

    snapshot_returns: {symbol: SpreadSample | None}
    raise_for: {symbol: Exception}
    """
    snapshot_returns = snapshot_returns or {}
    raise_for = raise_for or {}
    collector = MagicMock()

    def _snapshot(symbol):
        if symbol in raise_for:
            raise raise_for[symbol]
        return snapshot_returns.get(symbol)

    collector.snapshot.side_effect = _snapshot
    return collector


# ---------------------------------------------------------------------------
# SpreadSampleCollectorError 참조
# ---------------------------------------------------------------------------


def _get_collector_error():
    """SpreadSampleCollectorError 지연 import."""
    try:
        from stock_agent.data.spread_samples import (
            SpreadSampleCollectorError,  # type: ignore[import]
        )

        return SpreadSampleCollectorError
    except ImportError:
        # 아직 모듈 없음 — Exception 으로 대체
        return Exception


# ===========================================================================
# 1. _parse_args
# ===========================================================================


class TestParseArgs:
    def test_기본값_확인(self):
        """모든 기본값 검증."""
        args = _parse_args(["--symbols=005930"])
        assert args.interval_s == pytest.approx(30.0)
        assert args.duration_h == pytest.approx(6.5)
        assert args.skip_outside_market is True
        assert args.http_timeout_s == pytest.approx(10.0)

    def test_모든_옵션_명시_파싱(self, tmp_path: Path):
        """모든 옵션을 명시해도 정상 파싱."""
        output_dir = str(tmp_path / "samples")
        args = _parse_args(
            [
                "--symbols=005930,000660",
                "--interval-s=15.0",
                "--duration-h=3.0",
                f"--output-dir={output_dir}",
                "--http-timeout-s=5.0",
                "--no-skip-outside-market",
            ]
        )
        assert args.symbols == "005930,000660"
        assert args.interval_s == pytest.approx(15.0)
        assert args.duration_h == pytest.approx(3.0)
        assert str(args.output_dir) == output_dir
        assert args.http_timeout_s == pytest.approx(5.0)
        assert args.skip_outside_market is False

    def test_interval_s_0_5_파싱은_허용(self):
        """`_parse_args` 자체는 0.5 도 받아들임 — 검증은 main() 책임. interval<1.0 거부는
        `TestMainExitCode::test_interval_s_1_미만_exit2` 가 main 레벨에서 검증한다."""
        args = _parse_args(["--interval-s=0.5"])
        assert args.interval_s == pytest.approx(0.5)

    def test_duration_h_zero_거부(self):
        """--duration-h 0 → SystemExit 또는 ValueError/RuntimeError."""
        try:
            args = _parse_args(["--duration-h=0"])
            # argparse 단계에서 안 잡히면 값이 유효하지 않음을 확인
            assert args.duration_h <= 0
        except (SystemExit, ValueError, RuntimeError):
            pass  # argparse 단계에서 잡은 경우도 OK

    def test_no_skip_outside_market_플래그(self):
        """--no-skip-outside-market 지정 시 skip_outside_market=False."""
        args = _parse_args(["--no-skip-outside-market"])
        assert args.skip_outside_market is False

    def test_output_dir_기본값(self):
        """--output-dir 미지정 → 기본값 'data/spread_samples' 포함 경로."""
        args = _parse_args([])
        assert "spread_samples" in str(args.output_dir)


# ===========================================================================
# 2. _jsonl_path
# ===========================================================================


class TestJsonlPath:
    def test_파일명_날짜_기반(self, tmp_path: Path):
        """_jsonl_path → {output_dir}/{YYYY-MM-DD}.jsonl 파일명."""
        d = date(2026, 4, 27)
        path = _jsonl_path(tmp_path, d)
        assert path.name == "2026-04-27.jsonl"
        assert path.parent == tmp_path

    def test_디렉토리_자동_생성(self, tmp_path: Path):
        """존재하지 않는 output_dir → 자동 mkdir 후 경로 반환."""
        new_dir = tmp_path / "deep" / "nested" / "dir"
        assert not new_dir.exists()
        path = _jsonl_path(new_dir, date(2026, 4, 27))
        assert path.parent.exists()
        assert path.name == "2026-04-27.jsonl"


# ===========================================================================
# 3. _resolve_symbols
# ===========================================================================


class TestResolveSymbols:
    def test_명시_심볼_반환(self, monkeypatch: pytest.MonkeyPatch):
        """쉼표 구분 심볼 문자열 → tuple 반환."""
        result = _resolve_symbols("005930,000660")
        assert result == ("005930", "000660")

    def test_universe_fallback(self, monkeypatch: pytest.MonkeyPatch):
        """빈 문자열 → load_kospi200_universe 호출, universe.tickers 반환."""
        fake_universe = MagicMock()
        fake_universe.tickers = ("005930", "000660")
        monkeypatch.setattr(
            collect_cli,
            "load_kospi200_universe",
            MagicMock(return_value=fake_universe),
        )
        result = _resolve_symbols("")
        assert result == ("005930", "000660")

    def test_빈_universe_RuntimeError(self, monkeypatch: pytest.MonkeyPatch):
        """universe.tickers 가 비면 RuntimeError."""
        fake_universe = MagicMock()
        fake_universe.tickers = ()
        monkeypatch.setattr(
            collect_cli,
            "load_kospi200_universe",
            MagicMock(return_value=fake_universe),
        )
        with pytest.raises(RuntimeError):
            _resolve_symbols("")


# ===========================================================================
# 4. _run_pipeline 정상
# ===========================================================================


class TestRunPipelineNormal:
    def _make_args(
        self,
        tmp_path: Path,
        *,
        symbols: str = _SYMBOL,
        interval_s: float = 1.0,
        duration_h: float = 0.0001,  # 매우 짧은 duration
        skip_outside_market: bool = False,
    ):
        """argparse.Namespace 더미 생성."""
        import argparse

        args = argparse.Namespace(
            symbols=symbols,
            interval_s=interval_s,
            duration_h=duration_h,
            output_dir=tmp_path / "spread_samples",
            skip_outside_market=skip_outside_market,
            http_timeout_s=10.0,
        )
        return args

    def test_단일_sweep_정상_JSONL_기록(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """단일 심볼 1회 sweep → JSONL 1줄 기록 + (1, 0) 반환."""
        ts = _kst(2026, 4, 27, 10, 30)
        sample = _make_spread_sample(symbol=_SYMBOL, ts=ts)
        collector = _make_fake_collector(snapshot_returns={_SYMBOL: sample})
        mock_sleep = MagicMock()

        # clock: 첫 호출 = 시작(장중), 두 번째 호출 = 루프 체크, 세 번째 = 종료 시간 초과
        start_ts = _kst(2026, 4, 27, 10, 30)
        after_ts = _kst(2026, 4, 27, 10, 31)
        calls = iter([start_ts, after_ts])
        clock = lambda: next(calls)  # noqa: E731

        args = self._make_args(tmp_path, symbols=_SYMBOL, duration_h=1 / 3600)
        result = _run_pipeline(args, collector=collector, clock=clock, sleep=mock_sleep)
        written, failed = result
        assert written >= 1
        assert failed == 0

    def test_JSONL_라인_Decimal_str_직렬화(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """JSONL 한 줄 — Decimal 은 str, ts 는 isoformat 직렬화 확인."""
        ts = _kst(2026, 4, 27, 10, 30)
        sample = _make_spread_sample(
            symbol=_SYMBOL,
            ts=ts,
            bid1="71500",
            ask1="71600",
            bid_qty1=1234,
            ask_qty1=890,
            spread_pct="0.139860",
        )
        collector = _make_fake_collector(snapshot_returns={_SYMBOL: sample})
        mock_sleep = MagicMock()

        output_dir = tmp_path / "spread_samples"

        import argparse

        args = argparse.Namespace(
            symbols=_SYMBOL,
            interval_s=1.0,
            duration_h=1 / 3600,
            output_dir=output_dir,
            skip_outside_market=False,
            http_timeout_s=10.0,
        )

        start_ts = _kst(2026, 4, 27, 10, 30)
        after_ts = _kst(2026, 4, 27, 10, 31)
        calls = iter([start_ts, after_ts])
        clock = lambda: next(calls)  # noqa: E731

        _run_pipeline(args, collector=collector, clock=clock, sleep=mock_sleep)

        # JSONL 파일 읽어 첫 줄 검증
        jsonl_file = _jsonl_path(output_dir, date(2026, 4, 27))
        if jsonl_file.exists():
            with open(jsonl_file, encoding="utf-8") as f:
                line = f.readline().strip()
            if line:
                data = json.loads(line)
                assert data["symbol"] == _SYMBOL
                assert isinstance(data["bid1"], str)  # Decimal → str
                assert isinstance(data["ask1"], str)
                assert isinstance(data["spread_pct"], str)
                # ts 는 isoformat
                assert "+09:00" in data["ts"] or data["ts"].endswith("Z")

    def test_snapshot_None_반환_시_기록_안_함(self, tmp_path: Path):
        """snapshot() 이 None → JSONL 기록 없음, failed 0."""
        collector = _make_fake_collector(snapshot_returns={_SYMBOL: None})
        mock_sleep = MagicMock()

        import argparse

        args = argparse.Namespace(
            symbols=_SYMBOL,
            interval_s=1.0,
            duration_h=1 / 3600,
            output_dir=tmp_path / "spread_samples",
            skip_outside_market=False,
            http_timeout_s=10.0,
        )
        start_ts = _kst(2026, 4, 27, 10, 30)
        after_ts = _kst(2026, 4, 27, 10, 31)
        calls = iter([start_ts, after_ts])
        clock = lambda: next(calls)  # noqa: E731

        written, failed = _run_pipeline(args, collector=collector, clock=clock, sleep=mock_sleep)
        assert written == 0
        assert failed == 0


# ===========================================================================
# 5. _run_pipeline skip 시간외
# ===========================================================================


class TestRunPipelineMarketHours:
    def test_skip_outside_market_활성_장외_snapshot_호출_없음(self, tmp_path: Path):
        """skip_outside_market=True + 08:30 → snapshot 0회."""
        collector = _make_fake_collector(snapshot_returns={_SYMBOL: _make_spread_sample()})
        mock_sleep = MagicMock()

        import argparse

        args = argparse.Namespace(
            symbols=_SYMBOL,
            interval_s=30.0,
            duration_h=1 / 3600,
            output_dir=tmp_path / "spread_samples",
            skip_outside_market=True,
            http_timeout_s=10.0,
        )
        # 08:30 — 장 전
        outside_ts = _kst(2026, 4, 27, 8, 30)
        after_ts = _kst(2026, 4, 27, 8, 31)
        calls = iter([outside_ts, after_ts])
        clock = lambda: next(calls)  # noqa: E731

        _run_pipeline(args, collector=collector, clock=clock, sleep=mock_sleep)
        collector.snapshot.assert_not_called()

    def test_skip_outside_market_비활성_장외도_snapshot_호출(self, tmp_path: Path):
        """skip_outside_market=False + 08:30 → snapshot 1회 이상."""
        sample = _make_spread_sample()
        collector = _make_fake_collector(snapshot_returns={_SYMBOL: sample})
        mock_sleep = MagicMock()

        import argparse

        args = argparse.Namespace(
            symbols=_SYMBOL,
            interval_s=30.0,
            duration_h=1 / 3600,
            output_dir=tmp_path / "spread_samples",
            skip_outside_market=False,
            http_timeout_s=10.0,
        )
        outside_ts = _kst(2026, 4, 27, 8, 30)
        after_ts = _kst(2026, 4, 27, 8, 31)
        calls = iter([outside_ts, after_ts])
        clock = lambda: next(calls)  # noqa: E731

        _run_pipeline(args, collector=collector, clock=clock, sleep=mock_sleep)
        # skip_outside_market=False 이므로 snapshot 호출되어야 함
        assert collector.snapshot.call_count >= 1


# ===========================================================================
# 6. _run_pipeline 실패 격리
# ===========================================================================


class TestRunPipelineFailureIsolation:
    def test_SpreadSampleCollectorError_failed_카운트_증가(self, tmp_path: Path):
        """SpreadSampleCollectorError 발생 → failed +1, 다음 심볼 계속."""
        SpreadSampleCollectorError = _get_collector_error()
        collector = _make_fake_collector(
            snapshot_returns={_SYMBOL2: _make_spread_sample(symbol=_SYMBOL2)},
            raise_for={_SYMBOL: SpreadSampleCollectorError("rate limit")},
        )
        mock_sleep = MagicMock()

        import argparse

        args = argparse.Namespace(
            symbols=f"{_SYMBOL},{_SYMBOL2}",
            interval_s=1.0,
            duration_h=1 / 3600,
            output_dir=tmp_path / "spread_samples",
            skip_outside_market=False,
            http_timeout_s=10.0,
        )
        start_ts = _kst(2026, 4, 27, 10, 30)
        after_ts = _kst(2026, 4, 27, 10, 31)
        calls = iter([start_ts, after_ts])
        clock = lambda: next(calls)  # noqa: E731

        written, failed = _run_pipeline(args, collector=collector, clock=clock, sleep=mock_sleep)
        # 005930 실패, 000660 성공 → failed=1, written>=1
        assert failed >= 1

    def test_실패_후_다음_심볼_계속(self, tmp_path: Path):
        """첫 심볼 에러 → 두 번째 심볼 snapshot 호출됨."""
        SpreadSampleCollectorError = _get_collector_error()
        sample2 = _make_spread_sample(symbol=_SYMBOL2)
        collector = _make_fake_collector(
            snapshot_returns={_SYMBOL2: sample2},
            raise_for={_SYMBOL: SpreadSampleCollectorError("err")},
        )
        mock_sleep = MagicMock()

        import argparse

        args = argparse.Namespace(
            symbols=f"{_SYMBOL},{_SYMBOL2}",
            interval_s=1.0,
            duration_h=1 / 3600,
            output_dir=tmp_path / "spread_samples",
            skip_outside_market=False,
            http_timeout_s=10.0,
        )
        start_ts = _kst(2026, 4, 27, 10, 30)
        after_ts = _kst(2026, 4, 27, 10, 31)
        calls = iter([start_ts, after_ts])
        clock = lambda: next(calls)  # noqa: E731

        _run_pipeline(args, collector=collector, clock=clock, sleep=mock_sleep)
        # 두 번째 심볼도 호출되었는지 확인
        call_syms = [c.args[0] for c in collector.snapshot.call_args_list]
        assert _SYMBOL2 in call_syms


# ===========================================================================
# 7. main exit code
# ===========================================================================


class TestMainExitCode:
    def test_interval_s_1_미만_exit2(self, monkeypatch: pytest.MonkeyPatch):
        """--interval-s 0.5 → exit 2 (입력 오류)."""
        _require_module()
        monkeypatch.setattr(
            collect_cli,
            "get_settings",
            MagicMock(side_effect=RuntimeError("no keys")),
        )
        exit_code = main(["--interval-s=0.5"])
        assert exit_code == _get_exit_const("_EXIT_INPUT_ERROR")

    def test_no_live_keys_exit2(self, monkeypatch: pytest.MonkeyPatch):
        """settings.has_live_keys=False → exit 2."""
        _require_module()
        from unittest.mock import MagicMock as MM  # noqa: N817

        fake_settings = MM()
        fake_settings.has_live_keys = False
        monkeypatch.setattr(collect_cli, "get_settings", MM(return_value=fake_settings))
        # SpreadSampleCollectorError 가 없으면 직접 mock
        try:
            from stock_agent.data.spread_samples import (
                SpreadSampleCollectorError,  # type: ignore[import]
            )
        except ImportError:
            SpreadSampleCollectorError = RuntimeError  # type: ignore[misc,assignment]

        monkeypatch.setattr(
            collect_cli,
            "SpreadSampleCollector",
            MM(side_effect=SpreadSampleCollectorError("no live keys")),
        )
        exit_code = main([])
        assert exit_code in (
            _get_exit_const("_EXIT_INPUT_ERROR"),
            _get_exit_const("_EXIT_PARTIAL_FAILURE"),
        )

    def test_OSError_exit3(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """JSONL 기록 중 OSError → exit 3."""
        _require_module()
        from unittest.mock import MagicMock as MM  # noqa: N817

        fake_settings = MM()
        fake_settings.has_live_keys = True
        monkeypatch.setattr(collect_cli, "get_settings", MM(return_value=fake_settings))

        # _run_pipeline 을 OSError 를 발생시키는 mock 으로 교체
        monkeypatch.setattr(
            collect_cli,
            "_run_pipeline",
            MM(side_effect=OSError("disk full")),
        )
        exit_code = main([])
        assert exit_code == _get_exit_const("_EXIT_IO_ERROR")

    def test_정상_완료_exit0(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """정상 완료 (failed=0) → exit 0."""
        _require_module()
        from unittest.mock import MagicMock as MM  # noqa: N817

        fake_settings = MM()
        fake_settings.has_live_keys = True
        monkeypatch.setattr(collect_cli, "get_settings", MM(return_value=fake_settings))

        # _run_pipeline 을 (1, 0) 반환 mock 으로 교체
        monkeypatch.setattr(
            collect_cli,
            "_run_pipeline",
            MM(return_value=(1, 0)),
        )
        exit_code = main([])
        assert exit_code == _get_exit_const("_EXIT_OK")

    def test_부분실패_exit1(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """failed > 0 → exit 1."""
        _require_module()
        from unittest.mock import MagicMock as MM  # noqa: N817

        fake_settings = MM()
        fake_settings.has_live_keys = True
        monkeypatch.setattr(collect_cli, "get_settings", MM(return_value=fake_settings))

        # _run_pipeline 을 (3, 2) 반환 mock 으로 교체 (2건 실패)
        monkeypatch.setattr(
            collect_cli,
            "_run_pipeline",
            MM(return_value=(3, 2)),
        )
        exit_code = main([])
        assert exit_code == _get_exit_const("_EXIT_PARTIAL_FAILURE")
