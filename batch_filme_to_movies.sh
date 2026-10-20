#!/usr/bin/env bash
# Batch: rescan Filme → dry-run N → execute successes → mv to Movies → repeat.
# Movielist is rebuilt every batch from what is still in Filme.
# Skips are remembered so they are not retried forever.
# Paths: env / CLI > ~/.config/jellyfin-movie-renamer/paths.json > ~/Filme ~/Movies
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BATCH_SIZE="${BATCH_SIZE:-10}"
eval "$(python3 "$SCRIPT_DIR/paths_config.py" --shell)"
WORK="$WORK_DIR"
RENAMER="$SCRIPT_DIR/movie_renamer.py"
ONCE=0
RESET_SKIPS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --once) ONCE=1; shift ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --filme) FILME="$2"; shift 2 ;;
    --movies) MOVIES="$2"; shift 2 ;;
    --work) WORK="$2"; WORK_DIR="$2"; shift 2 ;;
    --reset-skips) RESET_SKIPS=1; shift ;;
    -h|--help)
      echo "Usage: $0 [--once] [--batch-size N] [--reset-skips] [--filme PATH] [--movies PATH] [--work PATH]"
      echo "Env: BATCH_SIZE FILME MOVIES WORK_DIR"
      echo "Config: ~/.config/jellyfin-movie-renamer/paths.json (same as triage GUI)"
      echo
      echo "Each batch rescans Filme (dirty source). Successes are moved to Movies (clean Jellyfin)."
      echo "IMDb skips go to \$WORK/permanent_skipped.txt and are ignored next round."
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

SKIP_ARCHIVE="$WORK/permanent_skipped.txt"
DVD_QUEUE="$WORK/dvd_manual.txt"
[[ "$RESET_SKIPS" -eq 1 ]] && rm -f "$SKIP_ARCHIVE"

mkdir -p "$WORK"
touch "$SKIP_ARCHIVE"
[[ -d "$FILME" ]] || { echo "Filme path missing: $FILME" >&2; exit 1; }
[[ -d "$MOVIES" ]] || { echo "Movies path missing: $MOVIES" >&2; exit 1; }
[[ -f "$RENAMER" ]] || { echo "Renamer missing: $RENAMER" >&2; exit 1; }

echo "Filme=$FILME"
echo "Movies=$MOVIES"
echo "batch=$BATCH_SIZE | skip-archive=$SKIP_ARCHIVE"

build_batch_list() {
  # Fresh scan of Filme, drop permanent skips, write up to BATCH_SIZE names.
  # Pure Python filter — avoids `set -o pipefail` dying on `grep | head` SIGPIPE.
  python3 "$RENAMER" --generate-inputfile-from="$FILME" --fileoutput="$WORK"
  local full="$WORK/movielist.txt"
  local out="$WORK/batch_list.txt"
  touch "$DVD_QUEUE"
  eval "$(python3 - "$full" "$SKIP_ARCHIVE" "$DVD_QUEUE" "$out" "$FILME" "$BATCH_SIZE" <<'PY'
import sys
from pathlib import Path

full, skip_path, dvd_path, out_path, filme, batch_size = sys.argv[1:7]
batch_size = int(batch_size)

def load_names(path):
    p = Path(path)
    if not p.is_file():
        return set()
    names = set()
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        names.add(line.split(" | ", 1)[0].strip())
    return names

ignore = load_names(skip_path) | load_names(dvd_path)
names = []
for line in Path(full).read_text(encoding="utf-8", errors="ignore").splitlines():
    name = line.strip()
    if not name or name.startswith("#"):
        continue
    if name in ignore:
        continue
    names.append(name)

remaining = len(names)
batch = names[:batch_size]
Path(out_path).write_text(
    "# BASE_PATH=%s\n%s" % (filme, "".join(n + "\n" for n in batch)),
    encoding="utf-8",
)
print("REMAINING=%d" % remaining)
print("BATCH_COUNT=%d" % len(batch))
PY
)"
}

parse_success_movable() {
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

while true; do
  build_batch_list
  echo
  echo "========== remaining≈$REMAINING | this batch=$BATCH_COUNT =========="

  if (( BATCH_COUNT == 0 )); then
    echo "Nothing left to try (Filme empty of non-skipped folders)."
    break
  fi

  : > "$WORK/success.txt"
  : > "$WORK/backlog.txt"
  : > "$WORK/backlog_skipped.txt"

  python3 "$RENAMER" \
    --input="$WORK/batch_list.txt" \
    --success-file="$WORK/success.txt" \
    --backlog-file="$WORK/backlog.txt"

  # Remember IMDb skips so the next rescan does not retry them forever
  if [[ -f "$WORK/backlog_skipped.txt" ]]; then
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      name="${line%% |*}"
      name="${name#"${name%%[![:space:]]*}"}"
      name="${name%"${name##*[![:space:]]}"}"
      [[ -n "$name" ]] || continue
      if ! grep -Fxq "$name" "$SKIP_ARCHIVE"; then
        printf '%s\n' "$name" >> "$SKIP_ARCHIVE"
        echo "Archived skip: $name"
      fi
    done < "$WORK/backlog_skipped.txt"
  fi

  # Folder renamed but no video → leave in Filme, don't retry as success-move
  if [[ -f "$WORK/backlog.txt" ]]; then
    while IFS= read -r name; do
      [[ -z "$name" ]] && continue
      if ! grep -Fxq "$name" "$SKIP_ARCHIVE"; then
        printf '%s\n' "$name" >> "$SKIP_ARCHIVE"
        echo "Archived backlog (no video): $name"
      fi
    done < "$WORK/backlog.txt"
  fi

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
          # Already in Movies from an earlier run — drop Filme leftover.
          echo "ALREADY in Movies: $new — removing Filme leftover"
          rm -rf "$src"
        else
          mv "$src" "$MOVIES/"
          echo "MOVED: $new"
        fi
      else
        echo "MISSING after execute: $src"
      fi
    done
  fi

  if (( ONCE )); then
    echo "Stopping after --once."
    break
  fi
done

echo
echo "Done. Skips/backlog kept in Filme; see $SKIP_ARCHIVE"
