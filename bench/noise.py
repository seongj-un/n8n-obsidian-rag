"""질문별 거리 분포 — 근거 판정 임계값 재조정용.

  4세대 게이트가 보는 값(best2mean / spread / lexHit)을 그대로 찍는다. 거리 창은 WINDOW=8,
  어휘 창은 LEX_WINDOW=6 으로 서로 다르다 — 여기서는 top-8을 받아 게이트에 그대로 넘긴다.
  임계값을 옮길 때는 IN/OUT 표본(`calib_set.py`)을 이걸로 돌려 분포 틈을 확인할 것.

  ※ 이 파일은 ollama 를 부른다. import 만으로 돌지 않도록 __main__ 가드 안에 둔다.
"""
import sys, os, sqlite3
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
# n8n sqlite 사본 경로. ./fetch-db.sh 로 받거나 N8N_DB 로 덮어쓴다.
DB = os.environ.get('N8N_DB', os.path.join(_HERE, 'database.sqlite'))


def main():
    from rag import embed, search, ground_check, WINDOW

    qs = sqlite3.connect(DB).execute(
        "select question, expect from data_table_user_kXB0IQeAeI6Y566S "
        "where enabled=1 order by id").fetchall()

    print(f"{'질문':>18s} | {'best':>6s} {'b2mean':>6s} {'spread':>6s} {'lexHit':>6s} {'판정':>4s} | "
          f"top-{WINDOW} 거리 분포 / 노트별 청크수")
    print("-" * 130)
    for q, expect in qs:
        v, _ = embed(q)
        docs, _ = search(v, topk=WINDOW)
        g = ground_check(docs, q)   # 어휘 절은 질문이 있어야 켜진다
        ds = [d["score"] for d in docs]
        cnt = Counter((d.get("metadata") or {}).get("source") for d in docs)
        dist = " ".join(f"{s:.3f}" for s in ds)
        srcs = " ".join(f"{k.split('/')[-1][:-3]}×{n}" for k, n in cnt.most_common())
        neg = "[음성]" if "음성 대조군" in (expect or "") else ""
        print(f"{q[:17]:>18s} | {g['best']:.4f} {g['best2mean']:.4f} {g['spread']:.4f} "
              f"{g.get('lexHit', 0):.4f} {('통과' if g['grounded'] else '차단'):>4s} | {dist}")
        print(f"{neg:>18s} |                             chars="
              f"{sum(len(d['text']) for d in docs):5d} | {srcs}")


if __name__ == '__main__':
    main()
