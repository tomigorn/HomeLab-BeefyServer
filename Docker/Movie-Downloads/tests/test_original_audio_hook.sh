#!/usr/bin/env bash
# Runs scripts/original-audio-hook.sh (Radarr/Sonarr Connect script on import/upgrade) against a temp queue file.
# Usage: tests/test_original_audio_hook.sh   (needs bash)
HERE=$(cd "$(dirname "$0")" && pwd)
HOOK="$HERE/../scripts/original-audio-hook.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
export OAH_QUEUE="$T/original-audio.queue"

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }
run() { rm -f "$OAH_QUEUE"; env -i PATH="$PATH" OAH_QUEUE="$OAH_QUEUE" "$@" bash "$HOOK" >/dev/null 2>&1; echo $?; }
queue() { [ -e "$OAH_QUEUE" ] && cat "$OAH_QUEUE" || echo "(none)"; }

assert_eq "radarr import queues id and path" "0" "$(run radarr_eventtype=Download radarr_movie_id=12 radarr_moviefile_path=/data/media/movies/A/A.mkv)"
assert_eq "radarr line" $'12\t/data/media/movies/A/A.mkv' "$(queue)"

run radarr_eventtype=Download radarr_movie_id=12 radarr_moviefile_path=/data/media/movies/A/A.mkv >/dev/null
OAH_QUEUE="$OAH_QUEUE" env -i PATH="$PATH" OAH_QUEUE="$OAH_QUEUE" radarr_eventtype=Download radarr_movie_id=13 radarr_moviefile_path=/data/B.mkv bash "$HOOK"
assert_eq "second import appends" $'12\t/data/media/movies/A/A.mkv\n13\t/data/B.mkv' "$(queue)"

assert_eq "sonarr multi-episode file queues one line per path" "0" "$(run sonarr_eventtype=Download sonarr_series_id=5 sonarr_episodefile_paths='/data/tv/S/1.mkv|/data/tv/S/2.mkv' sonarr_episodefile_path=/data/tv/S/1.mkv)"
assert_eq "sonarr lines" $'5\t/data/tv/S/1.mkv\n5\t/data/tv/S/2.mkv' "$(queue)"

run sonarr_eventtype=Download sonarr_series_id=5 sonarr_episodefile_path=/data/tv/S/3.mkv >/dev/null
assert_eq "sonarr single path fallback" $'5\t/data/tv/S/3.mkv' "$(queue)"

assert_eq "test event exits 0" "0" "$(run radarr_eventtype=Test)"
assert_eq "test event queues nothing" "(none)" "$(queue)"
assert_eq "grab event queues nothing" "0" "$(run radarr_eventtype=Grab radarr_movie_id=1 radarr_moviefile_path=/data/x.mkv)"
assert_eq "grab queue" "(none)" "$(queue)"
assert_eq "no path queues nothing" "0" "$(run radarr_eventtype=Download radarr_movie_id=1)"
assert_eq "no path queue" "(none)" "$(queue)"
assert_eq "not called by an app fails" "1" "$(run FOO=bar)"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
