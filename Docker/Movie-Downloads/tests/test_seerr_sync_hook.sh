#!/usr/bin/env bash
# Runs scripts/seerr-sync-hook.sh (Radarr/Sonarr Connect script on import/upgrade/rename) against a temp queue file.
# Usage: tests/test_seerr_sync_hook.sh   (needs bash)
HERE=$(cd "$(dirname "$0")" && pwd)
HOOK="$HERE/../scripts/seerr-sync-hook.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
Q="$T/seerr-sync.queue"

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }
run() { rm -f "$Q"; env -i PATH="$PATH" SSH_QUEUE="$Q" "$@" bash "$HOOK" >/dev/null 2>&1; echo $?; }
queue() { [ -e "$Q" ] && cat "$Q" || echo "(none)"; }

assert_eq "radarr import exits 0" "0" "$(run radarr_eventtype=Download radarr_moviefile_path=/data/media/movies/A/A.mkv)"
assert_eq "radarr import queues the path" "/data/media/movies/A/A.mkv" "$(queue)"
run radarr_eventtype=Rename radarr_moviefile_paths='/data/media/movies/A/A2.mkv|/data/media/movies/B/B.mkv' >/dev/null
assert_eq "radarr rename queues every path" $'/data/media/movies/A/A2.mkv\n/data/media/movies/B/B.mkv' "$(queue)"
run sonarr_eventtype=Download sonarr_episodefile_paths='/data/media/tv/S/1.mkv|/data/media/tv/S/2.mkv' sonarr_episodefile_path=/data/media/tv/S/1.mkv >/dev/null
assert_eq "sonarr multi-episode file queues one line per path" $'/data/media/tv/S/1.mkv\n/data/media/tv/S/2.mkv' "$(queue)"
run sonarr_eventtype=Download sonarr_episodefile_path=/data/media/tv/S/3.mkv >/dev/null
assert_eq "sonarr single path fallback" "/data/media/tv/S/3.mkv" "$(queue)"
env -i PATH="$PATH" SSH_QUEUE="$Q" radarr_eventtype=Download radarr_moviefile_path=/data/x.mkv bash "$HOOK"
assert_eq "second import appends" $'/data/media/tv/S/3.mkv\n/data/x.mkv' "$(queue)"

assert_eq "test event exits 0" "0" "$(run radarr_eventtype=Test)"
assert_eq "test event queues nothing" "(none)" "$(queue)"
assert_eq "grab event exits 0" "0" "$(run radarr_eventtype=Grab radarr_moviefile_path=/data/x.mkv)"
assert_eq "grab queues nothing" "(none)" "$(queue)"
assert_eq "no path queues nothing" "0" "$(run radarr_eventtype=Download)"
assert_eq "no path queue" "(none)" "$(queue)"
assert_eq "not called by an app fails" "1" "$(run FOO=bar)"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
