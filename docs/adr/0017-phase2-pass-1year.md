---
date: 2026-04-22
status: 승인됨
deciders: donggyu
related: [0016-kis-minute-bar-cache.md]
---

# ADR-0017: Phase 2 PASS 판정 기간 완화 — 2~3 년 → 1 년 (MDD > -15% 유지)

## 상태

승인됨 — 2026-04-22. plan.md 초기 기준(2~3 년 실데이터 MDD > -15%) 을 **번복**.

## 맥락

plan.md Phase 2 Verification 은 "2~3 년 실데이터 기반 MDD 낙폭 15% 이내" 를 PASS 조건으로 명시해 왔다. 이 기준은 프로젝트 초기에 "장기간 표본으로 국면 편향을 줄이고 walk-forward 에 준하는 데이터 폭을 확보한다" 는 가정에서 출발했다. 2026-04-22 Issue #35 (`KisMinuteBarLoader`) 구현 과정에서 이 가정을 밑받침하는 데이터 조달 경로 세 가지를 모두 재확인한 결과 **개인 프로젝트 규모에서 현실성 없음** 이 확정됐다:

1. **KIS Developers 분봉 API 1 년 보관 제약** — KIS 서버는 과거 분봉을 최대 1 년만 보관한다 (koreainvestment/open-trading-api 공식 샘플 `examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py:41` 명시). 본 PR 에서 도입한 `KisMinuteBarLoader` 로 2~3 년 백필 불가.

2. **외부 데이터 벤더 계약** — KRX 정보데이터시스템 유료 플랜·민간 증권 정보 벤더. 월 수만~수십만원 단위 구독료. 본 프로젝트 초기 자본 100~200 만원 기준 ROI 깨짐 (전략 기대수익 대비 데이터 비용 비대칭).

3. **공개 소스 대체** — `pykrx` 1.2.7 은 분봉 미지원. KRX 정보데이터시스템 스크래핑은 TOS·안정성 리스크. pykrx 업스트림 로드맵은 불투명 (2026-04 기준 분봉 추가 공지 없음).

세 경로 모두 "Phase 2 PASS 를 위해 2~3 년을 고수" 하려면 수개월 이상 블로킹되거나 예산 규모가 프로젝트 목표와 불일치한다. Issue #36 에 3 대안 평가 코멘트를 남긴 뒤 동일 세션에서 기준 자체 완화를 결정.

추가 맥락 — Phase 2 는 실전 전환 **최종 게이트가 아니다**. plan.md 로드맵상 실전 진입 직전 게이트는 **Phase 3 모의투자 연속 10 영업일 무중단 운영** (2026-04-22 기준 Phase 3 코드 산출물 전부 완료, 모의투자 대기). Phase 2 는 전략 구현의 sanity check — 파멸적 낙폭이 없음 · 체결·비용 모델이 설계대로 동작함 · 민감도 그리드 상 현재 기본값이 로버스트함 — 을 보는 관문이다. 이 sanity 역할에는 1 년 표본도 충분하며, 전체 시장 국면 편향 보정은 Phase 5 walk-forward 검증(별도 범위) 영역이다.

검토한 대안:

- **Issue #36 open 유지 + 벤더 평가 루프 지속**: "언젠가 데이터가 조달되면 재평가" 로 보류. 단점 — Phase 3 가 Phase 2 "공식 PASS" 대기 없이 진행 가능하므로, 실효적으로는 기한 없는 미해결 이슈로 남아 의사결정 부담만 누적. → **거부**.
- **Phase 2 기준 자체 폐기**: MDD 체크 없이 Phase 3 진입. 단점 — 백테스트 엔진·민감도 그리드·ORB 전략 산출물이 최소한의 객관적 품질 증거 없이 실전에 붙는다. 개인 자동매매 특성상 sanity 는 유지해야 함. → **거부**.
- **MDD 임계 자체 -20% 로 완화**: 기간 대신 임계를 풀어 통과 가능성 확보. 단점 — 파멸적 낙폭의 정의가 무의미해진다. 완화는 **기간** 축만 해야 "낙폭 제한" 이라는 기조를 유지할 수 있다. → **거부**.

## 결정

1. **Phase 2 PASS 기준 기간 축을 "2~3 년" → "1 년" 으로 완화한다**. MDD 임계값 `-0.15` (낙폭 절대값 15% 미만, strict greater) 자체는 변경 없이 유지 — `scripts/backtest.py::_MDD_PASS_THRESHOLD` 수치 수정 불필요.

2. **데이터 소스는 `KisMinuteBarLoader` (ADR-0016) 또는 동등한 CSV 스냅샷**. 1 년 범위가 KIS 서버 보관 한도와 정합해 `--loader=kis` 경로로 즉시 실행 가능.

3. **표본 구성 요구사항**: 연속 거래일 기준 최소 240 영업일 (약 1 년, 한국 주식시장 연 평균 영업일 수). 과도하게 짧은 기간(분기·반기) 은 검증 가치가 떨어지므로 허용 안 함. 240 영업일 미만으로 PASS 라벨이 찍히면 리포트 해석은 참고용으로만 사용한다.

4. **유니버스 범위**: `config/universe.yaml` KOSPI 200 전체 또는 운영자가 선별한 유동성 상위 부분집합. 단일 종목 백테스트는 sanity 목적상 허용하되 Phase 2 PASS 선언 근거로는 불충분 — 공식 선언은 다중 종목 실행.

5. **walk-forward·다년 표본은 Phase 5 로 유예**. 본 완화는 Phase 2 PASS 의 **필요조건** 을 현실화할 뿐, 과적합 방어·국면 편향 보정 책임을 면제하지 않는다. 운영자는 리포트 해석 시 이 한계를 염두에 둔다.

6. **Issue #36 close**. 본 ADR 로 해당 이슈의 "2~3 년 데이터 조달" 요구가 명시적으로 기각됐음을 기록. 이슈 close 시 코멘트에 ADR-0017 링크 포함.

7. **후속 이슈 생성**: "1 년치 KIS 분봉 백필 + `scripts/backtest.py` 실행 → Phase 2 PASS 판정". 본 완화 후 실제 PASS 선언까지의 단일 작업 단위.

8. **문서 동기화**: plan.md Verification § Phase 2 · Phase 2 잔여 항목, root `CLAUDE.md` 현재 상태, `README.md` Phase 상태 문단, `docs/adr/README.md` 인덱스 1 줄.

## 결과

**긍정**
- Phase 2 PASS 가 데이터 조달 없이 즉시 실행 가능한 경로로 전환. `KisMinuteBarLoader` + `scripts/backtest.py --loader=kis --from=<1년 전> --to=<오늘>` 실행만으로 판정 가능.
- Issue #36 미해결 상태로 인한 의사결정 부담 해제. 새 이슈(1 년 백필 + PASS 실행) 로 작업 단위 명확화.
- Phase 3 진입 일정이 벤더 조달 루프와 분리됨. 실전 진입 게이트가 Phase 3 모의투자 10 영업일 무중단으로 명확히 좁혀짐.
- plan.md "2~3 년" 초기 가정의 근거 없음이 ADR 로 영구 기록 — 미래에 동일 고민이 반복되지 않음.

**부정**
- **1 년 표본은 시장 국면 편향에 취약**. 2025~2026 년이 강세장 국면이면 MDD 가 과소평가될 가능성. 이 한계는 리포트 해석 시 운영자가 감수하며, Phase 5 walk-forward 검증으로 사후 보정.
- "2~3 년" 이 사라지면서 "충분히 긴 표본" 의 정량 기준이 약해짐 — 후속 전략 확장 시 기간 선택이 운영자 재량에 맡겨짐. 남용 방지를 위해 본 ADR 의 "240 영업일 최소" 조항을 참조 기준으로 사용.

**중립**
- MDD 임계값 `-0.15` 자체는 불변. ORB 전략 본체·`scripts/backtest.py` PASS 라벨 로직·민감도 그리드 모두 코드 수정 불필요.
- 본 ADR 은 plan.md 결정의 **번복** 이지 새 기술 스택·새 모듈 도입이 아니다. 구현 변경은 문서 동기화 한정.
- Issue #35 (KisMinuteBarLoader) 로 도입된 어댑터가 완화된 기준의 실행 경로로 직접 쓰이므로 ADR-0016 과 상호 정합.

## 추적

- 코드: 변경 없음 (기준 수정은 문서 영역).
- 문서: `plan.md` (Phase 2 Verification · Phase 2 잔여 항목), root `CLAUDE.md` (Phase 2 현재 상태), `README.md` (Phase 상태 문단), `docs/adr/README.md` (인덱스).
- 관련 ADR: [ADR-0016](./0016-kis-minute-bar-cache.md) (KIS 과거 분봉 어댑터 — 본 완화의 실행 경로).
- 관련 이슈: #36 (close 예정 — 본 ADR 로 기각 사유 기록), 후속 이슈 (1 년 백필 + PASS 실행 — 본 PR 병합 후 생성).
- 도입 PR: #46 (Issue #35 KIS 어댑터 PR 에 포함).
