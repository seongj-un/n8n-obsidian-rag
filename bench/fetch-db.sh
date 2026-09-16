#!/bin/sh
# 골든 질문(n8n Data Table)과 배포된 워크플로를 읽으려면 n8n의 sqlite 사본이 필요하다.
# 암호화된 크리덴셜이 들어 있으므로 저장소에 커밋하지 말 것 — .gitignore 에 넣어 뒀다.
#
# -wal 을 반드시 함께 받는다. n8n 은 WAL 모드로 돌아서 최근 커밋이 아직 database.sqlite 에
# 반영되지 않은 채 -wal 에만 있다. 본체만 받으면 오늘 고친 워크플로가 안 보인다.
set -e
cd "$(dirname "$0")"
docker cp n8n-n8n-1:/home/node/.n8n/database.sqlite     ./database.sqlite
docker cp n8n-n8n-1:/home/node/.n8n/database.sqlite-wal ./database.sqlite-wal
echo "받음: $(du -h database.sqlite | cut -f1) + wal $(du -h database.sqlite-wal | cut -f1)"
