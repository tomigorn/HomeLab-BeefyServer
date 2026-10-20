#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Movie Added / On Series Add): puts a new title on the right profile before its first search (README "Languages", "Old and rare titles").
# Runs inside the app container (bash, curl, jq); registered by scripts/language-policy.sh.
set -euo pipefail

ENG_SUFFIX=" - English original"
ANY="Any quality - old & rare (upgrades to 4K)"
OLD_YEARS=${LP_OLD_YEARS:-12}
NICHE_MIN_AGE=2
if [ -n "${radarr_eventtype:-}" ]; then
  event=$radarr_eventtype; want=MovieAdded; id=${radarr_movie_id:-}; kind=movie; ids=movieIds
  MAIN="4K HDR - UHD Bluray + WEB (1080p fallback)"
  VOTES='.ratings.tmdb.votes'; NICHE_VOTES=${LP_NICHE_VOTES:-100}
elif [ -n "${sonarr_eventtype:-}" ]; then
  event=$sonarr_eventtype; want=SeriesAdd; id=${sonarr_series_id:-}; kind=series; ids=seriesIds
  MAIN="4K HDR - WEB-2160p (1080p fallback)"
  VOTES='.ratings.votes'; NICHE_VOTES=${LP_NICHE_VOTES:-1000}
else
  echo "no radarr_eventtype/sonarr_eventtype: not called by Radarr or Sonarr" >&2; exit 1
fi
[ "$event" = "$want" ] || exit 0

CONFIG=${LP_CONFIG:-/config/config.xml}
xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
KEY=$(xml ApiKey)
URL="http://localhost:$(xml Port)$(xml UrlBase)/api/v3"
api() { local path=$1; shift; curl -sf -m 10 -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' "$URL$path" "$@"; }

title=$(api "/$kind/$id")
read -r MID MEN AID AEN < <(api /qualityprofile | jq -r --arg m "$MAIN" --arg a "$ANY" --arg s "$ENG_SUFFIX" \
  'map({(.name): .id}) | add | "\(.[$m] // 0) \(.[$m + $s] // 0) \(.[$a] // 0) \(.[$a + $s] // 0)"')
read -r current english year votes < <(jq -r \
  "\"\(.qualityProfileId) \(.originalLanguage.name == \"English\") \(.year // 0) \($VOTES // 0)\"" <<<"$title")
now=${LP_YEAR:-$(date +%Y)}

case $current in
  "$MID"|"$MEN") family=main;;
  "$AID"|"$AEN") family=any;;
  *) echo "$kind $id: profile $current kept"; exit 0;;
esac
reason=
if [ "$family" = main ] && [ "$AID" != 0 ] && [ "$year" -gt 0 ]; then
  if [ "$year" -le $((now - OLD_YEARS)) ]; then family=any; reason="old: $year"
  elif [ "$year" -le $((now - NICHE_MIN_AGE)) ] && [ "$votes" -lt "$NICHE_VOTES" ]; then family=any; reason="niche: $votes votes"
  fi
fi
case $family:$english in
  main:false) target=$MID;;
  main:true) target=$MEN;;
  any:false) target=$AID;;
  any:true) target=$AEN;;
esac
[ "$target" != 0 ] || target=$current
if [ "$english" = true ]; then reason="${reason:+$reason, }English original"; else reason="${reason:-not English original}"; fi

if [ "$target" != "$current" ]; then
  api "/$kind/editor" -X PUT -d "{\"$ids\":[$id],\"qualityProfileId\":$target}" >/dev/null
  echo "$kind $id: profile $current -> $target ($reason)"
else
  echo "$kind $id: profile $current kept"
fi
