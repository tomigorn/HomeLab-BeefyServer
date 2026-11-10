# Subtitle-Translate customizations (Docker/CUSTOMIZATIONS.md "Subtitle-Translate"). Sourced by post-update-check.sh.
ST_DIR="$DIR/../../Subtitle-Translate"
st_lingarr() { curl -s -m 60 -H "X-Api-Key: $(cat "$ST_DIR/secrets/lingarr.apikey" 2>/dev/null)" -H 'Content-Type: application/json' "http://127.0.0.1:9876/api$1" "${@:2}"; }
if want libretranslate lingarr; then
  check st.healthy '^healthy healthy$' "$(docker inspect -f '{{.State.Health.Status}}' subtitle-libretranslate subtitle-lingarr 2>&1 | xargs)"
fi
if want libretranslate; then
  check st.libretranslate-translates '^Guten Morgen' "$(docker exec subtitle-libretranslate ./venv/bin/python -c "import json,urllib.request as u; print(json.load(u.urlopen(u.Request('http://127.0.0.1:5000/translate', json.dumps({'q':'Good morning','source':'en','target':'de'}).encode(), {'Content-Type':'application/json'}), timeout=60))['translatedText'])" 2>&1)"
  check st.lt-local-build '^libretranslate-local:v1\.9\.6 1\.9\.6$' "$(docker inspect -f '{{.Config.Image}}' subtitle-libretranslate 2>&1) $(docker exec subtitle-libretranslate ./venv/bin/python -c "import importlib.metadata as m; print(m.version('libretranslate'))" 2>&1)"
  check st.lt-base-current '^same$' "$(a=$(docker exec subtitle-libretranslate cat /etc/base-images 2>&1 | xargs); b=$(awk '$1 == "FROM" {print $2}' "$ST_DIR/libretranslate/Dockerfile" | xargs); [[ $a == "$b" ]] && echo same || echo "image: $a")"
  check st.lt-functional '^OK$' "$(python3 "$ST_DIR/tests/test_libretranslate.py" 2>&1 | tail -1)"
  check st.no-egress '^internal=true$' "$(docker network inspect -f 'internal={{.Internal}}' subtitle-translate-internal 2>&1)"
fi
if want lingarr; then
  check st.lingarr-auth '^401 200$' "$(curl -s -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:9876/api/translate/languages) $(st_lingarr /translate/languages -o /dev/null -w '%{http_code}')"
  check st.lingarr-content '^0,1$' "$(st_lingarr /translate/content -X POST -d "{\"arrMediaId\":0,\"title\":\"post-update check $(date +%s)\",\"sourceLanguage\":\"en\",\"targetLanguage\":\"de\",\"mediaType\":\"Movie\",\"lines\":[{\"position\":0,\"line\":\"Thank you.\"},{\"position\":1,\"line\":\"See you tomorrow.\"}]}" | jq -r '[.[] | select(.line | length > 0) | .position] | join(",")' 2>&1)"
  check st.timer '^active$' "$(systemctl is-active subtitle-translate.timer 2>&1)"
fi
