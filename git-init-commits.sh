#!/usr/bin/env bash
# Obsidian RAG — 커밋 + GitHub 푸시
# 2026-09-16. git 은 사용자 셸에서 돌려야 한다 (macOS TCC 가 에이전트 셸의 디렉터리 열람을 막는다).
#
#   cd ~/Desktop/project/untitled1/n8n && bash git-init-commits.sh
#
# 이미 .git 이 있으면 지우고 다시 만든다 — 커밋 작성자 메일을 noreply 로 바꾸기 위해서다.
# 되돌리기: rm -rf .git   (파일은 그대로 남는다)

set -euo pipefail
cd "$(dirname "$0")"

REPO="n8n-obsidian-rag"
VIS="--public"
NOREPLY="seongj-un@users.noreply.github.com"

if [ -d .git ]; then
  echo "기존 .git 을 지운다 (커밋 메일을 noreply 로 바꾼다)"
  rm -rf .git
fi

git init -q
git config user.name "seongj-un"
git config user.email "$NOREPLY"
git add .gitignore .env.example
git commit -q -m "chore: .gitignore 와 .env.example

n8n 암호화 키가 커밋되면 DB 사본만으로 크리덴셜이 전부 복호화된다.
work-in-progress/ 는 436MB 라 저장소에 둘 것이 아니다."

git add docker-compose.yml initdb/ 2>/dev/null || git add docker-compose.yml
git commit -q -m "chore: docker-compose — n8n 2.36.9 고정, 실행 기록 자동 정리

이미지 태그를 박지 않으면 업그레이드가 조용히 끼어든다. 2026-09-14 에
2.36.9 → 2.38.7 로 올라가며 Node 26 이 되었고, 커뮤니티 노드의 isolated-vm
프리빌드가 없어 컴파일을 시도하다 실패했다(하드닝 이미지에 빌드도구가 없다).

EXECUTIONS_DATA_PRUNE 은 기본이 꺼져 있어 DB 가 계속 자란다. ② 는 이제
디스코드 메시지마다 실행이 하나씩 생긴다."

git add workflows/00-rag-core.json
git commit -q -m "feat(core): RAG 코어를 서브워크플로로 분리

②·④ 가 검색·근거 판정·노트 선별·Answer 를 각자 복제하고 있었다.
workflow_history 를 훑어 보니 하루에 같은 노드를 여덟 번 짝지어 고쳤다
(근거 판정 x4 · 노트 선별 x2 · Answer x2). 어긋나면 ④ 점수가 ② 를 대변하지 못한다.

- 근거 판정: 거리 3절 + 어휘 절. best2mean/spread/lexHit
- 노트 선별: 거리순 BAND 1.08, 비교형만 1.28, 예산 11000자
- 질문 되붙이기: 집계 노드가 질문을 삼켜 getQuestion() 이 상류를 뒤지는데
  그 노드가 경계 밖이 됐다. 집계 뒤에 되붙여 두 JS 를 한 글자도 안 고쳤다"

git add workflows/01-vault-index.json
git commit -q -m "feat(index): 볼트 증분 색인

해시로 대조해 바뀐 노트만 임베딩한다. 파일 읽기는 싸고 임베딩이 비싸다.
태스크 러너에 crypto 가 없어 cyrb53 을 직접 구현했다.
볼트를 하나도 못 읽으면 던져서 멈춘다 — 마운트가 끊겼을 때
Sweep 이 인덱스를 통째로 지우는 걸 막는 안전장치다."

git add workflows/02-discord-qa.json
git commit -q -m "feat(discord): 질의응답 — 대화 맥락·스레드·출처 링크·거절 기록

- 맥락 재작성: 지시대명사(REF)와 접속부사(CONN)를 갈랐다. CONN 을 방아쇠로
  쓰면 '그럼 임베딩은 뭐야?' 가 앞 주제로 오염된다. 후속 질문 0/8 → 7/8
- 게이트 재검색: 맨몸으로 막히면 그때만 맥락을 붙여 다시 검색한다.
  통과 경로는 +1~9ms, 차단이면 LLM 을 안 부르므로 두 배가 아니다
- 스레드: 커뮤니티 노드가 스레드를 지원하지 않아 REST 를 직접 부른다.
  토큰은 크리덴셜이 Authorization 헤더로 넣는다
- Reply: 커뮤니티 노드는 캐시에 없는 채널이면 **조용히 아무것도 안 한다**.
  REST 로 바꿔 Discord 가 만든 메시지 id 를 돌려받는다
- 받을까?: 트리거를 모든 메시지로 열되 봇이 답하던 스레드에서만 멘션 없이 받는다"

git add workflows/03-error-alert.json workflows/06-note-open-bridge.json
git commit -q -m "feat: 오류 알림과 노트 열기 다리

Discord 는 마스크 링크 URL 로 http(s) 만 받는다. obsidian:// 는 본문에서도
임베드에서도 글자 그대로 남았다(실측). 그래서 http 로 받아 302 로 넘긴다.
링크는 이 기계에서만 열린다 — 개인 볼트라 그게 맞다."

git add workflows/04-quality-eval.json
git commit -q -m "feat(eval): 답변 품질 평가 — 채점은 코드가 한다

qwen3:4b 는 흠을 정확히 찾아 적어 놓고도 숫자는 5를 준다. 그래서 모델에게는
찾는 일만 시키고(sameTopic·required·covered·gaveReason) 점수는 Record 가 만든다.
표본 40건에서 나쁜 답에 만점 주는 비율 60% → 15%, 건당 15.2초 → 약 6초.

- expectHit/devInCtx: 모델을 안 부르는 결정론적 지표. 같은 골든을 두 번 돌리면
  선별 노트는 60/60 같은데 채점 점수는 47/60 만 같다(답변 temp 0.4).
  회귀는 이 줄로 본다 — 종합 점수는 ±0.1 노이즈다
- 2턴 재생: prev_question 이 있으면 코어를 두 번 불러 대화를 재현한다
- 호출 간격: Gemini 무료 티어가 분당 24회에서 429 를 낸다. 폴백이 노드 안에서
  걸려 429 가 조용히 gemma3 답변으로 바뀌면 무엇을 잰 건지 알 수 없게 된다"

git add workflows/05-refusal-digest.json
git commit -q -m "feat(digest): 못 답한 질문을 모아 볼트에 남긴다

게이트가 막은 질문은 '내 볼트에 없는 것' 의 정확한 목록이다. 공부 볼트에서
이보다 값진 신호가 없는데 그동안 그냥 버려지고 있었다.

- 코어가 아니라 ② 에만 기록한다. 코어에 넣으면 ④ 평가가 통째로 들어와
  '사람이 실제로 물었는데 못 받았다' 는 신호를 덮는다
- 사용자 id 는 해시, 이름은 안 남긴다. 용도가 '무슨 노트를 쓸까' 지
  '누가 물었나' 가 아니다. 다만 한 사람이 3번과 3명이 1번씩은 우선순위가 다르다
- 분류는 lexHit 으로 가른다. 거리로는 안 갈린다(실측: 볼트에 없는 0.60/0.53 vs
  노트 있는 0.42/0.43 이 뒤섞인다). lexHit 은 0.33 vs 0.60~1.00 으로 깨끗하다
- 볼트에 위키링크로 쓴다 — 해당 노트에서 역링크로 '뭐가 빠졌다' 가 보인다"

git add bench/ 2>/dev/null || true
git commit -q -m "test(bench): 오프라인 하네스와 어긋남 검사

배포본과 하네스가 어긋나 검증이 거짓말을 한 적이 두 번 있다.
parity.py 가 배포 JS 를 node 로 돌려 파이썬 구현과 대조한다 — 모델 호출이 없다.

근거 판정 234 · 노트 선별 234 · 답변 정리 265 · 맥락 재작성 60 건 불일치 0.
덤으로 드리프트 검사 6종: 코어 사본이 다시 생겼는가, ④ 속 ② 원문 3종이
바이트 일치하는가, 토크나이저 3사본이 같은가, selfRefused 정규식 2사본이 같은가.

JS 와 파이썬의 함정 둘을 못 박았다:
- \\b 는 ASCII 기준이라 '카프카vs래빗' 에서 갈린다
- 공백 집합이 양방향으로 어긋난다(파이썬은 \\x1c-\\x1f 포함, BOM 제외).
  디스코드 붙여넣기에 BOM 이 실제로 섞인다" || true

git add README.md
git commit -q -m "docs: README — 구성·스택·되돌리는 법"


# ── 푸시 전 안전 검사 ────────────────────────────────────────────────────
echo
echo "=== 커밋 이력에 비밀이 들어갔는지 검사 ==="
LEAK=$(git log --all --numstat --format="" | awk '{print $3}' | sort -u \
  | grep -iE 'sqlite|encryption|\.key$|^\.env$|^work-in-progress/|^backup/' || true)
if [ -n "$LEAK" ]; then
  echo "중단한다 — 비밀이 커밋됐다:"
  echo "$LEAK"
  echo
  echo "rm -rf .git 로 지우고 .gitignore 를 고친 뒤 다시 돌려라."
  exit 1
fi
echo "  ✅ 비밀 파일 없음"

echo
echo "=== 개인정보 검사 ==="
PII=$(git grep -nIE '/Users/[A-Za-z0-9._-]+|[A-Za-z0-9._%+-]+@(naver|gmail|daum)\.' -- . || true)
if [ -n "$PII" ]; then
  echo "중단한다 — 호스트 경로나 개인 메일이 남아 있다:"
  echo "$PII" | head -10
  exit 1
fi
echo "  ✅ 호스트 경로·개인 메일 없음"

echo
echo "=== 커밋 ==="
git --no-pager log --oneline
echo
echo "저장소 크기: $(du -sh .git | cut -f1)"

echo
echo "=== GitHub 에 올린다 ($REPO $VIS) ==="
git branch -M main
if gh repo view "$REPO" >/dev/null 2>&1; then
  echo "  저장소가 이미 있다. 원격만 붙여 푸시한다."
  git remote add origin "https://github.com/$(gh api user -q .login)/$REPO.git" 2>/dev/null || true
  git push -u origin main
else
  gh repo create "$REPO" $VIS --source=. --remote=origin --push \
    --description "옵시디언 볼트를 근거로 디스코드에서 답하는 RAG 봇 (n8n)"
fi

echo
echo "완료: $(gh repo view "$REPO" --json url -q .url)"
