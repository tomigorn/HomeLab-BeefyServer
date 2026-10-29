#!/bin/sh
# Runs clamav/scan.sh against a temp tree with a fake clamscan/sigtool on PATH.
# Usage: tests/test_scan.sh   (POSIX sh; also runs inside the clamav-local image)
HERE=$(cd "$(dirname "$0")" && pwd)
SCAN="$HERE/../clamav/scan.sh"
SIG=EICAR-STANDARD-ANTIVIRUS-TEST-FILE
TAB=$(printf '\t')
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq()       { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }
assert_grep()     { if grep -q -- "$2" "$3" 2>/dev/null; then ok "$1"; else bad "$1" "no [$2] in $3"; fi; }
assert_line()     { if grep -qxF -- "$2" "$3" 2>/dev/null; then ok "$1"; else bad "$1" "no line [$2] in $3"; fi; }
esc() { printf '%s\n' "$1" | sed 's/[]\\.*^$+?(){}|[]/\\&/g'; }
assert_no_grep()  { if grep -q -- "$2" "$3" 2>/dev/null; then bad "$1" "unexpected [$2] in $3"; else ok "$1"; fi; }
metric() { awk -v n="$1" -v m="$2" '$1 ~ "^"n"[{ ]" && (m == "" || index($1, "mode=\""m"\"")) {print $2}' "$TEXTFILE/clamav.prom"; }

mkdir -p "$T/bin" "$T/db"
: > "$T/db/daily.cld"
cat > "$T/bin/clamscan" <<'EOF'
#!/bin/sh
echo "$*" >> "$FAKE_LOG"
targets=$(mktemp); move=; excl=; scanned=0; infected=0; errors=0
for a; do
  case $a in
    --file-list=*) cat "${a#*=}" >> "$targets" ;;
    --move=*) move=${a#*=} ;;
    --exclude-dir=*) excl=${a#*=}; printf '%s\n' "$excl" >> "$targets.excl" ;;
    --exclude=*) printf '%s\n' "${a#*=}" >> "$targets.excl" ;;
    -*) ;;
    *) printf '%s\n' "$a" >> "$targets" ;;
  esac
done
files=$(mktemp); denied=0
[ -n "$FAKE_VANISH" ] && rm -f "$FAKE_VANISH"
while IFS= read -r t; do
  if [ -d "$t" ]; then find "$t" -type f >> "$files"
  elif [ -f "$t" ]; then printf '%s\n' "$t" >> "$files"
  else echo "$t: No such file or directory"; echo "WARNING: $t: Can't access file"; errors=$((errors+1)); fi
done < "$targets"
[ -n "$excl" ] && { grep -Ev "$excl" "$files" > "$files.x"; mv "$files.x" "$files"; }
if [ -s "$targets.excl" ]; then
  while IFS= read -r re; do grep -Ev "$re" "$files" > "$files.x"; mv "$files.x" "$files"; done < "$targets.excl"
fi
while IFS= read -r f; do
  [ -n "$(find "$f" -size +2000k)" ] && continue
  [ -s "$f" ] || continue
  [ "$f" = "$FAKE_DENY" ] && { denied=$((denied+1)); errors=$((errors+1)); continue; }
  scanned=$((scanned+1))
  if grep -q EICAR-STANDARD-ANTIVIRUS-TEST-FILE "$f"; then
    infected=$((infected+1)); echo "$f: Eicar-Test-Signature FOUND"
    [ -n "$move" ] && mv "$f" "$move/" && echo "$f: moved to '$move/${f##*/}'"
  fi
done < "$files"
[ -n "$FAKE_FAIL" ] && { echo "ERROR: fake failure"; errors=$((errors+1)); }
printf '\n----------- SCAN SUMMARY -----------\nKnown viruses: 1\nEngine version: 1.4.6\nScanned directories: 0\nScanned files: %s\nInfected files: %s\nData scanned: 0.00 MB\n' "$scanned" "$infected"
[ "$denied" -gt 0 ] && echo "Total errors: $denied"
rm -f "$targets" "$targets.excl" "$files"
[ "$errors" -gt 0 ] && exit 2
[ "$infected" -gt 0 ] && exit 1
exit 0
EOF
cat > "$T/bin/sigtool" <<'EOF'
#!/bin/sh
printf 'File: %s\nBuild time: 19 Oct 2026 06:26 +0000\nVersion: 28158\n' "$2"
EOF
cat > "$T/bin/clamd" <<'EOF'
#!/bin/sh
for a; do case $a in --config-file=*) conf=${a#*=} ;; esac; done
[ -n "$FAKE_CLAMD_FAIL" ] && exit 1
cp "$conf" "$FAKE_CLAMD_CONF"
sock=$(awk '$1 == "LocalSocket" { print $2 }' "$conf")
trap 'rm -f "$sock"; exit 0' TERM INT
: > "$sock"
while :; do sleep 0.1; done
EOF
cat > "$T/bin/clamdscan" <<'EOF'
#!/bin/sh
ARGS_SEEN="$*"; conf=; ping=0; targets=; multi=0; move=
while [ $# -gt 0 ]; do
  case $1 in
    -c) conf=$2; shift ;;
    --config-file=*) conf=${1#*=} ;;
    --ping) ping=1; shift ;;
    --multiscan) multi=1 ;;
    --move=*) move=${1#*=} ;;
    --file-list=*) targets="$targets
$(cat "${1#*=}")" ;;
    -*) ;;
    *) targets="$targets
$1" ;;
  esac
  shift
done
sock=$(awk '$1 == "LocalSocket" { print $2 }' "$conf")
if [ "$ping" = 1 ]; then [ -e "$sock" ]; exit $?; fi
echo "$conf $multi $ARGS_SEEN" >> "$FAKE_CLAMD_LOG"
[ -n "$FAKE_SCANNED" ] && printf '%s\n' $targets >> "$FAKE_SCANNED"
files=$(mktemp); infected=0; errors=0
for t in $targets; do
  if [ -d "$t" ]; then find "$t" -type f >> "$files"
  elif [ -f "$t" ]; then echo "$t" >> "$files"
  else echo "ERROR: Can't access file $t"; errors=$((errors+1)); fi
done
awk '$1 == "ExcludePath" { print $2 }' "$conf" > "$files.excl"
while IFS= read -r re; do grep -Ev "$re" "$files" > "$files.x"; mv "$files.x" "$files"; done < "$files.excl"
while IFS= read -r f; do
  [ -n "$(find "$f" -size +2000k)" ] && continue
  [ "$f" = "$FAKE_DENY" ] && { echo "$f: Access denied. ERROR"; errors=$((errors+1)); continue; }
  grep -q EICAR-STANDARD-ANTIVIRUS-TEST-FILE "$f" && { infected=$((infected+1)); echo "$f: Eicar-Test-Signature FOUND"
    [ -n "$move" ] && mv "$f" "$move/" && echo "$f: moved to '$move/${f##*/}'"; }
done < "$files"
printf '\n----------- SCAN SUMMARY -----------\nInfected files: %s\n' "$infected"
[ "$errors" -gt 0 ] && echo "Total errors: $errors"
printf 'Time: 0.005 sec (0 m 0 s)\nStart Date: 2026:10:26 01:27:47\nEnd Date:   2026:10:26 01:27:47\n'
rm -f "$files" "$files.excl" "$files.x"
[ -n "$FAKE_CLOCK_SET" ] && echo "$FAKE_CLOCK_SET" > "$SCAN_NOW_FILE"
[ "$errors" -gt 0 ] && exit 2
[ "$infected" -gt 0 ] && exit 1
exit 0
EOF
chmod +x "$T/bin/clamscan" "$T/bin/sigtool" "$T/bin/clamd" "$T/bin/clamdscan"

export PATH="$T/bin:$PATH" DB_DIR="$T/db" HOST=testhost FAKE_LOG="$T/clamscan.log"
export FAKE_CLAMD_LOG="$T/clamdscan.log" FAKE_CLAMD_CONF="$T/clamd-used.conf" CLAMD_CONF="$T/clamd.conf" CLAMD_SOCK="$T/clamd.sock" CLAMD_THREADS=4
export SCAN_ROOTS="$T/red $T/black" QUARANTINE="$T/red/antivirus-quarantine" STATE="$T/state" TEXTFILE="$T/textfile"

reset() {
  rm -rf "$T/red" "$T/black" "$T/state" "$T/textfile" "$T/outside" "$FAKE_LOG" "$FAKE_CLAMD_LOG" "$T/clamd.conf" "$T/clamd.sock"
  mkdir -p "$T/red/movies" "$T/black/books" "$QUARANTINE" "$T/textfile" "$T/outside"
}
old()   { mkdir -p "$(dirname "$1")"; echo clean > "$1"; touch -t 202001010000 "$1"; }
fresh() { mkdir -p "$(dirname "$1")"; echo "${2:-clean}" > "$1"; }
mtime() { stat -c %Y "$1"; }
marker() { sleep 1; mkdir -p "$STATE"; touch "$STATE/last-run"; M0=$(mtime "$STATE/last-run"); sleep 1; }
advanced() { if [ "$(mtime "$STATE/last-run")" -gt "$M0" ] && [ ! -e "$STATE/last-run.next" ]; then ok "$1"; else bad "$1"; fi; }
kept() { assert_eq "$1" "$M0" "$(mtime "$STATE/last-run")"; }
run() { sh "$SCAN" "$@" > "$T/out" 2>&1; echo $?; }

echo "== new: only files newer than the marker"
reset; old "$T/red/movies/old.mkv"; marker
fresh "$T/red/movies/new.mkv"; fresh "$T/black/books/new.epub"; old "$T/red/movies/unpacked.mkv"
assert_eq "clean run exits 0" 0 "$(run new)"
assert_grep "new file in red listed" "$T/red/movies/new.mkv" "$STATE/new.list"
assert_grep "new file in black listed" "$T/black/books/new.epub" "$STATE/new.list"
assert_no_grep "old file not listed" old.mkv "$STATE/new.list"
assert_grep "old mtime, new ctime (unpacked) listed" unpacked.mkv "$STATE/new.list"
assert_eq "files_listed" 3 "$(metric clamav_files_listed new)"
assert_eq "files_scanned" 3 "$(metric clamav_files_scanned new)"
assert_grep "file-list + move + size limits" "--file-list=$STATE/new.list -i --move=$QUARANTINE --max-filesize=2047M --max-scansize=2047M" "$FAKE_LOG"
assert_eq "skipped_large 0" 0 "$(metric clamav_files_skipped_large new)"
advanced "marker advanced"

echo "== new: first run takes the last 24 h"
reset
fresh "$T/red/movies/new.mkv"
assert_eq "first run exits 0" 0 "$(run new)"
assert_eq "recent file listed" "$T/red/movies/new.mkv" "$(cat "$STATE/new.list")"
[ -e "$STATE/last-run" ] && ok "marker created" || bad "marker created"

echo "== new: nothing new skips clamscan"
reset; old "$T/red/movies/old.mkv"; marker
assert_eq "exits 0" 0 "$(run new)"
[ -e "$FAKE_LOG" ] && bad "clamscan not called" || ok "clamscan not called"
assert_eq "files_listed 0" 0 "$(metric clamav_files_listed new)"

echo "== new: excludes"
reset; marker
for p in antivirus-quarantine/x.exe some/quarantine/x.exe .snapshots/s1/x.exe .recyclebin/Show/x.mkv .clamav-queue/job1/0001 qbittorrent/incomplete/x.exe qbittorrent/config/qBittorrent/logs/qbittorrent.log.bak3 movies/a.part movies/b.!qB sonarr/logs.db-wal sonarr/logs.db-shm sonarr/logs.db-journal; do fresh "$T/red/$p"; done
fresh "$T/red/movies/keep.mkv"
run new > /dev/null
assert_eq "only keep.mkv listed" "$T/red/movies/keep.mkv" "$(cat "$STATE/new.list")"

echo "== new: SCAN_EXCLUDE_DIRS prunes an unreadable app dir"
reset; marker; fresh "$T/red/appcfg/db/app.db"; fresh "$T/red/movies/keep.mkv"; chmod 000 "$T/red/appcfg/db"
assert_eq "exits 0" 0 "$(SCAN_EXCLUDE_DIRS="$T/red/appcfg" run new)"
assert_eq "only keep.mkv listed" "$T/red/movies/keep.mkv" "$(cat "$STATE/new.list")"
assert_eq "scan_errors 0" 0 "$(metric clamav_scan_errors new)"
chmod 755 "$T/red/appcfg/db"

echo "== new: error keeps the marker"
reset; marker; fresh "$T/red/movies/new.mkv"
assert_eq "error exits 2" 2 "$(FAKE_FAIL=1 run new)"
kept "marker kept"
assert_eq "scan_errors 1" 1 "$(metric clamav_scan_errors new)"
assert_eq "retry picks the file again" 0 "$(run new)"
assert_grep "file listed on retry" new.mkv "$STATE/new.list"

echo "== new: a file vanishing before the scan is not an error"
reset; marker; fresh "$T/red/movies/a.mkv"; fresh "$T/red/movies/gone.mkv"
assert_eq "exits 0" 0 "$(FAKE_VANISH="$T/red/movies/gone.mkv" run new)"
assert_eq "files_vanished 1" 1 "$(metric clamav_files_vanished new)"
assert_eq "scan_errors 0" 0 "$(metric clamav_scan_errors new)"
assert_eq "files_scanned 1" 1 "$(metric clamav_files_scanned new)"
advanced "marker advanced"

echo "== new: an unreadable file stays an error"
reset; marker; fresh "$T/red/movies/a.mkv"; fresh "$T/red/movies/secret.mkv"
assert_eq "exits 2" 2 "$(FAKE_DENY="$T/red/movies/secret.mkv" run new)"
assert_eq "scan_errors 1" 1 "$(metric clamav_scan_errors new)"
assert_eq "files_vanished 0" 0 "$(metric clamav_files_vanished new)"
kept "marker kept"

echo "== path/all: vanished files are counted, not errors"
reset; fresh "$T/red/movies/a.mkv"; fresh "$T/red/movies/gone.mkv"
assert_eq "path exits 0" 0 "$(FAKE_VANISH="$T/red/movies/gone.mkv" run "$T/red/movies/a.mkv" "$T/red/movies/gone.mkv")"
assert_eq "path vanished 1" 1 "$(metric clamav_files_vanished path)"

echo "== new: infected file goes to quarantine"
reset; marker
fresh "$T/red/movies/evil.exe" "x $SIG x"; fresh "$T/red/movies/fine.mkv"
assert_eq "hit exits 1" 1 "$(run new)"
[ -e "$QUARANTINE/evil.exe" ] && [ ! -e "$T/red/movies/evil.exe" ] && ok "moved to quarantine" || bad "moved to quarantine"
assert_grep "index line" "^[0-9-]*T[0-9:]*[^$TAB]*$TAB$T/red/movies/evil.exe${TAB}Eicar-Test-Signature\$" "$QUARANTINE/index.txt"
assert_eq "last-infected" "$T/red/movies/evil.exe${TAB}Eicar-Test-Signature" "$(cat "$STATE/last-infected.txt")"
assert_eq "infected_last_run 1" 1 "$(metric clamav_infected_last_run new)"
assert_eq "infected_total 1" 1 "$(metric clamav_infected_total)"
advanced "marker advanced after hits"
fresh "$T/black/books/evil2.exe" "$SIG"
assert_eq "second hit exits 1" 1 "$(run new)"
assert_eq "infected_total is cumulative" 2 "$(metric clamav_infected_total)"
assert_eq "index has two lines" 2 "$(wc -l < "$QUARANTINE/index.txt" | tr -d ' ')"
assert_eq "clean run exits 0" 0 "$(run new)"
assert_eq "infected_last_run back to 0" 0 "$(metric clamav_infected_last_run new)"
assert_eq "infected_total kept" 2 "$(metric clamav_infected_total)"
assert_grep "last-infected kept after a clean run" evil2.exe "$STATE/last-infected.txt"

echo "== new: hardlinks are scanned once"
reset; marker
fresh "$T/red/torrents/a.mkv"; mkdir -p "$T/red/movies/A"; ln "$T/red/torrents/a.mkv" "$T/red/movies/A/a.mkv"; fresh "$T/red/movies/b.mkv"
assert_eq "exits 0" 0 "$(run new)"
assert_eq "two of three paths listed (one per inode)" 2 "$(wc -l < "$STATE/new.list" | tr -d ' ')"
assert_eq "files_scanned 2" 2 "$(metric clamav_files_scanned new)"

echo "== new: an infected inode is quarantined under every path"
reset; marker
fresh "$T/red/torrents/evil.mkv" "x $SIG x"; mkdir -p "$T/red/movies/E" "$T/black/books"; ln "$T/red/torrents/evil.mkv" "$T/red/movies/E/evil.mkv"
assert_eq "hit exits 1" 1 "$(run new)"
[ ! -e "$T/red/torrents/evil.mkv" ] && [ ! -e "$T/red/movies/E/evil.mkv" ] && ok "no path left outside quarantine" || bad "no path left outside quarantine" "$(find "$T/red" -name 'evil*')"
assert_eq "both links in quarantine" 2 "$(find "$QUARANTINE" -name 'evil.mkv*' | wc -l | tr -d ' ')"
assert_eq "an index line per path" 2 "$(grep -c "evil.mkv${TAB}Eicar" "$QUARANTINE/index.txt")"
assert_grep "the second link's own path indexed" "$TAB$T/red/movies/E/evil.mkv${TAB}Eicar" "$QUARANTINE/index.txt"
assert_eq "restore of the second link works" 0 "$(run restore "$(find "$QUARANTINE" -name 'evil.mkv.*' | head -1)")"

echo "== new through the running clamd: parallel clamdscan, same reporting"
reset; marker
printf 'LocalSocket %s\n' "$T/run.sock" > "$T/run.conf"; : > "$T/run.sock"
for i in 1 2 3 4 5; do fresh "$T/red/movies/m$i.mkv"; done; fresh "$T/black/books/bad.exe" "$SIG"
rm -f "$T/scanned"
assert_eq "hit exits 1" 1 "$(FAKE_SCANNED="$T/scanned" NEW_CLAMD=1 NEW_PARALLEL=3 CLAMD_RUNNING_CONF="$T/run.conf" run new)"
[ -e "$FAKE_LOG" ] && bad "clamscan not used" "$(cat "$FAKE_LOG")" || ok "clamscan not used"
assert_eq "three clamdscan runs" 3 "$(grep -c -- "-c $T/run.conf -i --move=$QUARANTINE --file-list=" "$FAKE_CLAMD_LOG")"
assert_eq "every listed file scanned once" 6 "$(sort -u "$T/scanned" | grep -c .)"
[ -e "$QUARANTINE/bad.exe" ] && ok "infected moved" || bad "infected moved"
assert_eq "files_scanned 6" 6 "$(metric clamav_files_scanned new)"
assert_eq "infected_last_run 1" 1 "$(metric clamav_infected_last_run new)"
assert_eq "scan_errors 0" 0 "$(metric clamav_scan_errors new)"
advanced "marker advanced"

echo "== new through clamd: files over the limit never reach clamd (it would refuse them as errors)"
reset; marker; printf 'LocalSocket %s\n' "$T/run.sock" > "$T/run.conf"; : > "$T/run.sock"
fresh "$T/red/movies/a.mkv"; dd if=/dev/zero of="$T/red/movies/huge.mkv" bs=1 count=0 seek=2146435073 2>/dev/null; rm -f "$T/scanned"
assert_eq "exits 0" 0 "$(FAKE_SCANNED="$T/scanned" NEW_CLAMD=1 CLAMD_RUNNING_CONF="$T/run.conf" run new)"
assert_no_grep "huge file not sent" huge.mkv "$T/scanned"
assert_eq "skipped_large 1" 1 "$(metric clamav_files_skipped_large new)"
assert_eq "files_scanned 1" 1 "$(metric clamav_files_scanned new)"

echo "== new through clamd: clamd down falls back to clamscan"
reset; marker; fresh "$T/red/movies/a.mkv"; rm -f "$T/run.sock"
assert_eq "exits 0" 0 "$(NEW_CLAMD=1 CLAMD_RUNNING_CONF="$T/run.conf" run new)"
assert_grep "clamscan used" "--file-list=$STATE/new.list" "$FAKE_LOG"
assert_grep "says so" "clamd not reachable" "$T/out"

echo "== prom file"
reset; marker; fresh "$T/red/movies/new.mkv"
run new > /dev/null
P=$TEXTFILE/clamav.prom
assert_grep "timestamp with host+mode" '^clamav_last_run_timestamp_seconds{host="testhost",mode="new"} [0-9]' "$P"
assert_grep "duration" '^clamav_run_duration_seconds{host="testhost",mode="new"} [0-9]' "$P"
assert_grep "signature age" '^clamav_signature_age_seconds{host="testhost"} [0-9]' "$P"
assert_grep "db version" '^clamav_db_version{host="testhost"} 28158$' "$P"
assert_grep "infected_total" '^clamav_infected_total{host="testhost"} 0$' "$P"
assert_eq "one TYPE line per metric" 11 "$(grep -c '^# TYPE clamav_' "$P")"
assert_eq "no temp files left" "clamav.prom" "$(ls -A "$TEXTFILE")"
sig_age=$(metric clamav_signature_age_seconds)
exp=$(( $(date +%s) - 1792391160 ))
[ $((exp - sig_age)) -ge 0 ] && [ $((exp - sig_age)) -le 5 ] && ok "signature age from build time" || bad "signature age from build time" "$sig_age vs $exp"
sh "$SCAN" "$T/red/movies" > /dev/null 2>&1
assert_grep "new mode kept after a path run" 'mode="new"' "$P"
assert_grep "path mode added" 'clamav_files_scanned{host="testhost",mode="path"} 1' "$P"

echo "== files over 2047M are counted as skipped"
reset; marker; fresh "$T/red/movies/new.mkv"; dd if=/dev/zero of="$T/red/movies/film.mkv" bs=1 count=0 seek=2146435073 2>/dev/null
dd if=/dev/zero of="$T/black/books/limit.mkv" bs=1 count=0 seek=2146435072 2>/dev/null
assert_eq "exits 0" 0 "$(run new)"
assert_eq "new: one over the limit" 1 "$(metric clamav_files_skipped_large new)"
run "$T/red/movies" "$T/black/books" > /dev/null
assert_eq "path: one over the limit" 1 "$(metric clamav_files_skipped_large path)"
SCAN_HOST=0 run all > /dev/null
assert_eq "all: one over the limit" 1 "$(metric clamav_files_skipped_large all)"

echo "== all: report only, roots + /host"
reset; fresh "$T/red/movies/evil.exe" "$SIG"; old "$T/black/books/old.epub"
assert_eq "all with a hit exits 1" 1 "$(SCAN_HOST=0 run all)"
[ -e "$T/red/movies/evil.exe" ] && ok "nothing moved" || bad "nothing moved"
assert_no_grep "no --move" "--move" "$FAKE_LOG"
assert_grep "recursive, one fs" "-r -i --cross-fs=no" "$FAKE_LOG"
assert_grep "quarantine excluded" "--exclude-dir=^$QUARANTINE" "$FAKE_LOG"
assert_no_grep "SCAN_HOST=0 skips /host" " /host" "$FAKE_LOG"
assert_eq "scanned both roots (old files too)" 2 "$(metric clamav_files_scanned all)"
assert_eq "infected_last_run all" 1 "$(metric clamav_infected_last_run all)"
rm -f "$FAKE_LOG"; run all > /dev/null
assert_grep "/host scanned by default" "$T/black /host\$" "$FAKE_LOG"
[ -e "$STATE/last-run" ] && bad "all leaves the new marker alone" || ok "all leaves the new marker alone"

echo "== all: SCAN_EXCLUDE_DIRS with clamscan"
reset; fresh "$T/red/movies/a.mkv"; fresh "$T/black/books/skipme/evil.exe" "$SIG"; fresh "$T/black/books/ok.epub"
assert_eq "excluded dir hides the hit" 0 "$(SCAN_HOST=0 SCAN_EXCLUDE_DIRS="$T/black/books/skipme" run all)"
grep -qF -- "--exclude-dir=^$(esc "$T/black/books/skipme")(/|\$)" "$FAKE_LOG" && ok "exclude-dir flag" || bad "exclude-dir flag"
assert_eq "scanned without the excluded dir" 2 "$(metric clamav_files_scanned all)"

echo "== all with clamd: same reporting as clamscan"
reset; fresh "$T/red/movies/evil.exe" "$SIG"; old "$T/black/books/old.epub"; fresh "$T/black/books/skipme/other.exe" "$SIG"
dd if=/dev/zero of="$T/red/movies/film.mkv" bs=1 count=0 seek=2146435073 2>/dev/null
: > "$T/red/movies/empty.nfo"
SCAN_HOST=0 SCAN_EXCLUDE_DIRS="$T/black/books/skipme" run all > /dev/null
cp "$STATE/hits.tsv" "$T/hits.scan"; cp "$TEXTFILE/clamav.prom" "$T/prom.scan"; cp "$STATE/last-infected.txt" "$T/inf.scan"
rm -f "$FAKE_LOG"
assert_eq "hit exits 1" 1 "$(USE_CLAMD=1 SCAN_HOST=0 SCAN_EXCLUDE_DIRS="$T/black/books/skipme" run all)"
[ -e "$FAKE_LOG" ] && bad "clamscan not used" || ok "clamscan not used"
assert_grep "multiscan with its own config" "^$CLAMD_CONF 1 " "$FAKE_CLAMD_LOG"
assert_no_grep "no --move" "--move" "$FAKE_CLAMD_LOG"
assert_eq "hits.tsv identical" "$(cat "$T/hits.scan")" "$(cat "$STATE/hits.tsv")"
assert_eq "last-infected identical" "$(cat "$T/inf.scan")" "$(cat "$STATE/last-infected.txt")"
for m in clamav_files_listed clamav_files_scanned clamav_scan_errors clamav_files_vanished clamav_infected_last_run clamav_files_skipped_large; do
  assert_eq "$m identical" "$(awk -v n=$m '$1 ~ "^"n"{" {print $2}' "$T/prom.scan")" "$(metric $m all)"
done
assert_eq "same set of metric names" "$(sed 's/ [0-9]*$//' "$T/prom.scan" | grep -v '^#' | sort)" "$(sed 's/ [0-9]*$//' "$TEXTFILE/clamav.prom" | grep -v '^#' | sort)"
[ -e "$T/red/movies/evil.exe" ] && ok "nothing moved" || bad "nothing moved"
[ -e "$CLAMD_SOCK" ] && bad "clamd stopped afterwards" || ok "clamd stopped afterwards"

echo "== all with clamd: config"
C=$FAKE_CLAMD_CONF
assert_grep "database dir" "^DatabaseDirectory $DB_DIR\$" "$C"
assert_grep "socket" "^LocalSocket $CLAMD_SOCK\$" "$C"
assert_grep "threads" "^MaxThreads 4\$" "$C"
assert_grep "one filesystem" "^CrossFilesystems no\$" "$C"
assert_grep "size limits" "^MaxFileSize 2047M\$" "$C"
assert_grep "no db reload" "^SelfCheck 0\$" "$C"
assert_line "quarantine excluded" "ExcludePath ^$(esc "$QUARANTINE")(/|\$)" "$C"
assert_line "excluded dir" "ExcludePath ^$(esc "$T/black/books/skipme")(/|\$)" "$C"
assert_no_grep "no network socket" "TCPSocket" "$C"

echo "== all with clamd: allow list, errors, startup failure"
reset; fresh "$T/red/movies/my [odd] file.exe" "$SIG"; mkdir -p "$STATE"; printf '%s\n' "$T/red/movies/my [odd] file.exe" > "$STATE/allow.txt"
assert_eq "allow-listed file is not reported" 0 "$(USE_CLAMD=1 SCAN_HOST=0 run all)"
assert_line "allow entry escaped as ExcludePath" "ExcludePath ^$(esc "$T/red/movies/my [odd] file.exe")\$" "$FAKE_CLAMD_CONF"
reset; fresh "$T/red/movies/a.mkv"; fresh "$T/red/movies/secret.mkv"
assert_eq "unreadable file exits 2" 2 "$(FAKE_DENY="$T/red/movies/secret.mkv" USE_CLAMD=1 SCAN_HOST=0 run all)"
assert_eq "error counted once" 1 "$(metric clamav_scan_errors all)"
reset; fresh "$T/red/movies/a.mkv"
assert_eq "clamd that does not start exits 2" 2 "$(FAKE_CLAMD_FAIL=1 CLAMD_WAIT=3 USE_CLAMD=1 SCAN_HOST=0 run all)"
assert_eq "startup failure is an error" 1 "$(metric clamav_scan_errors all)"
[ -e "$CLAMD_SOCK" ] && bad "no clamd left" || ok "no clamd left"

echo "== resumable host scan: window, chunks, progress"
export TZ=UTC
at() { date -d "$1" +%s; }
R="USE_CLAMD=1 SCAN_HOST=0 SCAN_RESUME=1 CHUNK_FILES=2 SCAN_NOW_FILE=$T/clock"
rrun() { env $R "$@" sh "$SCAN" all > "$T/out" 2>&1; echo $?; }
nscans() { grep -c . "$FAKE_CLAMD_LOG" 2>/dev/null || echo 0; }
reset; for n in 1 2 3 4 5; do fresh "$T/red/movies/f$n.mkv"; done
export FAKE_SCANNED="$T/scanned.log"; rm -f "$FAKE_SCANNED"
at "2026-10-28 12:00" > "$T/clock"
assert_eq "closed window (Wed noon) exits 0" 0 "$(rrun)"
assert_eq "closed window scans nothing" 0 "$(nscans)"
[ -e "$STATE/all.list" ] && bad "no list built when closed" || ok "no list built when closed"
at "2026-10-28 21:59" > "$T/clock"
assert_eq "Wed 21:59 still closed" 0 "$(rrun)"; assert_eq "21:59 scans nothing" 0 "$(nscans)"
at "2026-11-02 06:00" > "$T/clock"
assert_eq "Mon 06:00 closed" 0 "$(rrun)"; assert_eq "Mon 06:00 scans nothing" 0 "$(nscans)"
fresh "$T/red/movies/f2.mkv" "$SIG"
at "2026-10-28 23:00" > "$T/clock"
assert_eq "open night, stopped at 06:00 after chunk 1: exit 1 (hit)" 1 "$(FAKE_CLOCK_SET=$(at "2026-10-29 06:00") rrun)"
assert_eq "one chunk scanned" 1 "$(nscans)"
assert_eq "progress saved" 2 "$(cat "$STATE/all.done")"
[ -e "$STATE/all.list" ] && ok "list kept" || bad "list kept"
[ -e "$STATE/all.finished" ] && bad "pass not finished" || ok "pass not finished"
assert_eq "listed is the whole pass" 5 "$(metric clamav_files_listed all)"
assert_eq "scanned so far" 2 "$(metric clamav_files_scanned all)"
assert_eq "hit already visible" 1 "$(metric clamav_infected_last_run all)"
assert_grep "hit in last-infected" "f2.mkv" "$STATE/last-infected.txt"
at "2026-10-29 12:00" > "$T/clock"
assert_eq "Thu noon closed, progress untouched" 0 "$(rrun)"
assert_eq "still one scan" 1 "$(nscans)"
assert_eq "progress unchanged" 2 "$(cat "$STATE/all.done")"
at "2026-10-29 22:30" > "$T/clock"
assert_eq "next night resumes and finishes: exit 0 (no new hit)" 0 "$(rrun)"
assert_eq "all chunks scanned in total" 3 "$(nscans)"
assert_eq "scanned cumulative" 5 "$(metric clamav_files_scanned all)"
assert_eq "hit of the earlier night kept for the pass" 1 "$(metric clamav_infected_last_run all)"
assert_eq "each file scanned once" 5 "$(sort "$FAKE_SCANNED" | uniq -c | awk '$1 == 1' | wc -l | tr -d ' ')"
[ -e "$STATE/all.finished" ] && ok "pass finished" || bad "pass finished"
[ -e "$STATE/all.list" ] && bad "list removed" || ok "list removed"
at "2026-10-30 23:00" > "$T/clock"
assert_eq "finished 1 day ago: nothing to do" 0 "$(rrun)"
assert_eq "no new scan" 3 "$(nscans)"
at "2026-11-05 23:00" > "$T/clock"
fresh "$T/red/movies/f2.mkv"
assert_eq "week later: new pass, clean" 0 "$(rrun)"
assert_eq "second pass scanned everything again" 6 "$(nscans)"
assert_eq "infected reset by the new pass" 0 "$(metric clamav_infected_last_run all)"
at "2026-11-08 12:00" > "$T/clock"
rm -f "$STATE/all.finished"
assert_eq "Sunday daytime is open" 0 "$(rrun)"
assert_eq "Sunday scans" 9 "$(nscans)"
at "2026-11-02 05:30" > "$T/clock"; rm -f "$STATE/all.finished"
assert_eq "Mon 05:30 open (Sunday night)" 0 "$(rrun)"
assert_eq "Mon 05:30 scans" 12 "$(nscans)"
echo "== resumable host scan: excludes, errors, startup failure"
reset; fresh "$T/red/movies/a.mkv"; fresh "$T/black/books/skipme/b.mkv"; fresh "$T/black/books/c.epub"
: > "$T/red/movies/empty.nfo"; at "2026-10-28 23:00" > "$T/clock"; rm -f "$FAKE_SCANNED"
assert_eq "exclude run clean" 0 "$(SCAN_EXCLUDE_DIRS="$T/black/books/skipme" rrun)"
assert_eq "empty file not listed, exclude honoured" 2 "$(metric clamav_files_listed all)"
assert_no_grep "excluded file never scanned" skipme "$FAKE_SCANNED"
reset; fresh "$T/red/movies/a.mkv"; fresh "$T/red/movies/b.mkv"; fresh "$T/red/movies/x.mkv"; mkdir -p "$STATE"; printf '%s\n' "$T/red/movies/x.mkv" > "$STATE/allow.txt"
at "2026-10-28 23:00" > "$T/clock"; rm -f "$FAKE_SCANNED"
assert_eq "allow list run" 0 "$(rrun)"
assert_eq "allow-listed file not in the pass" 2 "$(metric clamav_files_listed all)"
reset; fresh "$T/red/movies/a.mkv"; fresh "$T/red/movies/secret.mkv"; at "2026-10-28 23:00" > "$T/clock"
assert_eq "unreadable file exits 2" 2 "$(FAKE_DENY="$T/red/movies/secret.mkv" rrun)"
assert_eq "error counted" 1 "$(metric clamav_scan_errors all)"
reset; fresh "$T/red/movies/a.mkv"; at "2026-10-28 23:00" > "$T/clock"
assert_eq "clamd start failure exits 2" 2 "$(FAKE_CLAMD_FAIL=1 CLAMD_WAIT=3 rrun)"
[ -e "$STATE/all.done" ] && [ "$(cat "$STATE/all.done")" != 0 ] && bad "no progress on failure" || ok "no progress on failure"
unset FAKE_SCANNED TZ

echo "== <path>: move under roots, report elsewhere"
reset
fresh "$T/red/movies/evil.exe" "$SIG"; fresh "$T/outside/my file.exe" "$SIG"; fresh "$T/black/books/ok.epub"
assert_eq "hits exit 1" 1 "$(run "$T/red/movies" "$T/outside/my file.exe" "$T/black/books/ok.epub")"
[ -e "$QUARANTINE/evil.exe" ] && ok "under roots: moved" || bad "under roots: moved"
[ -e "$T/outside/my file.exe" ] && ok "outside roots: left in place" || bad "outside roots: left in place"
assert_grep "outside hit reported" "$T/outside/my file.exe${TAB}Eicar-Test-Signature" "$STATE/last-infected.txt"
assert_eq "index only lists the moved file" 1 "$(wc -l < "$QUARANTINE/index.txt" | tr -d ' ')"
assert_eq "infected_last_run path" 2 "$(metric clamav_infected_last_run path)"
assert_eq "scanned all three" 3 "$(metric clamav_files_scanned path)"
assert_eq "clean path exits 0" 0 "$(run "$T/black/books")"
assert_eq "missing path given by hand exits 2" 2 "$(run "$T/nope")"
assert_eq "it is an error, not vanished" "1 0" "$(metric clamav_scan_errors path) $(metric clamav_files_vanished path)"

echo "== missing quarantine (disk not mounted) is an error"
reset; marker; fresh "$T/red/movies/new.mkv"; rmdir "$QUARANTINE"
assert_eq "exits 2" 2 "$(run new)"
[ -e "$QUARANTINE" ] && bad "quarantine not created" || ok "quarantine not created"
kept "marker kept"

echo "== restore a false positive"
reset; marker
fresh "$T/red/movies/my [odd] file.exe" "$SIG"
assert_eq "hit exits 1" 1 "$(run new)"
assert_eq "restore exits 0" 0 "$(run restore "my [odd] file.exe")"
[ -e "$T/red/movies/my [odd] file.exe" ] && [ ! -e "$QUARANTINE/my [odd] file.exe" ] && ok "moved back" || bad "moved back"
assert_grep "index line marked restored" "$TAB$T/red/movies/my \[odd\] file.exe${TAB}Eicar-Test-Signature${TAB}restored " "$QUARANTINE/index.txt"
assert_eq "allow list entry" "$T/red/movies/my [odd] file.exe" "$(cat "$STATE/allow.txt")"
assert_eq "new skips it" 0 "$(run new)"
assert_no_grep "not listed by new" "odd" "$STATE/new.list"
[ -e "$T/red/movies/my [odd] file.exe" ] && ok "still in place after new" || bad "still in place after new"
assert_eq "path mode skips it" 0 "$(run "$T/red/movies")"
[ -e "$T/red/movies/my [odd] file.exe" ] && ok "still in place after path" || bad "still in place after path"
assert_eq "path mode on the file itself skips it" 0 "$(run "$T/red/movies/my [odd] file.exe")"
assert_eq "all skips it" 0 "$(SCAN_HOST=0 run all)"
fresh "$T/red/movies/my [odd] file.exe.bak" "$SIG"
assert_eq "exact match only: a similar name is still caught" 1 "$(run "$T/red/movies")"
assert_eq "restore unknown file exits 2" 2 "$(run restore nope.exe)"
fresh "$T/black/books/dup.exe" "$SIG"; run new > /dev/null; fresh "$T/black/books/dup.exe"
assert_eq "restore over an existing file exits 2" 2 "$(run restore "$QUARANTINE/dup.exe")"
[ -e "$QUARANTINE/dup.exe" ] && ok "quarantined copy kept" || bad "quarantined copy kept"
assert_eq "allow list unchanged" 1 "$(wc -l < "$STATE/allow.txt" | tr -d ' ')"

echo "== usage"
assert_eq "no args exits 2" 2 "$(run)"
assert_eq "SCAN_ROOTS unset exits 2" 2 "$(SCAN_ROOTS= run new)"

echo; echo "PASS $PASS  FAIL $FAIL"
[ "$FAIL" -eq 0 ]
