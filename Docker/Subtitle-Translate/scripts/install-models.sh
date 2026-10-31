#!/usr/bin/env bash
# install-models.sh [--list]: download the Argos models named in LT_LANGUAGES (.env) into APPDATA_ROOT/libretranslate, then recreate LibreTranslate.
# The running container never downloads (internal network, LT_UPDATE_MODELS=false); this one-off container does, on the edge network.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
IMAGE=$(sed -n 's/^ *image: *\(libretranslate\/libretranslate:.*\)/\1/p' docker-compose.yaml)
sudo docker compose create --quiet-pull >/dev/null 2>&1 || sudo docker compose create >/dev/null
run() { sudo docker run --rm --user 65130:65130 --network subtitle-translate-edge --cap-drop ALL --security-opt no-new-privileges:true \
  -e HOME=/home/libretranslate -v "$APPDATA_ROOT/libretranslate:/home/libretranslate" --entrypoint ./venv/bin/python "$IMAGE" "$@"; }
if [[ ${1:-} == --list ]]; then
  run -c 'import argostranslate.package as p; p.update_package_index(); a=p.get_available_packages(); print(" ".join(sorted({x.from_code for x in a} | {x.to_code for x in a})))'
  exit
fi
run scripts/install_models.py --load_only_lang_codes "$LT_LANGUAGES"
sudo docker compose up -d --force-recreate libretranslate
