#!/usr/bin/env bash
# Batch: dry-run N → execute successes → mv to Movies → next batch until done.
# Resume-safe via offset file. Skips stay in Filme.
set -euo pipefail

BATCH_SIZE="${BATCH_SIZE:-10}"
FILME="${FILME:-/path/to/Source}"
MOVIES="${MOVIES:-/path/to/Destination}"
WORK="${WORK_DIR:-$HOME/filme_batch_work}"
MOVIELIST="${MOVIELIST:-$HOME/movielist.txt}"
RENAMER="$(cd "$(dirname "$0")" && pwd)/movie_renamer.py"
OFFSET_FILE="$WORK/offset"
ONCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --once) ONCE=1; shift ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --filme) FILME="$2"; shift 2 ;;
    --movies) MOVIES="$2"; shift 2 ;;
    --reset-offset) rm -f "$OFFSET_FILE"; shift ;;
    -h|--help)
      echo "Usage: $0 [--once] [--batch-size N] [--reset-offset] [--filme PATH] [--movies PATH]"
      echo "Env: BATCH_SIZE FILME MOVIES WORK_DIR MOVIELIST"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "$WORK"
[[ -d "$FILME" ]] || { echo "Filme path missing: $FILME" >&2; exit 1; }
[[ -d "$MOVIES" ]] || { echo "Movies path missing: $MOVIES" >&2; exit 1; }
[[ -f "$RENAMER" ]] || { echo "Renamer missing: $RENAMER" >&2; exit 1; }

if [[ ! -f "$MOVIELIST" ]]; then
  echo "Generating $MOVIELIST from $FILME ..."
  python3 "$RENAMER" --generate-inputfile-from="$FILME" --fileoutput="$(dirname "$MOVIELIST")"
fi

TOTAL=$(grep -v '^#' "$MOVIELIST" | grep -c '[^[:space:]]' || true)
OFFSET=0
[[ -f "$OFFSET_FILE" ]] && OFFSET=$(cat "$OFFSET_FILE")

echo "Total in list: $TOTAL | offset: $OFFSET | batch: $BATCH_SIZE"
echo "Filme=$FILME"
echo "Movies=$MOVIES"

parse_success_movable() {
  # Reads success.txt + optional backlog.txt → prints "original<TAB>new" for movable hits.
  local success_file="$1"
  local backlog_file="$2"
  python3 - "$success_file" "$backlog_file" <<'PY'
import sys
from pathlib import Path

success_path, backlog_path = sys.argv[1], sys.argv[2]
backlog = set()
bp = Path(backlog_path)
if bp.is_file():
    backlog = {line.strip() for line in bp.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()}

text = Path(success_path).read_text(encoding="utf-8", errors="ignore")
for block in text.split("\n\n"):
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("Folder: "):
        continue
    body = lines[0][len("Folder: "):]
    if " -> " not in body:
        continue
    original, new = body.split(" -> ", 1)
    original, new = original.strip(), new.strip()
    if original in backlog:
        continue
    print(f"{original}\t{new}")
PY
}

while (( OFFSET < TOTAL )); do
  echo
  echo "========== BATCH offset=$OFFSET limit=$BATCH_SIZE =========="

  # Fresh outputs for this batch
  : > "$WORK/success.txt"
  : > "$WORK/backlog.txt"
  : > "$WORK/backlog_skipped.txt"

  python3 "$RENAMER" \
    --input="$MOVIELIST" \
    --offset="$OFFSET" \
    --limit="$BATCH_SIZE" \
    --success-file="$WORK/success.txt" \
    --backlog-file="$WORK/backlog.txt"

  mapfile -t MOVABLE < <(parse_success_movable "$WORK/success.txt" "$WORK/backlog.txt" || true)

  if ((${#MOVABLE[@]} == 0)); then
    echo "No movable successes in this batch."
  else
    echo "Movable successes: ${#MOVABLE[@]}"
    {
      echo "# BASE_PATH=$FILME"
      for row in "${MOVABLE[@]}"; do
        printf '%s\n' "${row%%$'\t'*}"
      done
    } > "$WORK/success_run.txt"

    echo "--- EXECUTE ---"
    python3 "$RENAMER" \
      --input="$WORK/success_run.txt" \
      --execute \
      --success-file="$WORK/success_execute.txt" \
      --backlog-file="$WORK/backlog_execute.txt"

    echo "--- MOVE TO Movies ---"
    for row in "${MOVABLE[@]}"; do
      new="${row#*$'\t'}"
      src="$FILME/$new"
      dst="$MOVIES/$new"
      if [[ -e "$src" ]]; then
        if [[ -e "$dst" ]]; then
          echo "SKIP exists in Movies: $new"
        else
          mv "$src" "$MOVIES/"
          echo "MOVED: $new"
        fi
      else
        echo "MISSING after execute: $src"
      fi
    done
  fi

  OFFSET=$((OFFSET + BATCH_SIZE))
  echo "$OFFSET" > "$OFFSET_FILE"
  echo "Offset saved: $OFFSET / $TOTAL"

  if (( ONCE )); then
    echo "Stopping after --once."
    break
  fi
done

if (( OFFSET >= TOTAL )); then
  echo
  echo "Done. All $TOTAL list entries visited (skips remain in Filme)."
  rm -f "$OFFSET_FILE"
fi
