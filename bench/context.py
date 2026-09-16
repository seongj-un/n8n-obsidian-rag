# -*- coding: utf-8 -*-
"""맥락 재작성 — ② `맥락 재작성` Code 노드의 파이썬 이식. **모델을 부르지 않는다.**

  배포본(2026-09-15, sha1 2e2fa153ae96)을 그대로 옮긴 것이다. ②(1MhR2JfKyXHbiTIC)와
  임시 실험 워크플로(2Kkt53h5K0c8JJwL)의 JS 는 바이트 단위로 같다(확인 완료).
  값·규칙을 고칠 때는 n8n 노드와 여기를 **반드시 함께** 고칠 것. `parity.py` 가 매번 대조한다.

  [이 모듈이 파이프라인의 어디에 있나]
      질문 정리 → 세션 로드(Postgres) → **맥락 재작성** → Obsidian Vector Store → 근거 판정 → …
  즉 **검색 앞단**이다. 임베딩에 들어가는 질문 문자열 자체를 바꾸므로, 여기서 어긋나면
  하류(검색·게이트·선별·프롬프트)가 전부 다른 입력 위에서 돌아 벤치 결과가 조용히 거짓이 된다.

  [무엇을 바꾸고 무엇을 안 바꾸나]
  바꾸는 것은 **검색용 질문 하나뿐**이다. 재작성은 추측이고, 추측을 답변 프롬프트의 "질문"
  자리에 넣으면 잘못 추측했을 때 사용자가 하지도 않은 질문에 답하게 된다. 검색이 틀리면
  게이트가 막아주지만 답변은 막아줄 게 없다. 그래서
      question    → 검색·게이트·선별이 읽는다 (재작성본)
      rawQuestion → 답변 프롬프트가 읽는다 (사용자 원문)
      contextBlock→ 답변 프롬프트 맨 앞에 붙는다 (후속일 때만 비어 있지 않다)
  배포 Answer 노드의 user 텍스트는 정확히
      contextBlock + prompt_v1.build(rawQuestion, docs)[1]
  이다. `ab.py --pairs` 가 그 모양을 그대로 만든다.

  [후속 판정 — 두 갈래]
    ① 내용어가 0개              → 이 질문만으로는 검색어를 만들 수 없다
    ② REF 가 있고 내용어 ≤ 1개  → 지시어가 빠진 자리를 앞 차례가 채운다
  CONN(그럼/그러면/그래서/근데 …)은 **방아쇠가 아니다**. 앞 차례를 잇기만 할 뿐 가리키지
  않아서, 넣으면 "그럼 임베딩은 뭐야?"(주제 전환)에 직전 주제가 덧붙어 검색이 오염된다.
  CONN 은 내용어가 0일 때(규칙 ①) 비로소 의미를 갖는다.

  [파이썬으로 옮기면서 특별히 맞춘 것 — 아래 "JS↔파이썬 함정" 절 참고]
    · 토크나이저는 `rag.py` 의 것을 **그대로 가져다 쓴다**(복제하지 않는다).
    · JS 의 `\\s` / `String.prototype.trim()` 과 파이썬 `\\s` / `str.strip()` 은 집합이 다르다.
    · JS `Number()` 의 강제 변환 규칙을 흉내 낸다 (Postgres 가 age_s 를 **문자열**로 준다).
"""
import math
import re

# ── 토크나이저는 재정의하지 않는다 ───────────────────────────────────────────
# 배포 `맥락 재작성` 은 `근거 판정` 의 TAILS/STOP/RUN 을 복제해 쓴다(n8n 이 노드 간 코드
# 공유를 못 해서다). 하네스에는 그 제약이 없으므로 `rag.py` 것을 그대로 import 한다.
# 2026-09-15 확인: 배포 `근거 판정`·`맥락 재작성`·`rag.py` 의 TAILS(65개)·STOP(109개)·RUN
# 이 셋 다 같다. `parity.py` 가 이 전제를 매 실행 검증한다.
from rag import TAILS, STOP, _RUN, _LATIN

# ── 상수 (배포본과 같은 값) ─────────────────────────────────────────────────
TTL_S = 600        # 세션 수명(초)
MAX_TOPIC = 3      # 직전 주제어를 몇 개까지 끌어올지
CONTENT_MAX = 1    # REF 가 있을 때, 내용어가 이 개수 이하면 후속으로 본다

# ── JS↔파이썬 함정 ①: 단어 경계 ─────────────────────────────────────────────
# JS 정규식의 `\b` 는 ASCII 기준이라 한글 옆에서는 항상 경계가 서지만, 파이썬 `re` 는
# 한글을 단어 문자로 보아 경계가 서지 않는다. 이전 동기화 때 `노트 선별` 의 `\bvs\b` 가
# 바로 이 함정에 걸렸고, `retrieve_v2.py` 에서 `(?<![A-Za-z0-9_])vs(?![A-Za-z0-9_])` 로
# 맞춰 해결했다.
#   → 이 노드에는 같은 계열이 **없다**. 배포 JS 전체를 훑어 `\b`/`\B`/`\w`/`\W` 가 한 번도
#     쓰이지 않는 것을 확인했다. 지시어 판정은 전부 낱말 하나(RUN 이 이미 잘라 준 조각)에
#     대한 `^…$` 전체 일치라 경계 개념 자체가 끼어들지 않는다.
#   아래 `fullmatch` 를 쓰는 것도 같은 맥락이다 — 파이썬 `$` 는 끝의 개행 **앞**에서도
#   맞지만 JS `$`(m 플래그 없음)는 문자열 끝에서만 맞는다.

# ── JS↔파이썬 함정 ②: 공백 집합 ─────────────────────────────────────────────
# JS 의 `\s` = WhiteSpace ∪ LineTerminator, `.trim()` 도 같은 집합을 쓴다.
# 파이썬 `\s`(유니코드 모드)와 `str.strip()` 은 \x1c-\x1f·\x85 를 **포함**하고 ﻿ 를
# **제외**한다 — 정확히 반대로 어긋난다. 디스코드 붙여넣기에 ﻿(BOM/ZWNBSP)가 섞이는
# 일은 실제로 있으므로, JS 집합을 명시해 못 박는다.
_JS_WS = ("\t\n\x0b\f\r   "
          "           "
          "    　﻿")
_WS_CLS = "[" + re.escape(_JS_WS) + "]"
_WS_RUN = re.compile(_WS_CLS + "+")                                  # JS /\s+/g
_WS_LEAD = re.compile("^[" + re.escape(_JS_WS + ",.") + "]+")        # JS /^[\s,.]+/
_WS_PUNCT = re.compile(_WS_CLS + r"+([?!.,])")                       # JS /\s+([?!.,])/g


def _trim(s):
    """JS String.prototype.trim()."""
    return s.strip(_JS_WS)


# ── JS↔파이썬 함정 ③: 강제 변환 ─────────────────────────────────────────────
def _js_str(v):
    """JS `String(v || "")`. falsy(None/""/0/False)는 빈 문자열이 된다."""
    if not v:
        return ""
    return v if isinstance(v, str) else str(v)


_NUMERIC = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def _js_number(v):
    """JS `Number(v)`. 세션 로드(Postgres numeric)가 age_s 를 **문자열**로 준다
       — 실측: {"age_s": "1.113053"}. 파이썬 float() 은 'inf'/'1_0' 을 받지만 JS 는 NaN 이다."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return float("nan")
    s = _trim(v)
    if s == "":
        return 0.0                     # JS: Number("") === 0, Number("  ") === 0
    if s == "Infinity" or s == "+Infinity":
        return float("inf")
    if s == "-Infinity":
        return float("-inf")
    if re.fullmatch(r"[+-]?0[xX][0-9a-fA-F]+", s):
        return float(int(s, 16))
    if not _NUMERIC.fullmatch(s):      # 'inf' · 'nan' · '1_0' 을 여기서 떨군다
        return float("nan")
    return float(s)


# ── 지시어 — 두 갈래를 구분한다 ─────────────────────────────────────────────
# 원형(raw run)에 대고 맞춘다. 꼬리를 떼고 집합에 넣는 방식은 "저는"(=나는) → "저",
# "이야기" → "이" 처럼 지시어가 아닌 말을 지시어로 만든다.
_PART = "(?:[은는이가을를도만의랑와과]|에|에서|로|으로|라도|까지|부터)*"
# REF(지시대명사) — **재작성 방아쇠**. 지시관형사(그런/이런/저런)와 맨 관형사(그/이/저)는
# 일부러 뺐다. 그것들은 언제나 뒤따르는 명사를 꾸미고 그 명사가 곧 내용어라, 넣으면
# "그 얘기 말고 데드락 조건 알려줘" 가 후속으로 오판된다(꼬리 제거기가 "조건"→"조" 로
# 깎아 내용어를 1개로 세는 바람에 문턱을 넘는다). 빼도 잃는 게 없다 — "그런 거 언제 써?"
# 같은 건 내용어가 0이라 규칙 ①이 이미 잡는다.
_REF = re.compile("(?:그것|그거|그건|그걸|그게|이것|이거|이건|이걸|이게|"
                  "저것|저거|저건|저걸|저게|그놈|그분|그때|그쪽|걔|얘|쟤|얘네|걔네|"
                  "거기|저기|여기|아까|방금)" + _PART)
# CONN(담화 접속부사) — 앞 차례를 잇기만 할 뿐 가리키지 않는다. 방아쇠가 **아니다**.
_CONN = re.compile("(?:그럼|그러면|그래서|그런데|근데|그리고|그래|그러니까)")
# FILL — 방아쇠는 아니지만 재작성할 때는 걷어내는 말. 지시관형사와 맨 관형사가 여기 온다.
_FILL = re.compile("(?:그런|이런|저런|그|이|저)")


def is_ref(run):
    return _REF.fullmatch(run) is not None


def is_conn(run):
    return _CONN.fullmatch(run) is not None


def stems(q):
    """배포 JS 의 stems(). (원형, 어간, 라틴여부) 튜플을 등장 순서로 돌려준다.

    `rag.content_tokens()` 와 같은 기계지만 투영이 다르다 — 저쪽은 (어간, 라틴여부)만 주고
    지시어를 거르지 않는다. 여기서는 지시어 판정에 **원형**이 필요해서 한 겹 더 노출한다.
    꼬리 제거 규칙(TAILS 를 긴 것부터 1회만)은 rag.py 와 같은 목록·같은 순서를 쓴다.
    """
    out = []
    for run in _RUN.findall(_js_str(q)):
        latin = bool(_LATIN.match(run))
        t = run.lower() if latin else run
        if not latin:
            for tl in TAILS:                      # 꼬리 1회만 제거
                if len(t) > len(tl) and t.endswith(tl):
                    t = t[:-len(tl)]
                    break
        out.append((run, t, latin))
    return out


def content_words(q):
    """내용어 = 지시어도 골격어도 아닌 2자 이상 어간 (중복 제거, 등장 순서 유지).

    영문은 **원형**을 그대로 돌려준다 — 검색 질문에 다시 넣을 때 "JWT" 가 "jwt" 로
    뭉개지지 않게. (중복 판정은 소문자 어간으로 한다.)
    """
    out, seen = [], set()
    for run, t, latin in stems(q):
        if is_ref(run) or is_conn(run):
            continue
        if len(t) < 2 or t in STOP:
            continue
        if t not in seen:
            seen.add(t)
            out.append(run if latin else t)
    return out


def has_ref(q):
    return any(is_ref(run) for run, _t, _l in stems(q))


def has_conn(q):
    return any(is_conn(run) for run, _t, _l in stems(q))


def strip_deictic(q):
    """지시어 낱말을 통째로 걷어낸 나머지 — 재작성의 뼈대가 된다.

    배포본과 같이 **문자열 치환**이다(정규식이 아니다). 낱말이 다른 말 속에 부분 문자열로
    들어 있어도 같이 지워진다 — 배포본의 `s.split(run).join(" ")` 과 동작을 맞춘 것이다.
    """
    s = _js_str(q)
    for run, _t, _l in stems(q):
        if is_ref(run) or is_conn(run) or _FILL.fullmatch(run):
            s = s.replace(run, " ")
    return _trim(_WS_LEAD.sub("", _WS_RUN.sub(" ", s)))


# ── 본체 ────────────────────────────────────────────────────────────────────
def rewrite_node(question, sess=None):
    """배포 노드의 본체 그대로. `sess` 는 `세션 로드` 출력 모양을 **있는 그대로** 받는다
       — 키는 prev_question / prev_answer / prev_topic / age_s.

    배포본은 질문을 `$('질문 정리')` 에서, 세션을 `$input` 에서 가져온다. 하네스에는 상류
    노드가 없으므로 둘 다 인자로 받는다 — 같은 값이 들어간다.
    반환에 배포본의 `...q0` 전개(content/guildId/… )는 없다. 하네스에 q0 가 없어서이고,
    하류(검색·게이트·선별)는 그 키들을 읽지 않는다.
    """
    sess = sess or {}
    question = _js_str(question)

    prev_q = _js_str(sess.get("prev_question"))
    prev_lead = _js_str(sess.get("prev_answer"))
    prev_topic = _js_str(sess.get("prev_topic"))
    raw_age = sess.get("age_s")
    age_s = None if raw_age is None else _js_number(raw_age)
    alive = (bool(prev_q) and age_s is not None and math.isfinite(age_s)
             and 0 <= age_s < TTL_S)

    words = content_words(question)
    ref = has_ref(question)
    conn = has_conn(question)
    # 후속 판정 — 둘 중 하나
    #   ① 내용어가 아예 없다 (접속부사만 있어도 해당)
    #   ② REF 가 있고 내용어가 CONTENT_MAX 이하
    needy = len(words) == 0 or (ref and len(words) <= CONTENT_MAX)
    is_follow_up = alive and needy

    search_question = question
    context_block = ""
    if is_follow_up:
        # 직전 주제어: 직전 질문에서 뽑되, 직전 질문도 후속이었다면 이월된 topic 을 쓴다
        topic = " ".join(content_words(prev_q)[:MAX_TOPIC])
        if not topic:
            topic = prev_topic
        rest = strip_deictic(question)
        search_question = _trim(_WS_PUNCT.sub(r"\1", _WS_RUN.sub(" ", topic + " " + rest))) \
            or question
        context_block = ("[대화 맥락 — 바로 앞 차례]\n"
                         "이전 질문: " + prev_q + "\n"
                         + ("이전 답변 요지: " + prev_lead + "\n" if prev_lead else "")
                         + "위 맥락을 참고해 아래 질문의 지시어(그것/그건/그럼 …)가"
                           " 무엇을 가리키는지 판단하세요.\n\n")

    # 이월 topic — 이번 턴이 무엇에 관한 것이었는지. 후속이면 직전 것을 물려받는다.
    carry = " ".join(content_words(search_question)[:MAX_TOPIC]) or prev_topic

    return {
        "question": search_question,      # ← 하류(검색·게이트·선별)가 읽는 질문
        "rawQuestion": question,           # ← 사용자가 실제로 친 말. 답변 프롬프트가 읽는다
        "contextBlock": context_block,     # ← 후속일 때만 비어 있지 않다
        "followUp": is_follow_up,          # ← 하네스용 이름
        # 아래는 배포 노드가 내보내는 관측용 키. 이름까지 같게 두어 parity 가 통째로 비교한다.
        "ctxFollowUp": is_follow_up,
        "ctxTopic": carry,
        "ctxAgeS": age_s,
        "ctxPrevQuestion": prev_q,
        "ctxRef": ref,
        "ctxConn": conn,
        "ctxContentWords": len(words),
    }


# ── 친절한 API ──────────────────────────────────────────────────────────────
def _age_of(updated_at, now=None):
    """updated_at → 경과 초. datetime / ISO 문자열 / epoch 숫자를 받는다.

    naive datetime 은 UTC 로 본다(Postgres now() 가 timestamptz 라 배포본은 언제나 tz-aware).
    경계를 딱 맞춰 재고 싶으면 prev 에 age_s 를 직접 넣어라 — 그 값이 우선한다.
    """
    if updated_at is None:
        return None
    import datetime as _dt
    import time as _time
    if now is None:
        now = _time.time()
    elif isinstance(now, _dt.datetime):
        now = (now if now.tzinfo else now.replace(tzinfo=_dt.timezone.utc)).timestamp()
    if isinstance(updated_at, (int, float)) and not isinstance(updated_at, bool):
        then = float(updated_at)
    else:
        if isinstance(updated_at, str):
            s = updated_at.strip().replace("Z", "+00:00")
            updated_at = _dt.datetime.fromisoformat(s)
        then = (updated_at if updated_at.tzinfo
                else updated_at.replace(tzinfo=_dt.timezone.utc)).timestamp()
    return now - then


def session_view(prev, now=None):
    """`prev` 를 `세션 로드` 출력 모양으로 정규화한다.

    받는 모양 둘
      · rag_chat_sessions 행 그대로:  {question, answer_lead, topic, updated_at}  (+ age_s 가능)
      · `세션 로드` 출력 그대로:      {prev_question, prev_answer, prev_topic, age_s}
    """
    if not prev:
        return {}
    if "prev_question" in prev:            # 이미 `세션 로드` 출력 모양이다
        return dict(prev)
    age = prev.get("age_s")
    if age is None:
        age = _age_of(prev.get("updated_at"), now)
    return {"prev_question": prev.get("question"),
            "prev_answer": prev.get("answer_lead"),
            "prev_topic": prev.get("topic"),
            "age_s": age}


def rewrite(question, prev=None, now=None):
    """후속 질문이면 검색용 질문을 다시 쓴다.

    prev — 직전 차례 dict {question, answer_lead, topic, updated_at} 또는 None.
           (`세션 로드` 출력 모양도 그대로 받는다. age_s 를 직접 넣으면 그 값이 우선한다.)
    반환 — {question, rawQuestion, contextBlock, followUp, ctx*}
           prev 가 없거나 TTL 이 지났으면 question 은 **한 글자도 안 바뀐다**.
    """
    return rewrite_node(question, session_view(prev, now))


def answer_prompt_question(r):
    """답변 프롬프트가 읽어야 할 질문. 재작성본이 아니라 **원문**이다 — 위 모듈 주석 참고."""
    return r["rawQuestion"]


if __name__ == "__main__":      # 모델을 부르지 않지만 습관을 지킨다
    import sys
    from rag import content_tokens

    # (0) 토크나이저를 정말 공유하는지 — stems() 의 투영이 rag.content_tokens() 와 맞는가
    probes = ["리랭킹이랑 임베딩 차이 뭐야", "JWT 만료 처리 어떻게 해", "그건 왜 비싸?",
              "데드락 조건 알려줘", "m2 랑 M20 차이", "그럼 임베딩은 뭐야?"]
    for p in probes:
        mine, theirs = [], content_tokens(p)
        seen = set()
        for run, t, latin in stems(p):
            if len(t) < 2 or t in STOP or t in seen:
                continue
            seen.add(t)
            mine.append((t, latin))
        assert mine == theirs, (p, mine, theirs)
    print("토크나이저 공유 확인 — stems() 투영 == rag.content_tokens()  ✅")

    PREV = {"question": "리랭킹 알려줘", "answer_lead": "리랭킹은 후보의 순서를 다시 매긴다.",
            "topic": "리랭킹", "age_s": 5}
    CASES = [
        ("그럼 임베딩은 뭐야?", PREV, False, "CONN 은 방아쇠가 아니다 — 주제 전환"),
        ("그건 왜 비싸?", PREV, True, "REF + 내용어 1개"),
        ("그 얘기 말고 데드락 조건 알려줘", PREV, False, "맨 관형사는 REF 가 아니다"),
        ("그럼?", PREV, True, "내용어 0 — 규칙 ①"),
        ("그런 거 언제 써?", PREV, True, "지시관형사는 REF 가 아니지만 내용어 0이라 규칙 ①"),
        ("이건 BM25랑 뭐가 달라?", PREV, True, "REF + 내용어 1개 · 영문은 원형 보존"),
        ("리랭킹 알려줘", PREV, False, "세션은 살아 있어도 혼자 서는 질문"),
        ("그건 왜 비싸?", dict(PREV, age_s="599.999"), True, "TTL 경계 안 (문자열 age_s)"),
        ("그건 왜 비싸?", dict(PREV, age_s="600"), False, "TTL 경계 밖 (문자열 age_s)"),
        ("그건 왜 비싸?", dict(PREV, age_s=-1), False, "age_s 음수 — 죽은 세션"),
        ("그건 왜 비싸?", dict(PREV, age_s="abc"), False, "age_s 가 숫자가 아님 → NaN"),
        ("그건 왜 비싸?", None, False, "세션 없음"),
        ("그건 왜 비싸?", {"question": "", "topic": "리랭킹", "age_s": 5}, False,
         "직전 질문이 비었다 — alive 아님"),
    ]
    bad = 0
    print(f"{'질문':32s} {'age_s':>9s}  {'후속':5s} {'검색용 질문':28s} 근거")
    for q, prev, want, why in CASES:
        r = rewrite(q, prev)
        ok = r["followUp"] == want
        bad += not ok
        print(f"{q:32s} {str((prev or {}).get('age_s')):>9s}  "
              f"{str(r['followUp']):5s} {r['question']:28s} {'' if ok else '❌ '}{why}")
    sys.exit(1 if bad else 0)
