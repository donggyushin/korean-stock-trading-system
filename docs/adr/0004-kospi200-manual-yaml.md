---
date: 2026-04-19
status: 승인됨
deciders: donggyu
related: []
---

# ADR-0004: KOSPI 200 유니버스 수동 YAML 관리

## 상태

승인됨 — 2026-04-19.

## 맥락

Phase 1 유니버스 모듈 도입 시점에 KOSPI 200 구성종목을 자동으로 가져올 수 있는 안정 소스가 없음을 확인했다.

| 후보 | 문제 |
|---|---|
| `pykrx 1.2.7` 의 `get_index_portfolio_deposit_file` 등 지수 API | 현재 KRX 서버와 호환 깨짐 (HTTP 응답 포맷 변경 추정). 호출 시 빈 결과 또는 예외. |
| KIS Developers REST API | 인덱스 구성종목 조회 엔드포인트 미제공. |
| KRX 정보데이터시스템 [11006] CSV | 임시 가상 코드(`NNNNZ0` 형태, 신규 상장 직후 정식 티커 발급 전 표기) 가 섞여 옴 — 주문·조회 API 로 거래 불가. 자동 스크래핑 시 실전 매매 루프가 깨질 위험. |

KOSPI 200 정기변경은 연 2회(6월·12월 선·옵 동시만기일 익영업일 기준) 만 발생. 변경 빈도가 낮아 수동 관리 부담은 감내 가능.

## 결정

`config/universe.yaml` 에 **티커 6자리 정수만** 수동 하드코딩한다. 파일 구조:

```yaml
as_of_date: 2026-04-17
source: "KRX 정보데이터시스템 [11006]"
tickers:
  - "005930"  # 삼성전자
  - "000660"  # SK하이닉스
  - ...
```

로더(`load_kospi200_universe`) 는 정규식 `^\d{6}$` 으로 임시 가상 코드 자동 거부. CSV 원본에서 제외된 행은 주석으로 명시(`# 제외: NNNNZ0 종목명`).

운영자는 KOSPI 200 정기변경 시 KRX CSV 다운로드 후 수동 갱신 (연 2회). 변경 이력은 git log 로 감사.

## 결과

**긍정**
- 자동 소스 의존성 0 — `pykrx` 또는 KRX 서버 변경에 영향받지 않음.
- 임시 가상 코드 자동 차단 — 정식 티커만 유니버스 진입.
- git log 가 KOSPI 200 변경 이력의 신뢰 가능한 audit trail.

**부정**
- 정기변경 시 운영자 수동 작업 필요 (연 2회 약 1~2시간).
- 임시 변경(상장폐지·합병 등) 발생 시 즉시 반영하지 않으면 매매 루프가 거래 불가 종목을 시도할 위험.

**중립**
- `tickers: []` 빈 리스트는 예외가 아니라 `logger.warning` 후 빈 `KospiUniverse` 반환 — Phase 3 `main.py` 가 "유니버스 비면 매매 중단" 을 명시적으로 판단할 수 있도록.

## 추적

- 코드: `src/stock_agent/data/universe.py`, `config/universe.yaml`
- 문서: [src/stock_agent/data/CLAUDE.md](../../src/stock_agent/data/CLAUDE.md) "universe.py" 섹션
- 도입 PR: #6 (Phase 1 세·네 번째 산출물)
- 폐기 후보: pykrx 지수 API 가 KRX 와 호환 회복되거나 KIS 가 인덱스 구성종목 API 를 제공하면 재검토 (Phase 5 자동화 후보).
