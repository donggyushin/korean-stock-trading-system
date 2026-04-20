---
date: 2026-04-19
status: 승인됨
deciders: donggyu
related: [ADR-0002]
---

# ADR-0008: 단일 프로세스 전용 (멀티스레드/프로세스 safe 미제공)

## 상태

승인됨 — 2026-04-19. 5개 모듈(broker/data/strategy/risk/backtest) 공통.

## 맥락

Phase 1 broker 모듈 도입 시 `OrderRateLimiter` 를 어느 범위(단일 프로세스 vs 멀티프로세스) 에서 보장할지 결정이 필요했다. 이후 모듈에서도 같은 질문이 반복됐다.

운영 환경:
- 로컬 맥북 1대에서 장중(9:00~15:30 KST) 단일 트레이딩 루프 실행.
- 초기 자본 100~200만원, 동시 보유 3종목 한도.
- 주문 레이트 KIS 모의 2 req/s, 실전 19 req/s — 단일 프로세스에서도 충분히 여유.

멀티프로세스 필요 시나리오 검토:
- 백테스트 병렬 실행 — 가능하지만 인스턴스 분리(각 프로세스가 독립 `BacktestEngine`) 로 해결, 모듈 자체의 thread-safety 는 불필요.
- 실시간 시세 + 주문 동시 처리 — `RealtimeDataStore` 가 내부 데몬 스레드 1개만 운용, 외부에서 추가 스레드 접근 없음.
- 멀티 사용자/멀티 계좌 — 본 프로젝트 범위 밖.

## 결정

**모든 모듈은 단일 프로세스 전용으로 설계** 한다. 명시적 보장 범위:

| 모듈 | 동시성 보장 |
|---|---|
| `broker/` (`KisClient`, `OrderRateLimiter`) | 없음 — 단일 인스턴스 호출 보장 |
| `data/historical.py` | 없음 — SQLite 단일 connection |
| `data/realtime.py` | **내부** `threading.Lock` 으로 데몬 스레드 vs 메인 스레드 보호 (외부 멀티스레드 접근은 미보장) |
| `strategy/` (`ORBStrategy`) | 없음 — 순수 로직 |
| `risk/` (`RiskManager`) | 없음 — 인메모리 상태 |
| `backtest/` (`BacktestEngine`) | 없음 — `run()` 1회 소비, 재실행은 새 인스턴스 |

멀티프로세스 확장이 필요해지면 Phase 5 재설계 범위로 분리.

## 결과

**긍정**
- `threading.Lock`/`multiprocessing.Lock` 도입 부담 0 — 코드 단순.
- 테스트가 결정론적 — 동시성 테스트 불필요.
- 디버깅 시 race condition 가능성 0.

**부정**
- 백테스트 32 조합(`scripts/sensitivity.py`) 이 순차 실행 — Phase 2 데이터 규모(3년 분봉)에서 실측 후 병렬화 필요 여부 판단.
- 향후 멀티 계좌·멀티 전략 운영 시 재설계 필요.

**중립**
- `RealtimeDataStore` 내부 스레드는 외부에서 보이지 않음 — 사용자는 단일 스레드로 호출하면 됨.

## 추적

- 코드: 5개 모듈 공통 (특수 케이스만 `threading.Lock` 사용)
- 문서: 모듈별 `CLAUDE.md` 의 "범위 제외 (의도적 defer)" 섹션 — 모두 "멀티스레드·프로세스 safe — 단일 프로세스 전용" 문구 포함
- 도입 PR: #2 (Phase 1 첫 산출물 — broker), 이후 모든 모듈에 일관 적용
- 폐기 후보: 멀티 계좌·멀티 전략 운영 요구 발생 시 Phase 5 에서 재설계
