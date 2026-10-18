#!/usr/bin/env bash
# AI cross-check with Claude (make verify) without putting the key anywhere. The key is taken from (in order)
#   1. ANTHROPIC_API_KEY already in the environment;
#   2. a hidden prompt, when run in a terminal;
#   3. the clipboard, when run without a terminal (e.g. a `!` command from the Claude app): the script waits up to
#      2 minutes for you to COPY the key in the Console (copying the command itself first is fine), then clears the
#      clipboard. The key is never echoed, written to disk or saved in shell history.
# User-scoped keys (sk-ant-usr-…) also need ANTHROPIC_WORKSPACE_ID (wrkspc_…): asked for / waited for the same way.
#   bash scripts/verify_with_claude.sh [--limit 50] [--input data/x.json --gold data/x.json]
set -euo pipefail
cd "$(dirname "$0")/.."
PBPASTE="${PBPASTE:-pbpaste}"
PBCOPY="${PBCOPY:-pbcopy}"

wait_for_clipboard() {   # $1 = prefix to wait for, $2 = what to ask for; prints the value on stdout
  echo "→ Now COPY $2 (Cmd+C). Waiting up to 120 s…" >&2
  local v
  for _ in $(seq 1 120); do
    v="$($PBPASTE 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ "$v" == "$1"* ]]; then
      printf '' | $PBCOPY 2>/dev/null || true           # do not leave a secret on the clipboard
      echo "  got it." >&2
      printf %s "$v"
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for $2 on the clipboard." >&2
  return 1
}

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  if [ -t 0 ]; then
    printf "Paste your Anthropic API key (it stays hidden) and press Enter: "
    IFS= read -rs ANTHROPIC_API_KEY
    echo
  else
    ANTHROPIC_API_KEY="$(wait_for_clipboard sk-ant- 'your Anthropic API key from the Console')"
  fi
fi
ANTHROPIC_API_KEY="$(printf %s "$ANTHROPIC_API_KEY" | tr -d '[:space:]')"
export ANTHROPIC_API_KEY
case "$ANTHROPIC_API_KEY" in
  sk-ant-*) ;;
  *) echo "That is not an Anthropic API key (it must start with sk-ant-)." >&2; exit 2 ;;
esac

[[ "${ANTHROPIC_WORKSPACE_ID:-}" == wrkspc_PUT* ]] && ANTHROPIC_WORKSPACE_ID=""   # the placeholder from the docs
if [[ "$ANTHROPIC_API_KEY" == sk-ant-usr-* && -z "${ANTHROPIC_WORKSPACE_ID:-}" ]]; then
  if [ -t 0 ]; then
    printf "This key needs a workspace ID (Console → Settings → Workspaces, looks like wrkspc_...): "
    IFS= read -r ANTHROPIC_WORKSPACE_ID
  else
    ANTHROPIC_WORKSPACE_ID="$(wait_for_clipboard wrkspc_ 'your workspace ID (Console → Settings → Workspaces, wrkspc_…)')"
  fi
fi
export ANTHROPIC_WORKSPACE_ID="${ANTHROPIC_WORKSPACE_ID:-}"
[ -z "$ANTHROPIC_WORKSPACE_ID" ] && unset ANTHROPIC_WORKSPACE_ID

PY="${PY:-.venv/bin/python}"
ARGS=("$@")
[ ${#ARGS[@]} -eq 0 ] && ARGS=(--input data/demo_input.json --gold data/demo_gold.json)
"$PY" -W ignore -m eval.llm_verify "${ARGS[@]}" --out runs/verify
echo
sed -n '1,25p' runs/verify/report.md
echo
echo "Full report: runs/verify/report.md · decide each disagreement: open runs/verify/review.html"
