#!/usr/bin/env bash
# The parts of the language policy (README "Languages") that Recyclarr cannot
# express, applied through the apps' APIs. Idempotent: re-run after a rebuild or
# after adding a language to LANGS.
#   ~/Projects/Docker/Movie-Downloads/scripts/language-policy.sh
#
# Radarr + Sonarr: custom format "Language: Original + English" (8000 in every
#   profile, 0 in the "- English original" ones), one `lang-xx` tag per language and
#   an auto-tagging rule that sets it from the title's original language.
#   English-original titles on the main 4K profile and on Any quality move to their
#   "- English original" twins, others move back; titles added later get the same
#   move (plus old/niche -> Any quality) on add from the Connect script
#   language-policy-hook.sh, which this script registers.
# Bazarr: one subtitle profile per language ("English + German + <Language>",
#   bound to the `lang-xx` tag), tag-based profile selection, OpenSubtitles.com
#   without AI/machine translations.
set -euo pipefail

H=${H:-127.0.0.1}
RK=$(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/radarr/config.xml)
SK=$(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/sonarr/config.xml)
BK=$(grep -oP '(?<=apikey: ).*' /srv/appdata/bazarr/config/config.yaml | head -1)

# Bazarr code2 -> Radarr/Sonarr language name. English stays on the default
# "English + German" profile and needs no tag.
LANGS="es:Spanish fr:French it:Italian pt:Portuguese ja:Japanese ko:Korean zh:Chinese da:Danish sv:Swedish nb:Norwegian nl:Dutch ru:Russian"
CF_NAME="Language: Original + English"
CF_SCORE=8000
ENG_SUFFIX=" - English original"
ANY="Any quality - old & rare (upgrades to 4K)"
HOOK_NAME="Language policy"
HOOK_PATH=/scripts/language-policy-hook.sh

api() { # api <radarr|sonarr> <path> [curl args...]
  local app=$1 path=$2; shift 2
  local key port; case $app in radarr) key=$RK; port=7878;; sonarr) key=$SK; port=8989;; esac
  curl -sf -H "X-Api-Key: $key" -H 'Content-Type: application/json' "http://$H:$port/api/v3$path" "$@"
}
py() { python3 -c "$@"; }

for app in radarr sonarr; do
  echo "== $app"
  # English audio in addition to the original one, for titles whose original
  # language is not English: English present, the original present, and some
  # language other than the original present (so an English-original release
  # with only English audio does not match).
  CF_ID=$(api $app /customformat | py 'import json,sys; r=[c["id"] for c in json.load(sys.stdin) if c["name"]==sys.argv[1]]; print(r[0] if r else "")' "$CF_NAME")
  CF=$(py 'import json,sys
spec=lambda name,value,exc,neg: {"name":name,"implementation":"LanguageSpecification","negate":neg,"required":True,"fields":[{"name":"value","value":value},{"name":"exceptLanguage","value":exc}]}
print(json.dumps({"name":sys.argv[1],"includeCustomFormatWhenRenaming":False,"specifications":[spec("English",1,False,False),spec("Original Language",-2,False,False),spec("Not Only The Original Language",-2,True,False)]}))' "$CF_NAME")
  if [ -n "$CF_ID" ]; then
    api $app "/customformat/$CF_ID" -X PUT -d "$(echo "$CF" | py 'import json,sys; d=json.load(sys.stdin); d["id"]=int(sys.argv[1]); print(json.dumps(d))' "$CF_ID")" >/dev/null
  else
    CF_ID=$(api $app /customformat -X POST -d "$CF" | py 'import json,sys; print(json.load(sys.stdin)["id"])')
  fi
  echo "   custom format '$CF_NAME' id $CF_ID"

  # Score it in every profile Recyclarr manages (the stock ones are named zz ...);
  # 0 in the English-original ones, where it would fire on any extra dub.
  for PID in $(api $app /qualityprofile | py 'import json,sys; print(" ".join(str(p["id"]) for p in json.load(sys.stdin) if not p["name"].startswith("zz ")))'); do
    api $app "/qualityprofile/$PID" | py 'import json,sys
p=json.load(sys.stdin); cid=int(sys.argv[1]); score=0 if p["name"].endswith(sys.argv[3]) else int(sys.argv[2])
for f in p["formatItems"]:
    if f["format"]==cid: f["score"]=score
print(json.dumps(p))' "$CF_ID" "$CF_SCORE" "$ENG_SUFFIX" | api $app "/qualityprofile/$PID" -X PUT -d @- | py 'import json,sys; p=json.load(sys.stdin); print("   profile", p["name"], "->", [f["score"] for f in p["formatItems"] if f["format"]==int(sys.argv[1])][0])' "$CF_ID"
  done

  # English originals on the main 4K profile and on Any quality -> their English-original twins, and back.
  case $app in radarr) MAIN="4K HDR - UHD Bluray + WEB (1080p fallback)"; kind=movie; ids=movieIds;; sonarr) MAIN="4K HDR - WEB-2160p (1080p fallback)"; kind=series; ids=seriesIds;; esac
  for BASE in "$MAIN" "$ANY"; do
    read -r MID EID < <(api $app /qualityprofile | py 'import json,sys; n={p["name"]:p["id"] for p in json.load(sys.stdin)}; print(n[sys.argv[1]], n[sys.argv[1]+sys.argv[2]])' "$BASE" "$ENG_SUFFIX")
    read -r TO_ENG TO_MAIN < <(api $app "/$kind" | py 'import json,sys
items=json.load(sys.stdin); m,e=int(sys.argv[1]),int(sys.argv[2])
eng=lambda t: (t.get("originalLanguage") or {}).get("name")=="English"
print(",".join(str(t["id"]) for t in items if t["qualityProfileId"]==m and eng(t)) or "-", ",".join(str(t["id"]) for t in items if t["qualityProfileId"]==e and not eng(t)) or "-")' "$MID" "$EID")
    [ "$TO_ENG" = - ] || api $app "/$kind/editor" -X PUT -d "{\"$ids\":[$TO_ENG],\"qualityProfileId\":$EID}" >/dev/null
    [ "$TO_MAIN" = - ] || api $app "/$kind/editor" -X PUT -d "{\"$ids\":[$TO_MAIN],\"qualityProfileId\":$MID}" >/dev/null
    echo "   English original -> profile $EID: ${TO_ENG/-/none}; back to $MID: ${TO_MAIN/-/none}"
  done

  # The same move for every title added from now on, before its first search:
  # Connect "Custom Script" on add, running scripts/language-policy-hook.sh.
  case $app in radarr) ON=onMovieAdded;; sonarr) ON=onSeriesAdd;; esac
  NID=$(api $app /notification | py 'import json,sys; r=[n["id"] for n in json.load(sys.stdin) if n["name"]==sys.argv[1]]; print(r[0] if r else "")' "$HOOK_NAME")
  NOTE=$(api $app /notification/schema | py 'import json,sys
n=[s for s in json.load(sys.stdin) if s["implementation"]=="CustomScript"][0]
n.update({k:False for k in n if k.startswith("on") or k=="includeHealthWarnings"})
n.update({"name":sys.argv[1],sys.argv[2]:True,"tags":[]})
for f in n["fields"]:
    f["value"]={"path":sys.argv[3],"arguments":""}.get(f["name"])
if sys.argv[4]: n["id"]=int(sys.argv[4])
print(json.dumps(n))' "$HOOK_NAME" "$ON" "$HOOK_PATH" "$NID")
  if [ -n "$NID" ]; then api $app "/notification/$NID" -X PUT -d "$NOTE" >/dev/null; else api $app /notification -X POST -d "$NOTE" >/dev/null; fi
  echo "   connect: $(api $app /notification | py 'import json,sys; [print(n["name"], n["fields"][0]["value"], sys.argv[1]+"="+str(n[sys.argv[1]])) for n in json.load(sys.stdin) if n["name"]==sys.argv[2]]' "$ON" "$HOOK_NAME")"

  # One tag per language, set automatically from the original language. Radarr
  # and Sonarr apply auto tags on add and on every refresh.
  for pair in $LANGS; do
    code=${pair%%:*}; name=${pair#*:}; tag="lang-$code"
    LID=$(api $app /language | py 'import json,sys; print([l["id"] for l in json.load(sys.stdin) if l["name"]==sys.argv[1]][0])' "$name")
    TID=$(api $app /tag | py 'import json,sys; r=[t["id"] for t in json.load(sys.stdin) if t["label"]==sys.argv[1]]; print(r[0] if r else "")' "$tag")
    [ -n "$TID" ] || TID=$(api $app /tag -X POST -d "{\"label\":\"$tag\"}" | py 'import json,sys; print(json.load(sys.stdin)["id"])')
    RULE=$(py 'import json,sys
print(json.dumps({"name":sys.argv[1],"removeTagsAutomatically":True,"tags":[int(sys.argv[2])],"specifications":[{"name":sys.argv[3],"implementation":"OriginalLanguageSpecification","negate":False,"required":True,"fields":[{"name":"value","value":int(sys.argv[4])}]}]}))' "$tag" "$TID" "$name" "$LID")
    AID=$(api $app /autotagging | py 'import json,sys; r=[a["id"] for a in json.load(sys.stdin) if a["name"]==sys.argv[1]]; print(r[0] if r else "")' "$tag")
    if [ -n "$AID" ]; then
      api $app "/autotagging/$AID" -X PUT -d "$(echo "$RULE" | py 'import json,sys; d=json.load(sys.stdin); d["id"]=int(sys.argv[1]); print(json.dumps(d))' "$AID")" >/dev/null
    else
      api $app /autotagging -X POST -d "$RULE" >/dev/null
    fi
  done
  echo "   auto tags: $(api $app /autotagging | py 'import json,sys; print(", ".join(a["name"] for a in json.load(sys.stdin)))')"
done

echo "== Bazarr"
B="http://$H:6767/api"
bz() { curl -sf -H "X-API-KEY: $BK" "$@"; }
# Profiles: keep whatever exists, make sure the default "English + German" and one
# tagged profile per language are there. Posting the list replaces it, so the
# existing ones are carried over.
PROFILES=$(bz "$B/system/languages/profiles" | py 'import json,sys
profiles=json.load(sys.stdin)
by_name={p["name"]:p for p in profiles}
def item(i,lang,forced): return {"id":i,"language":lang,"audio_exclude":"False","audio_only_include":"False","hi":"False","forced":forced}
def profile(pid,name,langs,tag):
    items=[item(i+1,l,"False") for i,l in enumerate(langs)]
    items+=[item(len(items)+1,"en","True"),item(len(items)+2,"de","True")]
    return {"profileId":pid,"name":name,"cutoff":None,"items":items,"mustContain":[],"mustNotContain":[],"originalFormat":0,"tag":tag}
nxt=max([p["profileId"] for p in profiles]+[0])+1
if "English + German" not in by_name:
    profiles.append(profile(nxt,"English + German",["en","de"],None)); nxt+=1
for pair in sys.argv[1].split():
    code,name=pair.split(":")
    pname="English + German + "+name
    if pname in by_name:
        by_name[pname]["tag"]="lang-"+code
    else:
        profiles.append(profile(nxt,pname,["en","de",code],"lang-"+code)); nxt+=1
print(json.dumps(profiles))' "$LANGS")
ENABLED=$(py 'import sys; print(" ".join(["en","de"]+[p.split(":")[0] for p in sys.argv[1].split()]))' "$LANGS")
ARGS=()
for code in $ENABLED; do ARGS+=(--data-urlencode "languages-enabled=$code"); done
bz "$B/system/settings" -X POST "${ARGS[@]}" \
  --data-urlencode "languages-profiles=$PROFILES" \
  --data-urlencode "settings-general-serie_tag_enabled=true" \
  --data-urlencode "settings-general-movie_tag_enabled=true" \
  --data-urlencode "settings-opensubtitlescom-include_ai_translated=false" \
  --data-urlencode "settings-opensubtitlescom-include_machine_translated=false"
DEFAULT=$(bz "$B/system/languages/profiles" | py 'import json,sys; print([p["profileId"] for p in json.load(sys.stdin) if p["name"]=="English + German"][0])')
bz "$B/system/settings" -X POST \
  --data-urlencode "settings-general-serie_default_profile=$DEFAULT" \
  --data-urlencode "settings-general-movie_default_profile=$DEFAULT"
bz "$B/system/languages/profiles" | py 'import json,sys; [print("   profile", p["profileId"], p["name"], "tag", p["tag"], [i["language"]+("+f" if i["forced"]=="True" else "") for i in p["items"]]) for p in json.load(sys.stdin)]'
bz "$B/system/settings" | py 'import json,sys; d=json.load(sys.stdin); g=d["general"]; o=d["opensubtitlescom"]; print("   tags:", g["serie_tag_enabled"], g["movie_tag_enabled"], "defaults:", g["serie_default_profile"], g["movie_default_profile"], "| opensubtitles ai:", o["include_ai_translated"], "machine:", o["include_machine_translated"], "hash:", o["use_hash"])'

echo "== apply to existing titles (refresh = auto tags, then Bazarr re-sync)"
api radarr /command -X POST -d '{"name":"RefreshMovie"}' >/dev/null
api sonarr /command -X POST -d '{"name":"RefreshSeries"}' >/dev/null
sleep 20
bz "$B/system/tasks" -X POST --data-urlencode "taskid=update_movies" >/dev/null
bz "$B/system/tasks" -X POST --data-urlencode "taskid=update_series" >/dev/null
echo "done"
