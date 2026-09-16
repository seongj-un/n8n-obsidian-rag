"""검색 v1 — 노트 단위 선별 + 노트 전체 확장(parent-document retrieval).

근거:
  코퍼스가 90노트/339청크(노트당 평균 3.8청크, 노트 전체가 평균 2.9천자)로 작다.
  청크 top-8을 그대로 쓰면 관련 노트 2개 + 무관 노트 4~6개가 섞여 4B 모델이 희석된다.
  top-8 안에서 같은 노트가 2청크 이상 차지하면 그건 진짜 히트고, 1청크짜리는 대부분 노이즈다
  (12개 골든 질문 전부에서 성립 — 근거 판정 노드가 이미 sameTop으로 같은 신호를 쓴다).
  히트로 뽑힌 노트는 청크를 골라 담지 말고 원문 순서대로 통째로 넣는다 — 청크 경계에서
  잘린 파라미터·트레이드오프 설명이 되살아난다(HNSW 문항의 실패 원인).
"""
import subprocess, json, time
from collections import defaultdict

MIN_CHUNKS_PER_NOTE = 2   # top-K 안에서 이만큼 차지해야 '진짜 히트'
MAX_NOTES           = 3   # 프롬프트가 무한정 커지지 않게
FALLBACK_CHUNKS     = 3   # 2청크 이상인 노트가 하나도 없을 때

def _sql(q):
    out = subprocess.run(["docker","exec","-i","n8n-vectordb-1","psql","-U","n8n",
        "-d","obsidian_rag","-tAc",q], capture_output=True, text=True, timeout=60)
    if out.returncode: raise RuntimeError(out.stderr[:500])
    s = out.stdout.strip()
    return json.loads(s) if s else []

def retrieve(vec, topk=8, table="n8n_vectors"):
    lit = "[" + ",".join(f"{v:.7g}" for v in vec) + "]"
    t = time.time()
    # 1) 청크 top-K — 어느 노트가 뜨는지 보기 위한 정찰
    probe = _sql(f"select json_agg(row_to_json(t)) from (select id, text, metadata, "
                 f"(embedding <=> '{lit}'::vector) as score from {table} "
                 f"order by embedding <=> '{lit}'::vector limit {topk}) t;") or []
    src = lambda d: (d.get("metadata") or {}).get("source")

    owned = defaultdict(list)
    for d in probe:
        if src(d): owned[src(d)].append(d["score"])

    hits = [(s, min(v)) for s, v in owned.items() if len(v) >= MIN_CHUNKS_PER_NOTE]
    hits.sort(key=lambda x: x[1])
    notes = [s for s, _ in hits[:MAX_NOTES]]

    if not notes:
        # 히트로 볼 노트가 없다 — 근거 판정이 어차피 막을 자리지만 형태는 유지한다
        return probe[:FALLBACK_CHUNKS], probe, time.time() - t

    # 2) 뽑힌 노트의 청크를 원문 순서대로 통째로 — 선별하지 않는다
    inlist = ",".join("'" + s.replace("'","''") + "'" for s in notes)
    docs = _sql(f"select json_agg(row_to_json(t)) from (select id, text, metadata, "
                f"(embedding <=> '{lit}'::vector) as score from {table} "
                f"where metadata->>'source' in ({inlist})) t;") or []
    # 원문 순서는 uuid가 아니라 loc.lines.from 으로만 복원된다 (색인 시 텍스트 스플리터가 남긴 값)
    line_of = lambda d: (((d.get("metadata") or {}).get("loc") or {}).get("lines") or {}).get("from", 0)
    order = {s: i for i, s in enumerate(notes)}
    docs.sort(key=lambda d: (order.get(src(d), 99), line_of(d)))
    return docs, probe, time.time() - t
