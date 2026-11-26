#!/bin/sh
set -eu
systemctl --user start agentic-robotics-web.service
if command -v firefox >/dev/null 2>&1; then
  exec firefox --new-tab http://127.0.0.1:8787
fi
exec xdg-open http://127.0.0.1:8787
