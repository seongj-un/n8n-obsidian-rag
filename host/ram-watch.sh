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
# 회복은 하한보다 이만큼 더 비어야 인정한다. 여유가 13~14% 를 오가면 5분마다
# 압박/회복이 번갈아 뜬다 — 들어가는 선과 나오는 선을 벌려 둔다.
HYSTERESIS="${RAM_REAL_FREE_HYSTERESIS:-3}"
STATE="${HOME}/.local/state/n8n-ram-watch.state"
# 스왑은 램과 따로 상태를 든다. 스왑은 램이 풀린 뒤에도 몇 시간씩 안 줄어서,
# 한 상태로 묶으면 스왑이 높은 동안 램 압박/회복이 가려진다.
SWAP_THRESHOLD_GB="${SWAP_THRESHOLD_GB:-8}"     # 이 이상 쓰면 알림
SWAP_HYSTERESIS_GB="${SWAP_HYSTERESIS_GB:-2}"   # 하한 - 이만큼 아래로 내려와야 회복
SWAP_STATE="${HOME}/.local/state/n8n-ram-watch.swap.state"

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
#
# 커널 단계는 critical 만 친다. warn 은 이 맥에서 평소에 수시로 켜졌다 꺼진다 —
# 2026-09-17~28 알림 127건이 전부 warn 단독이었고(critical 0건), 그때 여유는
# 압박 16~22% · 회복 16~22% 로 구분되지 않았다. 신호가 아니라 잡음이었다.
prev=$(cat "$STATE" 2>/dev/null || echo ok)

# 이미 압박 중이면 하한 + HYSTERESIS 를 넘어야 풀린다.
limit=$THRESHOLD
[[ "$prev" == alert ]] && limit=$(( THRESHOLD + HYSTERESIS ))

reasons=()
(( real_free < limit )) && reasons+=("여유 ${real_free}% < ${limit}%")
(( lvl_num  >= 4 ))     && reasons+=("커널 압박 단계 ${level}")

if (( ${#reasons} > 0 )); then now=alert; else now=ok; fi

# ── 스왑 사용량 ──────────────────────────────────────────────────────────────
# vm.swapusage: "total = 5120.00M  used = 3805.56M  free = ..." — 단위는 M 이 기본이지만
# G 로 나오는 경우에도 MB 로 맞춘다. 못 읽으면 -1 → 이 조건은 빠진다.
swap=$(sysctl -n vm.swapusage 2>/dev/null | sed 's/^ *//')
swap_used_mb=$(print -r -- "$swap" | awk '{
  if (match($0, /used = [0-9.]+[MG]/)) {
    v = substr($0, RSTART + 7, RLENGTH - 8); u = substr($0, RSTART + RLENGTH - 1, 1)
    printf "%d", (u == "G" ? v * 1024 : v); exit
  }
  print -1 }')

swap_prev=$(cat "$SWAP_STATE" 2>/dev/null || echo ok)
swap_limit_gb=$SWAP_THRESHOLD_GB
[[ "$swap_prev" == alert ]] && swap_limit_gb=$(( SWAP_THRESHOLD_GB - SWAP_HYSTERESIS_GB ))
if (( swap_used_mb >= swap_limit_gb * 1024 )); then swap_now=alert; else swap_now=ok; fi
swap_used_gb=$(( swap_used_mb < 0 ? 0 : swap_used_mb )); swap_used_gb=$(printf '%.1f' $(( swap_used_gb / 1024.0 )))

# 상태 전환일 때만 — 5분마다 도배하지 않는다
[[ "$now" == "$prev" && "$swap_now" == "$swap_prev" ]] && exit 0

# 프로세스 목록(ps)은 안 싣는다 — wired 와 압축기가 어느 프로세스에도 안 잡혀서
# 상위 몇 개를 더해도 실제 사용량의 일부밖에 설명하지 못한다. 대신 내역을 싣는다.
# $1 이벤트 이름, $2 헤드라인 사유, $3 상태 파일, $4 새 상태
send() {
local payload
payload=$(EV="$1" REALFREE="$real_free" LEVEL="$level" SWAP="$swap" \
          REASON="$2" SWAPGB="$swap_used_gb" \
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
    "event":       os.environ["EV"],
    "realFreePct": int(os.environ["REALFREE"]),
    "level":       os.environ["LEVEL"],
    "reason":      os.environ["REASON"],
    "swap":        os.environ["SWAP"],
    "swapUsedGB":  float(os.environ["SWAPGB"]),
    "breakdown":   breakdown,
    "at":          datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}, ensure_ascii=False))')

# POST 가 실패하면 상태를 쓰지 않는다 — n8n 이 내려가 있었다면 다음 번에 다시 시도한다.
if curl -fsS --max-time 10 -X POST "$HOOK" \
        -H 'Content-Type: application/json' -d "$payload" >/dev/null 2>&1; then
  printf '%s' "$4" > "$3"
else
  print -r -- "$(date '+%F %T') POST 실패 ($1, 여유 ${real_free}%, 스왑 ${swap_used_gb}GB) — 상태 보류" >&2
fi
}

if [[ "$now" != "$prev" ]]; then
  if [[ "$now" == alert ]]; then send pressure "${(j:, :)reasons}" "$STATE" alert
  else                           send recovered "" "$STATE" ok; fi
fi
if [[ "$swap_now" != "$swap_prev" ]]; then
  if [[ "$swap_now" == alert ]]; then
    send swap "스왑 ${swap_used_gb}GB ≥ ${SWAP_THRESHOLD_GB}GB" "$SWAP_STATE" alert
  else
    send swap_recovered "스왑 ${swap_used_gb}GB < ${swap_limit_gb}GB" "$SWAP_STATE" ok
  fi
fi
