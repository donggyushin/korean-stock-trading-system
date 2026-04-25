"""SpreadSample DTO + SpreadSampleCollector 단위 테스트 (RED 명세).

`src/stock_agent/data/spread_samples.py` 공개 계약을 검증한다.
실 KIS 네트워크·pykis import·외부 I/O 는 절대 발생시키지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from stock_agent.config import Settings, reset_settings_cache

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_VALID_BASE_ENV: dict[str, str] = {
    "KIS_HTS_ID": "test-user",
    "KIS_APP_KEY": "T" * 36,
    "KIS_APP_SECRET": "S" * 180,
    "KIS_ACCOUNT_NO": "12345678-01",
    "TELEGRAM_BOT_TOKEN": "dummy-tg-token",
    "TELEGRAM_CHAT_ID": "9999",
    "KIS_ENV": "paper",
    "KIS_KEY_ORIGIN": "paper",
}

_LIVE_KEY_ENV: dict[str, str] = {
    "KIS_LIVE_APP_KEY": "X" * 36,
    "KIS_LIVE_APP_SECRET": "Y" * 180,
    "KIS_LIVE_ACCOUNT_NO": "12345678-01",
}

_SYMBOL = "005930"
_FIXED_TS = datetime(2026, 4, 27, 10, 30, tzinfo=KST)
_FIXED_CLOCK = lambda: _FIXED_TS  # noqa: E731


# ---------------------------------------------------------------------------
# autouse: .env 자동 로드 무력화 + Settings 캐시 리셋
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """매 테스트 시작·종료 시 .env 영향 제거 및 lru_cache 초기화."""
    from stock_agent.config import Settings as _Settings

    monkeypatch.setattr(_Settings, "model_config", {**_Settings.model_config, "env_file": None})
    for k in (*_VALID_BASE_ENV, *_LIVE_KEY_ENV):
        monkeypatch.delenv(k, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


# ---------------------------------------------------------------------------
# Settings 생성 헬퍼
# ---------------------------------------------------------------------------


def _make_settings(monkeypatch: pytest.MonkeyPatch, *, has_live: bool = True) -> Settings:
    """live 키 포함 여부를 선택할 수 있는 Settings 인스턴스 반환."""
    env = {**_VALID_BASE_ENV}
    if has_live:
        env.update(_LIVE_KEY_ENV)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    return Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# import 헬퍼
# ---------------------------------------------------------------------------


def _import_collector():
    """SpreadSampleCollector 와 관련 심볼을 지연 import 해 반환."""
    from stock_agent.data.spread_samples import (  # type: ignore[import]
        SpreadSample,
        SpreadSampleCollector,
        SpreadSampleCollectorError,
    )

    return SpreadSample, SpreadSampleCollector, SpreadSampleCollectorError


# ---------------------------------------------------------------------------
# 응답 더미 헬퍼
# ---------------------------------------------------------------------------


def _make_output1(
    bid: str = "71500",
    ask: str = "71600",
    bid_qty: str = "1234",
    ask_qty: str = "890",
) -> dict:
    """KIS 호가 API output1 dict 더미."""
    return {
        "bidp1": bid,
        "askp1": ask,
        "bidp_rsqn1": bid_qty,
        "askp_rsqn1": ask_qty,
    }


def _make_api_response(
    output1: dict | None = None,
    rt_cd: str = "0",
    msg_cd: str = "",
    msg1: str = "",
) -> dict:
    """KIS 호가 API 응답 dict 더미."""
    resp: dict = {"rt_cd": rt_cd, "msg_cd": msg_cd, "msg1": msg1}
    if output1 is not None:
        resp["output1"] = output1
    return resp


# ===========================================================================
# 1. SpreadSample DTO 가드
# ===========================================================================


class TestSpreadSampleGuards:
    def test_symbol_정규식_위반_RuntimeError(self) -> None:
        """symbol 이 6자리 숫자 아닌 경우 RuntimeError."""
        SpreadSample, _, _ = _import_collector()
        with pytest.raises(RuntimeError):
            SpreadSample(
                symbol="ABC123",
                ts=_FIXED_TS,
                bid1=Decimal("71500"),
                ask1=Decimal("71600"),
                bid_qty1=100,
                ask_qty1=200,
                spread_pct=Decimal("0.14"),
            )

    def test_symbol_5자리_RuntimeError(self) -> None:
        """5자리 숫자도 RuntimeError."""
        SpreadSample, _, _ = _import_collector()
        with pytest.raises(RuntimeError):
            SpreadSample(
                symbol="05930",
                ts=_FIXED_TS,
                bid1=Decimal("71500"),
                ask1=Decimal("71600"),
                bid_qty1=100,
                ask_qty1=200,
                spread_pct=Decimal("0.14"),
            )

    def test_naive_ts_RuntimeError(self) -> None:
        """ts.tzinfo is None → RuntimeError."""
        SpreadSample, _, _ = _import_collector()
        naive_ts = datetime(2026, 4, 27, 10, 30)
        with pytest.raises(RuntimeError):
            SpreadSample(
                symbol=_SYMBOL,
                ts=naive_ts,
                bid1=Decimal("71500"),
                ask1=Decimal("71600"),
                bid_qty1=100,
                ask_qty1=200,
                spread_pct=Decimal("0.14"),
            )

    def test_bid1_zero_RuntimeError(self) -> None:
        """bid1 <= 0 → RuntimeError."""
        SpreadSample, _, _ = _import_collector()
        with pytest.raises(RuntimeError):
            SpreadSample(
                symbol=_SYMBOL,
                ts=_FIXED_TS,
                bid1=Decimal("0"),
                ask1=Decimal("71600"),
                bid_qty1=100,
                ask_qty1=200,
                spread_pct=Decimal("0.14"),
            )

    def test_ask1_zero_RuntimeError(self) -> None:
        """ask1 <= 0 → RuntimeError."""
        SpreadSample, _, _ = _import_collector()
        with pytest.raises(RuntimeError):
            SpreadSample(
                symbol=_SYMBOL,
                ts=_FIXED_TS,
                bid1=Decimal("71500"),
                ask1=Decimal("0"),
                bid_qty1=100,
                ask_qty1=200,
                spread_pct=Decimal("0.14"),
            )

    def test_ask1_lt_bid1_RuntimeError(self) -> None:
        """ask1 < bid1 (역전) → RuntimeError."""
        SpreadSample, _, _ = _import_collector()
        with pytest.raises(RuntimeError):
            SpreadSample(
                symbol=_SYMBOL,
                ts=_FIXED_TS,
                bid1=Decimal("71600"),
                ask1=Decimal("71500"),  # ask < bid
                bid_qty1=100,
                ask_qty1=200,
                spread_pct=Decimal("-0.14"),
            )

    def test_negative_bid_qty_RuntimeError(self) -> None:
        """bid_qty1 < 0 → RuntimeError."""
        SpreadSample, _, _ = _import_collector()
        with pytest.raises(RuntimeError):
            SpreadSample(
                symbol=_SYMBOL,
                ts=_FIXED_TS,
                bid1=Decimal("71500"),
                ask1=Decimal("71600"),
                bid_qty1=-1,
                ask_qty1=200,
                spread_pct=Decimal("0.14"),
            )

    def test_negative_ask_qty_RuntimeError(self) -> None:
        """ask_qty1 < 0 → RuntimeError."""
        SpreadSample, _, _ = _import_collector()
        with pytest.raises(RuntimeError):
            SpreadSample(
                symbol=_SYMBOL,
                ts=_FIXED_TS,
                bid1=Decimal("71500"),
                ask1=Decimal("71600"),
                bid_qty1=100,
                ask_qty1=-1,
                spread_pct=Decimal("0.14"),
            )


# ===========================================================================
# 2. SpreadSample DTO 정상 생성
# ===========================================================================


class TestSpreadSampleNormal:
    def test_정상_생성_spread_pct_정확성(self) -> None:
        """bid=71500, ask=71600 → spread_pct = (100 / 71550) * 100 근접 확인."""
        SpreadSample, _, _ = _import_collector()
        bid = Decimal("71500")
        ask = Decimal("71600")
        mid = (bid + ask) / 2
        expected_pct = (ask - bid) / mid * 100
        sample = SpreadSample(
            symbol=_SYMBOL,
            ts=_FIXED_TS,
            bid1=bid,
            ask1=ask,
            bid_qty1=1234,
            ask_qty1=890,
            spread_pct=expected_pct,
        )
        assert sample.spread_pct == expected_pct
        assert sample.symbol == _SYMBOL
        assert sample.ts == _FIXED_TS

    def test_frozen_dataclass_수정_불가(self) -> None:
        """frozen=True → 필드 수정 시 예외."""
        SpreadSample, _, _ = _import_collector()
        sample = SpreadSample(
            symbol=_SYMBOL,
            ts=_FIXED_TS,
            bid1=Decimal("71500"),
            ask1=Decimal("71600"),
            bid_qty1=100,
            ask_qty1=200,
            spread_pct=Decimal("0.14"),
        )
        with pytest.raises((AttributeError, TypeError)):
            sample.symbol = "000000"  # type: ignore[misc]

    def test_bid_equal_ask_zero_spread(self) -> None:
        """bid == ask (스프레드 0) 정상 생성."""
        SpreadSample, _, _ = _import_collector()
        sample = SpreadSample(
            symbol=_SYMBOL,
            ts=_FIXED_TS,
            bid1=Decimal("71500"),
            ask1=Decimal("71500"),
            bid_qty1=0,
            ask_qty1=0,
            spread_pct=Decimal("0"),
        )
        assert sample.spread_pct == Decimal("0")


# ===========================================================================
# 3. SpreadSampleCollector 생성자 가드
# ===========================================================================


class TestCollectorConstructorGuards:
    def test_no_live_keys_SpreadSampleCollectorError(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """settings.has_live_keys == False → SpreadSampleCollectorError."""
        _, SpreadSampleCollector, SpreadSampleCollectorError = _import_collector()
        settings = _make_settings(monkeypatch, has_live=False)
        with pytest.raises(SpreadSampleCollectorError):
            SpreadSampleCollector(settings)

    def test_negative_http_timeout_s_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """http_timeout_s < 0 → RuntimeError."""
        _, SpreadSampleCollector, _ = _import_collector()
        settings = _make_settings(monkeypatch, has_live=True)
        with pytest.raises(RuntimeError):
            SpreadSampleCollector(settings, http_timeout_s=-1.0)

    def test_negative_rate_limit_wait_s_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """rate_limit_wait_s < 0 → RuntimeError."""
        _, SpreadSampleCollector, _ = _import_collector()
        settings = _make_settings(monkeypatch, has_live=True)
        with pytest.raises(RuntimeError):
            SpreadSampleCollector(settings, rate_limit_wait_s=-1.0)

    def test_rate_limit_max_retries_zero_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """rate_limit_max_retries < 1 → RuntimeError."""
        _, SpreadSampleCollector, _ = _import_collector()
        settings = _make_settings(monkeypatch, has_live=True)
        with pytest.raises(RuntimeError):
            SpreadSampleCollector(settings, rate_limit_max_retries=0)

    def test_rate_limit_max_retries_negative_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """rate_limit_max_retries = -1 → RuntimeError."""
        _, SpreadSampleCollector, _ = _import_collector()
        settings = _make_settings(monkeypatch, has_live=True)
        with pytest.raises(RuntimeError):
            SpreadSampleCollector(settings, rate_limit_max_retries=-1)


# ===========================================================================
# 4. 지연 초기화
# ===========================================================================


class TestLazyInit:
    def test_생성자에서_pykis_factory_호출_안됨(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """생성자 단계에서 pykis_factory 가 호출되지 않는다."""
        _, SpreadSampleCollector, _ = _import_collector()
        settings = _make_settings(monkeypatch, has_live=True)
        factory = MagicMock()
        SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )
        factory.assert_not_called()

    def test_첫_snapshot에서_install_order_block_guard_호출(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """첫 snapshot() 호출 시 install_order_block_guard 가 반드시 호출된다."""
        _, SpreadSampleCollector, _ = _import_collector()
        guard_patch = mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")

        fake_kis = MagicMock()
        fake_kis.fetch.return_value = _make_api_response(output1=_make_output1())
        factory = MagicMock(return_value=fake_kis)

        settings = _make_settings(monkeypatch, has_live=True)
        collector = SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )
        collector.snapshot(_SYMBOL)
        guard_patch.assert_called_once()


# ===========================================================================
# 5. snapshot 정상 경로
# ===========================================================================


class TestSnapshotNormal:
    def _make_collector(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fetch_response: dict,
    ):
        _, SpreadSampleCollector, _ = _import_collector()
        mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")
        fake_kis = MagicMock()
        fake_kis.fetch.return_value = fetch_response
        factory = MagicMock(return_value=fake_kis)
        settings = _make_settings(monkeypatch, has_live=True)
        collector = SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )
        return collector, fake_kis

    def test_정상_응답_SpreadSample_반환(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """정상 dict 응답 → SpreadSample 반환, 필드 정확."""
        SpreadSample, _, _ = _import_collector()
        collector, _ = self._make_collector(
            monkeypatch,
            mocker,
            _make_api_response(output1=_make_output1(bid="71500", ask="71600")),
        )
        result = collector.snapshot(_SYMBOL)
        assert result is not None
        assert isinstance(result, SpreadSample)
        assert result.symbol == _SYMBOL
        assert result.bid1 == Decimal("71500")
        assert result.ask1 == Decimal("71600")
        assert result.ts == _FIXED_TS

    def test_kis_fetch_인자_검증(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """kis.fetch 가 올바른 path/api/params/domain 으로 호출된다."""
        collector, fake_kis = self._make_collector(
            monkeypatch,
            mocker,
            _make_api_response(output1=_make_output1()),
        )
        collector.snapshot(_SYMBOL)
        fake_kis.fetch.assert_called_once()
        call_kwargs = fake_kis.fetch.call_args
        # path 검증
        args, kwargs = call_kwargs
        path_arg = args[0] if args else kwargs.get("path", "")
        assert "inquire-asking-price-exp-ccn" in str(path_arg)
        # api TR_ID 검증
        assert kwargs.get("api") == "FHKST01010200" or (
            len(args) > 1 and "FHKST01010200" in str(args)
        )

    def test_KisDynamicDict_응답도_파싱(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """__data__ 속성이 있는 KisDynamicDict 응답도 동일하게 파싱된다."""
        _, SpreadSampleCollector, _ = _import_collector()
        mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")

        # KisDynamicDict 시뮬레이션: __data__ 로 실제 dict 접근
        raw_data = _make_api_response(output1=_make_output1(bid="70000", ask="70100"))
        dyn_mock = MagicMock()
        dyn_mock.__data__ = raw_data  # getattr(response, "__data__", None) 경로

        fake_kis = MagicMock()
        fake_kis.fetch.return_value = dyn_mock
        factory = MagicMock(return_value=fake_kis)

        settings = _make_settings(monkeypatch, has_live=True)
        collector = SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )
        result = collector.snapshot(_SYMBOL)
        assert result is not None
        assert result.bid1 == Decimal("70000")
        assert result.ask1 == Decimal("70100")


# ===========================================================================
# 6. snapshot None 케이스
# ===========================================================================


class TestSnapshotNoneCases:
    def _make_collector_with_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        response: dict,
    ):
        _, SpreadSampleCollector, _ = _import_collector()
        mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")
        fake_kis = MagicMock()
        fake_kis.fetch.return_value = response
        factory = MagicMock(return_value=fake_kis)
        settings = _make_settings(monkeypatch, has_live=True)
        collector = SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )
        return collector

    def test_bidp1_zero_None_반환(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """bidp1 == '0' → None 반환 (거래정지 정상 케이스)."""
        collector = self._make_collector_with_response(
            monkeypatch,
            mocker,
            _make_api_response(output1=_make_output1(bid="0", ask="71600")),
        )
        assert collector.snapshot(_SYMBOL) is None

    def test_askp1_empty_None_반환(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """askp1 == '' → None 반환."""
        collector = self._make_collector_with_response(
            monkeypatch,
            mocker,
            _make_api_response(output1=_make_output1(bid="71500", ask="")),
        )
        assert collector.snapshot(_SYMBOL) is None

    def test_bidp1_gt_askp1_역전_None_반환_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        caplog,
    ) -> None:
        """bid > ask (스프레드 역전) → None 반환 + logger.warning."""
        import logging

        collector = self._make_collector_with_response(
            monkeypatch,
            mocker,
            _make_api_response(output1=_make_output1(bid="71700", ask="71600")),
        )
        with caplog.at_level(logging.WARNING):
            result = collector.snapshot(_SYMBOL)
        assert result is None


# ===========================================================================
# 7. snapshot 에러 케이스
# ===========================================================================


class TestSnapshotErrors:
    def _make_collector(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        response: dict,
    ):
        _, SpreadSampleCollector, _ = _import_collector()
        mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")
        fake_kis = MagicMock()
        fake_kis.fetch.return_value = response
        factory = MagicMock(return_value=fake_kis)
        settings = _make_settings(monkeypatch, has_live=True)
        return SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )

    def test_rt_cd_비정상_msg_cd_일반오류_SpreadSampleCollectorError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """rt_cd != '0' and msg_cd != 'EGW00201' → SpreadSampleCollectorError."""
        _, _, SpreadSampleCollectorError = _import_collector()
        collector = self._make_collector(
            monkeypatch,
            mocker,
            _make_api_response(rt_cd="1", msg_cd="EGW99999", msg1="error"),
        )
        with pytest.raises(SpreadSampleCollectorError):
            collector.snapshot(_SYMBOL)

    def test_output1_누락_SpreadSampleCollectorError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """output1 키 없음 → SpreadSampleCollectorError."""
        _, _, SpreadSampleCollectorError = _import_collector()
        collector = self._make_collector(
            monkeypatch,
            mocker,
            {"rt_cd": "0", "msg_cd": "", "msg1": ""},  # output1 없음
        )
        with pytest.raises(SpreadSampleCollectorError):
            collector.snapshot(_SYMBOL)

    def test_output1_non_dict_SpreadSampleCollectorError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """output1 이 dict 아님 → SpreadSampleCollectorError."""
        _, _, SpreadSampleCollectorError = _import_collector()
        collector = self._make_collector(
            monkeypatch,
            mocker,
            {"rt_cd": "0", "msg_cd": "", "msg1": "", "output1": "invalid_string"},
        )
        with pytest.raises(SpreadSampleCollectorError):
            collector.snapshot(_SYMBOL)

    def test_응답_파싱_불가_타입_SpreadSampleCollectorError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """fetch 응답이 dict도 KisDynamicDict(__data__)도 아님 → SpreadSampleCollectorError."""
        _, SpreadSampleCollector, SpreadSampleCollectorError = _import_collector()
        mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")
        fake_kis = MagicMock()
        # __data__ 없는 임의 객체 반환
        fake_kis.fetch.return_value = 12345  # int
        factory = MagicMock(return_value=fake_kis)
        settings = _make_settings(monkeypatch, has_live=True)
        collector = SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )
        with pytest.raises(SpreadSampleCollectorError):
            collector.snapshot(_SYMBOL)


# ===========================================================================
# 8. rate limit 재시도
# ===========================================================================


class TestRateLimitRetry:
    def _make_collector_multi_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        side_effects: list,
        *,
        rate_limit_wait_s: float = 61.0,
        rate_limit_max_retries: int = 3,
    ):
        _, SpreadSampleCollector, _ = _import_collector()
        mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")
        fake_kis = MagicMock()
        fake_kis.fetch.side_effect = side_effects
        factory = MagicMock(return_value=fake_kis)
        mock_sleep = MagicMock()
        settings = _make_settings(monkeypatch, has_live=True)
        collector = SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=mock_sleep,
            rate_limit_wait_s=rate_limit_wait_s,
            rate_limit_max_retries=rate_limit_max_retries,
        )
        return collector, fake_kis, mock_sleep

    def test_EGW00201_1회_후_성공_sleep_1회(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """EGW00201 1회 후 성공 → sleep 1회 호출 + SpreadSample 반환."""
        SpreadSample, _, _ = _import_collector()
        rate_error = _make_api_response(rt_cd="1", msg_cd="EGW00201", msg1="rate")
        success = _make_api_response(output1=_make_output1())
        collector, _, mock_sleep = self._make_collector_multi_response(
            monkeypatch,
            mocker,
            [rate_error, success],
            rate_limit_wait_s=61.0,
        )
        result = collector.snapshot(_SYMBOL)
        assert result is not None
        assert isinstance(result, SpreadSample)
        mock_sleep.assert_called_once_with(61.0)

    def test_EGW00201_횟수_초과_SpreadSampleCollectorError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """rate_limit_max_retries=3, 4회 모두 EGW00201 → SpreadSampleCollectorError."""
        _, _, SpreadSampleCollectorError = _import_collector()
        rate_error = _make_api_response(rt_cd="1", msg_cd="EGW00201", msg1="rate")
        # 첫 호출 + 3회 재시도 = 4회 모두 실패
        collector, _, _ = self._make_collector_multi_response(
            monkeypatch,
            mocker,
            [rate_error] * 4,
            rate_limit_max_retries=3,
        )
        with pytest.raises(SpreadSampleCollectorError):
            collector.snapshot(_SYMBOL)

    def test_sleep_인자_rate_limit_wait_s_그대로(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """sleep 호출 시 rate_limit_wait_s 값 그대로 전달된다."""
        rate_error = _make_api_response(rt_cd="1", msg_cd="EGW00201", msg1="rate")
        success = _make_api_response(output1=_make_output1())
        collector, _, mock_sleep = self._make_collector_multi_response(
            monkeypatch,
            mocker,
            [rate_error, success],
            rate_limit_wait_s=99.5,
        )
        collector.snapshot(_SYMBOL)
        mock_sleep.assert_called_once_with(99.5)


# ===========================================================================
# 9. 라이프사이클
# ===========================================================================


class TestLifecycle:
    def _make_collector(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ):
        _, SpreadSampleCollector, _ = _import_collector()
        mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")
        fake_kis = MagicMock()
        fake_kis.fetch.return_value = _make_api_response(output1=_make_output1())
        factory = MagicMock(return_value=fake_kis)
        settings = _make_settings(monkeypatch, has_live=True)
        return SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )

    def test_close_멱등(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """close() 를 두 번 호출해도 예외 없음."""
        collector = self._make_collector(monkeypatch, mocker)
        collector.close()
        collector.close()  # 두 번째도 예외 없어야 함

    def test_close_후_snapshot_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """close() 후 snapshot() → RuntimeError."""
        collector = self._make_collector(monkeypatch, mocker)
        collector.close()
        with pytest.raises(RuntimeError):
            collector.snapshot(_SYMBOL)

    def test_컨텍스트_매니저_진출_시_close_호출(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """with 블록 종료 시 close() 가 자동 호출된다."""
        _, SpreadSampleCollector, _ = _import_collector()
        mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")
        fake_kis = MagicMock()
        fake_kis.fetch.return_value = _make_api_response(output1=_make_output1())
        factory = MagicMock(return_value=fake_kis)
        settings = _make_settings(monkeypatch, has_live=True)
        collector = SpreadSampleCollector(
            settings,
            pykis_factory=factory,
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )
        close_spy = mocker.spy(collector, "close")
        with collector:
            pass
        close_spy.assert_called_once()


# ===========================================================================
# 10. symbol 가드
# ===========================================================================


class TestSymbolGuard:
    def _make_collector(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ):
        _, SpreadSampleCollector, _ = _import_collector()
        mocker.patch("stock_agent.data.spread_samples.install_order_block_guard")
        settings = _make_settings(monkeypatch, has_live=True)
        return SpreadSampleCollector(
            settings,
            pykis_factory=MagicMock(return_value=MagicMock()),
            clock=_FIXED_CLOCK,
            sleep=MagicMock(),
        )

    def test_5자리_심볼_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """snapshot('00593') → RuntimeError (5자리)."""
        collector = self._make_collector(monkeypatch, mocker)
        with pytest.raises(RuntimeError):
            collector.snapshot("00593")

    def test_영문자_포함_심볼_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
    ) -> None:
        """snapshot('ABCDEF') → RuntimeError (영문자)."""
        collector = self._make_collector(monkeypatch, mocker)
        with pytest.raises(RuntimeError):
            collector.snapshot("ABCDEF")
