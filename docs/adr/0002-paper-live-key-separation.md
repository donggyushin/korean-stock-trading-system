---
date: 2026-04-19
status: 승인됨
deciders: donggyu
related: [ADR-0008]
---

# ADR-0002: KIS paper/live 키 분리 (시세 전용 실전 키 3종)

## 상태

승인됨 — 2026-04-19.

## 맥락

Phase 1 실시간 시세 모듈 착수 시점에 KIS Developers 의 다음 제약이 확인됐다.

- KIS paper 도메인(`openapivts`)은 `/quotations/*` 시세 API 를 제공하지 않는다. paper 키로는 실시간 체결가·호가를 받을 수 없다.
- 모의투자 환경에서 시세를 얻으려면 실전 도메인으로 직접 호출해야 하며, 이를 위해 별도 실전 APP_KEY/SECRET 발급이 필요하다.
- 실전 키는 잘못 사용하면 실제 주문이 나갈 수 있어 구조적 안전장치가 필수.

대안으로 "paper 키로 모든 것을 처리" 는 시세 부재로 불가능, "실전 키 1세트로 통합" 은 사고 위험이 너무 컸다.

## 결정

**키 슬롯 2세트로 분리** 한다.

| 용도 | 환경변수 | 도메인 | 가드 |
|---|---|---|---|
| 주문·잔고 | `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO` (paper) | KIS paper | `install_paper_mode_guard` |
| 시세 (REST/WebSocket) | `KIS_LIVE_APP_KEY`, `KIS_LIVE_APP_SECRET`, `KIS_LIVE_ACCOUNT_NO` | KIS 실전 | `install_order_block_guard` (도메인 무관 `/trading/order*` 차단) |

`Settings.has_live_keys` 프로퍼티로 3종 all-or-none 검증. 실전 키 미설정 시 `RealtimeDataStore.start()` 가 fail-fast (`RealtimeDataError`).

HTS_ID 는 한 사람당 하나라 paper/실전 공유(`KIS_HTS_ID`), 계좌번호는 paper/실전이 달라 별도 필드 필수.

## 결과

**긍정**
- paper 환경에서 실수로 실전 주문이 나가는 사고를 두 가드(`paper_mode_guard`, `order_block_guard`)로 이중 차단.
- "시세 전용 PyKis 인스턴스" 가 구조적으로 보장됨 — `RealtimeDataStore._build_pykis` 에서만 실전 키 사용.
- 운영자가 실전 키를 발급받지 않은 상태에서도 paper 잔고 조회·백테스트는 정상 동작.

**부정**
- 운영자가 KIS Developers 포털에서 실전 앱을 별도 신청하고 **사용 IP 를 화이트리스트에 등록** 해야 함 (미등록 시 `EGW00123`).
- `.env` 관리 항목이 늘어남 (paper 4종 + 실전 3종 + 텔레그램 2종).

**중립**
- 두 가드 모두 `GUARD_MARKER_ATTR` 로 idempotent. 중복 설치 시 raise 하지 않고 거부만.

## 추적

- 코드: `src/stock_agent/config.py` (`Settings.has_live_keys`), `src/stock_agent/safety.py` (두 가드), `src/stock_agent/data/realtime.py` (`_build_pykis`)
- 문서: [src/stock_agent/data/CLAUDE.md](../../src/stock_agent/data/CLAUDE.md), [src/stock_agent/broker/CLAUDE.md](../../src/stock_agent/broker/CLAUDE.md) "안전 가드" 섹션
- 도입 PR: #7 (Phase 1 다섯 번째 산출물)
