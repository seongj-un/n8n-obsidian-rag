# Obsidian RAG — Discord 봇

옵시디언 볼트를 근거로 디스코드에서 질문에 답하는 RAG 봇. n8n 워크플로로 구성돼 있다.

## 구성

| 워크플로 | 하는 일 |
|---|---|
| `00-rag-core` | 검색 → 근거 판정 → 노트 선별 → 답변 생성. ②·④ 가 공유한다 |
| `01-vault-index` | 볼트를 해시로 대조해 **바뀐 노트만** 다시 색인 |
| `02-discord-qa` | 디스코드 질의응답. 대화 맥락 · 스레드 · 출처 링크 · 거절 기록 · 스레드 삭제 |
| `03-error-alert` | 워크플로 실패를 디스코드로 알린다 |
| `04-quality-eval` | 골든 60문항으로 답변 품질을 주 1회 측정 |
| `05-refusal-digest` | 못 답한 질문을 모아 주간 요약 + 볼트에 노트로 기록 |
| `06-note-open-bridge` | 디스코드 출처 링크를 `obsidian://` 로 넘기는 다리 |

## 스택

- **n8n** `2.36.9` (태그 고정 — `latest` 는 조용히 올라가 커뮤니티 노드를 깨뜨린다)
- **pgvector** (pg16) — 벡터 인덱스 + 세션 · 거절 로그
- **Ollama** — `bge-m3`(임베딩) · `qwen3:4b`(채점) · `gemma3:4b`(답변 폴백)
- **Gemini** `gemini-3.5-flash-lite` — 답변 생성(주)

## 처음 띄우기

```sh
cp .env.example .env      # VAULT_PATH 와 VECTORDB_PASSWORD 를 채운다
docker compose up -d
```

그다음 n8n(http://localhost:5678)에서 **크리덴셜 5개**를 만들고
`workflows/*.json` 을 불러온 뒤 각 노드의 크리덴셜을 연결한다:

| 크리덴셜 | 쓰는 곳 |
|---|---|
| Postgres | 벡터 스토어 · 세션 · 거절 로그 |
| Ollama | 임베딩 · 채점 · 답변 폴백 |
| Google Gemini (PaLM) | 답변 생성 |
| Discord Bot Trigger | ② 트리거 · 스레드 · 전송 |
| Discord Webhook | ③ 오류 알림 · ⑤ 주간 요약 |

**자기 환경에 맞게 바꿔야 하는 것**

- `02-discord-qa` 의 봇 user id 2개 (`Discord Trigger` 의 정규식, `받을까?` 의 조건)
- `06-note-open-bridge` 의 `VAULT`(옵시디언 볼트 이름)와 `PREFIX`(볼트 기준 하위 폴더)
- `05-refusal-digest` 가 쓰는 노트 경로 `/home/node/vault/rag-unanswered.md`

## 디렉터리

```
workflows/   n8n 워크플로 정의 (API 로 내보낸 것)
bench/       오프라인 측정 하네스. parity.py 가 배포본과 동작 일치를 검사한다
initdb/      pgvector 초기화 SQL
docker-compose.yml
```

## 워크플로 되돌리기

```sh
# 파일 → n8n
curl -X PUT http://localhost:5678/api/v1/workflows/<id> \
  -H "X-N8N-API-KEY: $KEY" -H 'Content-Type: application/json' \
  -d @workflows/02-discord-qa.json
```
각 JSON 의 `_id` 가 그 워크플로의 n8n id 다.

## 커밋하지 않는 것

암호화 키 · DB 사본 · `work-in-progress/` · `backup/`. `.gitignore` 참조.
**암호화 키를 잃으면 크리덴셜을 전부 다시 입력해야 한다** — 별도로 안전한 곳에 보관할 것.
