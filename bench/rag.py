"""n8n 워크플로 ②를 오프라인으로 그대로 복제한 하네스.
   임베딩(ollama) → pgvector 검색 → 프롬프트 조립 → 생성(ollama). 타이밍을 단계별로 쪼갠다."""
import json, math, re, subprocess, time, urllib.request

OLLAMA = "http://localhost:11434"
EMBED_MODEL = "bge-m3:latest"
CHAT_MODEL = "gemma3:4b"

def _post(path, payload, timeout=600):
    req = urllib.request.Request(OLLAMA + path,
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def embed(text, model=EMBED_MODEL, keep_alive=None):
    p = {"model": model, "input": text}
    if keep_alive is not None: p["keep_alive"] = keep_alive
    t = time.time()
    d = _post("/api/embed", p)
    return d["embeddings"][0], time.time() - t

def search(vec, topk=16, table="n8n_vectors"):
    """n8n PGVector 노드와 동일: 코사인 거리 <=> 오름차순 topK. 배포값은 topK=16."""
    lit = "[" + ",".join(f"{v:.7g}" for v in vec) + "]"
    sql = (f"select json_agg(row_to_json(t)) from (select text, metadata, "
           f"(embedding <=> '{lit}'::vector) as score from {table} "
           f"order by embedding <=> '{lit}'::vector limit {topk}) t;")
    t = time.time()
    out = subprocess.run(["docker","exec","-i","n8n-vectordb-1","psql","-U","n8n",
        "-d","obsidian_rag","-tAc",sql], capture_output=True, text=True, timeout=60)
    if out.returncode: raise RuntimeError(out.stderr[:500])
    return json.loads(out.stdout.strip()), time.time() - t

def generate(prompt, model=CHAT_MODEL, num_ctx=8192, temperature=0.4,
             num_predict=None, keep_alive=None, system=None):
    opts = {"num_ctx": num_ctx, "temperature": temperature, "penalize_newline": False}
    if num_predict is not None: opts["num_predict"] = num_predict
    p = {"model": model, "prompt": prompt, "stream": False, "options": opts}
    if keep_alive is not None: p["keep_alive"] = keep_alive
    if system: p["system"] = system
    d = _post("/api/generate", p)
    ns = 1e9
    return {
        "text": d["response"],
        "load_s": d.get("load_duration",0)/ns,
        "prefill_s": d.get("prompt_eval_duration",0)/ns,
        "decode_s": d.get("eval_duration",0)/ns,
        "total_s": d.get("total_duration",0)/ns,
        "in_tok": d.get("prompt_eval_count",0),
        "out_tok": d.get("eval_count",0),
    }

# ── 근거 판정 (4세대) ───────────────────────────────────────────────────────
# ②/④ 의 `근거 판정` 노드(4세대, 2026-09-15 배포)를 그대로 옮긴 것.
# ② 와 ④ 의 JS 는 바이트 단위로 동일하다(2026-09-15 확인, sha1 09ca75503128).
# 값과 로직을 고칠 때는 n8n 노드와 여기를 반드시 함께 고칠 것.
#
# 세대 요약
#   1·2세대 — "1위가 얼마나 독보적인가"(best/mean8, best/1위노트제외최소). 답이 두 노트에
#             걸친 비교형 질문은 1·2위가 동률이라 지표가 나빠져 둘 다 실패했다.
#   3세대   — 대비를 버리고 낙폭(spread)을 본다. 볼트에 없는 질문은 top-8 이 좁은 띠에
#             뭉치고(spread 작음), 있는 질문은 관련 노트만 확 가깝다(spread 큼).
#   4세대   — 3세대에 **어휘 절**을 OR 로 덧붙였다. 상수와 기존 두 절은 한 글자도 안 바뀌어서
#             기존에 통과하던 질문의 판정은 한 건도 달라지지 않는다.
#
# 왜 어휘인가: best / best2mean / spread 는 전부 같은 거리 공간에서 나와 서로 상관이 높다
# (|r| 0.4~0.6). 임계값을 굴려도 OUT 분포 안쪽에 박힌 IN 은 못 꺼낸다. 직교하는 축이
# 필요하다 — "질문의 내용어가 회수된 청크에 **문자 그대로** 나오는가".
#   데드락 → study/deadlock.md 본문에 그대로 있다 · 러스트/스위프트 → 볼트 어디에도 없다
#
# [2026-09-15 배포본 측정 — IN 57 / OUT 22 / 적대적 스트레스 20]
#   골든 오차단 3 → 0 · 음성 대조군 누수 0 → 0 · 골든 27문항 27/27
#   대가: 적대적 스트레스 6/20 → 8/20 (새로 열린 2건은 "트랜잭션 로그 파일 크기 관리",
#   "정렬 알고리즘 병렬 구현"). 볼트 어휘로만 된 질문에는 약하다.
#
# [한계 — 상수를 손대기 전에 읽을 것]
#   · OUT 22건 위에서 잰 "누수 0" 이다. 표본이 작다.
#   · LEX 의 실제 여유는 "토큰 1개" 다. 0.75~1.00 이 전부 같은 규칙("미적중 0개")이고
#     0.75 로 내리면 즉시 누수 2건. 숫자 여유 0.15 는 착시다.
#   · 음차 매핑(도커↔docker)을 추가하면 그 순간 샌다 — 하지 마라.
#   · FAR2 는 측정된 값이 아니다. 이 표본에서 0.60~0.70 아무 값이나 성적이 같다.
#   · STOP 목록이 모자라면 구제를 못 받을 뿐 누수로는 가지 않는다(어휘 절이 안 켜지고
#     3세대 동작으로 되돌아간다). 코퍼스가 아니라 한국어 문법에 대한 의존이라 재색인해도
#     안 깨진다.
NEAR = 0.420     # 1차 절대컷 — best2mean 이 이보다 가까우면 구조를 안 봐도 통과
FAR = 0.58       # 2차 절대 상한 — 이보다 멀면 낙폭이 커도 통과시키지 않는다
FALL = 0.098     # 2차 낙폭컷 — 창 안에서 이만큼 떨어져야 "구조가 있다"
FAR2 = 0.62      # 어휘 절 절대 상한 (데드락 b2m=0.5707 에 여유 0.049)
LEX = 0.90       # 어휘 절 컷 — 사실상 "내용어가 하나도 안 빠졌는가"(1.0)
                 # 평탄 구간 0.75 < LEX <= 1.0. 0.75 에서는 누수 2건이 난다.
# spread 는 창의 마지막 청크에 의존한다. 그래서 창을 top-8로 못박는다 — 검색 topK 를
# 16으로 넓혀도 이 보정이 깨지지 않게 하려는 것이다. topK 를 8 밑으로 줄이면 다시 재야 한다.
WINDOW = 8       # 거리 특징(best/best2mean/spread)이 보는 창
# 어휘 창은 거리 창과 **다르다**. 측정상 top-4~8 이 전부 (오차단 0, 누수 0, 구제 3/3) 로
# 같은데 top-10 부터 누수가 시작된다. 8은 그 경계 바로 앞이라 6으로 두 칸 물러섰다.
LEX_WINDOW = 6

# ── 문서 형태 흡수 ─────────────────────────────────────────────────────────
def _body(d):
    """n8n 은 {document:{pageContent,metadata}, score}, 오프라인 검색은 {text,metadata,score}.
       둘 다 받는다."""
    return (d.get("document") if isinstance(d, dict) and d.get("document") else d) or {}


def _src(d):
    return (_body(d).get("metadata") or {}).get("source") or None


def _r4(v):
    """JS Math.round(v*1e4)/1e4 와 같은 반올림(half-up). 파이썬 round 는 은행가 반올림이라 다르다."""
    return math.floor(v * 10000 + 0.5) / 10000


# ── 한국어 휴리스틱 토크나이저 (형태소 분석기 없이) ──────────────────────────
# 꼬리(조사/어미) — 긴 것부터, 1회만 제거. 2회 돌리면 "유사도랑"→"유사" 로 과제거된다.
TAILS = ["이랑", "에서", "으로", "에게", "한테", "부터", "까지", "보다", "처럼", "마다", "대로",
         "밖에", "조차", "라도", "이나", "하고", "에는", "과는", "와는", "이란", "라는", "이라",
         "은지", "는지", "한지", "인지", "이야", "예요", "이에", "해줘", "해야", "하는",
         "랑", "와", "과", "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "로", "다",
         "요", "야", "지", "고", "서", "며", "면", "나", "까", "죠", "네", "군", "데", "해", "라",
         "게", "걸", "건"]
# 질문 골격어 — 꼬리를 뗀 뒤 정확히 일치하면 버린다.
# 주의: 이 목록이 비면 어휘 절이 거의 발동하지 않는다(= 3세대로 되돌아간다).
STOP = set((
    "뭐 뭘 뭔 뭐야 뭐가 뭔지 무슨 무엇 어떻 어떻게 어떤 어디 언제 누가 얼마 얼마나 왜 "
    "알려 알려줘 설명 설명해 정리 가르쳐 말해 얘기 이야기 "
    "차이 다른 다르 달라 비교 각각 중에 중 골라 골라야 선택 고르 골랐 "
    "써 쓰 쓸 쓴 쓰나 쓰면 쓰는 써야 썼어 썼 사용 적용 "
    "필요 이유 방법 관계 정확 자꾸 헷갈 헷갈리 그냥 그리고 좀 진짜 혹시 대해 대한 관련 "
    "에서 으로 에게 한테 부터 까지 보다 처럼 이랑 하고 "
    "것 거 게 건 걸 수 때 때문 경우 정도 부분 내용 문제 이거 그거 저거 이런 그런 "
    "있어 없어 되나 되는 된다 맞아 맞나 인가 일까 할까 하면 한다 이란 라면").split())

_RUN = re.compile(r"[가-힣]+|[A-Za-z0-9]+(?:[-+.][A-Za-z0-9]+)*")
_LATIN = re.compile(r"^[A-Za-z0-9]")


def content_tokens(q):
    """배포 JS 의 contentTokens(). (토큰, 라틴여부) 튜플 리스트를 등장 순서로 돌려준다."""
    out, seen = [], set()
    for run in _RUN.findall(str(q or "")):
        latin = bool(_LATIN.match(run))
        t = run.lower() if latin else run
        if not latin:
            for tl in TAILS:                      # 꼬리 1회만 제거
                if len(t) > len(tl) and t.endswith(tl):
                    t = t[:-len(tl)]
                    break
        if len(t) < 2 or t in STOP:
            continue
        if t not in seen:
            seen.add(t)
            out.append((t, latin))
    return out


def _hit(tok, latin, hay):
    if not latin:
        return tok in hay
    # 영문/숫자는 단어 경계로 — "m2" 가 "m20" 에 걸리지 않게
    return re.search(r"(?:^|[^a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])", hay) is not None


def lex_hit_of(question, win):
    """lexHit = 내용어 중 창 안 청크 **본문 또는 노트 경로**에 그대로 나타나는 비율."""
    toks = content_tokens(question)
    if not toks:
        return 0.0, 0
    hays = []
    for d in win:
        b = _body(d)
        hays.append(((b.get("pageContent") or b.get("text") or "") + " " + (_src(d) or "")).lower())
    n = sum(1 for t, latin in toks if any(_hit(t, latin, h) for h in hays))
    return n / len(toks), len(toks)



def ground_check(docs, question=""):
    """배포 노드와 동일. `question` 은 어휘 절에만 쓰인다.

    배포본은 게이트 바로 앞이 집계 노드(Build Context / Pack Docs)라 $json.question 이
    흘러오지 않아 상류 노드에서 직접 가져온다(② `질문 정리` / ④ `Loop Over Questions`).
    하네스에는 상류 노드가 없으므로 질문을 인자로 받는다 — 같은 값이 들어간다.
    질문을 주지 않으면 lexHit 0 이 되어 3세대와 똑같이 동작한다(= 어휘 절만 꺼진다).
    """
    raw = docs or []
    scored = [d for d in raw
              if isinstance(d, dict) and isinstance(d.get("score"), (int, float))
              and not isinstance(d.get("score"), bool) and math.isfinite(d["score"])]

    # 출처 줄은 창(top-8) 안에 등장하는 노트를 거리 오름차순 등장 순서로 앞 3개.
    seen = []
    for d in raw[:WINDOW]:
        s = _src(d)
        if s and s not in seen:
            seen.append(s)
    sources_line = ", ".join(seen[:3]) or "없음"

    if not scored:
        return {"grounded": False, "best": None, "best2mean": None, "spread": None,
                "nearest": "없음", "sourcesLine": sources_line}

    # pgvector 가 거리 오름차순으로 주지만 순서를 믿지 않고 직접 정렬한다
    asc = sorted(scored, key=lambda d: d["score"])[:WINDOW]
    best = asc[0]["score"]
    best2mean = (asc[0]["score"] + asc[1]["score"]) / 2 if len(asc) >= 2 else best
    spread = asc[-1]["score"] - best
    lex_hit, n_tok = lex_hit_of(question, asc[:LEX_WINDOW])

    grounded = (best2mean < NEAR
                or (best2mean < FAR and spread > FALL)
                or (best2mean < FAR2 and lex_hit >= LEX))      # ← 4세대 어휘 절

    return {"grounded": grounded, "best": _r4(best), "best2mean": _r4(best2mean),
            "spread": _r4(spread), "nearest": _src(asc[0]) or "없음",
            "sourcesLine": sources_line,
            "lexHit": _r4(lex_hit), "lexTokens": n_tok}       # 관측용(하류는 안 읽음)


def no_ground_message(g):
    """근거 판정 노드가 함께 만들어 Discord 로 내보내는 안내문(노드에서는 out.noGroundMessage)."""
    return ("🔍 노트에서 관련 내용을 찾지 못했어요.\n\n"
            "검색은 됐지만 결과가 전부 질문과 거리가 멀어서(가장 가까운 노트 `"
            + (g.get("nearest") or "없음") + "`), 지어내지 않고 멈췄습니다.")
