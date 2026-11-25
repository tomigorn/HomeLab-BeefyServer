#!/usr/bin/env bash
# CI only: writes a dummy .env per Docker project from .env.example (blank values filled, except .github/ci-env-blank.txt), so `docker compose config` and policy_check.py run without the real secrets. Usage: .github/scripts/ci-compose-env.sh
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
KEEP="$ROOT/.github/ci-env-blank.txt"
for dir in "$ROOT"/Docker/*/; do
    [ -f "$dir.env.example" ] || continue
    [ -e "$dir.env" ] && continue
    project=$(basename "$dir")
    awk -v project="$project" -v keep="$KEEP" '
        BEGIN { while ((getline line < keep) > 0) { split(line, a, " "); if (line !~ /^#/) blank[a[1] " " a[2]] = 1 } }
        /^[A-Za-z_][A-Za-z0-9_]*=[ \t]*$/ {
            name = $0; sub(/=.*/, "", name)
            if (!((project " " name) in blank)) { print name "=ci-dummy-value"; next }
        }
        { print }' "$dir.env.example" > "$dir.env"
done
