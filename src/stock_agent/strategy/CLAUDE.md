# strategy — ORB 전략 엔진

stock-agent 의 전략 경계 모듈. `Strategy` Protocol + `ORBStrategy` 구현체를 제공하고,
분봉 DTO(`MinuteBar`)를 소비해 상위 레이어(backtest/execution)에 정규화된 시그널 DTO만 노출한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`strategy/__init__.py`)

`EntrySignal`, `ExitReason`, `ExitSignal`, `ORBStrategy`, `Signal`, `Strategy`, `StrategyConfig`, `StrategyError`

## 현재 상태 (2026-04-20 기준)

**Phase 2 첫 산출물 완료 (코드·테스트 레벨)** (2026-04-20)

### `base.py` — Protocol + DTO + 상수

- **`Strategy` Protocol** (`runtime_checkable`): `on_bar(symbol, bar) -> Signal`, `on_time(symbol, now) -> Signal`. 단일 구현에 ABC 상속 계층을 얹지 않고 최소 Protocol 로 정의.
- **`EntrySignal`** (`@dataclass(frozen=True)`): `symbol: str`, `entry_price: Decimal`, `stop_loss: Decimal`, `take_profit: Decimal`.
- **`ExitSignal`** (`@dataclass(frozen=True)`): `symbol: str`, `exit_price: Decimal`, `reason: ExitReason`.
- **`ExitReason`** (`Literal`): `"stop_loss" | "take_profit" | "force_close"`.
- **`Signal`** 타입 별칭: `EntrySignal | ExitSignal | None`.
- **`KST`** 상수: `ZoneInfo("Asia/Seoul")`. 시각 비교 참조용.

### `orb.py` — ORBStrategy 상태 머신

- **`StrategyConfig`** (`@dataclass(frozen=True)`):

  | 필드 | 기본값 | 설명 |
  |---|---|---|
  | `stop_loss_pct` | `Decimal("0.015")` | 손절 비율 (1.5%) |
  | `take_profit_pct` | `Decimal("0.030")` | 익절 비율 (3.0%) |
  | `or_start` | `time(9, 0)` | OR 집계 시작 (포함) |
  | `or_end` | `time(9, 30)` | OR 집계 종료 (포함) |
  | `force_close_at` | `time(15, 0)` | 강제청산 기준 시각 |

  `stop_loss_pct`, `take_profit_pct` 는 양수 필수. `or_start < or_end < force_close_at` 필수 (위반 시 `ValueError`). 시각 필드는 **naive `datetime.time`** (KST 기준 암묵 해석 — `bar.bar_time.time()` 이 naive 를 반환하므로 일관성 유지).

- **`ORBStrategy`** — per-symbol 독립 상태 머신:

  ```
  IDLE → (OR 집계 시작 바 도착) → FLAT → (OR-High 상향 돌파) → LONG → (손절/익절/강제청산) → CLOSED
  ```

  - **세션 경계 자동 리셋**: `bar.bar_time.date()` 변경 감지 시 해당 symbol 의 상태를 IDLE 로 초기화. 멀티데이 실행 및 백테스트 루프 모두 추가 작업 없이 동작.
  - **OR 집계**: `or_start <= bar.bar_time.time() <= or_end` 구간 분봉을 누적해 OR-High / OR-Low 갱신.

- **진입 규칙**: `bar.bar_time.time() > or_end` AND `bar.close > or_high` (strict greater) AND 상태 FLAT AND `bar.bar_time.time() < force_close_at`. OR 집계가 없으면 진입 안 함.
- **청산 규칙** (LONG 상태에서 `on_bar` 호출 순서):
  1. `bar.low <= stop_loss` → `ExitSignal(reason="stop_loss")`
  2. `bar.high >= take_profit` → `ExitSignal(reason="take_profit")`
  3. 동일 bar 에서 1·2 동시 성립 → **손절 우선** (보수적, 슬리피지 과소평가 방지)
- **강제청산**: `on_time(symbol, now)` 에서 `now.time() >= force_close_at` AND 상태 LONG → `ExitSignal(reason="force_close")`.
- **1일 1심볼 재진입 금지**: CLOSED 상태에서 돌파 반복 발생해도 무시.
- **`force_close_at` 이후 신규 진입 금지**: 그 시각 이후 FLAT 상태여도 진입 신호를 발생시키지 않음. 청산만 허용.

- **`StrategyError`**: 외부 예상치 못한 예외를 래핑. `loguru.exception` + `StrategyError(...) from e`. `RuntimeError` 는 사용자 입력 오류(symbol 포맷 위반, naive datetime 에 tzinfo 포함, 시간 역행, config 위반)에서 래핑 없이 전파.

### 에러 정책

- `RuntimeError` 전파 (사용자 입력 오류: symbol 비형식, config 위반, 시간 역행).
- 그 외 예외 → `StrategyError` 래핑 + `loguru.exception`. broker/data 기조와 동일.

### 테스트 현황

pytest **36 케이스 green** (전체 167건 중 신규 36):

| 그룹 | 케이스 수 |
|---|---|
| OR 누적 (분봉 집계) | 4 |
| 진입 시그널 | 5 |
| 청산 시그널 (손절/익절/동시성립) | 4 |
| 강제청산 (`on_time`) | 4 |
| 세션 전환 (날짜 변경 리셋) | 2 |
| 복수 심볼 독립성 | 2 |
| 입력 검증 (symbol 포맷, naive/aware 등) | 9 |
| `StrategyConfig` 검증 (pct 음수, 시간 역행 등) | 8 |
| 기타 회귀 | 2 |

외부 목킹 불필요. 순수 로직 — 네트워크·시계·파일·DB 미사용.

## 설계 원칙

- **라이브러리 타입 누출 금지**. `MinuteBar` 는 `data` 모듈 공개 DTO 로, strategy 는 이것만 소비한다. pykrx/python-kis 타입은 절대 노출하지 않는다.
- **얇은 래퍼**. 포지션 사이징·주문 실행·일일 손실 한도는 각각 `risk/manager.py`, `execution/executor.py` 책임. 이 모듈은 "분봉 → 시그널" 변환만.
- **코드 상수 우선**. `StrategyConfig` 는 생성자 주입. `config/strategy.yaml` 은 Phase 3 `main.py` 착수 시 도입 (지금은 코드 상수 + 주입). broker/data 와 동일 원칙.
- **결정론**. 동일 입력 → 동일 출력. 외부 상태 읽기 없음. 시각은 `bar.bar_time` 과 `on_time(now)` 인자로만 받는다.

## 테스트 정책

- 실 네트워크·시계·파일·DB 에 절대 접촉하지 않는다.
- 외부 목킹 불필요 — `ORBStrategy` 는 순수 로직 클래스이고 주입 의존이 없다.
- 테스트 파일 작성·수정은 반드시 `unit-test-writer` 서브에이전트 경유 (root CLAUDE.md 하드 규칙).
- 관련 테스트 파일: `tests/test_strategy_orb.py`.

## 소비자 참고

- **`backtest/engine.py`** (Phase 2): `backtesting.py` 래퍼가 `ORBStrategy.on_bar` 를 과거 `MinuteBar` 시계열에 순차 호출해 시그널을 수집하고 PnL 을 계산한다. `StrategyConfig` 는 백테스트 실행 시 생성자로 주입.
- **`execution/executor.py`** (Phase 3): 장중 루프에서 `RealtimeDataStore.get_current_bar(symbol)` 로 최신 분봉을 얻어 `ORBStrategy.on_bar` 에 넘기고, 분봉 경계 외 시각은 `on_time` 으로 강제청산 판정을 수행한다.
- **`main.py`** (Phase 3): `StrategyConfig` 를 `config/strategy.yaml` (Phase 3 착수 시 도입)에서 로드해 `ORBStrategy` 생성자에 주입한다.

## 범위 제외 (의도적 defer)

- **포지션 사이징** — `risk/manager.py` (Phase 2 다음 산출물)
- **일일 손실 한도·서킷브레이커** — `risk/manager.py`
- **주문 실행·체결 추적** — `execution/executor.py` (Phase 3)
- **거래대금·유동성 필터** — `MinuteBar.volume=0` 고정 제약으로 현 모듈 범위 밖. Phase 3 volume 실사 후 유니버스 필터 레이어에서 도입.
- **틱 기반 진입 (`on_tick`)** — Phase 3 에서 `Strategy` Protocol 확장 가능 지점으로 열어둠.
- **백테스트 엔진** — `backtest/engine.py` (Phase 2 세 번째 산출물)
- **`config/strategy.yaml`** — Phase 3 `main.py` 착수 시 도입
- **복수 전략 조합·A/B** — Phase 5
- **멀티스레드·프로세스 safe** — 단일 프로세스 전용 (broker/data 와 동일)
