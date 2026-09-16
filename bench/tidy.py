"""답변 정리 — ② 의 `답변 정리` 노드를 그대로 옮긴 것.
   노드 로직을 고칠 때는 n8n 과 여기를 반드시 함께 고칠 것.

  [왜 필요한가]
  형식 규칙(첫 줄 핵심 문장, `- **용어**: 설명` 불릿)은 프롬프트로 "부탁"한 것이라
  gemma3:4b 가 temperature 0.4 에서 종종 통째로 무시한다. 실측 4건 중 2건이 그랬다.
    실행 2106 — 658자를 개행 0개로 뱉어 한 문단이 됐다
    실행 2122 — 리드 문장 없이 라벨 없는 맨 문장 불릿 17개
    실행 2124 — 같은 모양으로 11개
  잘 나온 답변(2120/2121)과 비교하면 차이는 딱 둘이다: 리드 문장이 있느냐, 불릿에 굵은
  라벨이 있느냐. 라벨이 없으면 눈이 걸릴 데가 없어 전부 읽어야 한다.

  [하는 일] 내용은 한 글자도 바꾸지 않는다. 없는 문장을 지어내지도, 불릿을 합치지도 않는다.
    0) 출처 표기를 걷어낸다 (프롬프트가 [출처: …] 헤더와 함께 근거를 싣기 때문에 모델이 벤낀다)
    1) 줄 중간에 뭉친 불릿을 제 줄로 내린다
    2) 리드 문장이 없으면 첫 불릿을 리드로 올린다 (문장이 이미 거기 있다)
    3) 라벨 없는 불릿의 머리 주제어를 굵게 만든다 (보수적으로 — 애매하면 그냥 둔다)
    4) 빈 줄을 정규화한다 — 모델이 넣은 빈 줄은 살리고, 연속된 것만 하나로 줄이고,
       본문 뒤에 목록이 시작되면 한 줄 띄운다

  [2026-09-15 수정 — 블록 재조립을 버리고 빈 줄을 보존한다]
  이전에는 빈 줄을 전부 버리고 블록(본문/목록) 사이에만 다시 넣었다. Answer 의 SYSTEM 이
  질문 종류별 모양(비교=대조 줄, 동작=번호 목록, 정의=산문+불릿)으로 바뀌자 이 방식이
  의도한 문단을 뭉갰다 — 비교형의 "리드 문장 / **A** 대조 줄 / **B** 대조 줄 / 맺음" 이
  전부 한 덩어리로 붙고, 번호 목록(1. 2. 3.)은 불릿이 아니라 'text' 로 분류돼 산문 문단과
  한 블록이 됐다. 이제는 모델이 넣은 빈 줄을 존중하고 과한 것만 줄인다.
  1~4 는 이미 형식을 지킨 답변에는 아무 일도 하지 않는다. 구조상 멱등이다.
"""
import re

# `- **용어**:` 꼴의 불릿 머리. 뭉친 불릿을 찾을 때 lookahead 로 쓴다.
HEAD = r"(?:💡\s*)?\*\*[^*\n]{1,40}\*\*\s*[:：]"

# 출처 누출 패턴 — 프롬프트에 "출처나 파일명은 쓰지 말 것"이라고 적혀 있지만 근거를
# [출처: 경로] 헤더와 함께 싣기 때문에 모델이 종종 그대로 베낀다(실측: 벤치 답변 55건 중
# 11건에 위키링크가 샜고, 문장 끝 (출처: …) 형태도 관측됐다). 출처는 Reply to Discord 가
# 실제로 쓴 노트만 골라 따로 붙이므로 본문에 있으면 중복이자 노이즈다.
_SOURCE_SCRUB = (
    (re.compile(r"[（(]\s*출처\s*[:：][^)）]*[)）]"), ""),      # 문장 끝 (출처: study/x.md)
    (re.compile(r"\[\s*출처\s*[:：][^\]]*\]"), ""),            # 헤더를 그대로 베낀 [출처: …]
    (re.compile(r"\[\[([^\]|]*\|)?([^\]]*)\]\]"), r"\2"),      # 위키링크 — 표시 텍스트만 남긴다
    (re.compile(r"(?:^|\n)\s*출처\s*[:：][^\n]*"), ""),        # "출처: …" 로 시작하는 줄 통째
    (re.compile(r"(\S)[ \t]{2,}"), r"\1 "),                    # 지운 자리에 생긴 연속 공백
                                                              # (줄 앞 들여쓰기는 두 채로 — 중첩 불릿)
    (re.compile(r"\s+([.,!?)\]])"), r"\1"),                    # 구두점 앞에 남은 공백
    (re.compile(r"[ \t]+$", re.M), ""),
)

# 앞이 공백이 아닐 때만 자른다. 예전의 `([^\n])` 는 공백도 먹어서 들여쓴 중첩 불릿의
# 들여쓰기까지 삼켰다(2026-09-14 배포본에서 `(\S)` 로 고쳤다 — 주석은 보존한다고 적혀
# 있는데 코드가 반대였다).
_GLUED_BULLET = re.compile(r"(\S)[ \t]+[-•*][ \t]+(?=" + HEAD + ")")
_BULLET_MARK = re.compile(r"^[ \t]*[•*][ \t]+", re.M)
_TRAIL_WS = re.compile(r"[ \t]+$")
_IS_BULLET = re.compile(r"^\s*-\s")
_LEAD_DASH = re.compile(r"^-\s+")
_LEAD_LABEL = re.compile(r"^(?:💡\s*)?\*\*([^*\n]+)\*\*\s*[:：]\s*")

# 라벨 없는 불릿의 머리 주제어. 보수적으로 간다 — 조사 은/는 앞이 2~14자이고, 문장부호가
# 없고, 어미(…다)로 끝나지 않을 때만. "점수비가 크다는 건 …" 같은 관형절을 주제어로
# 오인하지 않기 위해서다. 조사 이/가는 제외한다("리랭커가 …"는 주제어가 아닌 경우가 많다).
# 수량자를 게으르게 둔다. 탐욕적이면 문장 뒤쪽 조사를 잡아
#   "M2는 실험으로 같은 결론에…" → 머리를 "M2는 실험으로 같"으로 오려낸다.
_LABEL = re.compile(
    r"^-\s+([^\s*:：.,!?()\[\]]{1,6}?(?:\s[^\s*:：.,!?()\[\]]{1,10}?){0,2}?)(은|는)\s+(.+)$")


def _is_bullet(line):
    # 들여쓴 중첩 불릿도 목록으로 본다. 안 그러면 4)가 중첩 항목을 목록의 시작으로 보고
    # 앞에 빈 줄을 끼워 넣는다.
    return bool(_IS_BULLET.match(line))


def tidy(text):
    """답변 한 건을 Discord 에서 읽히는 모양으로 정규화한다. 내용은 건드리지 않는다."""
    t = str(text or "").strip()

    # 0) 출처 표기 제거
    for pat, rep in _SOURCE_SCRUB:
        t = pat.sub(rep, t)

    # 1) 앞에 개행 없이 붙어 있는 불릿을 제 줄로 내린다 (개행 뭉침 사고 복구)
    t = _GLUED_BULLET.sub(r"\1\n- ", t)
    # 불릿 기호 통일 — Discord 는 •를 목록으로 렌더하지 않는다
    t = _BULLET_MARK.sub("- ", t)

    # 빈 줄을 버리지 않는다. 앞뒤 빈 줄만 떼고 줄 끝 공백만 없앤다.
    lines = [_TRAIL_WS.sub("", l) for l in t.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()

    # 2) 첫 줄이 불릿이면 리드 문장이 없다는 뜻 — 첫 불릿을 끌어올린다.
    #    불릿이 3개 미만이면 건드리지 않는다(짧은 답변에서 목록이 사라지면 더 이상하다).
    first_idx = next((i for i, l in enumerate(lines) if l != ""), -1)
    if first_idx != -1 and _is_bullet(lines[first_idx]) and sum(1 for l in lines if _is_bullet(l)) >= 3:
        lead = _LEAD_DASH.sub("", lines[first_idx], count=1)
        lead = _LEAD_LABEL.sub(r"\1: ", lead, count=1)   # 리드는 굵게 쓰지 않는 게 규칙이다
        lines = [lead.replace("**", "")] + lines[first_idx + 1:]

    # 3) 라벨 없는 불릿의 머리 주제어를 굵게.
    #    "임베딩 모델은 텍스트를 …" → "**임베딩 모델**: 텍스트를 …"
    out_lines = []
    for l in lines:
        if not _is_bullet(l) or "**" in l:
            out_lines.append(l)                          # 이미 라벨이 있으면 그대로
            continue
        m = _LABEL.match(l)
        if not m:
            out_lines.append(l)
            continue
        head = m.group(1)
        if len(head) < 2 or len(head) > 14 or head.endswith("다"):
            out_lines.append(l)                          # 관형절을 주제어로 오인하지 않는다
            continue
        out_lines.append("- **" + head + "**: " + m.group(3))
    lines = out_lines

    # 4) 빈 줄 정규화 — 모델이 넣은 것은 살리고, 과한 것만 줄이고, 목록 앞은 한 줄 띄운다.
    #    (블록을 통째로 재조립하던 방식은 대조·산문 형식의 문단을 뭉개서 버렸다.)
    out = []
    for line in lines:
        if line == "":
            if out and out[-1] != "":                    # 연속 빈 줄은 하나로
                out.append("")
            continue
        prev = out[-1] if out else None
        # 목록이 시작되는데 앞이 본문이면 한 줄 띄운다
        if prev and prev != "" and _is_bullet(line) and not _is_bullet(prev):
            out.append("")
        out.append(line)
    return "\n".join(out)


if __name__ == "__main__":                               # 사람 눈으로 보는 용도
    import json, sys
    for path in sys.argv[1:]:
        for r in json.load(open(path, encoding="utf-8")).get("results", []):
            a = r.get("answer")
            if not a:
                continue
            t = tidy(a)
            if t != a:
                print("=" * 70, "\n", r["question"], "\n--- 전 ---\n", a, "\n--- 후 ---\n", t)
