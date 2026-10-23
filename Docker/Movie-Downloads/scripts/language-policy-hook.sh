#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Movie Added / On Series Add): puts a new title on the right profile before its first search and turns its subs-<language> tags into one Bazarr profile (README "Languages", "Old and rare titles", "Per-request audio and subtitles", "Swiss German").
# Runs inside the app container (bash, curl, jq); registered by scripts/language-policy.sh. LP_TAGS_ONLY=1 skips the profile part except the tag-driven Swiss German move (language-policy.sh, request-tags.sh: existing titles).
set -euo pipefail

ENG_SUFFIX=" - English original"
ANY="Any quality - old & rare (upgrades to 4K)"
SWISS="Swiss German (Mundart) only - any quality"
SWISS_TAG=audio-swiss-german
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
LANGS_FILE=${LP_LANGUAGES:-/scripts/languages.tsv}
BAZARR_URL=${LP_BAZARR_URL:-http://bazarr:6767}
BAZARR_KEY=${LP_BAZARR_KEY:-/secrets/bazarr.apikey}
xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
KEY=$(xml ApiKey)
URL="http://localhost:$(xml Port)$(xml UrlBase)/api/v3"
api() { local path=$1; shift; curl -sf -m 10 -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' "$URL$path" "$@"; }

title=$(api "/$kind/$id")
read -r MID MEN AID AEN SID < <(api /qualityprofile | jq -r --arg m "$MAIN" --arg a "$ANY" --arg s "$ENG_SUFFIX" --arg w "$SWISS" \
  'map({(.name): .id}) | add | "\(.[$m] // 0) \(.[$m + $s] // 0) \(.[$a] // 0) \(.[$a + $s] // 0) \(.[$w] // 0)"')
read -r current english year votes < <(jq -r \
  "\"\(.qualityProfileId) \(.originalLanguage.name == \"English\") \(.year // 0) \($VOTES // 0)\"" <<<"$title")
now=${LP_YEAR:-$(date +%Y)}

set_profile() {
  local family reason= target
  case $current in
    "$MID"|"$MEN") family=main;;
    "$AID"|"$AEN") family=any;;
    *) echo "$kind $id: profile $current kept"; return;;
  esac
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
}

# Swiss German is mandatory with audio-swiss-german, whatever profile the title was added with; a title added on that profile gets the tag, whose release profile is the hard filter (Radarr and Sonarr).
swiss_profile() {
  [ "$SID" != 0 ] || return 1
  local stid profile lang
  stid=$(api /tag | jq -r --arg l "$SWISS_TAG" '.[] | select(.label == $l) | .id')
  [ -n "$stid" ] || return 1
  if jq -e --argjson t "$stid" '(.tags // []) | index($t)' <<<"$title" >/dev/null; then
    if [ "$current" != "$SID" ]; then
      api "/$kind/editor" -X PUT -d "{\"$ids\":[$id],\"qualityProfileId\":$SID}" >/dev/null
      echo "$kind $id: profile $current -> $SID ($SWISS_TAG)"
    else
      echo "$kind $id: profile $current kept"
    fi
  elif [ "$current" = "$SID" ]; then
    edit_tag "$stid" add
    echo "$kind $id: tag $SWISS_TAG added (Swiss German profile)"
  else
    return 1
  fi
  # Recyclarr puts the guide's language (Original) back whenever it updates the profile; Any lets a Swiss German dub of a foreign film through. Sonarr profiles have no language.
  profile=$(api "/qualityprofile/$SID")
  lang=$(jq -r '.language.name // "none"' <<<"$profile")
  if [ "$lang" != Any ] && [ "$lang" != none ]; then
    jq -c '.language = {id: -1, name: "Any"}' <<<"$profile" | api "/qualityprofile/$SID" -X PUT -d @- >/dev/null
    echo "profile $SID: language $lang -> Any"
  fi
}

edit_tag() { # edit_tag <tag id> <add|remove>
  api "/$kind/editor" -X PUT -d "{\"$ids\":[$id],\"tags\":[$1],\"applyTags\":\"$2\"}" >/dev/null
}

# Bazarr picks the first profile whose tag the title carries; lang-xx is not set while a subs-<language> tag is (auto-tagging rule), so bz-<codes> is the only match.
ensure_bazarr_profile() { # ensure_bazarr_profile <tag> <profile name> <codes...>
  local tag=$1 name=$2; shift 2
  [ -r "$BAZARR_KEY" ] || { echo "$kind $id: no Bazarr API key at $BAZARR_KEY, $tag not set" >&2; return 1; }
  local key profiles langs
  key=$(tr -d '[:space:]' < "$BAZARR_KEY")
  profiles=$(curl -sf -m 10 -H "X-API-KEY: $key" "$BAZARR_URL/api/system/languages/profiles")
  if jq -e --arg t "$tag" 'any(.[]; .tag == $t)' <<<"$profiles" >/dev/null; then return 0; fi
  langs=$(printf '%s\n' "$@" | jq -R . | jq -sc .)
  profiles=$(jq -c --arg t "$tag" --arg n "$name" --argjson l "$langs" '
    def item($i; $lang; $forced): {id: $i, language: $lang, audio_exclude: "False", audio_only_include: "False", hi: "False", forced: $forced};
    . + [{profileId: ((map(.profileId) | max // 0) + 1), name: $n, cutoff: null, mustContain: [], mustNotContain: [], originalFormat: 0, tag: $t,
          items: ([$l | to_entries[] | item(.key + 1; .value; "False")] + [item(($l | length) + 1; "en"; "True"), item(($l | length) + 2; "de"; "True")])}]' <<<"$profiles")
  curl -sf -m 10 -H "X-API-KEY: $key" -X POST --data-urlencode "languages-profiles=$profiles" "$BAZARR_URL/api/system/settings" >/dev/null
  echo "$kind $id: Bazarr profile '$name' ($tag) created"
}

slug() { tr 'A-Z ' 'a-z-' <<<"$1"; } # language name -> tag slug: Swiss German -> swiss-german
slug_code() { awk -F'\t' -v s="$1" '!/^#/ { n = tolower($2); gsub(/ /, "-", n); if (n == s) print $1 }' "$LANGS_FILE"; }

set_tags() {
  local tag_ids all labels orig code name codes names label tid
  tag_ids=$(jq -c '.tags // []' <<<"$title")
  [ "$tag_ids" != "[]" ] || return 0
  all=$(api /tag)
  labels=$(jq -r --argjson ids "$tag_ids" '.[] | select(.id as $i | $ids | index($i)) | .label' <<<"$all")
  tag_id() { jq -r --arg l "$1" '.[] | select(.label == $l) | .id' <<<"$all"; }
  orig=$(jq -r '.originalLanguage.name // ""' <<<"$title")
  code=$(awk -F'\t' -v n="$orig" '!/^#/ && $2 == n {print $1}' "$LANGS_FILE")

  oslug=$(slug "$orig")
  if [ -n "$code" ] && grep -qx "audio-$oslug" <<<"$labels"; then
    edit_tag "$(tag_id "audio-$oslug")" remove
    echo "$kind $id: audio-$oslug dropped (original language)"
  fi

  codes=()
  if [ -n "$code" ] && awk -F'\t' -v c="$code" '!/^#/ && $1 == c && $4 == "yes" {f=1} END {exit !f}' "$LANGS_FILE"; then codes+=("$code"); fi
  while read -r c; do
    [ -n "$c" ] && [ "$c" != "$code" ] && codes+=("$c")
  done < <(sed -n 's/^subs-//p' <<<"$labels" | while read -r s; do slug_code "$s"; done | grep -vx -e en -e de | sort)
  [ "${#codes[@]}" -gt 0 ] && grep -q '^subs-' <<<"$labels" || return 0

  label="bz-$(IFS=-; echo "${codes[*]}")"
  names="English + German"
  for c in "${codes[@]}"; do
    names+=" + $(awk -F'\t' -v c="$c" '!/^#/ && $1 == c {print $2}' "$LANGS_FILE")"
  done
  # "(requested)" keeps the name apart from the lang-xx profiles, which language-policy.sh finds by name.
  ensure_bazarr_profile "$label" "$names (requested)" en de "${codes[@]}"

  for old in $(grep '^bz-' <<<"$labels" | grep -vx "$label"); do edit_tag "$(tag_id "$old")" remove; done
  grep -qx "$label" <<<"$labels" && return 0
  tid=$(tag_id "$label")
  [ -n "$tid" ] || tid=$(api /tag -X POST -d "{\"label\":\"$label\"}" | jq -r .id)
  edit_tag "$tid" add
  echo "$kind $id: tag $label added"
}

swiss_profile || [ "${LP_TAGS_ONLY:-}" = 1 ] || set_profile
set_tags
