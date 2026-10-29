#!/usr/bin/env bash
# API keys never on curl's argv (visible in /proc while curl runs): scripts pass them as a header file or an @file form field (`-H @<(printf …)`, `--data-urlencode apikey@<(printf …)`).
# Usage: tests/test_no_keys_on_argv.sh
HERE=$(cd "$(dirname "$0")" && pwd)
hits=$(grep -nE '"X-Api-Key: \$|MediaBrowser Token=\\"\$|apikey=\$|api_key=\$' "$HERE"/../scripts/*.sh | grep -vE '^[^:]+:[0-9]+:\s*#')
if [ -z "$hits" ]; then echo "  ok   no API key on a curl command line"; echo "1 passed, 0 failed"; exit 0; fi
echo "  FAIL API key on a curl command line:"; sed 's/^/       /' <<<"$hits"; echo "0 passed, 1 failed"; exit 1
