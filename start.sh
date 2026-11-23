#!/usr/bin/env bash
# Start the tourism fetcher (background) and the Streamlit dashboard.
#
#   ./start.sh              # dashboard on port 8501
#   PORT=8600 ./start.sh    # dashboard on another port
#
# Logs: logs/fetcher.log (collector stdout/stderr) and logs/streamlit.log.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PORT="${PORT:-8501}"
VENV="$PROJECT_DIR/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "error: virtualenv missing at $VENV" >&2
  echo "create it with: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

mkdir -p logs

# shellcheck source=/dev/null
source "$VENV/bin/activate"

if pgrep -f "python3 fetcher.py" >/dev/null 2>&1; then
  echo "fetcher already running (pid $(pgrep -f 'python3 fetcher.py' | head -n 1))"
else
  nohup python3 fetcher.py >> logs/fetcher.log 2>&1 &
  echo "$!" > logs/fetcher.pid
  echo "fetcher started (pid $!) -> logs/fetcher.log"
fi

if curl -sf "http://localhost:$PORT/_stcore/health" >/dev/null 2>&1; then
  echo "dashboard already running at http://localhost:$PORT"
else
  nohup streamlit run app.py --server.port "$PORT" --server.headless true \
    >> logs/streamlit.log 2>&1 &
  echo "$!" > logs/streamlit.pid
  echo "dashboard starting (pid $!) -> logs/streamlit.log"
  for _ in $(seq 1 30); do
    if curl -sf "http://localhost:$PORT/_stcore/health" >/dev/null 2>&1; then
      echo "dashboard ready at http://localhost:$PORT"
      break
    fi
    sleep 1
  done
fi
