#!/usr/bin/env bash
# Stop the background fetcher and the Streamlit dashboard started by start.sh.
#
# PID files are removed first; pkill then catches processes started by hand.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

for name in fetcher streamlit; do
  pid_file="logs/$name.pid"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file")"
    if kill "$pid" 2>/dev/null; then
      echo "stopped $name (pid $pid)"
    fi
    rm -f "$pid_file"
  fi
done

pkill -f "python3 fetcher.py" 2>/dev/null && echo "stopped remaining fetcher processes"
pkill -f "streamlit run app.py" 2>/dev/null && echo "stopped remaining streamlit processes"

echo "done"
