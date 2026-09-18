#!/bin/zsh
# 램 감시기를 launchd 에 설치한다.
#
# 스크립트를 레포에서 직접 돌리지 않고 복사하는 이유 —
# 이 레포는 ~/Desktop 아래에 있고, macOS 는 Desktop 을 TCC 로 보호한다.
# launchd 에이전트는 그 권한이 없어서 레포 안의 파일을 "can't open input file" 로 못 읽는다.
#
# ram-watch.sh 를 고친 뒤에는 이 스크립트를 다시 돌려야 반영된다.

set -eu
HERE="${0:A:h}"
LABEL=com.seongjun.n8n-ram-watch

mkdir -p "$HOME/.local/bin" "$HOME/Library/LaunchAgents"
install -m 755 "$HERE/ram-watch.sh" "$HOME/.local/bin/n8n-ram-watch.sh"
# plist 의 __HOME__ 를 실제 홈으로 치환해 설치한다. 레포에 절대경로를 넣지 않으려는 것 —
# 이 레포는 원격으로 나가고, git-commit-update.sh 의 개인정보 검사가 홈 절대경로를 막는다.
sed "s|__HOME__|$HOME|g" "$HERE/$LABEL.plist" > "$HOME/Library/LaunchAgents/$LABEL.plist"
chmod 644 "$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "설치 완료 — 5분마다 돈다. 로그: /tmp/n8n-ram-watch.log"
