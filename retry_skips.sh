#!/usr/bin/env bash
# Re-probe permanent_skipped.txt one-by-one (dry-run + debug) → skipped_reasons.txt
set -euo pipefail

FILME="${FILME:-/mnt/nfs/huddle/media/Filme}"
WORK="${WORK_DIR:-$HOME/filme_batch_work}"
SKIP_ARCHIVE="${SKIP_ARCHIVE:-$WORK/permanent_skipped.txt}"
OUT="${OUT:-$WORK/skipped_reasons.txt}"
RENAMER="$(cd "$(dirname "$0")" && pwd)/movie_renamer.py"
LIMIT="${LIMIT:-0}"   # 0 = all
OFFSET="${OFFSET:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit) LIMIT="$2"; shift 2 ;;
    --offset) OFFSET="$2"; shift 2 ;;
    --filme) FILME="$2"; shift 2 ;;
    --skips) SKIP_ARCHIVE="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--limit N] [--offset N] [--skips FILE] [--out FILE] [--filme PATH]"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

[[ -f "$SKIP_ARCHIVE" ]] || { echo "Missing skip archive: $SKIP_ARCHIVE" >&2; exit 1; }
[[ -f "$RENAMER" ]] || { echo "Missing renamer: $RENAMER" >&2; exit 1; }
[[ -d "$FILME" ]] || { echo "Missing Filme: $FILME" >&2; exit 1; }
mkdir -p "$WORK"

mapfile -t NAMES < <(grep -v '^[[:space:]]*$' "$SKIP_ARCHIVE")
TOTAL=${#NAMES[@]}
echo "Skips: $TOTAL | offset=$OFFSET limit=$LIMIT | out=$OUT"

: > "$OUT"
START="$OFFSET"
END="$TOTAL"
if (( LIMIT > 0 )); then
  END=$((OFFSET + LIMIT))
  (( END > TOTAL )) && END=$TOTAL
fi

for ((i = START; i < END; i++)); do
  name="${NAMES[$i]}"
  echo
  echo "========== [$((i + 1))/$TOTAL] $name =========="
  folder="$FILME/$name"
  if [[ ! -d "$folder" ]]; then
    printf '%s | folder missing (already moved or renamed)\n' "$name" | tee -a "$OUT"
    continue
  fi

  list="$WORK/_retry_one.txt"
  printf '# BASE_PATH=%s\n%s\n' "$FILME" "$name" > "$list"
  log="$WORK/_retry_one.log"
  set +e
  python3 "$RENAMER" \
    --input="$list" \
    --debug \
    --success-file="$WORK/_retry_success.txt" \
    --backlog-file="$WORK/_retry_backlog.txt" \
    >"$log" 2>&1
  rc=$?
  set -e

  reason=""
  if [[ -f "$WORK/backlog_skipped.txt" ]]; then
    # renamer derives skipped from backlog-file name
    :
  fi
  skipped_file="$WORK/_retry_backlog_skipped.txt"
  if [[ -f "$skipped_file" ]]; then
    reason=$(awk -F' \\| ' 'NF>=2{print substr($0, index($0,$2)); exit}' "$skipped_file" || true)
  fi
  if [[ -z "$reason" ]]; then
    # fall back to last useful debug line
    reason=$(grep -E 'Skipping|Multiple versions|No suitable|No confident|Selected|Error searching' "$log" | tail -1 | sed 's/^[[:space:]]*//' || true)
  fi
  if [[ -z "$reason" ]]; then
    if grep -q 'Successfully processed: 1' "$log"; then
      reason="NOW MATCHES (dry-run success — remove from permanent_skipped and re-batch)"
    else
      reason="unknown (rc=$rc) — see $log"
    fi
  fi

  printf '%s | %s\n' "$name" "$reason" | tee -a "$OUT"
done

echo
echo "Wrote $OUT"
# rough tally
echo "--- reason counts ---"
awk -F' \\| ' 'NF>=2{print substr($0, index($0,$2))}' "$OUT" | sort | uniq -c | sort -rn
