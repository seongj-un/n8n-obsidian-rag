#!/bin/zsh
# 호스트(macOS) 램 감시. 상태가 바뀔 때만 n8n 웹훅을 때린다.
#
# 왜 n8n 안이 아니라 여기냐 — n8n 은 컨테이너 안이라 호스트 16GB 가 아니라
# 도커 VM 의 2.9GB 를 "전체 램"으로 본다. 맥북 램은 호스트에서만 잴 수 있다.
#
# launchd 가 5분마다 부른다 (host/com.seongjun.n8n-ram-watch.plist).

set -u

# launchd 는 최소 PATH 로 돈다. vm_stat(/usr/bin) · sysctl(/usr/sbin) · python3 · curl 을 찾게 한다.
export PATH=/usr/bin:/bin:/usr/sbin:/sbin

HOOK="${N8N_HOOK:-http://localhost:5678/webhook/ram-alert}"
THRESHOLD="${RAM_REAL_FREE_THRESHOLD:-12}"   # 실여유 % 하한
STATE="${HOME}/.local/state/n8n-ram-watch.state"

mkdir -p "${STATE:h}"

# ── 지표 1: 실여유 % ─────────────────────────────────────────────────────────
# 실여유 = 100 - (wired + 익명 + 압축기점유) / 전체
# memory_pressure 의 '여유 %' 는 압축기가 물고 있는 물리 램을 사용으로 치지 않아
# 수십 %p 낙관적으로 나온다. 그래서 그 값은 안 쓰고 여기서 직접 센다.
page_size=0; p_wired=0; p_anon=0; p_compr=0
eval "$(vm_stat 2>/dev/null | awk -F': *' '
  NR==1 { if (match($0, /page size of [0-9]+/))
            print "page_size=" substr($0, RSTART+13, RLENGTH-13); next }
  /^Pages wired down/             { gsub(/\./,"",$2); print "p_wired="$2 }
  /^Anonymous pages/              { gsub(/\./,"",$2); print "p_anon="$2 }
  /^Pages occupied by compressor/ { gsub(/\./,"",$2); print "p_compr="$2 }
')"

if (( page_size > 0 )); then
  total_pages=$(( $(sysctl -n hw.memsize) / page_size ))
  real_free=$(( 100 - (p_wired + p_anon + p_compr) * 100 / total_pages ))
else
  total_pages=0
  real_free=100   # vm_stat 을 못 읽으면 이 조건은 빠진다 — 없는 근거로 알리지 않는다.
fi

# ── 지표 2: 커널 압박 단계 (1=normal 2=warn 4=critical) ──────────────────────
lvl_num=$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || echo 1)
case "$lvl_num" in
  4) level=critical ;;
  2) level=warn ;;
  *) level=normal ;;
esac

# ── 판정: 둘 중 하나라도 걸리면 압박 ─────────────────────────────────────────
# 걸린 조건을 그대로 모아 헤드라인에 싣는다. 여유 % 만 띄우면 커널 압박 단계로
# 터진 알림이 '여유 20%' 를 달고 나와 회복 메시지와 구분이 안 된다.
reasons=()
(( real_free < THRESHOLD )) && reasons+=("여유 ${real_free}% < ${THRESHOLD}%")
(( lvl_num  >= 2 ))         && reasons+=("커널 압박 단계 ${level}")

if (( ${#reasons} > 0 )); then now=alert; else now=ok; fi

prev=$(cat "$STATE" 2>/dev/null || echo ok)
[[ "$now" == "$prev" ]] && exit 0     # 상태 전환일 때만 — 5분마다 도배하지 않는다

swap=$(sysctl -n vm.swapusage 2>/dev/null | sed 's/^ *//')

# 프로세스 목록(ps)은 안 싣는다 — wired 와 압축기가 어느 프로세스에도 안 잡혀서
# 상위 몇 개를 더해도 실제 사용량의 일부밖에 설명하지 못한다. 대신 내역을 싣는다.
payload=$(EV="$now" REALFREE="$real_free" LEVEL="$level" SWAP="$swap" \
          REASON="${(j:, :)reasons}" \
          PWIRED="$p_wired" PANON="$p_anon" PCOMPR="$p_compr" \
          PAGESIZE="$page_size" TOTALPAGES="$total_pages" python3 -c '
import json, os, datetime, unicodedata

ps = int(os.environ["PAGESIZE"])
tp = int(os.environ["TOTALPAGES"])
w  = int(os.environ["PWIRED"])
a  = int(os.environ["PANON"])
c  = int(os.environ["PCOMPR"])

def gb(pages):
    return pages * ps / float(2 ** 30)

def pad(label, width=10):
    # 한글은 두 칸을 먹는다. 글자 수가 아니라 표시 폭으로 맞춘다.
    shown = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in label)
    return label + " " * max(0, width - shown)

if ps and tp:
    L_WIRED, L_ANON, L_COMPR, L_TOTAL = "wired", "익명", "압축기", "합계"
    breakdown = "\n".join([
        pad(L_WIRED) + "%5.1f GB   ← 커널" % gb(w),
        pad(L_ANON)  + "%5.1f GB" % gb(a),
        pad(L_COMPR) + "%5.1f GB   ← 압축된 메모리" % gb(c),
        "-" * 26,
        pad(L_TOTAL) + "%5.1f GB / %.1f GB" % (gb(w + a + c), gb(tp)),
    ])
else:
    breakdown = "vm_stat 을 읽지 못했다 — 내역 없음"

print(json.dumps({
    "event":       "recovered" if os.environ["EV"] == "ok" else "pressure",
    "realFreePct": int(os.environ["REALFREE"]),
    "level":       os.environ["LEVEL"],
    "reason":      os.environ["REASON"],
    "swap":        os.environ["SWAP"],
    "breakdown":   breakdown,
    "at":          datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}, ensure_ascii=False))')

# POST 가 실패하면 상태를 쓰지 않는다 — n8n 이 내려가 있었다면 다음 번에 다시 시도한다.
if curl -fsS --max-time 10 -X POST "$HOOK" \
        -H 'Content-Type: application/json' -d "$payload" >/dev/null 2>&1; then
  printf '%s' "$now" > "$STATE"
else
  print -r -- "$(date '+%F %T') POST 실패 ($now, 여유 ${real_free}%) — 상태 보류" >&2
fi
