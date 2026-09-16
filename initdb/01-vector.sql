-- n8n의 PGVector 노드가 테이블은 알아서 만들지만 확장은 만들지 않는다.
-- DB 최초 생성 시 한 번 실행된다.
CREATE EXTENSION IF NOT EXISTS vector;
