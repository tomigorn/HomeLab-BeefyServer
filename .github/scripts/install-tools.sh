#!/usr/bin/env bash
# Installs pinned, checksum-verified linters into DEST (default ~/.local/bin). Usage: .github/scripts/install-tools.sh [-d DEST] shellcheck|hadolint|gitleaks|actionlint ...
set -euo pipefail
DEST="$HOME/.local/bin"
[ "${1:-}" = "-d" ] && { DEST=$2; shift 2; }
case "$(uname -m)" in
    x86_64) ARCH=x86_64 ;;
    aarch64|arm64) ARCH=aarch64 ;;
    *) echo "install-tools: unsupported architecture $(uname -m)" >&2; exit 2 ;;
esac
mkdir -p "$DEST"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fetch() { # url sha256 file
    curl -fsSL --retry 3 -o "$WORK/$3" "$1"
    echo "$2  $WORK/$3" | sha256sum -c - >/dev/null || { echo "install-tools: checksum mismatch for $1" >&2; exit 1; }
}

install_one() {
    case "$1:$ARCH" in
        shellcheck:x86_64) fetch https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.gz b7af85e41cc99489dcc21d66c6d5f3685138f06d34651e6d34b42ec6d54fe6f6 t.tgz ;;
        shellcheck:aarch64) fetch https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.aarch64.tar.gz 68a8133197a50beb8803f8d42f9908d1af1c5540d4bb05fdfca8c1fa47decefc t.tgz ;;
        hadolint:x86_64) fetch https://github.com/hadolint/hadolint/releases/download/v2.15.1/hadolint-linux-x86_64 c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507 hadolint ;;
        hadolint:aarch64) fetch https://github.com/hadolint/hadolint/releases/download/v2.15.1/hadolint-linux-arm64 f6198ef8090f404dbb771abfee086eb8c48ac177f30da7fd3510aca35b344b5d hadolint ;;
        gitleaks:x86_64) fetch https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb t.tgz ;;
        gitleaks:aarch64) fetch https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_arm64.tar.gz e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080 t.tgz ;;
        actionlint:x86_64) fetch https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 t.tgz ;;
        actionlint:aarch64) fetch https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_arm64.tar.gz 325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6 t.tgz ;;
        *) echo "install-tools: unknown tool $1" >&2; exit 2 ;;
    esac
    case "$1" in
        hadolint) install -m 0755 "$WORK/hadolint" "$DEST/hadolint" ;;
        shellcheck) tar -xzf "$WORK/t.tgz" -C "$WORK" && install -m 0755 "$WORK/shellcheck-v0.11.0/shellcheck" "$DEST/shellcheck" ;;
        *) tar -xzf "$WORK/t.tgz" -C "$WORK" "$1" && install -m 0755 "$WORK/$1" "$DEST/$1" ;;
    esac
}

for tool in "$@"; do install_one "$tool"; done
[ -z "${GITHUB_PATH:-}" ] || echo "$DEST" >> "$GITHUB_PATH"
