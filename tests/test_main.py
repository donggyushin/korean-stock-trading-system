"""stock_agent.main 공개 계약 단위 테스트 (RED 모드).

src/stock_agent/main.py 가 아직 존재하지 않으므로 모든 케이스가
ModuleNotFoundError 로 실패한다. 구현 후 GREEN 전환을 목표로 한다.

가드레일: KIS·텔레그램·외부 HTTP·실 KisClient·실 RealtimeDataStore 접촉 없음.
모든 외부 의존은 팩토리 주입 또는 mocker.patch 로 차단한다.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from signal import SIGTERM
from typing import Any
from unittest.mock import MagicMock

import pytest

from stock_agent.broker import KisClient
from stock_agent.data import UniverseLoadError
from stock_agent.data.universe import KospiUniverse
from stock_agent.execution import (
    DryRunOrderSubmitter,
    Executor,
    LiveOrderSubmitter,
    ReconcileReport,
    StepReport,
)

# ---------------------------------------------------------------------------
# import — 이 블록이 ModuleNotFoundError 로 실패하는 것이 RED 모드의 목표.
# ---------------------------------------------------------------------------
from stock_agent.main import (
    EXIT_INPUT_ERROR,
    EXIT_IO_ERROR,
    EXIT_OK,
    EXIT_UNEXPECTED,
    KST,
    Runtime,
    _build_order_submitter,
    _graceful_shutdown,
    _install_jobs,
    _on_daily_report,
    _on_force_close,
    _on_session_start,
    _on_step,
    _parse_args,
    build_runtime,
    main,
)

# ---------------------------------------------------------------------------
# 공통 상수 / 헬퍼
# ---------------------------------------------------------------------------

_DATE = date(2026, 4, 21)
_TICKERS = ("005930", "000660", "035420")


def _kst(h: int, m: int, s: int = 0) -> datetime:
    return datetime(_DATE.year, _DATE.month, _DATE.day, h, m, s, tzinfo=KST)


def _make_balance(total: int = 2_000_000, withdrawable: int = 1_900_000) -> MagicMock:
    """BalanceSnapshot 더블."""
    b = MagicMock()
    b.total = total
    b.withdrawable = withdrawable
    b.holdings = []
    return b


def _make_runtime(
    *,
    kis_client: MagicMock | None = None,
    realtime_store: MagicMock | None = None,
    executor: MagicMock | None = None,
    scheduler: MagicMock | None = None,
    args: argparse.Namespace | None = None,
) -> Runtime:
    """Runtime 더블 조립 헬퍼."""
    _kis = kis_client or MagicMock(spec=KisClient)
    _rt = realtime_store or MagicMock()
    _ex = executor or MagicMock(spec=Executor)
    _sc = scheduler or MagicMock()
    _args = args or _parse_args([])
    return Runtime(
        scheduler=_sc,
        executor=_ex,
        realtime_store=_rt,
        kis_client=_kis,
        args=_args,
    )


def _make_step_report() -> StepReport:
    reconcile = ReconcileReport(
        broker_holdings={},
        risk_holdings={},
        mismatch_symbols=(),
    )
    return StepReport(
        processed_bars=3,
        orders_submitted=1,
        halted=False,
        reconcile=reconcile,
    )


# ---------------------------------------------------------------------------
# 1. _parse_args — 기본값 및 명시 파싱
# ---------------------------------------------------------------------------


def test_parse_args_기본값() -> None:
    args = _parse_args([])
    assert args.dry_run is False
    assert args.starting_capital == 1_000_000
    assert args.universe_path is None
    assert args.log_dir == Path("logs")


def test_parse_args_모든_옵션_명시() -> None:
    args = _parse_args(
        [
            "--dry-run",
            "--starting-capital",
            "500000",
            "--universe-path",
            "/tmp/u.yaml",
            "--log-dir",
            "/tmp/logs",
        ]
    )
    assert args.dry_run is True
    assert args.starting_capital == 500_000
    assert args.universe_path == Path("/tmp/u.yaml")
    assert args.log_dir == Path("/tmp/logs")


def test_parse_args_음수_자본은_argparse_레벨에서_통과() -> None:
    # main() 에서 검증 — argparse 자체는 막지 않는다.
    args = _parse_args(["--starting-capital", "-1"])
    assert args.starting_capital == -1


def test_parse_args_universe_path_없으면_None() -> None:
    args = _parse_args([])
    assert args.universe_path is None


# ---------------------------------------------------------------------------
# 2. _build_order_submitter
# ---------------------------------------------------------------------------


def test_build_order_submitter_dry_run_True_는_DryRunOrderSubmitter() -> None:
    result = _build_order_submitter(dry_run=True, kis_client=MagicMock())
    assert isinstance(result, DryRunOrderSubmitter)


def test_build_order_submitter_dry_run_False_는_LiveOrderSubmitter() -> None:
    fake_kis = MagicMock(spec=KisClient)
    result = _build_order_submitter(dry_run=False, kis_client=fake_kis)
    assert isinstance(result, LiveOrderSubmitter)
    assert result._kis is fake_kis


# ---------------------------------------------------------------------------
# 3. build_runtime — 정상 경로
# ---------------------------------------------------------------------------


@pytest.fixture
def _mock_universe() -> KospiUniverse:
    return KospiUniverse(
        as_of_date=_DATE,
        source="test",
        tickers=_TICKERS,
    )


@pytest.fixture
def _fake_settings() -> MagicMock:
    s = MagicMock()
    s.has_live_keys = True
    return s


def test_build_runtime_정상_subscribe_각_티커_호출(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    fake_rt = MagicMock()
    fake_kis = MagicMock(spec=KisClient)
    fake_scheduler = MagicMock()
    args = _parse_args([])

    build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: fake_kis,
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=lambda: fake_scheduler,
        universe_loader=lambda p: _mock_universe,
        clock=lambda: _kst(9, 0),
    )

    subscribe_calls = [c[0][0] for c in fake_rt.subscribe.call_args_list]
    assert subscribe_calls == list(_TICKERS)


def test_build_runtime_dry_run_True_시_DryRunOrderSubmitter_주입(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    fake_rt = MagicMock()
    args = _parse_args(["--dry-run"])

    runtime = build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: MagicMock(spec=KisClient),
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=MagicMock,
        universe_loader=lambda p: _mock_universe,
    )

    assert isinstance(runtime.executor._order_submitter, DryRunOrderSubmitter)


def test_build_runtime_dry_run_False_시_LiveOrderSubmitter_주입(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    fake_rt = MagicMock()
    args = _parse_args([])  # dry_run=False

    runtime = build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: MagicMock(spec=KisClient),
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=MagicMock,
        universe_loader=lambda p: _mock_universe,
    )

    assert isinstance(runtime.executor._order_submitter, LiveOrderSubmitter)


def test_build_runtime_Runtime_필드_5개_반환(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    fake_rt = MagicMock()
    args = _parse_args([])

    runtime = build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: MagicMock(spec=KisClient),
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=MagicMock,
        universe_loader=lambda p: _mock_universe,
    )

    assert isinstance(runtime, Runtime)
    assert runtime.scheduler is not None
    assert isinstance(runtime.executor, Executor)
    assert runtime.realtime_store is fake_rt
    assert runtime.kis_client is not None
    assert runtime.args is args


# ---------------------------------------------------------------------------
# 4. build_runtime — 에러 경로
# ---------------------------------------------------------------------------


def test_build_runtime_has_live_keys_False_RuntimeError(_fake_settings: MagicMock) -> None:
    _fake_settings.has_live_keys = False
    args = _parse_args([])

    with pytest.raises(RuntimeError):
        build_runtime(
            args,
            _fake_settings,
            universe_loader=lambda p: KospiUniverse(
                as_of_date=_DATE, source="t", tickers=("005930",)
            ),
        )


def test_build_runtime_빈_유니버스_RuntimeError(_fake_settings: MagicMock) -> None:
    args = _parse_args([])
    empty_universe = KospiUniverse(as_of_date=_DATE, source="t", tickers=())

    with pytest.raises(RuntimeError, match="유니버스"):
        build_runtime(
            args,
            _fake_settings,
            kis_client_factory=lambda s: MagicMock(spec=KisClient),
            realtime_store_factory=lambda s: MagicMock(),
            scheduler_factory=MagicMock,
            universe_loader=lambda p: empty_universe,
        )


def test_build_runtime_UniverseLoadError_전파(_fake_settings: MagicMock) -> None:
    args = _parse_args([])

    def _fail(p: Any) -> KospiUniverse:
        raise UniverseLoadError("테스트용 로드 실패")

    with pytest.raises(UniverseLoadError):
        build_runtime(
            args,
            _fake_settings,
            kis_client_factory=lambda s: MagicMock(spec=KisClient),
            realtime_store_factory=lambda s: MagicMock(),
            scheduler_factory=MagicMock,
            universe_loader=_fail,
        )


# ---------------------------------------------------------------------------
# 5. _install_jobs — cron trigger 검증
# ---------------------------------------------------------------------------


def test_install_jobs_add_job_4회_호출() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    assert scheduler.add_job.call_count == 4


def test_install_jobs_session_start_cron_hour9_minute0() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    triggers = [c.kwargs.get("trigger") or c.args[1] for c in scheduler.add_job.call_args_list]
    # 실제 CronTrigger 객체는 str 표현으로 검증
    [str(t) for t in triggers]
    # on_session_start 는 첫 번째 add_job
    first_trigger = [c for c in scheduler.add_job.call_args_list][0]
    trigger_obj = (
        first_trigger.kwargs.get("trigger") or first_trigger.args[1]
        if first_trigger.args
        else first_trigger.kwargs.get("trigger")
    )
    assert isinstance(trigger_obj, CronTrigger)


@pytest.mark.parametrize(
    "job_index, expected_hour, expected_minute, expected_second",
    [
        (0, 9, 0, 0),  # on_session_start
        (2, 15, 0, 0),  # on_force_close
        (3, 15, 30, 0),  # on_daily_report
    ],
    ids=["session_start", "force_close", "daily_report"],
)
def test_install_jobs_cron_시각_검증(
    job_index: int,
    expected_hour: int,
    expected_minute: int,
    expected_second: int,
) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    call_kwargs = scheduler.add_job.call_args_list[job_index]
    trigger: CronTrigger = call_kwargs.kwargs.get("trigger") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
    )
    assert isinstance(trigger, CronTrigger)

    # CronTrigger.fields 에서 field.name 으로 값 추출
    field_map = {f.name: f for f in trigger.fields}
    assert str(field_map["hour"]) == str(expected_hour)
    assert str(field_map["minute"]) == str(expected_minute)
    assert str(field_map["second"]) == str(expected_second)


def test_install_jobs_step_hour_range_9_14() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    # on_step 은 두 번째(index=1) add_job
    step_call = scheduler.add_job.call_args_list[1]
    trigger: CronTrigger = step_call.kwargs.get("trigger") or (
        step_call.args[1] if len(step_call.args) > 1 else None
    )
    assert isinstance(trigger, CronTrigger)
    field_map = {f.name: f for f in trigger.fields}
    assert str(field_map["hour"]) == "9-14"


def test_install_jobs_모두_mon_fri_Asia_Seoul() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    for idx, c in enumerate(scheduler.add_job.call_args_list):
        trigger: CronTrigger = c.kwargs.get("trigger") or (c.args[1] if len(c.args) > 1 else None)
        trigger_msg = f"job[{idx}] trigger 는 CronTrigger 여야 한다"
        assert isinstance(trigger, CronTrigger), trigger_msg
        tz_msg = f"job[{idx}] timezone 이 Asia/Seoul 이어야 한다"
        assert str(trigger.timezone) == "Asia/Seoul", tz_msg
        field_map = {f.name: f for f in trigger.fields}
        dow_msg = f"job[{idx}] day_of_week 이 mon-fri 여야 한다"
        assert str(field_map["day_of_week"]) == "mon-fri", dow_msg


# ---------------------------------------------------------------------------
# 6. _on_session_start 콜백 동작
# ---------------------------------------------------------------------------


def test_on_session_start_잔고보다_CLI자본_작으면_CLI값_사용(mocker: Any) -> None:
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=3_000_000)
    fake_executor = MagicMock(spec=Executor)
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor)
    args = _parse_args(["--starting-capital", "1000000"])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    fake_executor.start_session.assert_called_once_with(_DATE, 1_000_000)


def test_on_session_start_잔고보다_CLI자본_크면_잔고값_사용(mocker: Any) -> None:
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=2_000_000)
    fake_executor = MagicMock(spec=Executor)
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor)
    args = _parse_args(["--starting-capital", "3000000"])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    fake_executor.start_session.assert_called_once_with(_DATE, 2_000_000)


def test_on_session_start_잔고_0이면_start_session_미호출(mocker: Any) -> None:
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=0)
    fake_executor = MagicMock(spec=Executor)
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    fake_executor.start_session.assert_not_called()
    mock_logger.error.assert_called_once()


def test_on_session_start_예외발생시_reraise_안함(mocker: Any) -> None:
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.side_effect = RuntimeError("잔고 조회 실패")
    fake_executor = MagicMock(spec=Executor)
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()  # raise 하면 안 됨

    mock_logger.exception.assert_called_once()


# ---------------------------------------------------------------------------
# 7. _on_step 콜백 동작
# ---------------------------------------------------------------------------


def test_on_step_executor_step_호출(mocker: Any) -> None:
    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.return_value = _make_step_report()
    runtime = _make_runtime(executor=fake_executor)
    now = _kst(9, 5)
    clock = lambda: now  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()

    fake_executor.step.assert_called_once_with(now)


def test_on_step_예외발생시_reraise_안함(mocker: Any) -> None:
    from stock_agent.execution import ExecutorError

    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.side_effect = ExecutorError("step 실패")
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor)
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()  # raise 하면 안 됨

    mock_logger.exception.assert_called_once()


# ---------------------------------------------------------------------------
# 8. _on_force_close 콜백 동작
# ---------------------------------------------------------------------------


def test_on_force_close_executor_force_close_all_호출(mocker: Any) -> None:
    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.return_value = _make_step_report()
    runtime = _make_runtime(executor=fake_executor)
    now = _kst(15, 0)
    clock = lambda: now  # noqa: E731

    cb = _on_force_close(runtime, clock)
    cb()

    fake_executor.force_close_all.assert_called_once_with(now)


def test_on_force_close_예외발생시_logger_critical(mocker: Any) -> None:
    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.side_effect = RuntimeError("포지션 청산 실패")
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor)
    clock = lambda: _kst(15, 0)  # noqa: E731

    cb = _on_force_close(runtime, clock)
    cb()  # raise 하면 안 됨

    mock_logger.critical.assert_called_once()


# ---------------------------------------------------------------------------
# 9. _on_daily_report 콜백 동작
# ---------------------------------------------------------------------------


def test_on_daily_report_logger_info_최소_1회(mocker: Any) -> None:
    fake_executor = MagicMock(spec=Executor)
    fake_executor._risk_manager = MagicMock()
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor)
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    assert mock_logger.info.call_count >= 1


def test_on_daily_report_예외발생시_reraise_안함(mocker: Any) -> None:
    fake_executor = MagicMock(spec=Executor)
    fake_executor._risk_manager = MagicMock()
    # _risk_manager 접근 시 예외 유발
    type(fake_executor).is_halted = property(
        lambda self: (_ for _ in ()).throw(RuntimeError("리포트 실패"))
    )
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor)
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()  # raise 하면 안 됨

    mock_logger.exception.assert_called_once()


# ---------------------------------------------------------------------------
# 10. _graceful_shutdown 순서
# ---------------------------------------------------------------------------


def test_graceful_shutdown_순서_scheduler_rt_kis() -> None:
    call_order: list[str] = []

    fake_scheduler = MagicMock()
    fake_scheduler.shutdown.side_effect = lambda wait: call_order.append("scheduler.shutdown")

    fake_rt = MagicMock()
    fake_rt.close.side_effect = lambda: call_order.append("rt.close")

    fake_kis = MagicMock(spec=KisClient)
    fake_kis.close.side_effect = lambda: call_order.append("kis.close")

    runtime = _make_runtime(
        scheduler=fake_scheduler,
        realtime_store=fake_rt,
        kis_client=fake_kis,
    )

    _graceful_shutdown(runtime, SIGTERM, None)

    assert call_order == ["scheduler.shutdown", "rt.close", "kis.close"]


def test_graceful_shutdown_scheduler_shutdown_wait_False() -> None:
    fake_scheduler = MagicMock()
    runtime = _make_runtime(scheduler=fake_scheduler)

    _graceful_shutdown(runtime, SIGTERM, None)

    fake_scheduler.shutdown.assert_called_once_with(wait=False)


def test_graceful_shutdown_close_예외여도_다음_단계_진행(mocker: Any) -> None:
    call_order: list[str] = []

    fake_scheduler = MagicMock()
    fake_scheduler.shutdown.side_effect = lambda wait: call_order.append("scheduler")

    fake_rt = MagicMock()
    fake_rt.close.side_effect = RuntimeError("rt close 실패")

    fake_kis = MagicMock(spec=KisClient)
    fake_kis.close.side_effect = lambda: call_order.append("kis")

    mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(
        scheduler=fake_scheduler,
        realtime_store=fake_rt,
        kis_client=fake_kis,
    )

    _graceful_shutdown(runtime, SIGTERM, None)  # raise 하면 안 됨

    assert "kis" in call_order  # rt 실패해도 kis.close 호출됨


# ---------------------------------------------------------------------------
# 11. main() exit code 매핑
# ---------------------------------------------------------------------------


def _base_patches(mocker: Any) -> dict[str, MagicMock]:
    """main() 이 통과하기 위한 최소 patch 집합."""
    KospiUniverse(as_of_date=_DATE, source="t", tickers=("005930",))
    fake_settings = MagicMock()
    fake_settings.has_live_keys = True

    patches: dict[str, MagicMock] = {}
    patches["get_settings"] = mocker.patch(
        "stock_agent.main.get_settings", return_value=fake_settings
    )
    patches["configure_logging"] = mocker.patch("stock_agent.main._configure_logging")
    patches["build_runtime"] = mocker.patch("stock_agent.main.build_runtime")
    patches["signal_signal"] = mocker.patch("stock_agent.main.signal.signal")

    fake_rt = MagicMock()
    fake_kis = MagicMock(spec=KisClient)
    fake_scheduler = MagicMock()
    fake_executor = MagicMock(spec=Executor)

    fake_runtime = Runtime(
        scheduler=fake_scheduler,
        executor=fake_executor,
        realtime_store=fake_rt,
        kis_client=fake_kis,
        args=_parse_args([]),
    )
    patches["build_runtime"].return_value = fake_runtime

    return patches


def test_main_정상종료_EXIT_OK(mocker: Any) -> None:
    _base_patches(mocker)
    # scheduler.start() 즉시 반환 (mock 기본 동작)

    result = main([])

    assert result == EXIT_OK


def test_main_configure_logging_OSError_EXIT_IO_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)
    patches["configure_logging"].side_effect = OSError("로그 디렉토리 생성 실패")

    result = main([])

    assert result == EXIT_IO_ERROR


def test_main_get_settings_예외_EXIT_INPUT_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)
    patches["get_settings"].side_effect = RuntimeError("설정 로드 실패")

    result = main([])

    assert result == EXIT_INPUT_ERROR


def test_main_build_runtime_RuntimeError_EXIT_INPUT_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)
    patches["build_runtime"].side_effect = RuntimeError("has_live_keys=False")

    result = main([])

    assert result == EXIT_INPUT_ERROR


def test_main_build_runtime_UniverseLoadError_EXIT_INPUT_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)
    patches["build_runtime"].side_effect = UniverseLoadError("유니버스 로드 실패")

    result = main([])

    assert result == EXIT_INPUT_ERROR


def test_main_starting_capital_0이면_EXIT_INPUT_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)

    result = main(["--starting-capital", "0"])

    assert result == EXIT_INPUT_ERROR
    patches["build_runtime"].assert_not_called()  # build_runtime 전에 막아야 함


def test_main_starting_capital_음수_EXIT_INPUT_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)

    result = main(["--starting-capital", "-1"])

    assert result == EXIT_INPUT_ERROR
    patches["build_runtime"].assert_not_called()


def test_main_KeyboardInterrupt_EXIT_OK(mocker: Any) -> None:
    patches = _base_patches(mocker)
    patches["build_runtime"].return_value.scheduler.start.side_effect = KeyboardInterrupt

    result = main([])

    assert result == EXIT_OK


# ---------------------------------------------------------------------------
# 12. main() 리소스 정리 (finally 블록)
# ---------------------------------------------------------------------------


def test_main_정상종료시_realtime_close_kis_close_호출(mocker: Any) -> None:
    patches = _base_patches(mocker)
    runtime = patches["build_runtime"].return_value

    main([])

    runtime.realtime_store.close.assert_called_once()
    runtime.kis_client.close.assert_called_once()


def test_main_예외시에도_realtime_close_kis_close_호출(mocker: Any) -> None:
    patches = _base_patches(mocker)
    runtime = patches["build_runtime"].return_value
    runtime.scheduler.start.side_effect = RuntimeError("스케줄러 크래시")

    with contextlib.suppress(Exception):
        main([])

    runtime.realtime_store.close.assert_called_once()
    runtime.kis_client.close.assert_called_once()


def test_main_realtime_start_는_scheduler_start_전에_호출(mocker: Any) -> None:
    patches = _base_patches(mocker)
    runtime = patches["build_runtime"].return_value
    call_order: list[str] = []

    runtime.realtime_store.start.side_effect = lambda: call_order.append("rt.start")
    runtime.scheduler.start.side_effect = lambda: call_order.append("scheduler.start")

    main([])

    rt_idx = call_order.index("rt.start")
    sc_idx = call_order.index("scheduler.start")
    assert rt_idx < sc_idx, "realtime_store.start() 가 scheduler.start() 보다 먼저 호출되어야 한다"


# ---------------------------------------------------------------------------
# 상수 검증
# ---------------------------------------------------------------------------


def test_exit_code_상수_값() -> None:
    assert EXIT_OK == 0
    assert EXIT_UNEXPECTED == 1
    assert EXIT_INPUT_ERROR == 2
    assert EXIT_IO_ERROR == 3


def test_KST_상수_UTC_플러스_9() -> None:
    assert timezone(timedelta(hours=9)) == KST


def test_Runtime_frozen_dataclass() -> None:
    assert dataclasses.is_dataclass(Runtime)
    # frozen 이면 FrozenInstanceError 발생
    runtime = _make_runtime()
    with pytest.raises(dataclasses.FrozenInstanceError):
        runtime.scheduler = MagicMock()  # type: ignore[misc]
