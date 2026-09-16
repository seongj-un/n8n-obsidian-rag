# -*- coding: utf-8 -*-
"""Moonshot(Kimi) 호출. ollama용 generate()와 같은 모양의 dict를 돌려줘서
   ab.py 가 모델만 바꿔 끼울 수 있게 한다.

   키는 이 파일에 적지 않는다. 아래 순서로 찾는다:
     1) 환경변수 MOONSHOT_API_KEY
     2) ~/.moonshot_key  (chmod 600 으로 본인이 만들 것)
   키 값을 출력하거나 로그에 남기지 않는다.
"""
import json, os, time, urllib.request, urllib.error

BASE = "https://api.moonshot.ai/v1/chat/completions"
DEFAULT_MODEL = "kimi-k2.6"          # k2.5는 공식 가격표에서 내려갔다 (2026-09-14 확인)

def _key():
    k = os.environ.get("MOONSHOT_API_KEY")
    if k: return k.strip()
    p = os.path.expanduser("~/.moonshot_key")
    if os.path.exists(p):
        with open(p) as f: return f.read().strip()
    raise RuntimeError("Moonshot 키를 찾지 못했다. MOONSHOT_API_KEY 를 넣거나 ~/.moonshot_key 를 만들 것.")

def generate(prompt, model=DEFAULT_MODEL, system=None, temperature=0.4,
             max_tokens=1024, timeout=300, **_ignored):
    """ollama generate() 와 같은 키를 돌려준다. prefill/decode 구분은 API가 주지 않으므로
       total_s 만 실측이고 prefill_s/decode_s 는 0으로 둔다 — 비교표에서 섞지 말 것."""
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": msgs, "temperature": temperature,
            "max_tokens": max_tokens, "stream": False}
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + _key()})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Moonshot {e.code}: {e.read()[:300].decode('utf-8','replace')}") from None
    wall = time.time() - t
    u = d.get("usage", {})
    return {
        "text": d["choices"][0]["message"]["content"],
        "load_s": 0.0, "prefill_s": 0.0, "decode_s": 0.0,
        "total_s": wall,
        "in_tok": u.get("prompt_tokens", 0),
        "out_tok": u.get("completion_tokens", 0),
        "cached_tok": u.get("cached_tokens", 0),
        "model": d.get("model", model),
    }

# USD / 1M tokens — platform.kimi.ai 2026-09-14 확인. 값이 바뀌면 여기만 고칠 것.
PRICE = {
    "kimi-k2.6":      {"hit": 0.16, "miss": 0.95, "out": 4.00},
    "kimi-k2.7-code": {"hit": 0.19, "miss": 0.95, "out": 4.00},
    "kimi-k3":        {"hit": 0.30, "miss": 3.00, "out": 15.00},
}

def cost(r, model=DEFAULT_MODEL):
    p = PRICE.get(model)
    if not p: return None
    cached = r.get("cached_tok", 0) or 0
    miss = max(r["in_tok"] - cached, 0)
    return (cached * p["hit"] + miss * p["miss"] + r["out_tok"] * p["out"]) / 1e6
