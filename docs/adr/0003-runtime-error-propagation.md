---
date: 2026-04-19
status: 승인됨
deciders: donggyu
related: []
---

# ADR-0003: 사용자 입력 오류는 `RuntimeError` 전파, `ValueError` 미사용

## 상태

승인됨 — 2026-04-19. broker/data/strategy/risk/backtest 5개 모듈 공통 기조.

## 맥락

Phase 1 broker 모듈 도입 시 입력 검증 실패(예: 6자리 심볼 정규식 위반, naive datetime, 음수 가격)에 어떤 예외 타입을 쓸지 결정이 필요했다.

Python 표준 관행은 `ValueError`(부적절한 값) / `TypeError`(잘못된 타입) 분리지만, 다음 문제가 있었다.

1. **모듈 자체 예외(`KisClientError`, `StrategyError`, `RiskManagerError`) 와 충돌** — 어떤 건 `ValueError`, 어떤 건 `*Error` 로 잡아야 하는지 호출자가 두 가지 분기를 알아야 함.
2. **"호출자가 고쳐야 하는 사용자 입력 오류" 와 "라이브러리 내부 결함" 의 의도 구분** 이 더 중요. `ValueError` 는 둘 다 표현해 의도가 흐려진다.
3. **테스트에서 `with pytest.raises(...)` 작성 시** 모듈 경계에서 통일된 예외 타입이 단순.

## 결정

| 오류 유형 | 예외 클래스 | 예시 |
|---|---|---|
| 사용자(호출자)가 고쳐야 하는 입력 오류 | `RuntimeError` (래핑 없이 전파) | symbol 포맷, naive datetime, 음수 qty/price, `start > end`, 세션 미시작 |
| 외부 의존(KIS, pykrx, PyYAML, csv) 호출 실패 | 모듈 자체 `*Error` (`KisClientError`, `RealtimeDataError`, `MinuteCsvLoadError` 등) — `from e` 체인 + `loguru.exception` | 네트워크 오류, 파싱 실패, DB I/O |
| 상태 머신 무결성 위반 | 모듈 자체 `*Error` (`StrategyError`, `RiskManagerError`) | 미보유 심볼 청산, long 상태에서 last_close·entry_price 둘 다 None |

`assert` 는 사용하지 않는다 — `python -O` 에서 제거되어 프로덕션 silent 실패 위험.

## 결과

**긍정**
- 호출자는 두 가지만 구분: "내가 잘못 호출했나(`RuntimeError`)" vs "외부/내부 결함인가(`*Error`)".
- generic `except Exception` 금지 기조와 자연스럽게 결합 — `ValueError` 가 없으니 잡을 일도 없음.
- 모듈 5종(broker/data/strategy/risk/backtest) 전체에서 일관된 패턴.

**부정**
- Python 표준 관행과 다름 — 외부 기여자가 처음 보면 `ValueError` 를 기대할 수 있음.
- `pydantic` Settings 검증은 별도(`pydantic.ValidationError`) — config 경계에서만 예외 처리 패턴이 다르다.

**중립**
- `pytest.raises(RuntimeError, match=...)` 패턴이 거의 모든 모듈에서 반복 등장.

## 추적

- 코드: `src/stock_agent/broker/kis_client.py`, `src/stock_agent/strategy/orb.py`, `src/stock_agent/risk/manager.py`, `src/stock_agent/backtest/engine.py` 의 입력 검증 가드
- 문서: 각 모듈 `CLAUDE.md` 의 "예외 경계 설계" 또는 "에러 정책" 섹션
- 도입 PR: #2 (Phase 1 첫 산출물 — broker)
