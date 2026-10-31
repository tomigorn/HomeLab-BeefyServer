#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Movie Added / On Series Add): puts a new title on the right profile before its first search, gives titles on Any quality the old-rare tag (min. 1 seeder indexers) and turns its subs-<language> tags into one Bazarr profile (README "Languages", "Old and rare titles", "Per-request audio and subtitles", "Swiss German").
# Runs inside the app container (bash, curl, jq); registered by scripts/language-policy.sh. LP_TAGS_ONLY=1 skips the profile part except the tag-driven Swiss German move (language-policy.sh: existing titles).
# LP_RECOMPUTE=1 (request-tags.sh, a title's tags or profile changed): profile from scratch = default rules + remaining tags; LP_PREV_PROFILE / LP_PREV_TAGS (ids, comma-separated) tell a profile the user picked (kept with the profile-pinned tag) from one the tags set.
# A Connect call (no LP_TAGS_ONLY / LP_RECOMPUTE) writes "<kind> <id>" to title-added.stamp next to the config at exit: systemd/clear-data-wipe-added.path then snapshots Seerr while the new request still exists.
set -euo pipefail

ENG_SUFFIX=" - English original"
ANY="4K preferred · older versions allowed"
SWISS="Swiss German (Mundart) only - any quality"
SWISS_TAG=audio-swiss-german
PIN_TAG=profile-pinned
OLD_RARE_TAG=old-rare
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
if [ -z "${LP_TAGS_ONLY:-}" ] && [ -z "${LP_RECOMPUTE:-}" ]; then
  trap 'echo "$kind $id" > "$(dirname "$CONFIG")/title-added.stamp" 2>/dev/null || true' EXIT
fi
xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
KEY=$(xml ApiKey)
URL="http://localhost:$(xml Port)$(xml UrlBase)/api/v3"
api() { local path=$1; shift; curl -sf -m 10 -H @<(printf 'X-Api-Key: %s\n' "$KEY") -H 'Content-Type: application/json' "$URL$path" "$@"; }

title=$(api "/$kind/$id")
read -r MID MEN AID AEN SID < <(api /qualityprofile | jq -r --arg m "$MAIN" --arg a "$ANY" --arg s "$ENG_SUFFIX" --arg w "$SWISS" \
  'map({(.name): .id}) | add | "\(.[$m] // 0) \(.[$m + $s] // 0) \(.[$a] // 0) \(.[$a + $s] // 0) \(.[$w] // 0)"')
read -r current english year votes < <(jq -r \
  "\"\(.qualityProfileId) \(.originalLanguage.name == \"English\") \(.year // 0) \($VOTES // 0)\"" <<<"$title")
now=${LP_YEAR:-$(date +%Y)}

set_profile() {
  local family reason= target
  if pinned; then echo "$kind $id: profile $current kept ($PIN_TAG)"; return; fi
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
    current=$target
  else
    echo "$kind $id: profile $current kept"
  fi
}

# Swiss German is mandatory with audio-swiss-german, whatever profile the title was added with; a title added on that profile gets the tag, whose release profile is the hard filter (Radarr and Sonarr).
swiss_profile() {
  [ "$SID" != 0 ] || return 1
  local stid
  stid=$(api /tag | jq -r --arg l "$SWISS_TAG" '.[] | select(.label == $l) | .id')
  [ -n "$stid" ] || return 1
  if jq -e --argjson t "$stid" '(.tags // []) | index($t)' <<<"$title" >/dev/null; then
    if [ "$current" != "$SID" ]; then
      move "$SID" "$SWISS_TAG"
    else
      echo "$kind $id: profile $current kept"
    fi
    unpin
  elif [ "$current" = "$SID" ]; then
    edit_tag "$stid" add
    echo "$kind $id: tag $SWISS_TAG added (Swiss German profile)"
  else
    return 1
  fi
  swiss_language
}

# Recyclarr puts the guide's language (Original) back whenever it updates the profile; Any lets a Swiss German dub of a foreign film through. Sonarr profiles have no language.
swiss_language() {
  local profile lang
  profile=$(api "/qualityprofile/$SID")
  lang=$(jq -r '.language.name // "none"' <<<"$profile")
  if [ "$lang" != Any ] && [ "$lang" != none ]; then
    jq -c '.language = {id: -1, name: "Any"}' <<<"$profile" | api "/qualityprofile/$SID" -X PUT -d @- >/dev/null
    echo "profile $SID: language $lang -> Any"
  fi
}

edit_tag() { # edit_tag <tag id> <add|remove>
  api "/$kind/editor" -X PUT -d "{\"$ids\":[$id],\"tags\":[$1],\"applyTags\":\"$2\"}" >/dev/null
  title=$(jq -c --argjson t "$1" --arg how "$2" '.tags = (if $how == "add" then ((.tags // []) + [$t] | unique) else ((.tags // []) - [$t]) end)' <<<"$title")
}
has_tag() { jq -e --argjson t "$1" '(.tags // []) | index($t)' <<<"$title" >/dev/null; }
tag_named() { api /tag | jq -r --arg l "$1" '.[] | select(.label == $l) | .id'; }
move() { # move <profile id> <reason>
  api "/$kind/editor" -X PUT -d "{\"$ids\":[$id],\"qualityProfileId\":$1}" >/dev/null
  echo "$kind $id: profile $current -> $1 ($2)"
  current=$1
}

# profile-pinned: the user picked this profile (My request, Radarr/Sonarr); tag changes keep it until the Swiss German tag or a pick of the default replaces it.
pinned() { local t; t=$(tag_named "$PIN_TAG"); [ -n "$t" ] && has_tag "$t"; }
pin() {
  local t; t=$(tag_named "$PIN_TAG")
  [ -n "$t" ] || t=$(api /tag -X POST -d "{\"label\":\"$PIN_TAG\"}" | jq -r .id)
  has_tag "$t" || edit_tag "$t" add
}
unpin() {
  local t; t=$(tag_named "$PIN_TAG")
  if [ -n "$t" ] && has_tag "$t"; then edit_tag "$t" remove; echo "$kind $id: tag $PIN_TAG removed"; fi
}

# default_profile: sets DEF / DEF_REASON from the rules alone (main 4K, old/niche -> Any quality, English original -> twin).
default_profile() {
  local family=main reason=
  if [ "$AID" != 0 ] && [ "$year" -gt 0 ]; then
    if [ "$year" -le $((now - OLD_YEARS)) ]; then family=any; reason="old: $year"
    elif [ "$year" -le $((now - NICHE_MIN_AGE)) ] && [ "$votes" -lt "$NICHE_VOTES" ]; then family=any; reason="niche: $votes votes"
    fi
  fi
  case $family:$english in
    main:false) DEF=$MID;;
    main:true) DEF=$MEN;;
    any:false) DEF=$AID;;
    any:true) DEF=$AEN;;
  esac
  [ "$DEF" != 0 ] || DEF=$MID
  [ "$DEF" != 0 ] || DEF=$current
  if [ "$english" = true ]; then DEF_REASON="${reason:+$reason, }English original"; else DEF_REASON="${reason:-not English original}"; fi
}

recompute() {
  local stid swiss=false swiss_new=true picked=false
  stid=$(tag_named "$SWISS_TAG")
  if [ "$SID" != 0 ] && [ -n "$stid" ] && has_tag "$stid"; then swiss=true; fi
  if [ -n "${LP_PREV_PROFILE:-}" ]; then
    [ "$LP_PREV_PROFILE" = "$current" ] || picked=true
    if [ -n "$stid" ] && tr ',' '\n' <<<"${LP_PREV_TAGS:-}" | grep -qx "$stid"; then swiss_new=false; fi
  fi
  if [ "$swiss" = true ] && [ "$picked" = true ] && [ "$current" != "$SID" ] && [ "$swiss_new" = false ]; then
    edit_tag "$stid" remove
    echo "$kind $id: tag $SWISS_TAG removed (profile $current picked)"
    swiss=false
  fi
  if [ "$swiss" = true ]; then
    if [ "$current" = "$SID" ]; then echo "$kind $id: profile $current kept"; else move "$SID" "$SWISS_TAG"; fi
    unpin; swiss_language; return
  fi
  default_profile
  if [ "$picked" = true ]; then
    if [ "$SID" != 0 ] && [ "$current" = "$SID" ] && [ -n "$stid" ]; then
      edit_tag "$stid" add
      echo "$kind $id: tag $SWISS_TAG added (Swiss German profile picked)"
      unpin; swiss_language
    elif [ "$current" = "$DEF" ]; then
      unpin; echo "$kind $id: profile $current kept (default)"
    else
      pin; echo "$kind $id: profile $current kept (picked, $PIN_TAG)"
    fi
    return
  fi
  if pinned; then echo "$kind $id: profile $current kept ($PIN_TAG)"; return; fi
  case $current in
    "$MID"|"$MEN"|"$AID"|"$AEN"|"$SID") ;;
    *) echo "$kind $id: profile $current kept"; return;;
  esac
  if [ "$DEF" != "$current" ]; then move "$DEF" "default: $DEF_REASON"; else echo "$kind $id: profile $current kept"; fi
}

# Bazarr picks the first profile whose tag the title carries; lang-xx is not set while a subs-<language> tag is (auto-tagging rule), so bz-<codes> is the only match.
# Forced items only when the audio has that language (audio_only_include): foreign-parts subtitles are useless with a dub in another language.
ensure_bazarr_profile() { # ensure_bazarr_profile <tag> <profile name> <codes...>
  local tag=$1 name=$2; shift 2
  [ -r "$BAZARR_KEY" ] || { echo "$kind $id: no Bazarr API key at $BAZARR_KEY, $tag not set" >&2; return 1; }
  local key profiles langs
  key=$(tr -d '[:space:]' < "$BAZARR_KEY")
  profiles=$(curl -sf -m 10 -H "X-API-KEY: $key" "$BAZARR_URL/api/system/languages/profiles")
  if jq -e --arg t "$tag" 'any(.[]; .tag == $t)' <<<"$profiles" >/dev/null; then return 0; fi
  langs=$(printf '%s\n' "$@" | jq -R . | jq -sc .)
  profiles=$(jq -c --arg t "$tag" --arg n "$name" --argjson l "$langs" '
    def item($i; $lang; $forced): {id: $i, language: $lang, audio_exclude: "False", audio_only_include: $forced, hi: "False", forced: $forced};
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
  label=
  if [ "${#codes[@]}" -gt 0 ] && grep -q '^subs-' <<<"$labels"; then
    label="bz-$(IFS=-; echo "${codes[*]}")"
    names="English + German"
    for c in "${codes[@]}"; do
      names+=" + $(awk -F'\t' -v c="$c" '!/^#/ && $1 == c {print $2}' "$LANGS_FILE")"
    done
    # "(requested)" keeps the name apart from the lang-xx profiles, which language-policy.sh finds by name.
    ensure_bazarr_profile "$label" "$names (requested)" en de "${codes[@]}"
  fi

  # A removed subs-* tag drops its bz tag; request-tags.sh deletes Bazarr profiles no title uses any more.
  for old in $(grep '^bz-' <<<"$labels" | grep -vx "$label"); do edit_tag "$(tag_id "$old")" remove; echo "$kind $id: tag $old removed"; done
  [ -n "$label" ] || return 0
  grep -qx "$label" <<<"$labels" && return 0
  tid=$(tag_id "$label")
  [ -n "$tid" ] || tid=$(api /tag -X POST -d "{\"label\":\"$label\"}" | jq -r .id)
  edit_tag "$tid" add
  echo "$kind $id: tag $label added"
}

# The torrent indexer copies with minimum seeders 1 carry this tag, so only Any quality titles use them (README "Indexers").
old_rare_tag() {
  local on_any=false has=false tid
  if [ "$AID" != 0 ] && [ "$current" = "$AID" ]; then on_any=true; fi
  if [ "$AEN" != 0 ] && [ "$current" = "$AEN" ]; then on_any=true; fi
  tid=$(api /tag | jq -r --arg l "$OLD_RARE_TAG" '.[] | select(.label == $l) | .id')
  if [ -n "$tid" ] && jq -e --argjson t "$tid" '(.tags // []) | index($t)' <<<"$title" >/dev/null; then has=true; fi
  if [ "$on_any" = true ] && [ "$has" = false ]; then
    [ -n "$tid" ] || tid=$(api /tag -X POST -d "{\"label\":\"$OLD_RARE_TAG\"}" | jq -r .id)
    edit_tag "$tid" add
    echo "$kind $id: tag $OLD_RARE_TAG added (Any quality)"
  elif [ "$on_any" = false ] && [ "$has" = true ]; then
    edit_tag "$tid" remove
    echo "$kind $id: tag $OLD_RARE_TAG removed (not on Any quality)"
  fi
}

if [ "${LP_RECOMPUTE:-}" = 1 ]; then
  recompute
else
  swiss_profile || [ "${LP_TAGS_ONLY:-}" = 1 ] || set_profile
fi
old_rare_tag
set_tags
