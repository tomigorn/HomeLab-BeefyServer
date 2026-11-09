# Wrong-film guard year rule (README "Wrong-film guard"), the one copy for scripts/grab-guard.sh and scripts/queue-watchdog.py.
# Input {app, got, year, second, first, last, ended, now, air}: got = year parsed from the release title, year/second = the title's year and Radarr's secondary year, first/last = Sonarr's firstAired/lastAired year, ended = Sonarr status ended, now = the current year, air = air years of the grabbed episodes. Output true = the release year fits.
def off($a; $b): if $a > $b then $a - $b else $b - $a end;
.got as $got
| (if (.ended | not) and .last > 0 and .last < .now then .now else .last end) as $last
| def near($a): $a > 0 and off($a; $got) <= 2;
  $got == 0 or .year == 0 or near(.year) or near(.second)
  or (.app == "sonarr" and ((.first > 0 and $last > 0 and $got >= .first and $got <= $last) or near(.first) or any(.air[]; off(.; $got) <= 1)))
