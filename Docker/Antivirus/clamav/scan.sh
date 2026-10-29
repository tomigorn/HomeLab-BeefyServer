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
USE_CLAMD=${USE_CLAMD:-0}
CLAMD_CONF=${CLAMD_CONF:-/tmp/clamd-hostscan.conf}
CLAMD_SOCK=${CLAMD_SOCK:-/tmp/clamd-hostscan.sock}
CLAMD_THREADS=${CLAMD_THREADS:-$(nproc 2>/dev/null || echo 4)}
CLAMD_WAIT=${CLAMD_WAIT:-600}
NEW_CLAMD=${NEW_CLAMD:-0}
NEW_PARALLEL=${NEW_PARALLEL:-4}
CLAMD_RUNNING_CONF=${CLAMD_RUNNING_CONF:-/etc/clamav/clamd.conf}
SCAN_RESUME=${SCAN_RESUME:-0}
CHUNK_FILES=${CHUNK_FILES:-20000}
PASS_DAYS=${PASS_DAYS:-6}

usage() {
	cat >&2 <<EOF
usage: scan.sh new        files under \$SCAN_ROOTS changed since the last good run, infected ones to \$QUARANTINE
       scan.sh all        everything under \$SCAN_ROOTS and /host (SCAN_HOST=0 skips it), report only
       scan.sh <path>...  quarantined when under \$SCAN_ROOTS, else report only
       scan.sh restore <file in quarantine>  move back to its original path and never flag it again
exit:  0 clean, 1 infected, 2 errors
env:   SCAN_RESUME=1 (with USE_CLAMD=1) scans \`all\` in chunks only 22:00-06:00 and Sundays (local time), resuming where it stopped
       USE_CLAMD=1 runs \`all\` through a private clamd (multiscan, all cores); SCAN_EXCLUDE_DIRS = directories `all` and `new` skip
       NEW_CLAMD=1 runs \`new\` through the container's running clamd (\$CLAMD_RUNNING_CONF), NEW_PARALLEL (4) clamdscan at once; clamd down = clamscan
       \`new\` scans one path per inode (hardlinks); an infected inode is quarantined under every path
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
now() {
	if [ -n "$SCAN_NOW_FILE" ]; then cat "$SCAN_NOW_FILE"; else date +%s; fi
}

# Low-tariff window, local time: 22:00-06:00 every day, all day Sunday.
window_open() {
	case $(date -d "@$1" +%H) in 22 | 23 | 0[0-5]) return 0 ;; esac
	[ "$(date -d "@$1" +%u)" = 7 ]
}

# Seconds until the window closes (a minute early at worst), 0 when closed.
window_left() {
	t=$(now)
	k=0
	while window_open $((t + k * 60)) && [ "$k" -lt 2880 ]; do k=$((k + 1)); done
	[ "$k" -gt 0 ] && k=$((k - 1))
	echo $((k * 60))
}

resume_gate() {
	if ! window_open "$(now)"; then
		echo "scan.sh all: outside the 22:00-06:00 / Sunday window, nothing to do"
		return 1
	fi
	if [ ! -s "$STATE/all.list" ] && [ -s "$STATE/all.finished" ] && [ $(( $(now) - $(cat "$STATE/all.finished") )) -lt $((PASS_DAYS * 86400)) ]; then
		echo "scan.sh all: last pass finished less than $PASS_DAYS days ago, nothing to do"
		return 1
	fi
	return 0
}

[ "$MODE" = all ] && [ "$SCAN_RESUME" = 1 ] && { resume_gate || exit 0; }

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
	# .recyclebin: the arrs' deleted files; .clamav-queue: links the completion queue scans itself
	set -- $SCAN_ROOTS -xdev \( -path "$QUARANTINE" -o -path '*/quarantine' -o -path '*/.snapshots' -o -path '*/.recyclebin' -o -path '*/.clamav-queue' -o -path '*/incomplete' -o -path '*/qBittorrent/logs'
	for d in $SCAN_EXCLUDE_DIRS; do set -- "$@" -o -path "$d"; done
	find "$@" \) -prune -o \
		-type f ! -name '*.part' ! -name '*.!qB' ! -name '*-wal' ! -name '*-shm' ! -name '*-journal' \
		-exec stat -c '%Z %Y %d:%i %n' {} + > "$STATE/find.out" 2> "$STATE/find.err"
	VANISHED=$((VANISHED + $(grep -c 'No such file or directory$' "$STATE/find.err")))
	# one path per inode; the other links go to new.links as "<scanned path> TAB <link>"
	awk -v s="$since" -v links="$STATE/new.links" '$1 > s || $2 > s {
		k = $3; p = $0; sub(/^[0-9]+ [0-9]+ [0-9]+:[0-9]+ /, "", p)
		if (k in seen) { print seen[k] "\t" p > links; next }
		seen[k] = p; print p }' "$STATE/find.out"
	grep -v 'No such file or directory$' "$STATE/find.err" | sed 's/^/ERROR: /' >> "$LOG"
	! grep -qv 'No such file or directory$' "$STATE/find.err"
}

rx() {
	printf '%s\n' "$1" | sed 's/[]\\.*^$+?(){}|[]/\\&/g'
}

count_files() {
	mode=$1; shift
	set -- "$@" -xdev '(' -path "$QUARANTINE"
	for d in $SCAN_EXCLUDE_DIRS; do set -- "$@" -o -path "$d"; done
	set -- "$@" ')' -prune -o -type f
	case $mode in
		large) set -- "$@" -size +"$MAXBYTES"c ;;
		*) set -- "$@" -size +0c ! -size +"$MAXBYTES"c ;;
	esac
	find "$@" -print 2>/dev/null | wc -l | tr -d ' '
}

count_large() {
	count_files large "$@"
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
			"$QUARANTINE"/* | */quarantine/* | */.snapshots/* | */incomplete/* | */qBittorrent/logs/* | *.part | *.!qB | *-wal | *-shm | *-journal) ;;
			*) [ -f "$f" ] && printf '%s\n' "$f" ;;
		esac
	done
	return 0
}

# quarantine_links: every other link of an infected, moved file follows it into quarantine (else the library or seeding copy stays)
quarantine_links() {
	[ -s "$STATE/new.links" ] || return 0
	awk '/ FOUND$/ { sub(/ FOUND$/, ""); i = match($0, /: [^:]*$/); print substr($0, 1, i - 1) "\t" substr($0, i + 2) }' "$LOG" > "$STATE/new.found"
	while IFS="$TAB" read -r hit sig; do
		grep -qF "$hit: moved to " "$LOG" || continue
		awk -F '\t' -v h="$hit" '$1 == h { print $2 }' "$STATE/new.links" | while IFS= read -r l; do
			[ -e "$l" ] || continue
			base=${l##*/}; dest=$QUARANTINE/$base; n=1
			while [ -e "$dest" ]; do dest=$(printf '%s/%s.%03d' "$QUARANTINE" "$base" "$n"); n=$((n + 1)); done
			mv "$l" "$dest" && printf '%s: %s FOUND\n%s: moved to '\''%s'\''\n' "$l" "$sig" "$l" "$dest" >> "$LOG"
		done
	done < "$STATE/new.found"
	rm -f "$STATE/new.found"
}

# clamd_new: the list split over NEW_PARALLEL clamdscan clients of the running clamd (one file per clamd thread)
clamd_new() {
	rm -f "$STATE"/new.part.*
	# over MAXBYTES: clamd streams over TCP and refuses those as errors; clamscan skips them (counted in SKIPPED)
	while IFS= read -r f; do
		[ "$(stat -c %s "$f" 2> /dev/null || echo 0)" -gt "$MAXBYTES" ] || printf '%s\n' "$f"
	done < "$1" > "$STATE/new.fit"
	[ -s "$STATE/new.fit" ] || { rm -f "$STATE/new.fit"; echo "Scanned files: 0" >> "$LOG"; return; }
	awk -v n="$NEW_PARALLEL" -v d="$STATE/new.part" '{ print > (d "." (NR - 1) % n) }' "$STATE/new.fit"
	rm -f "$STATE/new.fit"
	pids=
	for c in "$STATE"/new.part.*; do
		clamdscan -c "$CLAMD_RUNNING_CONF" -i --move="$QUARANTINE" --file-list="$c" > "$c.log" 2>&1 &
		pids="$pids $!"
	done
	for p in $pids; do
		wait "$p"
		r=$?
		[ "$r" -gt "$RC" ] && RC=$r
	done
	cat "$STATE"/new.part.*.log >> "$LOG"
	rm -f "$STATE"/new.part.*
	echo "Scanned files: $((LISTED - SKIPPED))" >> "$LOG"
}

run_new() {
	list=$STATE/new.list
	: > "$STATE/new.links"
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
	[ "$LISTED" -gt 0 ] || return 0
	if [ "$NEW_CLAMD" = 1 ] && clamdscan -c "$CLAMD_RUNNING_CONF" --ping 1 > /dev/null 2>&1; then
		clamd_new "$list"
	else
		[ "$NEW_CLAMD" = 1 ] && echo "scan.sh new: clamd not reachable, scanning with clamscan" >> "$LOG"
		clam --file-list="$list" -i --move="$QUARANTINE" $LIMITS
	fi
	quarantine_links
}

write_clamd_conf() {
	cat <<EOF
LocalSocket $CLAMD_SOCK
Foreground yes
DatabaseDirectory $DB_DIR
TemporaryDirectory /tmp
MaxThreads $CLAMD_THREADS
CrossFilesystems no
MaxFileSize 2047M
MaxScanSize 2047M
SelfCheck 0
ExcludePath ^$(rx "$QUARANTINE")(/|\$)
EOF
	for d in $SCAN_EXCLUDE_DIRS; do echo "ExcludePath ^$(rx "$d")(/|\$)"; done
	if [ -s "$ALLOW" ]; then
		while IFS= read -r a; do echo "ExcludePath ^$(rx "$a")\$"; done < "$ALLOW"
	fi
}

prepare_pass() {
	if [ ! -s "$STATE/all.list" ]; then
		set -- $targets -xdev '(' -path "$QUARANTINE"
		for d in $SCAN_EXCLUDE_DIRS; do set -- "$@" -o -path "$d"; done
		set -- "$@" ')' -prune -o -type f -size +0c ! -size +"$MAXBYTES"c -print
		find "$@" 2> /dev/null | sort > "$STATE/all.list.tmp"
		if [ -s "$ALLOW" ]; then
			awk 'NR == FNR { allow[$0] = 1; next } !($0 in allow)' "$ALLOW" "$STATE/all.list.tmp" > "$STATE/all.list"
			rm -f "$STATE/all.list.tmp"
		else
			mv "$STATE/all.list.tmp" "$STATE/all.list"
		fi
		echo 0 > "$STATE/all.done"
		: > "$STATE/all.pass-hits"
		echo "$SKIPPED" > "$STATE/all.skipped"
		rm -f "$STATE/all.finished"
	fi
	PASS_TOTAL=$(wc -l < "$STATE/all.list" | tr -d ' ')
	LISTED=$PASS_TOTAL
	SKIPPED=$(cat "$STATE/all.skipped" 2> /dev/null || echo 0)
}

resume_scan() {
	done=$(cat "$STATE/all.done")
	while [ "$done" -lt "$PASS_TOTAL" ]; do
		left=$(window_left)
		[ "$left" -ge 60 ] || break
		tail -n +$((done + 1)) "$STATE/all.list" | head -n "$CHUNK_FILES" > "$STATE/all.chunk"
		timeout "$left" clamdscan -c "$CLAMD_CONF" --multiscan -i --file-list="$STATE/all.chunk" >> "$LOG" 2>&1
		r=$?
		case $r in 124 | 137 | 143) break ;; esac
		[ "$r" -gt "$RC" ] && RC=$r
		done=$((done + $(wc -l < "$STATE/all.chunk" | tr -d ' ')))
		echo "$done" > "$STATE/all.done"
	done
	rm -f "$STATE/all.chunk"
	if [ "$done" -ge "$PASS_TOTAL" ]; then
		now > "$STATE/all.finished"
		rm -f "$STATE/all.list"
	fi
	echo "Scanned files: $done" >> "$LOG"
}

clamd_all() {
	write_clamd_conf > "$CLAMD_CONF"
	rm -f "$CLAMD_SOCK"
	counter=
	if [ "$SCAN_RESUME" = 1 ]; then
		prepare_pass
	else
		count_files scanned $targets > "$STATE/scanned.count" &
		counter=$!
	fi
	clamd --config-file="$CLAMD_CONF" > "$STATE/clamd.log" 2>&1 &
	cpid=$!
	i=0
	until clamdscan -c "$CLAMD_CONF" --ping 1 > /dev/null 2>&1; do
		i=$((i + 1))
		if ! kill -0 "$cpid" 2> /dev/null || [ "$i" -gt "$CLAMD_WAIT" ]; then
			echo "ERROR: clamd did not start" >> "$LOG"
			sed 's/^/ERROR: clamd: /' "$STATE/clamd.log" | tail -5 >> "$LOG"
			kill "$cpid" 2> /dev/null
			wait "$cpid" 2> /dev/null
			[ -n "$counter" ] && wait "$counter"
			RC=2
			return
		fi
		sleep 1
	done
	if [ "$SCAN_RESUME" = 1 ]; then
		resume_scan
		kill "$cpid" 2> /dev/null
		wait "$cpid" 2> /dev/null
		return
	fi
	clamdscan -c "$CLAMD_CONF" --multiscan -i $targets >> "$LOG" 2>&1
	r=$?
	[ "$r" -gt "$RC" ] && RC=$r
	kill "$cpid" 2> /dev/null
	wait "$cpid" 2> /dev/null
	wait "$counter"
	echo "Scanned files: $(cat "$STATE/scanned.count")" >> "$LOG"
}

run_all() {
	targets=$SCAN_ROOTS
	[ "$SCAN_HOST" = 1 ] && targets="$targets /host"
	if [ "$USE_CLAMD" = 1 ] && [ "$SCAN_RESUME" = 1 ]; then
		[ -s "$STATE/all.list" ] || SKIPPED=$(count_large $targets)
		clamd_all
		return
	fi
	SKIPPED=$(count_large $targets)
	if [ "$USE_CLAMD" = 1 ]; then
		clamd_all
		return
	fi
	set --
	for d in $SCAN_EXCLUDE_DIRS; do set -- "$@" "--exclude-dir=^$(rx "$d")(/|\$)"; done
	clam -r -i --cross-fs=no --exclude-dir="^$QUARANTINE" "$@" $LIMITS $targets
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
	/^ERROR: Can.t access file / { next }
	/^ERROR: / { e++; next }
	/: [^:]*No such file or directory\. ERROR$/ { v++; cv++; next }
	/: No such file or directory$/ { p = $0; sub(/: No such file or directory$/, "", p); gone[p] = 1; next }
	/^WARNING: .*: Can.t access file$/ { q = $0; sub(/^WARNING: /, "", q); sub(/: Can.t access file$/, "", q); if (q in gone) v++; else e++; next }
	/^WARNING: / { e++ }
	/^Total errors:/ { e += $3 }
	END { e -= cv; print v + 0, (e > 0 ? e : 0) }' "$LOG")
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
INFECTED_M=$INFECTED
if [ "$MODE" = all ] && [ "$SCAN_RESUME" = 1 ] && [ "$USE_CLAMD" = 1 ] && [ -e "$STATE/all.pass-hits" ]; then
	cat "$HITS" >> "$STATE/all.pass-hits"
	sort -u -o "$STATE/all.pass-hits" "$STATE/all.pass-hits"
	INFECTED_M=$(wc -l < "$STATE/all.pass-hits" | tr -d ' ')
	[ "$INFECTED_M" -gt 0 ] && cp "$STATE/all.pass-hits" "$STATE/last-infected.txt"
fi

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
clamav_infected_last_run{$L} $INFECTED_M
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
