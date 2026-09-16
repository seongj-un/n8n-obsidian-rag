"""첫 줄 핵심 문장이 살아 있는지를 경로별로 확인한다.
   n8n의 ChatOllama는 /api/chat 으로 role:system 을 보낸다. 오프라인 벤치는 /api/generate 의
   system 파라미터를 썼다 — 같은 내용이라도 템플릿이 달라 형식 준수가 갈릴 수 있다."""
import sys, os, json, sqlite3, urllib.request
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# n8n sqlite 사본 경로. ./fetch-db.sh 로 받거나 N8N_DB 로 덮어쓴다. (전에는 정의가 빠져 NameError 였다)
DB = os.environ.get('N8N_DB', os.path.join(_HERE, 'database.sqlite'))
from rag import embed, search
from retrieve_v2 import select
from prompt_v0 import build as build_v0
from prompt_v1 import SYSTEM as SYSTEM_MSG

def post(path, p):
    r=urllib.request.Request("http://localhost:11434"+path, data=json.dumps(p).encode(),
                             headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(r, timeout=600))
OPT={"num_ctx":8192,"temperature":0.4,"penalize_newline":False}

def ctx_of(docs):
    return "\n\n---\n\n".join("[출처: "+((d.get("metadata") or {}).get("source") or "알 수 없음")+"]\n"+(d.get("text") or "")
                              for d in docs)
def lead_ok(t):
    first=(t or "").strip().split("\n")[0].strip()
    return bool(first) and not first.startswith("-") and not first.startswith("*")

if __name__ != '__main__':
    raise SystemExit(0)   # ollama 를 부르는 스크립트다. import 로 돌지 않게 한다.

qs=[r[0] for r in sqlite3.connect(DB).execute(
    "select question from data_table_user_kXB0IQeAeI6Y566S where enabled=1 order by id limit 6")]
print(f"{'질문':>16s} | {'chat+system':>12s} | {'chat 통짜':>10s} | {'현행(A)':>8s}")
tally={'a':0,'b':0,'c':0}
for q in qs:
    v,_=embed(q,keep_alive="4h"); probe,_=search(v,topk=16); docs=select(probe, q)   # 비교형 BAND 는 질문이 있어야 켜진다
    user="다음은 사용자의 Obsidian 노트에서 검색된 내용입니다.\n\n"+ctx_of(docs)+"\n\n=====\n질문: "+q+"\n답변:"
    # A안: system 분리 (지금 배포된 것)
    a=post("/api/chat",{"model":"gemma3:4b","stream":False,"keep_alive":"4h","options":OPT,
        "messages":[{"role":"system","content":SYSTEM_MSG},{"role":"user","content":user}]})["message"]["content"]
    # B안: 같은 내용을 user 한 턴에 통짜로 (system 역할 안 씀)
    b=post("/api/chat",{"model":"gemma3:4b","stream":False,"keep_alive":"4h","options":OPT,
        "messages":[{"role":"user","content":SYSTEM_MSG+"\n\n=====\n"+user}]})["message"]["content"]
    # C안: 현행 프롬프트 그대로 (고정 블록이 문서 뒤)
    c=post("/api/chat",{"model":"gemma3:4b","stream":False,"keep_alive":"4h","options":OPT,
        "messages":[{"role":"user","content":build_v0(q,docs)}]})["message"]["content"]
    for k,t in (('a',a),('b',b),('c',c)):
        if lead_ok(t): tally[k]+=1
    print(f"{q[:15]:>16s} | {('OK' if lead_ok(a) else '없음'):>12s} | {('OK' if lead_ok(b) else '없음'):>10s} | {('OK' if lead_ok(c) else '없음'):>8s}")
print(f"{'첫줄 유지':>16s} | {tally['a']}/{len(qs)}".ljust(34) + f"| {tally['b']}/{len(qs)}".ljust(13) + f"| {tally['c']}/{len(qs)}")
