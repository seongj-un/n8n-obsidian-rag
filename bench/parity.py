# -*- coding: utf-8 -*-
"""배포 JS ↔ 파이썬 하네스 대조 — **모델을 한 번도 부르지 않는다**.

  하네스가 배포본과 어긋나면 벤치 결과는 조용히 거짓이 된다. 그걸 눈으로 잡을 수 없으니
  기계로 잡는다. 저장된 n8n 실행 기록에서 노드 **입력**을 꺼내, 같은 입력을 배포 JS(`node`)와
  이 디렉터리의 파이썬에 각각 넣고 출력을 비교한다.

  대조 대상
    `근거 판정`    → rag.ground_check()
    `노트 선별`    → retrieve_v2.select_full()
    `답변 정리`    → tidy.tidy()
    `맥락 재작성`  → context.rewrite_node()

  2026-09-15: ④ 가 2턴 문항을 재생하면서 `맥락 재작성`·`세션 기록`·`답변 정리` 의 **사본**을
  갖게 됐다. 별도 Code 노드가 아니라 ④ `후속 턴 준비` 안에 JSON 문자열로 박혀 있다
  (`// ②-SRC` 표지 아래). 아래 `wf4_embedded()` 가 그걸 꺼내 ② 와 바이트 대조한다 —
  갈라지면 ④ 의 점수는 더 이상 ② 에 대한 말이 아니므로 **실패로 친다**.
  덤으로 ④ 의 2턴 실행 기록은 `맥락 재작성` 대조 표본으로도 쓴다(ctx_fixtures 참조).

  `맥락 재작성` 표본은 ② 말고 임시 실험 워크플로 ②T(2Kkt53h5K0c8JJwL)에서도 긁는다.
  맥락 기능은 2026-09-15 05:45 UTC 에 ② 로 나갔고 그 뒤 실사용 실행이 아직 얼마 없는 반면,
  ②T 에는 후속 시나리오와 오탐 대조군이 100건 넘게 쌓여 있다. ②T 의 Code 노드 4개는 ② 와
  바이트 단위로 같다 — 아래에서 매번 확인하고 다르면 표본을 쓰지 않는다.

  배포 JS 는 n8n 공개 API 로 그때그때 받아 온다(워크플로는 읽기만 한다 — GET).
  실행 기록의 `execution_data.data` 는 flatted 형식이라 평범한 JSON.parse 로 못 읽는다.
  아래 `unflatten()` 이 인덱스 참조를 되짚는다.

  쓰는 법:
      python3 parity.py                 # 최근 실행 기록으로 전부 대조
      python3 parity.py --limit 10      # 실행 10건만
      python3 parity.py -v              # 불일치를 자세히

  전제: `node` 가 PATH 에 있고, `./fetch-db.sh` 로 받은 최신 database.sqlite(+ -wal)가 있고,
       n8n 이 localhost:5678 에 떠 있을 것.
"""
import argparse, json, os, re, sqlite3, subprocess, sys, tempfile, urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
DB = os.environ.get('N8N_DB', os.path.join(_HERE, 'database.sqlite'))
N8N = os.environ.get('N8N_URL', 'http://localhost:5678')
WF2 = '1MhR2JfKyXHbiTIC'          # ② Discord 질의응답
WF4 = 'o2fB9QN4uRpwyaD0'          # ④ 답변 품질 평가
WF2T = '2Kkt53h5K0c8JJwL'         # ②T [임시] 맥락 실험 — 맥락 재작성 표본 공급처

GATE_KEYS = ('grounded', 'best', 'best2mean', 'spread', 'nearest', 'sourcesLine',
             'lexHit', 'lexTokens')
PICK_KEYS = ('pickedNotes', 'pickedChunks', 'ctxChars', 'sourcesLine', 'pickBand', 'pickCompare')
CTX_KEYS = ('question', 'rawQuestion', 'contextBlock', 'ctxFollowUp', 'ctxTopic',
            'ctxAgeS', 'ctxPrevQuestion', 'ctxRef', 'ctxConn', 'ctxContentWords')


def api_key():
    return sqlite3.connect(DB).execute(
        "select apiKey from user_api_keys where label='claude code'").fetchone()[0]


def workflow(wid, key):
    r = urllib.request.Request(N8N + '/api/v1/workflows/' + wid, headers={'X-N8N-API-KEY': key})
    return json.load(urllib.request.urlopen(r, timeout=60))


def unflatten(text):
    """flatted 형식(인덱스 참조 배열)을 되짚어 원래 객체로 복원한다."""
    slots = json.loads(text)
    done = {}

    def build(i):
        if i in done:
            return done[i]
        v = slots[i]
        if isinstance(v, list):
            out = []
            done[i] = out
            for e in v:
                out.append(res(e))
            return out
        if isinstance(v, dict):
            out = {}
            done[i] = out
            for k, e in v.items():
                out[k] = res(e)
            return out
        done[i] = v
        return v

    def res(e):
        if isinstance(e, str):
            try:
                idx = int(e)
            except ValueError:
                return e
            return build(idx) if 0 <= idx < len(slots) else e
        return e

    return build(0)


def wf4_embedded(wf4):
    """④ `후속 턴 준비` 안에 박힌 ② 노드 원문을 꺼낸다. 없으면 {} (2턴 적용 전)."""
    node = next((n for n in wf4['nodes'] if n['name'] == '후속 턴 준비'), None)
    if not node:
        return {}
    m = re.search(r'const SRC = \{(.*?)\n\};', node['parameters']['jsCode'], re.S)
    if not m:
        return {'__err': '`const SRC = {…}` 를 못 찾았다 — 노드 모양이 바뀌었다'}
    try:
        return json.loads('{' + m.group(1).rstrip().rstrip(',') + '}')
    except Exception as e:
        return {'__err': 'SRC 를 JSON 으로 못 읽었다: %s' % e}


def _runs(rdd, name, branch=0):
    out = []
    for run in rdd.get(name, []):
        main = ((run.get('data') or {}).get('main') or [])
        items = main[branch] if len(main) > branch else None
        out.append([it['json'] for it in (items or [])])
    return out


def _wf0_id():
    """② 의 `RAG 코어` 노드가 가리키는 서브워크플로 id. 추출 전이면 None."""
    try:
        c = sqlite3.connect(DB)
        row = c.execute('select nodes from workflow_entity where id=?', (WF2,)).fetchone()
        for n in json.loads(row[0]):
            if n.get('name') == 'RAG 코어':
                return n['parameters']['workflowId']['value']
    except Exception:
        pass
    return None


def fixtures(limit):
    """실행 기록에서 (게이트/선별용 케이스, 정리용 원문) 을 뽑는다."""
    c = sqlite3.connect(DB)
    ids = [r[0] for r in c.execute(
        "select id from execution_entity where workflowId in (?,?,?) and status='success' "
        "order by id desc limit ?", (WF2, WF4, _wf0_id() or WF2, limit))]
    cases, texts = [], []
    for eid in ids:
        row = c.execute('select data from execution_data where executionId=?', (eid,)).fetchone()
        if not row:
            continue
        try:
            rdd = unflatten(row[0])['resultData']['runData']
        except Exception:
            continue
        # 질문은 ② `질문 정리`, ④ `Loop Over Questions`(2번 출력) 가 갖고 있다 — 배포 노드의
        # getQuestion() 이 보는 곳과 같다.
        qs = [x[0].get('question', '') for x in _runs(rdd, '입력 정리') if x] \
            or [x[0].get('question', '') for x in _runs(rdd, 'Loop Over Questions', 1) if x] \
            or [x[0].get('question', '') for x in _runs(rdd, '질문 정리') if x]
        for i, packed in enumerate(_runs(rdd, 'Pack Docs') or _runs(rdd, 'Build Context')):
            if not packed or 'docs' not in packed[0]:
                continue
            cases.append({'exec': eid, 'question': qs[i] if i < len(qs) else (qs[0] if qs else ''),
                          'docs': packed[0]['docs']})
        for name in ('Answer', '답변'):
            for run in _runs(rdd, name):
                for it in run:
                    t = it.get('text')
                    if isinstance(t, str) and t.strip():
                        texts.append(t)
    # 벤치 결과 JSON 의 모델 원출력도 정리 대조 표본으로 쓴다
    for fn in os.listdir(_HERE):
        if not fn.endswith('.json'):
            continue
        try:
            j = json.load(open(os.path.join(_HERE, fn), encoding='utf-8'))
        except Exception:
            continue
        if isinstance(j, dict):
            for r in j.get('results', []) or []:
                for k in ('answer_raw', 'answer'):
                    v = r.get(k)
                    if isinstance(v, str) and v.strip():
                        texts.append(v)
    return cases, list(dict.fromkeys(texts))


def ctx_fixtures(limit):
    """`맥락 재작성` 의 입력을 실행 기록에서 뽑는다.

    노드가 읽는 것은 딱 둘이다 — `$('질문 정리').first().json`(q0)과 `$input.first().json`
    (= `세션 로드` 출력). 그 둘을 그대로 뜬다. 노드가 실제로 내놓은 출력도 함께 담아
    "JS 재실행 == 그때 기록된 출력" 까지 확인한다(표본이 상한 경우를 잡는다).
    age_s 가 Postgres numeric 이라 **문자열**로 들어온다는 점이 중요하다 — 파이썬 쪽
    `_js_number()` 가 JS `Number()` 를 흉내 내는 이유가 이 표본에 있다.
    """
    c = sqlite3.connect(DB)
    ids = [r[0] for r in c.execute(
        "select id from execution_entity where workflowId in (?,?) and status='success' "
        "order by id desc limit ?", (WF2, WF2T, limit))]
    out = []
    for eid in ids:
        row = c.execute('select data from execution_data where executionId=?', (eid,)).fetchone()
        if not row:
            continue
        try:
            rdd = unflatten(row[0])['resultData']['runData']
        except Exception:
            continue
        if '맥락 재작성' not in rdd:
            continue                      # 맥락 배포 이전 실행
        q0s = _runs(rdd, '질문 정리')
        sesses = _runs(rdd, '세션 로드')
        for i, run in enumerate(_runs(rdd, '맥락 재작성')):
            if not run:
                continue
            q0 = (q0s[i] if i < len(q0s) else (q0s[0] if q0s else [None]))
            sess = (sesses[i] if i < len(sesses) else (sesses[0] if sesses else [None]))
            if not q0 or not q0[0]:
                continue
            out.append({'exec': eid, 'q0': q0[0],
                        'sess': (sess[0] if sess and sess[0] else {}),
                        'recorded': {k: run[0].get(k) for k in CTX_KEYS}})
    return out


def ctx_fixtures_wf4(limit):
    """④ 의 2턴 재생(`후속 턴 준비`)을 `맥락 재작성` 표본으로 쓴다.

    ④ 는 이 노드 안에서 ② 의 `맥락 재작성` 을 두 번 돌린다. 여기서 뽑는 것은 **후속 턴**
    쪽이다 — q0 는 mtFollow, 세션은 노드가 출력에 실어 보낸 mtSess 다. 선행 턴 쪽은
    세션이 비어 있어 표본으로서 값이 없으므로 건너뛴다.
    """
    c = sqlite3.connect(DB)
    ids = [r[0] for r in c.execute(
        "select id from execution_entity where workflowId=? and status='success' "
        "order by id desc limit ?", (WF4, limit))]
    out = []
    for eid in ids:
        row = c.execute('select data from execution_data where executionId=?', (eid,)).fetchone()
        if not row:
            continue
        try:
            rdd = unflatten(row[0])['resultData']['runData']
        except Exception:
            continue
        for run in _runs(rdd, '후속 턴 준비'):
            if not run or not run[0].get('mtSess'):
                continue
            j = run[0]
            out.append({'exec': eid, 'q0': {'question': j.get('mtFollow')},
                        'sess': j['mtSess'],
                        'recorded': {k: j.get(k) for k in CTX_KEYS}})
    return out


def _nan_to_none(v):
    """JSON.stringify(NaN) 는 null 이다. 파이썬 NaN 을 같은 자리에 맞춘다."""
    return None if isinstance(v, float) and v != v else v


RUNNER = r"""
const fs = require('fs');
const code = JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
// 배포 getQuestion() 은 $json.question 이 있으면 거기서 끝난다. 그래도 $() 가 불릴 수
// 있으니 "이 워크플로에는 없는 노드" 처럼 던지는 스텁을 둔다.
const $ = (name) => { throw new Error('no node ' + name); };
const $input = { first: () => ({ json: {} }) };
const mk = (n) => new Function('$json', '$', '$input', code[n]);
const gate = mk('근거 판정'), pick = mk('노트 선별'), tidyN = mk('답변 정리');
// `맥락 재작성` 은 $json 을 안 읽고 $('질문 정리') 와 $input 만 읽는다. 케이스마다
// 그 둘을 실행 기록에서 뜬 값으로 갈아끼운 스코프를 만들어 준다.
const ctxN = mk('맥락 재작성');
const inp = JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const pc = (d) => ((d.document || d).pageContent) || '';
const out = { gate: [], pick: [], tidy: [], ctx: [] };
for (const c of inp.cases) {
  const j = { question: c.question, docs: c.docs };
  try { const g = gate(j,$,$input)[0].json;
        out.gate.push({grounded:g.grounded,best:g.best,best2mean:g.best2mean,spread:g.spread,
                       nearest:g.nearest,sourcesLine:g.sourcesLine,lexHit:g.lexHit,lexTokens:g.lexTokens});
  } catch(e){ out.gate.push({__err:String(e)}); }
  try { const p = pick(j,$,$input)[0].json;
        out.pick.push({pickedNotes:p.pickedNotes,pickedChunks:p.pickedChunks,ctxChars:p.ctxChars,
                       sourcesLine:p.sourcesLine,pickBand:p.pickBand,pickCompare:p.pickCompare,
                       texts:(p.docs||[]).map(pc)});
  } catch(e){ out.pick.push({__err:String(e)}); }
}
for (const t of inp.texts) {
  try { out.tidy.push(tidyN({text:t},$,$input)[0].json.text); } catch(e){ out.tidy.push('__ERR__'+e); }
}
const CTX_KEYS = inp.ctxKeys;
for (const c of inp.ctx) {
  const $$ = (name) => {
    if (name === '질문 정리') return { first: () => ({ json: c.q0 }) };
    throw new Error('no node ' + name);
  };
  const $in = { first: () => ({ json: c.sess }) };
  try { const r = ctxN(c.q0, $$, $in)[0].json;
        const o = {}; for (const k of CTX_KEYS) o[k] = r[k];
        out.ctx.push(o);
  } catch(e){ out.ctx.push({__err:String(e)}); }
}
fs.writeFileSync(process.argv[4], JSON.stringify(out));
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=60, help='훑을 실행 기록 수')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()

    from rag import ground_check
    from retrieve_v2 import select_full
    from tidy import tidy
    from context import rewrite_node

    key = api_key()
    wf2, wf4 = workflow(WF2, key), workflow(WF4, key)

    def js_of(wf):
        return {n['name']: n['parameters']['jsCode']
                for n in wf['nodes'] if (n.get('parameters') or {}).get('jsCode')}

    # 2026-09-15: 코어(`근거 판정`·`노트 선별`·`Answer`)가 서브워크플로 ⓪ 로 빠졌다.
    # id 를 박아 두면 ⓪ 를 다시 만들 때 깨지므로 ② 의 `RAG 코어` 노드에서 뽑는다.
    wf0_id = next((n['parameters']['workflowId']['value']
                   for n in wf2['nodes'] if n['name'] == 'RAG 코어'), None)
    if not wf0_id:
        print('② 에 `RAG 코어` 노드가 없다 — 추출 전 구조인가? 코어 JS 는 ② 에서 읽는다.')
        code = js_of(wf2)
    else:
        code = js_of(workflow(wf0_id, key))      # 근거 판정 · 노트 선별
        code.update(js_of(wf2))                  # 답변 정리 · 맥락 재작성 (② 에 남아 있다)

    # 코어가 빠진 뒤로는 "②·④ 가 같은가"가 아니라 "사본이 남아 있지 않은가"를 본다.
    # 사본이 다시 생기면 오늘 고친 어긋남이 그대로 되돌아온다.
    if wf0_id:
        code4 = js_of(wf4)
        for name in ('근거 판정', '노트 선별'):
            dup = [w for w, c in (('②', js_of(wf2)), ('④', code4)) if name in c]
            print(f"코어 `{name}` 사본 없음: {'예' if not dup else '**아니오 — ' + ','.join(dup) + ' 에 사본이 생겼다**'}")
    else:
        code4 = js_of(wf4)
        for name in ('근거 판정', '노트 선별'):
            same = code.get(name) == code4.get(name)
            print(f"②/④ `{name}` 동일: {'예' if same else '**아니오 — 갈라졌다**'}")

    # ④ 가 2턴 재생을 하며 갖게 된 ② 사본이 바이트로 같은가.
    # 갈라지면 ④ 의 점수는 ② 에 대한 말이 아니게 된다 — 통과시키지 않는다.
    emb = wf4_embedded(wf4)
    emb_bad = []
    if not emb:
        print('④ 에 `후속 턴 준비` 가 없다 — 2턴 재생 적용 전이다. ② 사본 대조 건너뜀')
    elif '__err' in emb:
        print('④ `후속 턴 준비` 에서 ② 원문을 못 꺼냈다: ' + emb['__err'])
        emb_bad = ['__err']
    else:
        for name in ('맥락 재작성', '세션 기록', '답변 정리'):
            same = emb.get(name) == code.get(name)
            if not same:
                emb_bad.append(name)
            print(f"④ 속 ② 원문 `{name}` 바이트 일치: {'예' if same else '**아니오 — 갈라졌다**'}")

    # 2026-09-15: `거절 기록`(② 거절 질문 로그)이 토크나이저를 3번째로 복제한다.
    # 갈라지면 주간 요약의 집계 키가 게이트와 다른 말을 하게 된다.
    def _tok_block(js):
        m = re.search(r'const TAILS = \[(.*?)\];.*?const STOP = new Set\(\((.*?)\)\.split', js, re.S)
        return (m.group(1), m.group(2)) if m else None
    blocks = {n: _tok_block(code[n]) for n in ('근거 판정', '맥락 재작성', '거절 기록') if code.get(n)}
    if len(blocks) >= 2:
        ok = len(set(map(str, blocks.values()))) == 1
        print(f"토크나이저 {len(blocks)}사본 일치: {'예' if ok else '**아니오 — 갈라졌다**'}")

    # `selfRefused` 정규식은 ④ `Record` 와 ② `거절 기록` 두 곳에 있다.
    # 갈라지면 ④ 의 "근거 도달 실패" 집계와 거절 표가 서로 다른 말을 한다.
    def _selfref(js):
        m = re.search(r'/찾지\\s\*못했.*?/', js)
        return m.group(0) if m else None
    a, b = _selfref(code.get('거절 기록') or ''), _selfref(js_of(wf4).get('Record') or '')
    if a and b:
        print(f"selfRefused 정규식 2사본 일치: {'예' if a == b else '**아니오 — 갈라졌다**'}")

    # JS 를 못 받았는데 조용히 통과하는 일이 없게 한다 (None 끼리 같다고 나오던 구멍)
    missing = [n for n in ('근거 판정', '노트 선별', '답변 정리', '맥락 재작성') if not code.get(n)]
    if missing:
        print(f'배포 JS 를 못 받았다: {missing} — 대조가 무의미하므로 멈춘다.')
        return 1
    # ②T 는 맥락 표본 공급처일 뿐이다. 코드가 갈라졌다면 그 표본으로 ② 를 대변할 수 없다.
    try:
        code2t = {n['name']: n['parameters']['jsCode']
                  for n in workflow(WF2T, key)['nodes'] if (n.get('parameters') or {}).get('jsCode')}
        # ②T 는 추출 전 구조다. ② 에 남은 두 노드와 ⓪ 로 옮겨간 두 노드를 나눠 본다.
        t_same = all(code.get(n) == code2t.get(n)
                     for n in ('맥락 재작성', '답변 정리', '근거 판정', '노트 선별'))
        print(f"②T Code 노드 4개 일치: {'예' if t_same else '**아니오 — 갈라졌다**'}")
    except Exception as e:
        print(f"②T 를 못 읽었다({e}) — 맥락 표본은 ② 실행 기록만 쓴다")

    cases, texts = fixtures(a.limit)
    ctxs = ctx_fixtures(a.limit) + ctx_fixtures_wf4(a.limit)
    print(f"표본 — 게이트/선별 {len(cases)}건 · 정리 {len(texts)}건 · 맥락 {len(ctxs)}건")
    if not cases:
        print('실행 기록이 없다. ./fetch-db.sh 로 최신 사본을 받아라.')
        return 1
    if not ctxs:
        print('⚠️  `맥락 재작성` 표본이 0건이다 — 맥락 대조를 건너뛴다. '
              './fetch-db.sh 로 최신 사본을 받아라.')

    with tempfile.TemporaryDirectory() as d:
        cp, ip, op, rp = (os.path.join(d, x) for x in ('code.json', 'in.json', 'out.json', 'r.js'))
        json.dump(code, open(cp, 'w'), ensure_ascii=False)
        json.dump({'cases': cases, 'texts': texts, 'ctxKeys': list(CTX_KEYS),
                   'ctx': [{'q0': c['q0'], 'sess': c['sess']} for c in ctxs]},
                  open(ip, 'w'), ensure_ascii=False)
        open(rp, 'w').write(RUNNER)
        r = subprocess.run(['node', rp, cp, ip, op], capture_output=True, text=True)
        if r.returncode:
            print('node 실패:', r.stderr[:800])
            return 1
        js = json.load(open(op, encoding='utf-8'))

    pc = lambda d: (d.get('document') or d).get('pageContent') or ''
    bad_g, bad_p, bad_t = [], [], []
    for i, c in enumerate(cases):
        g = ground_check(c['docs'], c['question'])
        if {k: js['gate'][i].get(k) for k in GATE_KEYS} != {k: g.get(k) for k in GATE_KEYS}:
            bad_g.append((i, c))
        p = select_full(c['docs'], c['question'])
        if ({k: js['pick'][i].get(k) for k in PICK_KEYS} != {k: p.get(k) for k in PICK_KEYS}
                or js['pick'][i].get('texts') != [pc(x) for x in p['docs']]):
            bad_p.append((i, c))
    for i, t in enumerate(texts):
        if tidy(t) != js['tidy'][i]:
            bad_t.append((i, t))

    bad_c, stale = [], []
    for i, c in enumerate(ctxs):
        py = {k: _nan_to_none(rewrite_node(c['q0'].get('question'), c['sess']).get(k))
              for k in CTX_KEYS}
        if js['ctx'][i] != py:
            bad_c.append((i, c, py))
        # 덤 — JS 재실행이 그때 기록된 출력과도 같은가(표본이 상하지 않았는가)
        if js['ctx'][i] != c['recorded']:
            stale.append(i)

    n, m, k = len(cases), len(texts), len(ctxs)
    print(f"근거 판정    {n - len(bad_g)}/{n}  불일치 {len(bad_g)}")
    print(f"노트 선별    {n - len(bad_p)}/{n}  불일치 {len(bad_p)}")
    print(f"답변 정리    {m - len(bad_t)}/{m}  불일치 {len(bad_t)}")
    print(f"맥락 재작성  {k - len(bad_c)}/{k}  불일치 {len(bad_c)}"
          + (f"   (그때 기록된 출력과 어긋난 표본 {len(stale)}건)" if stale else ""))
    if a.verbose:
        for i, c in bad_g[:5]:
            print('\n[게이트]', c['exec'], c['question'][:40],
                  '\n  js:', js['gate'][i], '\n  py:', ground_check(c['docs'], c['question']))
        for i, c in bad_p[:5]:
            print('\n[선별]', c['exec'], c['question'][:40],
                  '\n  js:', {k: js['pick'][i].get(k) for k in PICK_KEYS},
                  '\n  py:', {k: select_full(c['docs'], c['question']).get(k) for k in PICK_KEYS})
        for i, t in bad_t[:5]:
            print('\n[정리]\n  js:', repr(js['tidy'][i][:300]), '\n  py:', repr(tidy(t)[:300]))
        for i, c, py in bad_c[:5]:
            print('\n[맥락]', c['exec'], repr(c['q0'].get('question')), 'sess=', c['sess'])
            for key in CTX_KEYS:
                if js['ctx'][i].get(key) != py.get(key):
                    print(f"   {key}\n     js: {js['ctx'][i].get(key)!r}\n     py: {py.get(key)!r}")
    failed = bool(bad_g or bad_p or bad_t or bad_c or emb_bad)
    print('\n' + ('⚠️  어긋났다 — 벤치 결과를 믿지 말고 배포 JS 를 다시 옮겨라.'
                  if failed else '✅ 완전 일치 — 하네스가 배포본을 대변한다.'))
    return 1 if failed else 0


if __name__ == '__main__':      # 모델을 부르지 않지만 습관을 지킨다
    sys.exit(main())
