#!/usr/bin/env bash
# stock-agent Stop hook — unit test coverage reminder.
#
# Fires at end of Claude's turn. If src/stock_agent/ Python 파일이 추가/수정됐는데
# tests/ 디렉토리는 손대지 않은 경우, unit-test-writer 서브에이전트 호출을 유도하는
# 리마인더를 stderr 로 출력하고 exit 2 (Claude 가 이어서 판단).
# /tmp 마커로 세션당 1회만 발화.

set -euo pipefail

PAYLOAD="$(cat)"
SESSION_ID="$(
  printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    print(json.loads(sys.stdin.read()).get("session_id", "unknown"))
except Exception:
    print("unknown")
' 2>/dev/null || echo "unknown"
)"

MARKER="/tmp/stock-agent-testcov-${SESSION_ID}"

# One-shot per session
if [ -e "$MARKER" ]; then
  exit 0
fi

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -z "$PROJECT_ROOT" ] && exit 0

# Safety: only fire inside stock-agent
case "$PROJECT_ROOT" in
  */stock-agent) : ;;
  *) exit 0 ;;
esac

cd "$PROJECT_ROOT"

CHANGED="$(git status --porcelain --untracked-files=all)"
[ -z "$CHANGED" ] && exit 0

# 트리거 대상: src/stock_agent/ 아래의 .py 파일 (단, __init__.py 는 제외)
SRC_REGEX='^src/stock_agent/.+\.py$'
SRC_INIT_REGEX='^src/stock_agent/(.+/)?__init__\.py$'
TESTS_REGEX='^tests/.+\.py$'

SRC_CHANGED=0
TESTS_CHANGED=0
SRC_LIST=""

while IFS= read -r line; do
  # porcelain: "XY path" — path starts at column 4; handle rename "old -> new"
  f="${line:3}"
  f="${f##* -> }"

  # Ignore .claude/ internals
  case "$f" in
    .claude/*) continue ;;
  esac

  if printf '%s' "$f" | grep -Eq "$SRC_REGEX"; then
    # __init__.py 는 무시
    if printf '%s' "$f" | grep -Eq "$SRC_INIT_REGEX"; then
      continue
    fi
    SRC_CHANGED=1
    SRC_LIST="${SRC_LIST}  - ${f}"$'\n'
  fi

  if printf '%s' "$f" | grep -Eq "$TESTS_REGEX"; then
    TESTS_CHANGED=1
  fi
done <<< "$CHANGED"

if [ "$SRC_CHANGED" = "1" ] && [ "$TESTS_CHANGED" = "0" ]; then
  touch "$MARKER"
  {
    echo "[test-coverage-check] src/stock_agent/ 의 Python 파일이 변경됐지만 tests/ 는 갱신되지 않았습니다."
    echo ""
    echo "변경된 src 파일:"
    printf '%s' "$SRC_LIST"
    echo ""
    echo "CLAUDE.md 코드 스타일 정책상 단위 테스트가 권장됩니다 (pytest, 외부 API 목킹)."
    echo "변경에 다음 중 하나가 포함됐다면 unit-test-writer 서브에이전트로 단위 테스트를"
    echo "작성하세요:"
    echo "  - 새 함수/클래스/모듈 추가"
    echo "  - 기존 로직의 분기·경계 변경"
    echo "  - 회귀 위험이 있는 버그 수정"
    echo ""
    echo "단순 포맷·주석·docstring 수정처럼 테스트할 동작이 없는 변경이라면 무시해도 됩니다."
    echo "이 리마인더는 이번 세션에 재표시되지 않습니다."
  } >&2
  exit 2
fi

exit 0
