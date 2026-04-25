"""SpreadSampleCollector — KIS 호가(orderbook) 1단계 스냅샷 어댑터.

ADR-0019 Phase 2 복구 로드맵 **Step B** (Issue #75) 인프라. 백테스트 슬리피지
0.1% 가정 (ADR-0006) 의 실측 검증을 위해, 실전 시세 도메인 KIS API 의
주식현재가 호가/예상체결 조회 (`/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn`,
TR `FHKST01010200`) 를 호출해 1단계 호가(bid1/ask1) + 잔량을 정규화한다.

설계 (`kis_minute_bars.py` 와 동일 기조)
- **실전(live) 키 전용**: `settings.has_live_keys=False` → 생성자에서
  `SpreadSampleCollectorError` fail-fast. paper 도메인엔 `/quotations/*` API 가 없다.
- **지연 PyKis 초기화**: 첫 `snapshot()` 호출 시 `pykis_factory` 호출 + PyKis
  인스턴스 생성. 직후 `install_order_block_guard` 설치 — 이 인스턴스로는 주문이
  절대 나가지 않도록 도메인 무관 차단.
- **`kis.fetch()` 로우레벨 직접 호출**: python-kis 2.1.6 가 본 API 를 래핑하지 않음.
- **EGW00201 rate limit 재시도**: `sleep(rate_limit_wait_s)` 후 최대
  `rate_limit_max_retries` 회. 그 외 응답 에러는 즉시 `SpreadSampleCollectorError`.
- **0/빈 문자열/역전 스프레드 흡수**: 실거래 응답에 흔한 노이즈 (거래정지 시 0,
  대량호가 빠짐 빈 문자열, 짧은 시점의 cross book) 는 `None` 반환으로 정상 흡수.
  운영자가 호출 측에서 "샘플 0건" 으로 인식하도록 한다.

비스코프 (의도적 defer)
- WebSocket 호가 스트림 (Phase 5 후보).
- 10단계 호가 전체 (`bidp2..bidp10`, `askp2..askp10`) — Step B 검증 목적상 1단계로
  충분 — 시장가 슬리피지 가정과 직접 비교.
- SQLite 캐시 — JSONL 누적이 더 단순하고 분석 용이 (수집 빈도 30s × 199 종목 ×
  6.5h × 5d ≒ 770만 행 = ≒ 1GB / 주, gzip 후 100~150MB).

테스트 정책
- 실 KIS 네트워크·실 pykis import 절대 금지. `pykis_factory` 에 `MagicMock`
  반환 람다 주입.
- 외부 I/O 0 — 본 모듈은 KIS API 호출만 책임지고 JSONL 직렬화는 호출 스크립트
  (`scripts/collect_spread_samples.py`) 가 담당.
"""

from __future__ import annotations

import re
import threading
import time as _time_mod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import TracebackType
from typing import Any

from loguru import logger

from stock_agent.config import Settings
from stock_agent.safety import install_order_block_guard

PyKisFactory = Callable[..., Any]
"""`PyKis` 생성자와 호환되는 팩토리 타입. 테스트는 `MagicMock` 반환 팩토리 주입."""

ClockFn = Callable[[], datetime]
"""현재 시각 제공자. 테스트 결정론화를 위해 주입. KST aware datetime 기대."""

SleepFn = Callable[[float], None]
"""슬립 함수. 레이트 리밋 대기에 사용. 테스트는 `MagicMock` 주입."""

KST = timezone(timedelta(hours=9))
_SYMBOL_RE = re.compile(r"^\d{6}$")
_API_PATH = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
_TR_ID = "FHKST01010200"
_RATE_LIMIT_MSG_CD = "EGW00201"


class SpreadSampleCollectorError(Exception):
    """KIS 호가 스냅샷 수집 실패 공통 예외. 원본 예외는 `__cause__` 로 보존."""


@dataclass(frozen=True, slots=True)
class SpreadSample:
    """1단계 호가 1회 스냅샷. KST aware ts 와 양수 가격을 강제한다.

    `spread_pct` 는 `(ask1 - bid1) / mid * 100` 형태로 호출자가 계산해 주입.
    `bid1 == ask1` (스프레드 0) 은 정상 케이스로 허용 — 호가 갭 없음.
    """

    symbol: str
    ts: datetime
    bid1: Decimal
    ask1: Decimal
    bid_qty1: int
    ask_qty1: int
    spread_pct: Decimal

    def __post_init__(self) -> None:
        if not _SYMBOL_RE.match(self.symbol):
            raise RuntimeError(f"symbol 은 6자리 숫자여야 합니다: {self.symbol!r}")
        if self.ts.tzinfo is None:
            raise RuntimeError("ts 는 tz-aware datetime 이어야 합니다.")
        if self.bid1 <= 0:
            raise RuntimeError(f"bid1 > 0 필요 (got={self.bid1})")
        if self.ask1 <= 0:
            raise RuntimeError(f"ask1 > 0 필요 (got={self.ask1})")
        if self.ask1 < self.bid1:
            raise RuntimeError(f"inverted spread: ask1 < bid1 (ask={self.ask1} bid={self.bid1})")
        if self.bid_qty1 < 0:
            raise RuntimeError(f"bid_qty1 >= 0 필요 (got={self.bid_qty1})")
        if self.ask_qty1 < 0:
            raise RuntimeError(f"ask_qty1 >= 0 필요 (got={self.ask_qty1})")


class SpreadSampleCollector:
    """KIS 호가 1회 조회기.

    공개 API: `snapshot(symbol) -> SpreadSample | None`, `close()` (멱등),
    컨텍스트 매니저.

    `RealtimeDataStore` 와 책임 경계 다름 — 본 클래스는 1회 단발성 호가 조회 전용.
    실시간 체결가 스트림은 `RealtimeDataStore` 가 담당.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        pykis_factory: PyKisFactory | None = None,
        clock: ClockFn | None = None,
        http_timeout_s: float = 10.0,
        rate_limit_wait_s: float = 61.0,
        rate_limit_max_retries: int = 3,
        sleep: SleepFn | None = None,
    ) -> None:
        """
        Args:
            settings: `.env` 에서 로드된 검증된 설정. `has_live_keys=True` 필수.
            pykis_factory: `PyKis` 호환 팩토리. `None` 이면 지연 import.
            clock: KST aware datetime 반환자. `None` 이면 `datetime.now(KST)`.
            http_timeout_s: HTTP timeout 힌트 (초). 음수 → `RuntimeError`.
            rate_limit_wait_s: EGW00201 수신 시 대기 (초). 음수 → `RuntimeError`.
            rate_limit_max_retries: 재시도 최대 횟수 (>= 1). 위반 → `RuntimeError`.
            sleep: 슬립 함수. `None` 이면 `time.sleep`.

        Raises:
            RuntimeError: 파라미터 범위 위반.
            SpreadSampleCollectorError: `has_live_keys=False`.
        """
        if http_timeout_s < 0:
            raise RuntimeError(f"http_timeout_s >= 0 필요 (got={http_timeout_s})")
        if rate_limit_wait_s < 0:
            raise RuntimeError(f"rate_limit_wait_s >= 0 필요 (got={rate_limit_wait_s})")
        if rate_limit_max_retries < 1:
            raise RuntimeError(f"rate_limit_max_retries >= 1 필요 (got={rate_limit_max_retries})")
        if not settings.has_live_keys:
            raise SpreadSampleCollectorError(
                "SpreadSampleCollector 는 실전 APP_KEY 3종 (KIS_LIVE_APP_KEY · "
                "KIS_LIVE_APP_SECRET · KIS_LIVE_ACCOUNT_NO) 이 필요합니다. "
                "paper 도메인에는 시세 API(`/quotations/*`) 가 없어 실전 도메인을 "
                "호출해야 하며, paper 키로는 real 도메인 인증이 거부됩니다(EGW02004)."
            )

        self._settings = settings
        self._pykis_factory = pykis_factory
        self._clock: ClockFn = clock or (lambda: datetime.now(KST))
        self._http_timeout_s = http_timeout_s
        self._rate_limit_wait_s = rate_limit_wait_s
        self._rate_limit_max_retries = rate_limit_max_retries
        self._sleep: SleepFn = sleep or _time_mod.sleep

        self._kis: Any | None = None
        self._closed = False
        self._lock = threading.Lock()

    def snapshot(self, symbol: str) -> SpreadSample | None:
        """`symbol` 의 1단계 호가를 1회 조회.

        반환값:
            `SpreadSample`: 정상 응답 + 양수 호가 + 정상 스프레드.
            `None`: 거래정지(0)·대량호가 빠짐(빈 문자열)·역전(`bid1 > ask1`)·
                파싱 불가. 호출자는 "샘플 0건" 으로 인지하면 됨.

        Raises:
            RuntimeError: 이미 close · 심볼 포맷 위반.
            SpreadSampleCollectorError: KIS API 응답 에러 (rate limit 한도 초과 포함) ·
                응답 dict 아님 · output1 누락/타입 위반 · fetch 호출 자체 실패.
        """
        if self._closed:
            raise RuntimeError("SpreadSampleCollector 는 이미 close() 되었습니다.")
        if not _SYMBOL_RE.match(symbol):
            raise RuntimeError(f"symbol 은 6자리 숫자여야 합니다: {symbol!r}")

        kis = self._ensure_kis()
        response_dict = self._fetch_with_retry(kis, symbol)

        output1 = response_dict.get("output1")
        if not isinstance(output1, dict):
            raise SpreadSampleCollectorError(
                f"output1 누락 또는 dict 가 아닙니다: type={type(output1).__name__} symbol={symbol}"
            )

        bid1 = _parse_decimal(output1.get("bidp1"))
        ask1 = _parse_decimal(output1.get("askp1"))
        if bid1 is None or ask1 is None:
            return None
        if bid1 <= 0 or ask1 <= 0:
            return None
        if ask1 < bid1:
            logger.warning(f"호가 역전 감지 — symbol={symbol} bid1={bid1} ask1={ask1} (None 반환)")
            return None

        bid_qty1 = _parse_int(output1.get("bidp_rsqn1")) or 0
        ask_qty1 = _parse_int(output1.get("askp_rsqn1")) or 0
        spread_pct = (ask1 - bid1) / ((ask1 + bid1) / Decimal(2)) * Decimal(100)

        return SpreadSample(
            symbol=symbol,
            ts=self._clock(),
            bid1=bid1,
            ask1=ask1,
            bid_qty1=bid_qty1,
            ask_qty1=ask_qty1,
            spread_pct=spread_pct,
        )

    def close(self) -> None:
        """리소스 정리. 멱등."""
        if self._closed:
            return
        self._closed = True

    def __enter__(self) -> SpreadSampleCollector:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- 내부 ---------------------------------------------------------

    def _ensure_kis(self) -> Any:
        """지연 초기화된 실전 키 PyKis 인스턴스 반환. 최초 호출 시 가드 설치."""
        with self._lock:
            if self._kis is not None:
                return self._kis
            factory = self._pykis_factory
            if factory is None:
                from pykis import PyKis  # noqa: PLC0415

                factory = PyKis
            assert self._settings.kis_live_app_key is not None
            assert self._settings.kis_live_app_secret is not None
            assert self._settings.kis_live_account_no is not None
            live_appkey = self._settings.kis_live_app_key.get_secret_value()
            live_secret = self._settings.kis_live_app_secret.get_secret_value()
            try:
                kis = factory(
                    id=self._settings.kis_hts_id,
                    account=self._settings.kis_live_account_no,
                    appkey=live_appkey,
                    secretkey=live_secret,
                    keep_token=True,
                )
            except Exception as exc:
                raise SpreadSampleCollectorError("PyKis 실전 인스턴스 생성 실패") from exc
            install_order_block_guard(kis)
            self._kis = kis
            return kis

    def _fetch_with_retry(self, kis: Any, symbol: str) -> dict[str, Any]:
        """`kis.fetch` 호출 + EGW00201 rate limit 자동 재시도."""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
        }
        max_attempts = self._rate_limit_max_retries + 1
        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            try:
                response = kis.fetch(
                    _API_PATH,
                    api=_TR_ID,
                    params=params,
                    domain="real",
                )
            except SpreadSampleCollectorError:
                raise
            except RuntimeError:
                raise
            except Exception as exc:
                raise SpreadSampleCollectorError(f"KIS fetch 호출 실패: symbol={symbol}") from exc

            response_data = getattr(response, "__data__", None)
            if isinstance(response_data, dict):
                response_dict: dict[str, Any] = response_data
            elif isinstance(response, dict):
                response_dict = response
            else:
                raise SpreadSampleCollectorError(
                    f"KIS 응답이 dict 가 아닙니다: type={type(response).__name__} symbol={symbol}"
                )

            rt_cd = response_dict.get("rt_cd", "0")
            msg_cd = response_dict.get("msg_cd", "")
            if rt_cd == "0":
                return response_dict
            if msg_cd == _RATE_LIMIT_MSG_CD:
                if attempts >= max_attempts:
                    break
                logger.warning(
                    f"KIS rate limit (EGW00201) — {self._rate_limit_wait_s}s 대기 후 "
                    f"재시도 ({attempts}/{self._rate_limit_max_retries}) symbol={symbol}"
                )
                self._sleep(self._rate_limit_wait_s)
                continue
            msg = response_dict.get("msg1", "")
            raise SpreadSampleCollectorError(
                f"KIS API 에러 rt_cd={rt_cd} msg_cd={msg_cd} msg={msg!r} symbol={symbol}"
            )

        raise SpreadSampleCollectorError(
            f"KIS rate limit 재시도 한도 초과 "
            f"(max_retries={self._rate_limit_max_retries}, symbol={symbol})"
        )


# ---- 모듈 수준 순수 함수 ----------------------------------------------


def _parse_decimal(raw: Any) -> Decimal | None:
    """KIS 응답 가격 문자열을 `Decimal` 로 안전 변환. 실패·빈문자 → `None`."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return d


def _parse_int(raw: Any) -> int | None:
    """KIS 응답 잔량 문자열을 `int` 로 안전 변환. 실패·빈문자 → `None`."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(Decimal(s))
    except (InvalidOperation, ValueError):
        return None
