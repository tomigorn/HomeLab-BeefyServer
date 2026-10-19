#!/bin/sh
# Same file on fastpi, beefy and tower: Docker/antivirus/README.md in the fastpi repo.

STATE=${STATE:-/state}
TEXTFILE=${TEXTFILE:-/textfile}
DB_DIR=${DB_DIR:-/var/lib/clamav}
HOST=${HOST:-$(hostname)}
SCAN_HOST=${SCAN_HOST:-1}
MAXBYTES=2146435072
LIMITS="--max-filesize=2047M --max-scansize=2047M"
TAB=$(printf '\t')

usage() {
	cat >&2 <<EOF
usage: scan.sh new        files under \$SCAN_ROOTS changed since the last good run, infected ones to \$QUARANTINE
       scan.sh all        everything under \$SCAN_ROOTS and /host (SCAN_HOST=0 skips it), report only
       scan.sh <path>...  quarantined when under \$SCAN_ROOTS, else report only
       scan.sh restore <file in quarantine>  move back to its original path and never flag it again
exit:  0 clean, 1 infected, 2 errors
EOF
	exit 2
}

[ $# -gt 0 ] || usage
if [ -z "$SCAN_ROOTS" ] || [ -z "$QUARANTINE" ]; then
	echo "scan.sh: SCAN_ROOTS and QUARANTINE must be set" >&2
	exit 2
fi
mkdir -p "$STATE" "$TEXTFILE"
ALLOW=$STATE/allow.txt

restore() {
	case $1 in */*) q=$1 ;; *) q=$QUARANTINE/$1 ;; esac
	name=${q##*/}
	base=$(printf '%s\n' "$name" | sed 's/\.[0-9][0-9][0-9]$//')
	[ -f "$q" ] || { echo "restore: $q not found" >&2; exit 2; }
	n=$(awk -F '\t' -v b="$base" 'NF == 3 { f = $2; sub(/.*\//, "", f); if (f == b) n = NR } END { print n + 0 }' "$QUARANTINE/index.txt" 2>/dev/null)
	[ "$n" -gt 0 ] || { echo "restore: no index.txt line for $name" >&2; exit 2; }
	orig=$(awk -F '\t' -v n="$n" 'NR == n { print $2 }' "$QUARANTINE/index.txt")
	[ -e "$orig" ] && { echo "restore: $orig exists, not overwriting" >&2; exit 2; }
	mkdir -p "$(dirname "$orig")" && mv "$q" "$orig" || exit 2
	awk -v n="$n" -v d="$(date -Iseconds)" 'NR == n { $0 = $0 "\trestored " d } { print }' "$QUARANTINE/index.txt" > "$QUARANTINE/.index.tmp" &&
		mv "$QUARANTINE/.index.tmp" "$QUARANTINE/index.txt"
	grep -qxF "$orig" "$ALLOW" 2>/dev/null || printf '%s\n' "$orig" >> "$ALLOW"
	echo "restored $orig, added to $ALLOW"
	exit 0
}

[ "$1" = restore ] && { [ $# -eq 2 ] || usage; restore "$2"; }

case $1 in
	new) MODE=new ;;
	all) MODE=all ;;
	*) MODE=path ;;
esac
LOG=$STATE/last-$MODE.log
START=$(date +%s)
RC=0
: > "$LOG"

clam() {
	if [ -s "$ALLOW" ]; then
		while IFS= read -r a; do
			set -- "$@" "--exclude=^$(printf '%s\n' "$a" | sed 's/[]\\.*^$+?(){}|[]/\\&/g')\$"
		done < "$ALLOW"
	fi
	clamscan "$@" >> "$LOG" 2>&1
	r=$?
	[ "$r" -gt "$RC" ] && RC=$r
}

under_roots() {
	for root in $SCAN_ROOTS; do
		case $1 in "$root" | "$root"/*) return 0 ;; esac
	done
	return 1
}

list_find() {
	if [ -e "$STATE/last-run" ]; then since=$(stat -c %Y "$STATE/last-run"); else since=$(( $(date +%s) - 86400 )); fi
	find $SCAN_ROOTS -xdev \
		\( -path "$QUARANTINE" -o -path '*/quarantine' -o -path '*/.snapshots' -o -path '*/incomplete' \) -prune -o \
		-type f ! -name '*.part' ! -name '*.!qB' ! -name '*-wal' ! -name '*-shm' ! -name '*-journal' \
		-exec stat -c '%Z %Y %n' {} + > "$STATE/find.out" 2> "$STATE/find.err"
	VANISHED=$((VANISHED + $(grep -c 'No such file or directory$' "$STATE/find.err")))
	awk -v s="$since" '$1 > s || $2 > s { sub(/^[0-9]+ [0-9]+ /, ""); print }' "$STATE/find.out"
	grep -v 'No such file or directory$' "$STATE/find.err" | sed 's/^/ERROR: /' >> "$LOG"
	! grep -qv 'No such file or directory$' "$STATE/find.err"
}

count_large() {
	find "$@" -xdev -path "$QUARANTINE" -prune -o -type f -size +"$MAXBYTES"c -print 2>/dev/null | wc -l | tr -d ' '
}

list_btrfs() {
	: > "$STATE/last-gen.next"
	: > "$STATE/find-new.list"
	for root in $SCAN_ROOTS; do
		gen=$(awk -v r="$root" '$2 == r { print $1 }' "$STATE/last-gen" 2>/dev/null)
		btrfs subvolume find-new "$root" "${gen:-0}" > "$STATE/find-new.out" 2>> "$LOG" || return 1
		awk -v r="$root" '/^transid marker was/ { print $4, r }' "$STATE/find-new.out" >> "$STATE/last-gen.next"
		awk -v r="$root" '/^inode / { sub(/^.* flags [^ ]+ /, ""); print r "/" $0 }' "$STATE/find-new.out" >> "$STATE/find-new.list"
	done
	sort -u "$STATE/find-new.list" | while IFS= read -r f; do
		case $f in
			"$QUARANTINE"/* | */quarantine/* | */.snapshots/* | */incomplete/* | *.part | *.!qB | *-wal | *-shm | *-journal) ;;
			*) [ -f "$f" ] && printf '%s\n' "$f" ;;
		esac
	done
	return 0
}

run_new() {
	list=$STATE/new.list
	touch "$STATE/last-run.next"
	sleep 1  # busybox find -newer compares whole seconds
	if [ "$SCAN_BTRFS" = 1 ]; then list_btrfs > "$list.all" || RC=2; else list_find > "$list.all" || RC=2; fi
	if [ -s "$ALLOW" ]; then
		awk 'NR == FNR { allow[$0] = 1; next } !($0 in allow)' "$ALLOW" "$list.all" > "$list"
	else
		mv "$list.all" "$list"
	fi
	while IFS= read -r f; do
		if [ -e "$f" ]; then printf '%s\n' "$f"; else VANISHED=$((VANISHED + 1)); fi
	done < "$list" > "$list.x"
	mv "$list.x" "$list"
	LISTED=$(wc -l < "$list" | tr -d ' ')
	SKIPPED=$(while IFS= read -r f; do stat -c %s "$f"; done < "$list" 2>/dev/null | awk -v m="$MAXBYTES" '$1 > m { n++ } END { print n + 0 }')
	[ "$LISTED" -gt 0 ] && clam --file-list="$list" -i --move="$QUARANTINE" $LIMITS
}

run_all() {
	targets=$SCAN_ROOTS
	[ "$SCAN_HOST" = 1 ] && targets="$targets /host"
	SKIPPED=$(count_large $targets)
	clam -r -i --cross-fs=no --exclude-dir="^$QUARANTINE" $LIMITS $targets
}

run_paths() {
	SKIPPED=$(count_large "$@")
	: > "$STATE/path-move.list"
	: > "$STATE/path-report.list"
	for p in "$@"; do
		if [ ! -e "$p" ]; then
			echo "ERROR: $p: No such file or directory" >> "$LOG"
			RC=2
		elif under_roots "$p"; then
			printf '%s\n' "$p" >> "$STATE/path-move.list"
		else
			printf '%s\n' "$p" >> "$STATE/path-report.list"
		fi
	done
	[ -s "$STATE/path-move.list" ] && clam --file-list="$STATE/path-move.list" -r -i --exclude-dir="^$QUARANTINE" --move="$QUARANTINE" $LIMITS
	[ -s "$STATE/path-report.list" ] && clam --file-list="$STATE/path-report.list" -r -i --cross-fs=no $LIMITS
}

LISTED=
SKIPPED=0
VANISHED=0
if [ ! -d "$QUARANTINE" ]; then
	echo "ERROR: $QUARANTINE missing (disk not mounted?)" >> "$LOG"
	RC=2
	LISTED=0
else
	case $MODE in
		new) run_new ;;
		all) run_all ;;
		path) run_paths "$@" ;;
	esac
fi
cat "$LOG"

HITS=$STATE/hits.tsv
awk '/ FOUND$/ { sub(/ FOUND$/, ""); i = match($0, /: [^:]*$/); print substr($0, 1, i - 1) "\t" substr($0, i + 2) }' "$LOG" > "$HITS"
INFECTED=$(wc -l < "$HITS" | tr -d ' ')
SCANNED=$(awk -F': ' '/^Scanned files:/ { s += $2 } END { print s + 0 }' "$LOG")
[ -n "$LISTED" ] || LISTED=$SCANNED
set -- $(awk '
	/^ERROR: / { e++; next }
	/: No such file or directory$/ { p = $0; sub(/: No such file or directory$/, "", p); gone[p] = 1; next }
	/^WARNING: .*: Can.t access file$/ { q = $0; sub(/^WARNING: /, "", q); sub(/: Can.t access file$/, "", q); if (q in gone) v++; else e++; next }
	/^WARNING: / { e++ }
	/^Total errors:/ { e += $3 }
	END { print v + 0, e + 0 }' "$LOG")
VANISHED=$((VANISHED + $1))
ERRORS=0
if [ "$RC" -ge 2 ]; then
	ERRORS=$2
	[ "$ERRORS" -gt 0 ] || [ "$1" -gt 0 ] || ERRORS=1
fi

if [ "$INFECTED" -gt 0 ]; then
	cp "$HITS" "$STATE/last-infected.txt"
	now=$(date -Iseconds)
	while IFS="$TAB" read -r path sig; do
		grep -qF "$path: moved to " "$LOG" && printf '%s\t%s\t%s\n' "$now" "$path" "$sig" >> "$QUARANTINE/index.txt"
	done < "$HITS"
fi
TOTAL=$(( $(cat "$STATE/infected-total" 2>/dev/null || echo 0) + INFECTED ))
echo "$TOTAL" > "$STATE/infected-total"

if [ "$MODE" = new ] && [ "$ERRORS" -eq 0 ]; then
	mv "$STATE/last-run.next" "$STATE/last-run"
	[ -e "$STATE/last-gen.next" ] && mv "$STATE/last-gen.next" "$STATE/last-gen"
fi

L="host=\"$HOST\",mode=\"$MODE\""
cat > "$STATE/metrics-$MODE" <<EOF
clamav_last_run_timestamp_seconds{$L} $(date +%s)
clamav_run_duration_seconds{$L} $(( $(date +%s) - START ))
clamav_files_listed{$L} $LISTED
clamav_files_scanned{$L} $SCANNED
clamav_scan_errors{$L} $ERRORS
clamav_files_vanished{$L} $VANISHED
clamav_infected_last_run{$L} $INFECTED
clamav_files_skipped_large{$L} $SKIPPED
EOF

db=$(ls "$DB_DIR"/daily.cld "$DB_DIR"/daily.cvd 2>/dev/null | head -1)
if [ -n "$db" ] && info=$(sigtool --info "$db" 2>/dev/null); then
	built=$(printf '%s\n' "$info" | awk '/^Build time:/ {
		m = index("JanFebMarAprMayJunJulAugSepOctNovDec", $4); printf "%s-%02d-%02d %s", $5, (m + 2) / 3, $3, $6 }')
	built=$(TZ=UTC date -d "$built" +%s 2>/dev/null)
	DB_VERSION=$(printf '%s\n' "$info" | awk '/^Version:/ { print $2 }')
	[ -n "$built" ] && SIG_AGE=$(( $(date +%s) - built ))
fi

{
	cat "$STATE"/metrics-*
	echo "clamav_infected_total{host=\"$HOST\"} $TOTAL"
	[ -n "$SIG_AGE" ] && echo "clamav_signature_age_seconds{host=\"$HOST\"} $SIG_AGE"
	[ -n "$DB_VERSION" ] && echo "clamav_db_version{host=\"$HOST\"} $DB_VERSION"
} | sort | awk '
	{ name = $1; sub(/\{.*/, "", name) }
	name != prev { type = (name == "clamav_infected_total") ? "counter" : "gauge"; print "# TYPE " name " " type; prev = name }
	{ print }' > "$TEXTFILE/.clamav.prom.tmp"
mv "$TEXTFILE/.clamav.prom.tmp" "$TEXTFILE/clamav.prom"

echo "scan.sh $MODE: listed=$LISTED scanned=$SCANNED infected=$INFECTED errors=$ERRORS vanished=$VANISHED"
[ "$ERRORS" -gt 0 ] && exit 2
[ "$INFECTED" -gt 0 ] && exit 1
exit 0
