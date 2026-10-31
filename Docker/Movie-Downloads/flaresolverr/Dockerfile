# FlareSolverr v3.5.2 plus the Debian security updates (Chromium, base libraries) the upstream image lacks; rebuild: README "FlareSolverr".
FROM ghcr.io/flaresolverr/flaresolverr@sha256:c80ae007ce2ccdcd217a12426e4f039ef763ff90738c808d38810c3e59323767
USER root
RUN apt-get update \
 && apt-get -y --only-upgrade install chromium chromium-common chromium-driver libexpat1 perl-base libpcre2-8-0 libbrotli1 libssl3 openssl \
 && o=$(stat -c %u:%g /app/chromedriver) && install -o "${o%:*}" -g "${o#*:}" -m 0755 /usr/bin/chromedriver /app/chromedriver && rm /usr/bin/chromedriver \
 && rm -rf /var/lib/apt/lists/*
USER flaresolverr
