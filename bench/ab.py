import sys, json, sqlite3, time, argparse
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# n8n sqlite 사본 경로. docker cp 로 받아 두고 N8N_DB 로 덮어쓸 수 있다.
DB = os.environ.get('N8N_DB', os.path.join(_HERE, 'database.sqlite'))
B=_HERE
sys.path.insert(0,B)
from rag import embed, search, generate, ground_check
from retrieve_v1 import retrieve as retrieve_v1
from retrieve_v2 import select as select_v2
from prompt_v1 import build as build_v1
from prompt_v0 import build as build_v0
from tidy import tidy
from context import rewrite


ap=argparse.ArgumentParser(
    epilog="""--pairs 파일 모양 (JSON 배열). 원소는 둘 중 하나다.

  ["리랭킹 알려줘", "그건 왜 비싸?"]        대화 — 차례를 **순서대로 실제로 돌린다**.
                                          앞 차례의 진짜 답변에서 answer_lead 를 뽑아
                                          다음 차례의 prev 로 넘긴다(배포 `세션 기록` 과 같은 방식).
                                          원소 1개짜리 리스트는 그냥 단발 질문이다.

  {"question": "그건 왜 비싸?",             prev 를 직접 준다 — 앞 차례를 돌리지 않으므로
   "prev": {"question": "리랭킹 알려줘",     모델 호출이 절반이고 TTL 경계를 age_s 로 딱 집을 수 있다.
            "answer_lead": "리랭킹은 …",     prev 키: question / answer_lead / topic /
            "topic": "리랭킹",               updated_at 또는 age_s.
            "age_s": 5},
   "expect": "…"}
""", formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--retrieval',default='v0',choices=['v0','v1','v2'])
ap.add_argument('--model',default='gemma3:4b')
ap.add_argument('--topk',type=int,default=16)   # ② Obsidian Vector Store / ④ Retrieve 의 배포값
ap.add_argument('--prompt',default='v0',choices=['v0','v1'])
ap.add_argument('--pairs',help='(직전질문, 질문) 쌍 / 대화 JSON 파일. 생략하면 데이터테이블의 단발 질문을 쓴다')
ap.add_argument('--out',required=True)
a=ap.parse_args()

# ⚠️ 생성 모델만은 배포본과 다르다 — README '하네스가 대변하지 못하는 것' 참고.
print("⚠️  배포본 ②/④ 의 주 답변 모델은 Gemini(models/gemini-3.5-flash-lite)다. "
      f"이 하네스는 ollama 만 부르므로 지금 재는 것은 ② 의 **폴백 경로**({a.model})다.\n"
      "   검색·근거 판정·노트 선별·답변 정리는 모델과 무관하므로 그대로 배포본을 대변한다.",
      flush=True)


# ── 입력 ────────────────────────────────────────────────────────────────────
# 단발 질문은 데이터테이블에서, 후속 질문은 --pairs 파일에서. 둘 다 아래 같은 모양으로
# 펴서 한 루프로 돌린다 — 단발 경로는 prev=None 이라 rewrite() 가 질문을 한 글자도
# 안 바꾼다. 즉 --pairs 를 안 주면 예전과 **완전히 같은 것**을 잰다.
#   턴 = {conv, turn, question, expect, prev, chain}
#         prev  — 이 차례에 물려줄 직전 차례 dict (없으면 None)
#         chain — True 면 prev 를 앞 차례의 **실제 결과**로 채운다(그때 prev 는 무시)
def load_turns():
    if not a.pairs:
        qs = sqlite3.connect(DB).execute(
            "select question, expect from data_table_user_kXB0IQeAeI6Y566S "
            "where enabled=1 order by id").fetchall()
        return [{'conv': i, 'turn': 0, 'question': q, 'expect': e, 'prev': None, 'chain': False}
                for i, (q, e) in enumerate(qs)]
    turns = []
    for ci, item in enumerate(json.load(open(a.pairs, encoding='utf-8'))):
        if isinstance(item, str):
            item = [item]
        if isinstance(item, (list, tuple)):        # 대화 — 앞 차례를 실제로 돌려 prev 를 만든다
            for ti, q in enumerate(item):
                turns.append({'conv': ci, 'turn': ti, 'question': q, 'expect': None,
                              'prev': None, 'chain': ti > 0})
        elif isinstance(item, dict):               # prev 를 직접 준 단일 차례
            turns.append({'conv': ci, 'turn': 0, 'question': item['question'],
                          'expect': item.get('expect'), 'prev': item.get('prev'),
                          'chain': False})
        else:
            raise SystemExit(f'--pairs {ci}번 원소를 못 읽겠다: {item!r}')
    return turns


turns = load_turns()


def session_record(question, ctx, answer):
    """배포 `세션 기록` 노드와 같은 모양으로 이번 차례를 남긴다 — 다음 차례의 prev 가 된다.
       lead = 정리된 답변의 첫 비어 있지 않은 줄에서 ** 를 떼고 200자. 근거 없음이면 빈 문자열."""
    lead = ''
    if answer:
        first = next((l for l in answer.split('\n') if l.strip()), '')
        lead = first.replace('**', '').strip()[:200]
    return {'question': ctx['rawQuestion'] or question, 'answer_lead': lead,
            'topic': ctx['ctxTopic'], 'age_s': 0}


embed("워밍업", keep_alive="30m")            # 콜드로드 비용을 측정에서 분리한다
results=[]
last={}                                      # conv → 직전 차례의 세션 기록
for t in turns:
    q, expect = t['question'], t['expect']
    prev = last.get(t['conv']) if t['chain'] else t['prev']
    # ── 맥락 재작성 (배포 ② 에서 검색 **앞단**) ──────────────────────────────
    # 검색·근거 판정·노트 선별은 재작성된 question 을, 답변 프롬프트는 rawQuestion 과
    # contextBlock 을 읽는다. 배포 Answer 노드의 user 텍스트와 같은 조립이다.
    ctx = rewrite(q, prev)
    sq, raw = ctx['question'], ctx['rawQuestion']

    v, te = embed(sq, keep_alive="30m")
    if a.retrieval=='v0':
        docs, ts = search(v, topk=a.topk)
        probe = docs
    elif a.retrieval=='v1':
        docs, probe, ts = retrieve_v1(v, topk=a.topk)
    else:
        probe, ts = search(v, topk=a.topk)
        # 배포본은 질문을 상류 노드에서 가져온다(② `맥락 재작성` → `질문 정리` / ④ `Loop Over Questions`).
        # 하네스는 인자로 넘긴다 — 안 넘기면 비교형 BAND 와 어휘 절이 통째로 꺼진다.
        docs = select_v2(probe, sq)
    g = ground_check(probe, sq)              # 판정은 언제나 정찰 top-K 모양으로 한다
    rec = {"question": sq, "raw_question": raw, "expect": expect, **g,
           "conv": t['conv'], "turn": t['turn'], "follow_up": ctx['followUp'],
           "ctx_topic": ctx['ctxTopic'], "prev_question": ctx['ctxPrevQuestion'],
           "embed_s": round(te,2), "search_s": round(ts,2),
           "notes": sorted({(d.get("metadata") or {}).get("source") for d in docs}),
           "ctx_chars": sum(len(d["text"]) for d in docs), "n_chunks": len(docs)}
    if g["grounded"]:
        # 프롬프트에는 **원문**을 넣는다. 재작성은 추측이고, 잘못 추측하면 사용자가 하지도
        # 않은 질문에 답하게 된다 — 직전 차례는 contextBlock 으로 따로 붙여 모델이 스스로 푼다.
        if a.prompt=='v0':
            p, sysmsg = build_v0(raw, docs), None
        else:
            sysmsg, p = build_v1(raw, docs)
        p = ctx['contextBlock'] + p          # 후속일 때만 비어 있지 않다
        r = generate(p, model=a.model, keep_alive="30m", system=sysmsg)
        # 배포본은 Answer 뒤에 `답변 정리` 노드가 붙는다. Discord가 실제로 보는 건 정리본이다.
        rec.update(answer=tidy(r["text"]), answer_raw=r["text"], prompt_chars=len(p), prefill_s=round(r["prefill_s"],2),
                   decode_s=round(r["decode_s"],2), in_tok=r["in_tok"], out_tok=r["out_tok"],
                   wall_s=round(te+ts+r["total_s"],2))
    else:
        rec.update(answer=None, wall_s=round(te+ts,2), prefill_s=0, decode_s=0, in_tok=0, out_tok=0)
    results.append(rec)
    last[t['conv']] = session_record(q, ctx, rec["answer"])
    mark = ('  ↳ ' if t['turn'] else '') + (f"{raw[:20]} → {sq[:20]}" if ctx['followUp'] else q[:22])
    print(f"  {mark:26s} grounded={str(g['grounded']):5s} ctx={rec['ctx_chars']:5d}자 "
          f"wall={rec['wall_s']:5.1f}s notes={len(rec['notes'])}"
          + ("  [후속]" if ctx['followUp'] else ""), flush=True)

json.dump({"config": vars(a), "results": results}, open(a.out,"w"), ensure_ascii=False, indent=2)
gr=[r for r in results if r["grounded"]]
fu=[r for r in results if r["follow_up"]]
print(f"\n[검색 {a.retrieval} / 프롬프트 {a.prompt} / {a.model}] 근거통과 {len(gr)}/{len(results)} · "
      f"평균 컨텍스트 {sum(r['ctx_chars'] for r in results)//len(results)}자 · "
      f"평균 wall {sum(r['wall_s'] for r in results)/len(results):.1f}s · "
      f"prefill {sum(r['prefill_s'] for r in gr)/max(len(gr),1):.1f}s · "
      f"decode {sum(r['decode_s'] for r in gr)/max(len(gr),1):.1f}s")
if fu:
    print(f"후속으로 판정된 차례 {len(fu)}/{len(results)} · "
          f"그중 근거통과 {sum(1 for r in fu if r['grounded'])}/{len(fu)}")
