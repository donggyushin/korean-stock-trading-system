"""SQLite 원장 — EntryEvent / ExitEvent / DailySummary 영속화.

책임 범위
- Executor 가 방출한 `EntryEvent`·`ExitEvent` 를 `orders` 테이블에 1행 1이벤트로
  append.
- `main._on_daily_report` 가 조립한 `DailySummary` 를 `daily_pnl` 테이블에
  session_date PK `INSERT OR REPLACE` 로 기록.

범위 제외 (의도적 defer)
- 주간 회고 리포트 CLI (`scripts/weekly_report.py` 등) — MVP 는 SQL 직접 쿼리.
- KIS 체결조회 API 통합 — 실체결가 정확도 향상은 별도 PR. 현재는
  `backtest/costs.py` 산식으로 추정한 체결가를 기록.
- PostgreSQL 전환 — plan.md:71 "추후" 영역.
- 부분체결 기록 — Executor 가 즉시 전량 체결 가정이라 모델링 불필요.
- 스키마 마이그레이션 프레임워크 — v1 초기 릴리스이므로 `schema_version`
  테이블 + 분기 훅만.

공개 API
- `TradingRecorder` (Protocol, @runtime_checkable) — notifier 와 동일 기조의
  의존성 역전 경계. `Executor` 는 이 타입을 **모른다** — `main.py` 콜백이
  `StepReport.entry_events`·`exit_events` 를 순회하며 notifier 와 나란히 호출.
- `SqliteTradingRecorder` — 단일 파일(기본 `data/trading.db`), WAL 저널,
  autocommit + `BEGIN IMMEDIATE` 는 스키마 init 한정.
- `NullTradingRecorder` — 생성자 실패 폴백 (notifier 의 `NullNotifier` 와 동일
  기조 — 부분 기능 손실 > 세션 전체 실패).
- `StorageError` — 스키마 init 실패·치명적 초기화 실패 래퍼 (`__cause__` 보존).

실패 정책 (notifier.py `_record_failure` 패턴 재사용)
- `record_*` 메서드 내부의 `sqlite3.Error` 는 raise 하지 않고 silent (매매
  루프 보호). 연속 실패 카운터 증가 + 매회 `logger.warning`. 연속 실패가
  `consecutive_failure_threshold` (기본 5) 에 도달하면 `logger.critical` 1회
  만 방출 (dedupe). 성공 1회 시 카운터·플래그 모두 리셋 → 다음 연속 실패가
  다시 threshold 에 도달하면 critical 재방출.
- 생성자 내부의 스키마 init 실패는 `StorageError` 로 raise — 폴백
  (`NullTradingRecorder`) 선택은 호출자(main.py `_default_recorder_factory`)
  책임.
- `close()` 이후 `record_*` 호출은 warning 1회 + silent, 카운터 불변 (세션
  종료 내구성).

스키마 v1 (3 테이블 + 2 인덱스 + schema_version)
- `orders`: 모든 매수·매도 체결을 `order_number` PK 로 append.
- `daily_pnl`: `session_date` PK, 재실행 시 `INSERT OR REPLACE`.
- `schema_version`: 현재 버전 v1. 향후 마이그레이션 분기 진입점.

PRAGMA (파일 기반만)
- `journal_mode = WAL` (동시 읽기 + append 쓰기 성능)
- `synchronous = NORMAL`
- `foreign_keys = ON`

스레드 모델
- 단일 프로세스·단일 caller 전용 (broker/strategy/risk/data/execution 와
  동일). 동시 호출 금지.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import Protocol, runtime_checkable

from loguru import logger

from stock_agent.execution import EntryEvent, ExitEvent
from stock_agent.monitor import DailySummary

KST = timezone(timedelta(hours=9))

_DEFAULT_DB_PATH = Path("data/trading.db")
_SCHEMA_VERSION = 1
_DEFAULT_FAILURE_THRESHOLD = 5


_CREATE_SCHEMA_VERSION_SQL = """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    )
"""

_CREATE_ORDERS_SQL = """
    CREATE TABLE IF NOT EXISTS orders (
        order_number TEXT PRIMARY KEY,
        session_date TEXT NOT NULL,
        symbol       TEXT NOT NULL,
        side         TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
        qty          INTEGER NOT NULL CHECK (qty > 0),
        fill_price   TEXT NOT NULL,
        ref_price    TEXT NOT NULL,
        exit_reason  TEXT CHECK (
            exit_reason IN ('stop_loss', 'take_profit', 'force_close')
            OR exit_reason IS NULL
        ),
        net_pnl_krw  INTEGER,
        filled_at    TEXT NOT NULL
    )
"""

_CREATE_ORDERS_INDEX_SESSION = (
    "CREATE INDEX IF NOT EXISTS idx_orders_session ON orders(session_date)"
)
_CREATE_ORDERS_INDEX_SYMBOL = "CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol)"

_CREATE_DAILY_PNL_SQL = """
    CREATE TABLE IF NOT EXISTS daily_pnl (
        session_date         TEXT PRIMARY KEY,
        starting_capital_krw INTEGER,
        realized_pnl_krw     INTEGER NOT NULL,
        realized_pnl_pct     REAL,
        entries_today        INTEGER NOT NULL,
        halted               INTEGER NOT NULL CHECK (halted IN (0, 1)),
        mismatch_symbols     TEXT NOT NULL,
        recorded_at          TEXT NOT NULL
    )
"""


class StorageError(Exception):
    """스토리지 초기화·치명 실패 래퍼. 원본은 `__cause__` 에 보존."""


@runtime_checkable
class TradingRecorder(Protocol):
    """거래 이벤트 영속화 경계 (Protocol).

    `main.py` 콜백이 `StepReport.entry_events`·`exit_events` 와 `DailySummary`
    를 notifier 와 나란히 이 인터페이스로 포워딩한다. Executor 는 이 타입을
    직접 의존하지 않는다 (Protocol 의존성 역전, notifier 와 동일 기조).
    """

    def record_entry(self, event: EntryEvent) -> None: ...

    def record_exit(self, event: ExitEvent) -> None: ...

    def record_daily_summary(self, summary: DailySummary) -> None: ...

    def close(self) -> None: ...


class NullTradingRecorder:
    """no-op `TradingRecorder`.

    `_default_recorder_factory` 가 `SqliteTradingRecorder` 조립 실패 시 폴백
    으로 주입한다 — 영속화 부재가 세션 전체 실패보다 덜 위험하다는
    판단(ADR-0013). 로그(loguru sink) 는 여전히 유지되므로 사후 재구성 경로가
    완전히 닫히지 않는다.
    """

    def record_entry(self, event: EntryEvent) -> None:  # noqa: ARG002
        return None

    def record_exit(self, event: ExitEvent) -> None:  # noqa: ARG002
        return None

    def record_daily_summary(self, summary: DailySummary) -> None:  # noqa: ARG002
        return None

    def close(self) -> None:
        return None


class SqliteTradingRecorder:
    """SQLite 기반 `TradingRecorder` — 단일 파일 원장.

    생성 시 스키마 v1 을 적용(IF NOT EXISTS + schema_version 기록) 하고 PRAGMA
    (WAL·NORMAL·foreign_keys) 를 설정한다. `record_*` 는 autocommit 단건
    INSERT 로 기록 — 스키마 init 만 `BEGIN IMMEDIATE` 로 원자성 확보.
    """

    def __init__(
        self,
        *,
        db_path: str | Path = _DEFAULT_DB_PATH,
        consecutive_failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
    ) -> None:
        """
        Args:
            db_path: SQLite 파일 경로. `":memory:"` 도 허용 (테스트용).
                파일 경로이면 부모 디렉토리를 자동 생성한다. 경로가 기존
                디렉토리이면 `StorageError` 로 fail-fast.
            consecutive_failure_threshold: 연속 실패 몇 번째에서 `logger.critical`
                1회 경보를 낼지. 기본 5. 1 이상.

        Raises:
            StorageError: 스키마 init 실패·디렉토리 경로 오지정·connect 실패.
                원본 예외는 `__cause__` 로 보존.
            RuntimeError: `consecutive_failure_threshold` 가 1 미만.
        """
        if consecutive_failure_threshold <= 0:
            raise RuntimeError(
                "SqliteTradingRecorder.consecutive_failure_threshold 는 1 이상이어야 "
                f"합니다 (got={consecutive_failure_threshold})."
            )
        self._closed = False
        self._consecutive_failures = 0
        self._critical_emitted = False
        self._threshold = consecutive_failure_threshold
        self._is_memory = isinstance(db_path, str) and db_path == ":memory:"

        self._conn = self._open_connection(db_path)
        try:
            self._apply_pragmas()
            self._init_schema()
        except sqlite3.Error as e:
            self._safe_close_conn()
            raise StorageError(
                f"SqliteTradingRecorder 스키마 초기화 실패: {e.__class__.__name__}: {e}"
            ) from e
        except StorageError:
            self._safe_close_conn()
            raise

    @staticmethod
    def _open_connection(db_path: str | Path) -> sqlite3.Connection:
        """connect + 디렉토리·권한 가드.

        Raises:
            StorageError: 경로가 디렉토리이거나 connect 실패.
        """
        if isinstance(db_path, str) and db_path == ":memory:":
            try:
                return sqlite3.connect(":memory:", isolation_level=None)
            except sqlite3.Error as e:
                raise StorageError(
                    f"SqliteTradingRecorder: sqlite3.connect(':memory:') 실패: {e}"
                ) from e

        path = Path(db_path)
        if path.exists() and path.is_dir():
            raise StorageError(
                f"SqliteTradingRecorder: db_path={path} 는 디렉토리입니다. 파일 경로를 지정하세요."
            )
        parent = path.parent
        if parent and str(parent) not in ("", "."):
            # mkdir 실패는 아래 connect 에서 sqlite3.Error 로 귀결 →
            # StorageError 로 래핑되며 원본은 __cause__ 에 보존.
            with contextlib.suppress(OSError):
                parent.mkdir(parents=True, exist_ok=True)
        try:
            return sqlite3.connect(str(path), isolation_level=None)
        except sqlite3.Error as e:
            raise StorageError(
                f"SqliteTradingRecorder: sqlite3.connect({path}) 실패: {e.__class__.__name__}: {e}"
            ) from e

    def _apply_pragmas(self) -> None:
        """WAL·NORMAL·foreign_keys. WAL 은 파일 기반에서만 적용."""
        if not self._is_memory:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _init_schema(self) -> None:
        """스키마 v1 을 IF NOT EXISTS 로 적용. `BEGIN IMMEDIATE` 로 원자성."""
        cur = self._conn.cursor()
        try:
            cur.execute(_CREATE_SCHEMA_VERSION_SQL)
            row = cur.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = row[0] if row and row[0] is not None else 0

            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.execute(_CREATE_ORDERS_SQL)
                cur.execute(_CREATE_DAILY_PNL_SQL)
                cur.execute(_CREATE_ORDERS_INDEX_SESSION)
                cur.execute(_CREATE_ORDERS_INDEX_SYMBOL)
                if current < _SCHEMA_VERSION:
                    cur.execute(
                        "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                        (_SCHEMA_VERSION,),
                    )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        finally:
            cur.close()

    def _safe_close_conn(self) -> None:
        """예외 경로용 연결 닫기 — 실패 무시."""
        try:
            self._conn.close()
        except Exception as e:  # noqa: BLE001 — 이미 실패 경로, 덮어쓰기 방지
            logger.warning(
                f"storage._safe_close_conn: connection close 실패 (무시): "
                f"{e.__class__.__name__}: {e}"
            )

    # ---- Protocol impl --------------------------------------------------

    def record_entry(self, event: EntryEvent) -> None:
        """EntryEvent 를 orders 테이블(buy) 에 INSERT.

        close 후 호출은 warning + silent. `sqlite3.Error` 는 silent fail +
        연속 실패 dedupe 경보.
        """
        if self._closed:
            logger.warning(
                "storage.record_entry: 이미 close() 된 recorder — 무시 "
                f"(order_number={event.order_number})"
            )
            return
        if event.timestamp.tzinfo is None:
            # DTO __post_init__ 가 이미 tz-aware 를 강제하지만 defensive depth.
            logger.warning(
                "storage.record_entry: naive timestamp 감지 — reject "
                f"(order_number={event.order_number})"
            )
            self._consecutive_failures += 1
            self._maybe_emit_critical("record_entry_naive_ts")
            return
        session_date = event.timestamp.date().isoformat()
        try:
            self._conn.execute(
                "INSERT INTO orders "
                "(order_number, session_date, symbol, side, qty, fill_price, "
                " ref_price, exit_reason, net_pnl_krw, filled_at) "
                "VALUES (?, ?, ?, 'buy', ?, ?, ?, NULL, NULL, ?)",
                (
                    event.order_number,
                    session_date,
                    event.symbol,
                    int(event.qty),
                    str(event.fill_price),
                    str(event.ref_price),
                    event.timestamp.isoformat(),
                ),
            )
            self._on_success()
        except sqlite3.Error as e:
            self._on_failure("record_entry", e)

    def record_exit(self, event: ExitEvent) -> None:
        """ExitEvent 를 orders 테이블(sell) 에 INSERT. `ref_price` 는 `fill_price` 복사.

        ExitEvent 는 참고가 필드가 없으므로 orders.ref_price 는 fill_price 와
        동일 값으로 기록. 주문 의도·추정 체결가 격차 분석이 필요하면 별도
        컬럼을 v2 에 추가.
        """
        if self._closed:
            logger.warning(
                "storage.record_exit: 이미 close() 된 recorder — 무시 "
                f"(order_number={event.order_number})"
            )
            return
        if event.timestamp.tzinfo is None:
            logger.warning(
                "storage.record_exit: naive timestamp 감지 — reject "
                f"(order_number={event.order_number})"
            )
            self._consecutive_failures += 1
            self._maybe_emit_critical("record_exit_naive_ts")
            return
        session_date = event.timestamp.date().isoformat()
        try:
            self._conn.execute(
                "INSERT INTO orders "
                "(order_number, session_date, symbol, side, qty, fill_price, "
                " ref_price, exit_reason, net_pnl_krw, filled_at) "
                "VALUES (?, ?, ?, 'sell', ?, ?, ?, ?, ?, ?)",
                (
                    event.order_number,
                    session_date,
                    event.symbol,
                    int(event.qty),
                    str(event.fill_price),
                    str(event.fill_price),
                    event.reason,
                    int(event.net_pnl_krw),
                    event.timestamp.isoformat(),
                ),
            )
            self._on_success()
        except sqlite3.Error as e:
            self._on_failure("record_exit", e)

    def record_daily_summary(self, summary: DailySummary) -> None:
        """DailySummary 를 daily_pnl 테이블에 session_date PK INSERT OR REPLACE.

        `mismatch_symbols` 는 `json.dumps(list)` 로 직렬화. `halted` 는 0/1.
        `recorded_at` 은 호출 시점 KST aware `now()` — 동일 세션의 재실행·재집계
        이력을 구분하기 위해.
        """
        if self._closed:
            logger.warning(
                "storage.record_daily_summary: 이미 close() 된 recorder — 무시 "
                f"(session_date={summary.session_date})"
            )
            return
        recorded_at = datetime.now(KST).isoformat()
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_pnl "
                "(session_date, starting_capital_krw, realized_pnl_krw, "
                " realized_pnl_pct, entries_today, halted, mismatch_symbols, "
                " recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    summary.session_date.isoformat(),
                    summary.starting_capital_krw,
                    int(summary.realized_pnl_krw),
                    summary.realized_pnl_pct,
                    int(summary.entries_today),
                    1 if summary.halted else 0,
                    json.dumps(list(summary.mismatch_symbols)),
                    recorded_at,
                ),
            )
            self._on_success()
        except sqlite3.Error as e:
            self._on_failure("record_daily_summary", e)

    def close(self) -> None:
        """멱등 close. 실패 경로에서도 호출 가능."""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"storage.close: connection close 실패 (무시): {e.__class__.__name__}: {e}"
            )

    def __enter__(self) -> SqliteTradingRecorder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- 내부 상태 -----------------------------------------------------

    def _on_success(self) -> None:
        self._consecutive_failures = 0
        self._critical_emitted = False

    def _on_failure(self, op: str, err: Exception) -> None:
        self._consecutive_failures += 1
        logger.warning(
            f"storage.{op} 실패 (silent): {err.__class__.__name__}: {err} "
            f"consecutive={self._consecutive_failures}"
        )
        self._maybe_emit_critical(op)

    def _maybe_emit_critical(self, op: str) -> None:
        if self._consecutive_failures >= self._threshold and not self._critical_emitted:
            logger.critical(
                f"storage.persistent_failure: {op} 연속 "
                f"{self._consecutive_failures}회 실패 (threshold={self._threshold}). "
                "DB 파일 권한·디스크 공간·WAL 잠금 등 운영자 확인 필요."
            )
            self._critical_emitted = True
