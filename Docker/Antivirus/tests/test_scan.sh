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
    --exclude-dir=*) excl=${a#*=} ;;
    --exclude=*) printf '%s\n' "${a#*=}" >> "$targets.excl" ;;
    -*) ;;
    *) printf '%s\n' "$a" >> "$targets" ;;
  esac
done
files=$(mktemp)
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
  scanned=$((scanned+1))
  if grep -q EICAR-STANDARD-ANTIVIRUS-TEST-FILE "$f"; then
    infected=$((infected+1)); echo "$f: Eicar-Test-Signature FOUND"
    [ -n "$move" ] && mv "$f" "$move/" && echo "$f: moved to '$move/${f##*/}'"
  fi
done < "$files"
[ -n "$FAKE_FAIL" ] && { echo "ERROR: fake failure"; errors=$((errors+1)); }
printf '\n----------- SCAN SUMMARY -----------\nKnown viruses: 1\nEngine version: 1.4.6\nScanned directories: 0\nScanned files: %s\nInfected files: %s\nData scanned: 0.00 MB\n' "$scanned" "$infected"
rm -f "$targets" "$targets.excl" "$files"
[ "$errors" -gt 0 ] && exit 2
[ "$infected" -gt 0 ] && exit 1
exit 0
EOF
cat > "$T/bin/sigtool" <<'EOF'
#!/bin/sh
printf 'File: %s\nBuild time: 19 Oct 2026 06:26 +0000\nVersion: 28158\n' "$2"
EOF
chmod +x "$T/bin/clamscan" "$T/bin/sigtool"

export PATH="$T/bin:$PATH" DB_DIR="$T/db" HOST=testhost FAKE_LOG="$T/clamscan.log"
export SCAN_ROOTS="$T/red $T/black" QUARANTINE="$T/red/antivirus-quarantine" STATE="$T/state" TEXTFILE="$T/textfile"

reset() {
  rm -rf "$T/red" "$T/black" "$T/state" "$T/textfile" "$T/outside" "$FAKE_LOG"
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
for p in antivirus-quarantine/x.exe some/quarantine/x.exe .snapshots/s1/x.exe qbittorrent/incomplete/x.exe movies/a.part movies/b.!qB; do fresh "$T/red/$p"; done
fresh "$T/red/movies/keep.mkv"
run new > /dev/null
assert_eq "only keep.mkv listed" "$T/red/movies/keep.mkv" "$(cat "$STATE/new.list")"

echo "== new: error keeps the marker"
reset; marker; fresh "$T/red/movies/new.mkv"
assert_eq "error exits 2" 2 "$(FAKE_FAIL=1 run new)"
kept "marker kept"
assert_eq "scan_errors 1" 1 "$(metric clamav_scan_errors new)"
assert_eq "retry picks the file again" 0 "$(run new)"
assert_grep "file listed on retry" new.mkv "$STATE/new.list"

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

echo "== prom file"
reset; marker; fresh "$T/red/movies/new.mkv"
run new > /dev/null
P=$TEXTFILE/clamav.prom
assert_grep "timestamp with host+mode" '^clamav_last_run_timestamp_seconds{host="testhost",mode="new"} [0-9]' "$P"
assert_grep "duration" '^clamav_run_duration_seconds{host="testhost",mode="new"} [0-9]' "$P"
assert_grep "signature age" '^clamav_signature_age_seconds{host="testhost"} [0-9]' "$P"
assert_grep "db version" '^clamav_db_version{host="testhost"} 28158$' "$P"
assert_grep "infected_total" '^clamav_infected_total{host="testhost"} 0$' "$P"
assert_eq "one TYPE line per metric" 10 "$(grep -c '^# TYPE clamav_' "$P")"
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
assert_eq "missing path exits 2" 2 "$(run "$T/nope")"

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
