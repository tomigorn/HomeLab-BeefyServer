# Codec minimum size rule (README "Codec minimum size"), the one copy for scripts/grab-guard.sh and scripts/codec-size-report.py.
# Input {quality, title, codec?, size, runtime, episodes?}: quality = Radarr/Sonarr quality name, codec = mediaInfo videoCodec (else read from the title), size in bytes, runtime in minutes per film or episode, episodes in the release (season pack), default 1.
# Output {codec, min, perMin, checked, ok}: MB = MiB as in the apps' own size check; checked = the quality is in scope; ok false = below the codec minimum.
def num: if type == "string" then (tonumber? // 0) else (. // 0) end;
def tok($re): test("(^|[^a-z0-9])(" + $re + ")([^a-z0-9]|$)"; "i");
def codec_of:
  if tok("x264|h[ .]?264|avc") then "x264"
  elif tok("x265|h[ .]?265|hevc") then "x265"
  elif tok("av1") then "av1"
  else "unknown" end;
["WEBDL-720p", "WEBRip-720p", "Bluray-720p", "WEBDL-1080p", "WEBRip-1080p", "Bluray-1080p"] as $scope
| (if (.codec // "") != "" then .codec else .title // "" end | codec_of) as $codec
| (if $codec == "x265" or $codec == "av1" then 15 else 25 end) as $min
| (.size | num) as $size
| ((.runtime | num) * ([.episodes | num, 1] | max)) as $minutes
| (if $size > 0 and $minutes > 0 then $size / 1048576 / $minutes else null end) as $raw
| (.quality as $q | any($scope[]; . == $q)) as $checked
| {codec: $codec, min: $min, perMin: (if $raw then ($raw * 10 | round) / 10 else null end), checked: $checked, ok: (($checked | not) or $raw == null or $raw >= $min)}
