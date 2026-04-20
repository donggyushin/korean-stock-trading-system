---
date: 2026-04-19
status: 승인됨
deciders: donggyu
related: []
---

# ADR-0005: `tests/` 작성·수정은 `unit-test-writer` 서브에이전트 강제 (PreToolUse 가드)

## 상태

승인됨 — 2026-04-19. PR #5(2026-04-20) 에서 fail-closed 강화.

## 맥락

Claude Code 메인 assistant 가 `tests/` 하위 파일을 직접 작성·수정할 경우 다음 사고 위험이 있었다.

1. **외부 의존 목킹 누락** — KIS API 클라이언트 mock 을 깜빡하면 테스트 실행 중 실제 모의투자 주문이 나갈 수 있다 (paper 환경이라도 거래 흔적·레이트 리밋 영향).
2. **파일·DB·시계 의존 잔류** — `datetime.now()`, 절대 경로 파일 쓰기, 실제 SQLite DB 쓰기를 그대로 둘 위험.
3. **목킹 패턴 불일치** — 동일 모듈인데 테스트 파일마다 mock 주입 방식이 달라지면 회귀 디버깅이 어려워짐.

자동화된 정책 강제 없이 "주의해서 작성" 으로 두면 한 번의 실수가 실주문으로 이어질 수 있어 시스템 차원 가드가 필요했다.

## 결정

**PreToolUse 훅(`.claude/hooks/tests-writer-guard.sh`) 으로 메인 assistant 의 `tests/` 직접 쓰기를 fail-closed 차단** 한다.

- 차단 대상: `Write`/`Edit`/`NotebookEdit` 도구가 `tests/**.py` 를 타겟할 때
- 우회 경로: `unit-test-writer` 서브에이전트 (PreToolUse payload 의 `agent_id` 필드 존재로 판별)
- 종료 코드: exit 2 (도구 호출 거부)

`.claude/agents/unit-test-writer.md` 가 서브에이전트 system prompt 를 정의하고, 외부 의존(KIS API·텔레그램·시계·파일·DB) 100% 목킹과 실주문 차단을 강제한다.

예외: 임포트 경로·네이밍 단순 리팩터처럼 테스트 로직 자체가 바뀌지 않는 수정은 사용자에게 **명시적 확인** 을 받고 우회할 수 있다.

## 결과

**긍정**
- 실주문·실네트워크 접촉 사고 위험을 시스템 차원에서 차단 — 한 명의 사용자가 실수해도 훅이 막음.
- 모든 테스트가 동일한 목킹 규율(`pykis_factory`, `pykrx_factory`, `clock` 주입 패턴)을 따름.
- 542건 pytest 가 외부 I/O 0건으로 실행 가능 (CI 에서 Wall-clock 빠르고 결정론적).

**부정**
- 메인 assistant 가 단순 테스트 추가도 서브에이전트를 호출해야 해 작업 흐름이 1단계 늘어남.
- 사용자가 정책을 모르고 직접 편집을 시도하면 exit 2 로 거부 — 처음에는 혼란.

**중립**
- 훅 파일이 `.claude/hooks/` 에 위치 — Claude Code 설정 변경 시 영향 받음 (다른 IDE 에서는 작동 안 함).

## 추적

- 코드: `.claude/hooks/tests-writer-guard.sh`, `.claude/agents/unit-test-writer.md`
- 문서: [CLAUDE.md](../../CLAUDE.md) "테스트 작성 정책 (하드 규칙)" 섹션
- 도입 PR: #1 (서브에이전트), #3 (가드 훅 1차), #5 (fail-closed 전환)
