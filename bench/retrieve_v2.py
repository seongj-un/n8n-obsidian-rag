"""검색 v2 — ②/④ 의 `노트 선별` 노드를 그대로 옮긴 것. 2차 DB 조회 없이 top-K를 노트 단위로
   걸러 원문 순서로 되돌린다. ② 와 ④ 의 JS 는 바이트 단위로 동일하다(2026-09-15 확인).
   노드 로직을 고칠 때는 n8n 과 여기를 반드시 함께 고칠 것.

  [왜 필요한가]
  topK 는 거리와 무관하게 늘 16개를 준다. 청크가 거리순이라 한 노트의 앞뒤가 무관한 노트를
  사이에 두고 끊긴다. 2026-09-11: HNSW 문항이 ef_construction/ef_search 를 답변에서 빠뜨렸는데
  정작 그 값이 든 심화 청크는 이미 회수돼 있었다 — 회수가 아니라 배치가 문제였다.

  [2026-09-14 수정 — 청크 수로 뽑던 것을 거리로 바꿨다]
  처음엔 "top-K에서 2청크 이상 차지한 노트만 남긴다"는 규칙이었다. 이게 틀렸다. 관련성을
  거리가 아니라 슬롯 점유 수로 판정하는 셈이라, 거리는 가까운데 청크를 적게 가진 정답 노트가
  탈락한다. 실행 2110 "리랭킹 알려줘" — 거리 2위인 retrieval-reranking.md(1청크)가 탈락하고
  더 먼 score-threshold-design.md(2청크)가 들어가, 임계값 설계 얘기가 돌아왔다.
  그래서 노트를 best 거리로 줄 세우고 1위 대비 BAND 안에 드는 노트만 추가한다.

  ── [2026-09-15 수정 — 비교형 질문에만 BAND 를 넓힌다] ─────────────────────
  단일 BAND 1.08 은 "답이 두 노트에 걸친 질문"을 구조적으로 못 푼다. 비교형은 두 주제가
  서로 다른 노트에 있고, 질문 임베딩은 둘 중 한쪽으로 쏠린다. 그 결과 2위 노트가 1위의
  1.09~1.34배로 밀려 BAND 밖으로 떨어진다. 측정(골든 27문항, 549청크/94노트):

    질문                          2위(또는 3위) expect 노트          1위 대비   1.08에서
    CSRF랑 CORS가 헷갈리는데       study/csrf.md                     1.1412    탈락
    카프카랑 RabbitMQ 중에         study/kafka.md                    1.1665    탈락
    bge-m3는 다른 임베딩 모델이랑   study/embeddings.md               1.2233    탈락
    JWT를 세션 쿠키 대신           study/jwt-token-management.md     1.3354    탈락
    하이브리드 서치랑 리랭킹        study/retrieval-reranking.md      1.0926    탈락
                                 study/hybrid-search.md            1.1174    탈락

    CSRF 문항은 컨텍스트에 CORS 만 들어가 모델이 "CSRF 설명이 없어 비교 불가"로 거절했고
    ④ 평가 0/0/0, 종합 4.98 → 4.80.

  [왜 BAND 단독 상향이 아닌가 — 같은 회수율에서 정밀도가 다르다]
    설정                          expect 회수  무관 노트/질의  평균 ctx
    이전 1.08 (전체)                22/28       0.23          3,780자
    단순상향 1.25 (전체)             27/28       0.55          4,756자
    비교형만 1.28 (채택)             27/28       0.36          4,512자   ← 노이즈 35% 적다
    절대거리컷 best<=0.48           27/28       1.00          5,348자
    2위전용 BAND2 1.34              26/28       0.55          4,634자
  비교형이 아닌 12문항은 선별 결과가 1.08 시절과 **한 글자도 안 바뀐다**.

  [왜 1.28 인가] 회수 27/28 은 BAND_CMP ∈ [1.2233, 1.3272] 전 구간에서 동일하다(폭 0.104).
  1.28 의 여유는 아래 0.0567 / 위 0.0472. 예전에 코퍼스 변경으로 깨졌던 임계값의 여유가
  0.0055 였다 — 그보다 한 자릿수 크다. 28/28 은 BAND_CMP >= 1.3355(JWT 한 문항)에서만
  열리고 위로 0.031 밖에 못 가므로 과적합이라 보고 노리지 않았다.

  [예산도 함께 올렸다 — 8500 이면 BAND 를 올려도 소용이 없다]
    카프카 문항: rabbitmq.md 6,688자 + kafka.md 2,955자 = 9,643자 > 8500 → kafka 탈락
    즉 BAND 를 1.40 까지 올려도 8500 에서는 26/28 이 천장이다. 필요한 최소값은 9,643.
    캐시된 79질의에서 이 설정이 만드는 최대 컨텍스트가 10,363자라 11000 은 사실상 아무
    데도 안 물린다. 평탄 구간 [9643, ∞), 아래 여유 1,357자.

  [비용] 평균 컨텍스트 3,780 → 4,512자 (+19%). 최악(카프카) 6,596 → 9,491자.
         Gemini Flash Lite 의 실제 prefill 증가는 배포본에서도 **아직 측정하지 않았다**.

  [버린 대안] topK 24 로 넓히기(1등 노트가 예산을 다 먹어 2·3등이 잘린다),
             dev/ 청크에 거리 페널티, MAX_NOTES 4(회수 이득 0, 무관 노트 0.36 → 0.59).
"""
import re

BAND = 1.08            # 기본 — 비교형이 아닌 질문
BAND_CMP = 1.28        # 비교형 질문. 평탄 구간 [1.22337, 1.32720] 의 거의 한가운데
MAX_NOTES = 3
MAX_CTX_CHARS = 11000  # 2026-09-15: 8500 → 11000. 8500 은 BAND 상향을 무력화한다.
FALLBACK_CHUNKS = 3    # 점수가 하나도 없을 때 — 근거 판정이 이미 막았어야 할 모양

# ── 비교 신호 휴리스틱 ──────────────────────────────────────────────────────
# 형태소 분석기 없이 "두 주제를 나란히 놓는 질문인가"만 본다. 의도적으로 **중복**시켜 놨다:
# 골든 27문항에서 아래 아홉 갈래 중 어느 하나를 빼도 회수 27/28 이 그대로다.
# 오판의 방향도 안전하다 — 미탐이면 BAND 1.08 로 되돌아가고(= 이전 배포본과 같은 동작),
# 오탐이면 무관 노트가 한둘 늘 뿐 정답은 안 잃는다.
# 알려진 오탐: `과/와` 는 접속 조사라 "결과 ", "효과 " 같은 명사 꼬리에도 걸린다.
#
# [JS 와 다른 한 곳 — 의도적] 배포 JS 의 `\bvs\b` 는 JS 정규식의 \b 가 ASCII \w 기준이라
# "카프카vs래빗" 에도 걸린다. 파이썬 re 의 \b 는 한글을 단어 문자로 보므로 같은 문자열에서
# 안 걸린다. 그래서 여기서는 ASCII 단어 경계를 명시적으로 쓴다 — 동작을 JS 에 맞춘 것이다.
_CMP = re.compile(
    "[가-힣A-Za-z0-9]랑(?![가-힣])"          # 카프카랑 / 유사도랑
    "|[가-힣A-Za-z0-9]이랑"                  # 모델이랑
    "|[가-힣A-Za-z0-9](?:와|과)(?=\\s|\\Z)"  # 카프카와 RabbitMQ / CORS과
    "|(?<![A-Za-z0-9_])vs(?![A-Za-z0-9_])"   # JS 의 \bvs\b 와 같은 동작
    "|차이|다른|다르|달라|비교|중에|헷갈|대신", re.I)

_WIKILINK = re.compile(r"\[\[([^\]|]*\|)?([^\]]*)\]\]")


def is_compare(question):
    """배포 JS 의 CMP.test(question). 질문을 못 구하면 False → BAND 1.08."""
    return bool(_CMP.search(str(question or "")))


def _body(d):
    """n8n 은 {document:{pageContent,metadata}, score}, 오프라인 검색은 {text,metadata,score}."""
    return (d.get("document") if isinstance(d, dict) and d.get("document") else d) or {}


def _src(d):
    return (_body(d).get("metadata") or {}).get("source") or None


def _line(d):
    return (((_body(d).get("metadata") or {}).get("loc") or {}).get("lines") or {}).get("from", 0)


def _text_key(b):
    """본문이 담긴 키. n8n=pageContent, 오프라인 psql=text."""
    return "pageContent" if "pageContent" in b else "text"


def _text(d):
    b = _body(d)
    return b.get(_text_key(b)) or ""


def jslen(s):
    """JS String.length 와 같은 UTF-16 코드유닛 길이. 파이썬 len() 은 코드포인트를 세서
       BMP 밖 문자(이모지 등)를 1로 센다 — 노트 본문에 이모지가 있으면 예산이 어긋난다."""
    return len(str(s or "").encode("utf-16-le")) // 2


def unlink(s):
    """옵시디언 위키링크는 본문에 파일명을 끌고 들어온다. 형식 규칙에 '출처 쓰지 말 것'이라고
       적어두긴 했지만 프롬프트로 부탁할 일이 아니다 — 근거 판정과 같은 이유로 여기서 걷어낸다."""
    return _WIKILINK.sub(lambda m: m.group(2), str(s or ""))


def select_full(probe, question=""):
    """`노트 선별` 노드의 출력 전체. probe = 거리 오름차순 top-K 청크.

    배포본은 앞이 집계 노드라 $json.question 이 없을 수 있어 상류 노드
    (② `질문 정리` / ④ `Loop Over Questions`)에서 가져온다. 하네스는 인자로 받는다.
    질문을 주지 않으면 비교형 판정이 꺼져 BAND 1.08 로 동작한다 — 배포본이 질문을
    못 구했을 때와 같은 폴백이다.
    """
    docs = probe or []
    compare = is_compare(question)
    band = BAND_CMP if compare else BAND

    owned = {}                                   # 삽입 순서 = 거리 오름차순
    for d in docs:
        s = _src(d)
        if s:
            owned.setdefault(s, []).append(d)

    rank = []
    for s, chunks in owned.items():
        scores = [c.get("score") for c in chunks
                  if isinstance(c.get("score"), (int, float)) and not isinstance(c.get("score"), bool)]
        if scores:
            rank.append((s, min(scores)))
    rank.sort(key=lambda x: x[1])                # 파이썬 sort 도 JS 처럼 안정 정렬이다

    if not rank:
        picked = list(docs[:FALLBACK_CHUNKS])
    else:
        limit = rank[0][1] * band
        picked, used = [], 0
        for s, best in rank[:MAX_NOTES]:
            if picked and best > limit:
                break                            # 1위는 무조건, 나머지는 BAND 안일 때만
            chunks = sorted(owned[s], key=_line)  # 원문 순서로 되돌린다
            size = sum(jslen(_text(c)) for c in chunks)
            if picked and used + size > MAX_CTX_CHARS:
                break
            picked += chunks
            used += size

    # 받은 모양 그대로 돌려준다 — Answer 의 (d.document || d) 매핑이 계속 통해야 한다.
    out = []
    for d in picked:
        b = _body(d)
        cleaned = dict(b)
        cleaned[_text_key(b)] = unlink(b.get(_text_key(b)))
        if isinstance(d, dict) and d.get("document"):
            wrapped = dict(d)
            wrapped["document"] = cleaned
            out.append(wrapped)
        else:
            out.append(cleaned)

    # 출처 줄은 실제로 쓴 노트만. 근거 판정의 것은 창 전체라 한 글자도 안 쓴 노트까지 걸린다.
    used_sources = list(dict.fromkeys(s for s in (_src(d) for d in out) if s))

    return {"docs": out,
            "pickedNotes": used_sources,
            "pickedChunks": len(out),
            "ctxChars": sum(jslen(_text(d)) for d in out),
            "sourcesLine": ", ".join(used_sources[:3]) or "없음",
            "pickBand": band,        # 관측용 — 하류는 안 읽는다
            "pickCompare": compare}


def select(probe, question=""):
    """하위호환: 청크 리스트만 돌려준다(ab.py / leadline.py 가 이 모양을 쓴다)."""
    return select_full(probe, question)["docs"]
