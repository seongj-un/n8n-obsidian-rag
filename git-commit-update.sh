#!/usr/bin/env bash
# 수정사항 커밋 + 푸시. 2026-09-16
#   cd ~/Desktop/project/untitled1/n8n && bash git-commit-update.sh
set -euo pipefail
cd "$(dirname "$0")"

git add workflows/02-discord-qa.json README.md
git commit -q -m "feat(discord): 스레드에서 '삭제' 한 마디로 그 스레드를 지운다

대화가 끝난 스레드를 손으로 지우는 게 번거롭다. 스레드 안에서 '삭제' 만
치면 그 스레드와 세션이 함께 사라진다.

오발 방지 — 조건 셋을 전부 만족해야 한다 (13가지 경우로 확인):
- 멘션·!ask 를 걷어낸 본문이 '삭제' 계열 한 단어와 **정확히 일치**해야 한다.
  '삭제하는 방법 알려줘' · '인덱스 삭제는 어떻게 해?' 는 질문으로 처리된다
- 봇이 답하던 스레드 안이어야 한다(rag_chat_sessions.is_thread)
- 일반 채널에서는 절대 안 걸린다 — 채널을 지우는 사고를 막는다

되돌릴 수 없다. 스레드 안의 질문·답변이 함께 사라진다.
봇에 Manage Threads 권한이 필요하다 — 없으면 403 이고, 그래도 봇은
멈추지 않는다(onError=continue). 세션 행은 지워 유령 맥락을 남기지 않는다."

echo
echo "=== 비밀·개인정보 검사 ==="
LEAK=$(git log --all --numstat --format="" | awk '{print $3}' | sort -u \
  | grep -iE 'sqlite|encryption|\.key$|^\.env$|^work-in-progress/|^backup/' || true)
[ -n "$LEAK" ] && { echo "중단 — 비밀이 커밋됐다:"; echo "$LEAK"; exit 1; }
PII=$(git grep -nIE '/Users/[A-Za-z0-9._-]+|[A-Za-z0-9._%+-]+@(naver|gmail|daum)\.' -- . || true)
[ -n "$PII" ] && { echo "중단 — 개인정보가 남아 있다:"; echo "$PII" | head; exit 1; }
echo "  ✅ 없음"

git push
echo
git --no-pager log --oneline -3
