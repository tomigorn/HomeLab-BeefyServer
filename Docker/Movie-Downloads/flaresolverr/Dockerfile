# FlareSolverr plus every Debian upgrade the upstream image lacks (Chromium, base libraries). Base: the newest upstream release, pinned by digest at build time (scripts/flaresolverr-rebuild.sh, README "FlareSolverr").
ARG FLARESOLVERR_BASE=ghcr.io/flaresolverr/flaresolverr:latest
FROM ${FLARESOLVERR_BASE}
USER root
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
 && o=$(stat -c %u:%g /app/chromedriver) && install -o "${o%:*}" -g "${o#*:}" -m 0755 /usr/bin/chromedriver /app/chromedriver && rm /usr/bin/chromedriver \
 && rm -rf /var/lib/apt/lists/*
USER flaresolverr
