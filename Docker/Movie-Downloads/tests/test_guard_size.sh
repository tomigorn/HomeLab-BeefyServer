#!/usr/bin/env bash
# Runs scripts/guard-size.jq (codec minimum per minute of runtime) on sample inputs.
# Usage: tests/test_guard_size.sh   (needs bash, jq)
HERE=$(cd "$(dirname "$0")" && pwd)
RULE="$HERE/../scripts/guard-size.jq"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }
MIB=1048576
# rule <json input>: prints "codec min perMin ok"
rule() { jq -r -f "$RULE" <<<"$1" 2>&1 | jq -r '"\(.codec) \(.min) \(.perMin) \(.ok)"' 2>&1; }
codec() { jq -r -f "$RULE" <<<"{\"quality\": \"WEBDL-1080p\", \"title\": \"$1\", \"size\": 0, \"runtime\": 0}" | jq -r '"\(.codec) \(.min)"'; }

echo "guard-size codec from the release title"
assert_eq "x264" "x264 25" "$(codec Film.2010.1080p.BluRay.x264-GRP)"
assert_eq "H.264 with a dot" "x264 25" "$(codec Film.2010.1080p.WEB-DL.DDP5.1.H.264-GRP)"
assert_eq "H 264 with a space (Sonarr titles)" "x264 25" "$(codec 'Show S01 1080p AMZN WEB-DL DDP5 1 H 264-FLUX')"
assert_eq "h264 lower case" "x264 25" "$(codec film.2010.1080p.web.h264-grp)"
assert_eq "AVC" "x264 25" "$(codec 'Film 2010 1080p BluRay AVC DTS-HD MA 5.1-GRP')"
assert_eq "x265" "x265 15" "$(codec 'Frieren S01 1080p BluRay x265 10bit-GRP')"
assert_eq "H.265" "x265 15" "$(codec Film.2010.1080p.WEB-DL.DDP5.1.H.265-GRP)"
assert_eq "H 265 with a space" "x265 15" "$(codec 'Jury Duty S01 1080p AMZN WEB-DL DDP5 1 H 265-FLUX')"
assert_eq "HEVC" "x265 15" "$(codec '[SubsPlease] Show - 01 (1080p HEVC)')"
assert_eq "AV1" "av1 15" "$(codec 'Show.S01.1080p.WEB-DL.AV1.Opus-GRP')"
assert_eq "no codec in the name: unknown, the stricter 25" "unknown 25" "$(codec Film.2010.1080p.BluRay-GRP)"
assert_eq "x2640 is no codec token" "unknown 25" "$(codec Film.x2640.1080p-GRP)"

echo "guard-size codec from mediaInfo (dry run)"
for c in x264 h264 AVC; do assert_eq "mediaInfo $c" "x264 25" "$(jq -r -f "$RULE" <<<"{\"quality\": \"Bluray-1080p\", \"title\": \"x\", \"codec\": \"$c\", \"size\": 0, \"runtime\": 0}" | jq -r '"\(.codec) \(.min)"')"; done
for c in x265 h265 HEVC hevc; do assert_eq "mediaInfo $c" "x265 15" "$(jq -r -f "$RULE" <<<"{\"quality\": \"Bluray-1080p\", \"title\": \"x\", \"codec\": \"$c\", \"size\": 0, \"runtime\": 0}" | jq -r '"\(.codec) \(.min)"')"; done
assert_eq "mediaInfo AV1" "av1 15" "$(jq -r -f "$RULE" <<<'{"quality": "Bluray-1080p", "title": "x", "codec": "AV1", "size": 0, "runtime": 0}' | jq -r '"\(.codec) \(.min)"')"
assert_eq "mediaInfo VC1: unknown 25" "unknown 25" "$(jq -r -f "$RULE" <<<'{"quality": "Bluray-1080p", "title": "x", "codec": "VC1", "size": 0, "runtime": 0}' | jq -r '"\(.codec) \(.min)"')"
assert_eq "empty mediaInfo codec falls back to the title" "x265 15" "$(jq -r -f "$RULE" <<<'{"quality": "Bluray-1080p", "title": "A.x265-G", "codec": "", "size": 0, "runtime": 0}' | jq -r '"\(.codec) \(.min)"')"

echo "guard-size per-minute math"
assert_eq "film: x264 5.5 GB / 108 min = 48.6 MB/min, kept" "x264 25 48.6 true" \
  "$(rule "{\"quality\": \"Bluray-1080p\", \"title\": \"Fair.Game.2010.1080p.BluRay.x264-G\", \"size\": $((5248 * MIB)), \"runtime\": 108}")"
assert_eq "film: x264 2 GB / 100 min = 20.5 MB/min, below 25" "x264 25 20.5 false" \
  "$(rule "{\"quality\": \"WEBDL-1080p\", \"title\": \"Film.2020.1080p.WEB-DL.x264-G\", \"size\": $((2050 * MIB)), \"runtime\": 100}")"
assert_eq "film: x265 2 GB / 100 min = 20.5 MB/min, kept" "x265 15 20.5 true" \
  "$(rule "{\"quality\": \"WEBDL-1080p\", \"title\": \"Film.2020.1080p.WEB-DL.x265-G\", \"size\": $((2050 * MIB)), \"runtime\": 100}")"
assert_eq "exactly at the minimum is kept" "x265 15 15 true" \
  "$(rule "{\"quality\": \"Bluray-1080p\", \"title\": \"F.x265-G\", \"size\": $((1500 * MIB)), \"runtime\": 100}")"
assert_eq "episode: x265 400 MB / 24 min = 16.7, kept" "x265 15 16.7 true" \
  "$(rule "{\"quality\": \"Bluray-1080p\", \"title\": \"Show.S01E01.1080p.BluRay.x265-G\", \"size\": $((400 * MIB)), \"runtime\": 24, \"episodes\": 1}")"
assert_eq "episode: x264 500 MB / 24 min = 20.8, below 25" "x264 25 20.8 false" \
  "$(rule "{\"quality\": \"Bluray-1080p\", \"title\": \"Show.S01E01.1080p.BluRay.x264-G\", \"size\": $((500 * MIB)), \"runtime\": 24, \"episodes\": 1}")"
assert_eq "season pack: 5.4 GB / 12 eps / 24 min = 19.2 (x265), kept" "x265 15 19.2 true" \
  "$(rule "{\"quality\": \"Bluray-1080p\", \"title\": \"Show.S01.1080p.BluRay.x265-G\", \"size\": $((5530 * MIB)), \"runtime\": 24, \"episodes\": 12}")"
assert_eq "season pack: 3 GB / 12 eps / 24 min = 10.7 (x265), below 15" "x265 15 10.7 false" \
  "$(rule "{\"quality\": \"Bluray-1080p\", \"title\": \"Show.S01.1080p.BluRay.x265-G\", \"size\": $((3072 * MIB)), \"runtime\": 24, \"episodes\": 12}")"
assert_eq "episodes missing counts as one" "x265 15 10 false" \
  "$(rule "{\"quality\": \"WEBDL-720p\", \"title\": \"S.x265-G\", \"size\": $((240 * MIB)), \"runtime\": 24}")"
assert_eq "episodes 0 counts as one" "x265 15 10 false" \
  "$(rule "{\"quality\": \"WEBDL-720p\", \"title\": \"S.x265-G\", \"size\": $((240 * MIB)), \"runtime\": 24, \"episodes\": 0}")"

echo "guard-size scope"
for q in WEBDL-720p WEBRip-720p Bluray-720p WEBDL-1080p WEBRip-1080p Bluray-1080p; do
  assert_eq "$q is checked" "false" "$(rule "{\"quality\": \"$q\", \"title\": \"F.x264-G\", \"size\": $((100 * MIB)), \"runtime\": 100}" | cut -d' ' -f4)"
done
for q in Remux-1080p "Bluray-1080p Remux" Bluray-2160p WEBDL-2160p Remux-2160p HDTV-1080p HDTV-720p DVD SDTV Unknown Bluray-480p; do
  assert_eq "$q is not checked" "true" "$(rule "{\"quality\": \"$q\", \"title\": \"F.x264-G\", \"size\": $((100 * MIB)), \"runtime\": 100}" | cut -d' ' -f4)"
done
assert_eq "runtime 0 (unknown): kept, like the apps' own size check" "true" "$(rule "{\"quality\": \"Bluray-1080p\", \"title\": \"F.x264-G\", \"size\": $((100 * MIB)), \"runtime\": 0}" | cut -d' ' -f4)"
assert_eq "size 0 (unknown): kept" "true" "$(rule '{"quality": "Bluray-1080p", "title": "F.x264-G", "size": 0, "runtime": 100}' | cut -d' ' -f4)"
assert_eq "sizes as strings (Connect env) work" "x264 25 20.5 false" \
  "$(rule "{\"quality\": \"WEBDL-1080p\", \"title\": \"F.x264-G\", \"size\": \"$((2050 * MIB))\", \"runtime\": \"100\", \"episodes\": \"1\"}")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
