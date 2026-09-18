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

  [후속 판정 — 세 갈래]
    ① 내용어가 0개              → 이 질문만으로는 검색어를 만들 수 없다
    ② REF 가 있고 내용어 ≤ 1개  → 지시어가 빠진 자리를 앞 차례가 채운다
    ③ CONT 가 있고 **내용어가 전부 CONT** → 되풀이 요청. 주제는 앞 차례에 있다
  CONT(자세히/더/계속/예시 …)는 앞 차례를 **되풀이해 달라는 요청**이라 주제어를 하나도
  담지 않는다. rag_refusals 44·45·46 이 근거다(2026-09-16). ③ 의 문턱은 개수가 아니라
  **0** 이다 — `임베딩 더 자세히 알려줘` 처럼 새 주제가 하나라도 섞이면 방아쇠가 안 걸린다.
  CONN(그럼/그러면/그래서/근데 …)은 **1차 방아쇠가 아니다**. 앞 차례를 잇기만 할 뿐
  가리키지 않아서, 넣으면 "그럼 임베딩은 뭐야?"(주제 전환)에 직전 주제가 덧붙어 검색이
  오염된다. CONN 은 내용어가 0일 때(규칙 ①) 비로소 의미를 갖는다.

  [재검색 — 2026-09-15 추가]
  1차 판정이 "후속이 아니다" 로 끝났는데 **게이트가 막으면**, ② 가 맥락을 붙여 한 번 더
  검색한다. 그 두 번째 질문을 이 모듈이 미리 만들어 `ctxRetryQuestion` 으로 내보낸다.
  이 값은 1차 검색에 한 글자도 영향을 주지 않는다 — 1차 출력 10키는 바이트 그대로다.
  거기서는 CONN 도 방아쇠다. 오염이 없는 이유는 규칙이 아니라 순서다: 오염될 질문
  ("그럼 임베딩은 뭐야?")은 1차에서 게이트를 통과하므로 재검색 자체가 돌지 않는다.
  **재검색을 실제로 돌릴지는 여기가 아니라 ② `재검색 판정`(IF) 이 게이트 결과로 정한다.**

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
RETRY_CONTENT_MAX = 2  # 재검색용. 게이트가 이미 한 번 막은 뒤라 1차보다 한 칸 넉넉하다

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
# CONT(이어달라는 말) — 2026-09-16 추가. 앞 차례를 **되풀이해 달라는 요청**이라 주제어를
# 담지 않는다. REF(가리킨다)·CONN(잇는다)과 성질이 다르다. 굴절형은 손으로 나열한다 —
# 어간에 대고 맞추면 "더라"→"더" 처럼 엉뚱한 말이 걸리므로 **원형(run)** 에 대고 맞춘다.
_CONT = re.compile("(?:자세히|자세하게|자세한|상세히|상세하게|상세한|더자세히|"
                   "더|더더|좀더|조금더|계속|이어서|추가로|예시)")
# FILL — 방아쇠는 아니지만 재작성할 때는 걷어내는 말. 지시관형사와 맨 관형사가 여기 온다.
_FILL = re.compile("(?:그런|이런|저런|그|이|저)")


def is_ref(run):
    return _REF.fullmatch(run) is not None


def is_conn(run):
    return _CONN.fullmatch(run) is not None


def is_cont(run):
    return _CONT.fullmatch(run) is not None


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


def content_words(q, drop_cont=False):
    """내용어 = 지시어도 골격어도 아닌 2자 이상 어간 (중복 제거, 등장 순서 유지).

    영문은 **원형**을 그대로 돌려준다 — 검색 질문에 다시 넣을 때 "JWT" 가 "jwt" 로
    뭉개지지 않게. (중복 판정은 소문자 어간으로 한다.)

    drop_cont 를 켜면 CONT 도 함께 뺀다. **기본값은 지금까지와 한 글자도 다르지 않다** —
    단발 질문의 ctxTopic/ctxContentWords 가 바이트 그대로 남아야 해서다. 켜는 곳은 두
    군데뿐이고 둘 다 후속일 때만 도는 자리다(규칙 ③ 판정 · 재작성 topic).
    """
    out, seen = [], set()
    for run, t, latin in stems(q):
        if is_ref(run) or is_conn(run):
            continue
        if drop_cont and is_cont(run):
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


def has_cont(q):
    return any(is_cont(run) for run, _t, _l in stems(q))


def strip_deictic(q):
    """지시어 낱말을 통째로 걷어낸 나머지 — 재작성의 뼈대가 된다.

    배포본과 같이 **문자열 치환**이다(정규식이 아니다). 낱말이 다른 말 속에 부분 문자열로
    들어 있어도 같이 지워진다 — 배포본의 `s.split(run).join(" ")` 과 동작을 맞춘 것이다.
    """
    s = _js_str(q)
    for run, _t, _l in stems(q):
        # CONT 는 **2자 이상일 때만** 걷어낸다. 이건 정규식이 아니라 부분 문자열 치환이라
        # 한 글자 "더" 를 지우면 같은 문장의 "더미데이터" 까지 "미데이터" 로 깎인다.
        # 한 글자를 남겨도 잃는 게 없다 — 어간이 1자라 어차피 내용어가 아니다.
        cont2 = is_cont(run) and len(run) >= 2
        if is_ref(run) or is_conn(run) or cont2 or _FILL.fullmatch(run):
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
    cont = has_cont(question)
    # 내용어에서 CONT 를 뺀 나머지 = 이번 질문이 **스스로 들고 온 주제**.
    own_topic = len(content_words(question, True)) if cont else len(words)
    # 후속 판정 — 셋 중 하나
    #   ① 내용어가 아예 없다 (접속부사만 있어도 해당)
    #   ② REF 가 있고 내용어가 CONTENT_MAX 이하
    #   ③ CONT 가 있고 내용어가 **전부** CONT (문턱은 개수가 아니라 0이다)
    needy = (len(words) == 0 or (ref and len(words) <= CONTENT_MAX)
             or (cont and own_topic == 0))
    is_follow_up = alive and needy

    def _rewrite():
        """1차 판정과 재검색이 **같은 함수**를 쓴다 (배포 JS 의 rewrite())."""
        # 직전 주제어: 직전 질문에서 뽑되, 직전 질문도 후속이었다면 이월된 topic 을 쓴다.
        # CONT 를 빼고 뽑는다 — 직전 질문이 `더 자세히 알려줘` 였으면 주제어가 "자세히" 로
        # 잡혀 그 오염이 다음 턴으로 번진다(실행 2995 가 실제로 그랬다).
        topic = " ".join(content_words(prev_q, True)[:MAX_TOPIC])
        if not topic:
            topic = prev_topic
        # 질문이 **이미 들고 있는** 주제어는 빼고 붙인다. 안 그러면
        # `judge 더 자세히 알려줘` 의 재검색이 `judge judge 더 알려줘` 가 되어 같은 낱말이
        # 두 번 들어가고 임베딩이 그쪽으로 쏠린다(2026-09-18 실측: 표본 5개 전부에서
        # best2mean 이 0.03~0.08 나빠졌다). 재검색은 게이트가 이미 막은 뒤에만 도는
        # 경로라 그 폭이 당락을 가른다.
        # **빼기만 한다** — 없던 낱말을 넣지 않으므로 겹치지 않는 질문은 한 글자도 안 바뀐다.
        own = {w.lower() for w in content_words(question)}
        topic = " ".join(w for w in topic.split() if w and w.lower() not in own)
        rest = strip_deictic(question)
        q = _trim(_WS_PUNCT.sub(r"\1", _WS_RUN.sub(" ", topic + " " + rest))) or question
        block = ("[대화 맥락 — 바로 앞 차례]\n"
                 "이전 질문: " + prev_q + "\n"
                 + ("이전 답변 요지: " + prev_lead + "\n" if prev_lead else "")
                 + "위 맥락을 참고해 아래 질문의 지시어(그것/그건/그럼 …)가"
                   " 무엇을 가리키는지 판단하세요.\n\n")
        return q, block

    search_question = question
    context_block = ""
    if is_follow_up:
        search_question, context_block = _rewrite()

    # ── 재검색용 예비 재작성 — 1차 검색에는 쓰이지 않는다 ────────────────────
    # 1차에서 이미 재작성했다면 재검색해 봐야 같은 질문이라 뜻이 없다.
    # CONT 도 여기서는 방아쇠다 — 1차 규칙 ③ 은 "내용어가 전부 CONT" 일 때만 걸리므로
    # `judge 더 자세히 알려줘`(내용어 1개 + CONT)는 1차에서 일부러 흘려보낸다.
    # CONT 방아쇠에만 own_topic <= 1 을 더 건다: CONT 낱말은 비교 부사로도 쓰여서
    # (`더 빠른 인덱스 뭐 있어?`) 개수 문턱만으로는 새 질문까지 걸린다.
    retry_needy = (alive and not is_follow_up
                   and ((ref or conn) or (cont and own_topic <= 1))
                   and len(words) <= RETRY_CONTENT_MAX)
    ctx_retry_question = ""
    ctx_retry_context = ""
    if retry_needy:
        q, block = _rewrite()
        if q != search_question:
            ctx_retry_question, ctx_retry_context = q, block

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
        # ↓ 재검색 전용. ② `재검색 판정` 이 게이트 결과를 보고 쓴다.
        "ctxCanRetry": bool(ctx_retry_question),
        "ctxRetryQuestion": ctx_retry_question,
        "ctxRetryContext": ctx_retry_context,
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
        # ── CONT (규칙 ③) — 2026-09-16. rag_refusals 44·45·46 이 근거다 ──────────
        ("더 자세히 알려줘", PREV, True, "규칙 ③ — 내용어가 전부 CONT (거절 44)"),
        ("자세히", PREV, True, "규칙 ③ — CONT 하나뿐"),
        ("계속", PREV, True, "규칙 ③ — CONT 하나뿐"),
        ("예시?", PREV, True, "규칙 ③ — CONT 하나뿐"),
        ("좀 더 알려줘", PREV, True, "규칙 ① 이 이미 잡는다(좀·알려줘=STOP, 더=1자)"),
        ("임베딩 더 자세히 알려줘", PREV, False,
         "**오염 경계** — 새 주제 1개 + CONT. 1차 방아쇠는 안 걸린다(거기 리랭킹을 붙이면 안 된다)"),
        ("judge 더 자세히 알려줘", PREV, False,
         "새 주제 1개 + CONT — 1차는 흘려보내고 게이트가 막으면 재검색이 구제한다 (거절 45·46)"),
        ("계속 에러나는데 왜 그래?", PREV, False, "CONT 낱말이 부사로 쓰인 새 질문"),
        ("더 빠른 인덱스 뭐 있어?", PREV, False, "'더' 가 비교 부사 — 스스로 주제 2개를 들고 왔다"),
        # ── 주제어 중복 (2026-09-18) ────────────────────────────────────────
        ("리랭킹 더 자세히 알려줘", PREV, False,
         "스스로 들고 온 주제 1개 + CONT — 1차는 흘려보낸다(주제가 앞 차례와 같은 말이어도)"),
    ]
    # 재검색 자격도 함께 본다 — 1차에서 흘려보낸 것을 게이트 뒤에서 누가 받나
    RETRY = [
        ("judge 더 자세히 알려줘", PREV, True, "CONT + 스스로 들고 온 주제 1개 → 재검색 자격"),
        ("임베딩 더 자세히 알려줘", PREV, True, "같은 규칙. 게이트를 통과하면 재검색은 안 돈다"),
        ("더 빠른 인덱스 뭐 있어?", PREV, False, "스스로 들고 온 주제 2개 → 자격 없음"),
        ("리랭킹 알려줘", PREV, False, "REF/CONN/CONT 가 하나도 없다 → 절대 대상이 아니다"),
        # 주제어가 질문에 이미 있으면 **두 번 붙이지 않는다**. 이 줄이 없으면
        # "리랭킹 리랭킹 더 알려줘" 로 돌아간다(2026-09-18 실측: best2mean 0.3847 → 0.4308).
        ("리랭킹 더 자세히 알려줘", PREV, True, "주제어가 이미 질문에 있다 → 중복 없이 붙는다"),
    ]
    bad = 0
    print(f"{'질문':32s} {'age_s':>9s}  {'후속':5s} {'검색용 질문':28s} 근거")
    for q, prev, want, why in CASES:
        r = rewrite(q, prev)
        ok = r["followUp"] == want
        bad += not ok
        print(f"{q:32s} {str((prev or {}).get('age_s')):>9s}  "
              f"{str(r['followUp']):5s} {r['question']:28s} {'' if ok else '❌ '}{why}")
    print(f"\n{'질문':32s} {'재검색자격':10s} {'재검색 질문':28s} 근거")
    for q, prev, want, why in RETRY:
        r = rewrite(q, prev)
        ok = r["ctxCanRetry"] == want
        bad += not ok
        print(f"{q:32s} {str(r['ctxCanRetry']):10s} {r['ctxRetryQuestion']:28s} "
              f"{'' if ok else '❌ '}{why}")
    sys.exit(1 if bad else 0)
