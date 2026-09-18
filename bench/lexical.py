"""어휘 채널 시제품 — 아직 배포본에 없다. 프로덕션을 건드리지 않는 실험용.

`dev/obsidian-rag-hybrid-plan` A안은 bge-m3 의 sparse 헤드를 전제하는데,
**ollama 는 dense 만 준다**(`/api/embed` 응답에 embeddings 뿐, `/api/embed_full` 404).
sparse 를 쓰려면 FlagEmbedding+torch 사이드카가 필요하다 — "한계비용 0" 이 아니다.

그래서 여기서는 같은 노트의 C안(자체 역색인)을 재본다. C안의 기각 사유였던
"IDF·길이 정규화가 없다"는 코퍼스가 고정(904청크)이라 BM25 를 그대로 계산하면 해소된다.
토크나이저는 **배포된 `근거 판정` 의 것을 그대로 쓴다**(`rag.content_tokens`) —
질의와 문서를 같은 규칙으로 쪼개야 하고, 그 규칙은 이미 parity 로 검증돼 있다.

모델을 부르지 않는다. `import` 만으로는 아무것도 돌지 않는다.
"""
import json, math, subprocess
from collections import Counter

import rag

K1, B = 1.2, 0.75          # BM25 표준값
_IDX = None


def _fetch_chunks():
    sql = ("select json_agg(row_to_json(t)) from (select text, metadata from n8n_vectors) t;")
    out = subprocess.run(["docker", "exec", "-i", "n8n-vectordb-1", "psql", "-U", "n8n",
                          "-d", "obsidian_rag", "-tAc", sql],
                         capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError(out.stderr[:500])
    return json.loads(out.stdout.strip())


def build():
    """코퍼스 전체를 배포 토크나이저로 쪼개 BM25 역색인을 만든다."""
    global _IDX
    rows = _fetch_chunks()
    docs = []
    df = Counter()
    for r in rows:
        toks = [t for t, _ in rag.content_tokens(r["text"])]
        tf = Counter(toks)
        docs.append({"src": r["metadata"]["source"],
                     "line": int(r["metadata"]["loc"]["lines"]["from"]),
                     "text": r["text"], "tf": tf, "len": max(len(toks), 1)})
        for t in tf:
            df[t] += 1
    N = len(docs)
    avgdl = sum(d["len"] for d in docs) / N
    idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
    _IDX = {"docs": docs, "idf": idf, "avgdl": avgdl, "N": N}
    return _IDX


def search(question, topn=16):
    """BM25 상위 topn. 반환 모양은 dense 검색과 맞춘다(score 는 BM25 점수 — 클수록 좋다)."""
    ix = _IDX or build()
    qt = [t for t, _ in rag.content_tokens(question)]
    if not qt:
        return []
    scored = []
    for d in ix["docs"]:
        s = 0.0
        for t in qt:
            f = d["tf"].get(t, 0)
            if not f:
                continue
            s += ix["idf"].get(t, 0.0) * f * (K1 + 1) / (f + K1 * (1 - B + B * d["len"] / ix["avgdl"]))
        if s > 0:
            scored.append({"source": d["src"], "line": d["line"], "text": d["text"], "bm25": s})
    scored.sort(key=lambda x: -x["bm25"])
    return scored[:topn]


def augment(picked_docs, picked_notes, question, cap_per_note=2, budget=11000):
    """A안 그대로 — **이미 선별된 노트 안에서만** dense 가 놓친 청크를 BM25 상위로 채운다.

    노트 목록도, dense 순위도, 게이트도 건드리지 않는다. 그래서 임계값 재측정이 필요 없다.
    반환: (합친 청크 리스트, 추가된 것들)
    """
    have = {(d["metadata"]["source"], int(d["metadata"]["loc"]["lines"]["from"])) for d in picked_docs}
    used = sum(len(d["text"]) for d in picked_docs)
    per = Counter()
    added = []
    for c in search(question, topn=60):
        if c["source"] not in picked_notes:
            continue
        key = (c["source"], c["line"])
        if key in have or per[c["source"]] >= cap_per_note:
            continue
        if used + len(c["text"]) > budget:
            break
        have.add(key); per[c["source"]] += 1; used += len(c["text"])
        added.append(c)
    merged = [{"metadata": {"source": d["metadata"]["source"],
                            "loc": d["metadata"]["loc"]}, "text": d["text"]} for d in picked_docs]
    merged += [{"metadata": {"source": c["source"], "loc": {"lines": {"from": c["line"]}}},
                "text": c["text"]} for c in added]
    merged.sort(key=lambda d: (d["metadata"]["source"], d["metadata"]["loc"]["lines"]["from"]))
    return merged, added
