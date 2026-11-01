#!/bin/sh
# Whole-host scan: root with DAC_READ_SEARCH (a non-root uid gets no effective caps in Docker), results handed back to the uids that own the shared state.
# Lowest CPU and idle disk priority: the Pi runs the edge stack next to it.
nice -n 19 ionice -c 3 /scan.sh all
rc=$?
# TEXTFILE is pruned when it lives under STATE (beefy): its owners differ.
find "${STATE:-/state}" -path "${TEXTFILE:-/textfile}" -prune -o -exec chown "${STATE_OWNER:-65039:65039}" {} +
chown "${TEXTFILE_OWNER:-65039:65046}" "${TEXTFILE:-/textfile}/clamav.prom"
exit $rc
