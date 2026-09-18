#!/bin/zsh
# 램 감시기를 걷어낸다. n8n 워크플로 ⑦ 는 그대로 남는다(직접 비활성화할 것).
set -u
LABEL=com.seongjun.n8n-ram-watch
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" "$HOME/.local/bin/n8n-ram-watch.sh" \
      "$HOME/.local/state/n8n-ram-watch.state"
echo "제거 완료"
