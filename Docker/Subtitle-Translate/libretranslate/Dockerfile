# LibreTranslate on a current Debian/Python base (README "Local image"): upstream's own final stage (python:3.11-slim-bookworm + user + /app), with /app and its venv taken unchanged from the official release image, so code, Python packages and behaviour stay those of the release.
# Pins (A-30): the weekly rebuild (_global/rebuild-local.conf) moves both digests to their tag's current one; a new LibreTranslate release = change both tags here by hand (README "Update").
FROM libretranslate/libretranslate:v1.9.6@sha256:1de2d7056bb8ad607a412f4563d9abe324ff632b43b5be9428bcc8e213aebb32 AS upstream

FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

# The two bases this image was built from, for the post-update check (st.lt-base-current); the weekly rebuild moves these digests together with the FROM lines.
RUN printf '%s\n' libretranslate/libretranslate:v1.9.6@sha256:1de2d7056bb8ad607a412f4563d9abe324ff632b43b5be9428bcc8e213aebb32 python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39 > /etc/base-images

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
 && rm -rf /var/lib/apt/lists/*

RUN addgroup --system --gid 1032 libretranslate \
 && adduser --system --uid 1032 libretranslate \
 && mkdir -p /home/libretranslate/.local \
 && chown -R libretranslate:libretranslate /home/libretranslate/
ENV HOME=/home/libretranslate

WORKDIR /app
COPY --from=upstream --chown=1032:1032 /app /app
COPY --from=upstream --chown=1032:1032 /app/venv/bin/ltmanage /usr/bin/

# Fixed versions of the flagged Python packages, majors held; waitress is never started (gunicorn serves), it only drops off the scan. pip/setuptools/wheel of the system Python and pip of the venv are not used at runtime.
RUN ./venv/bin/pip install --no-cache-dir --upgrade "certifi>=2026.10.18" "urllib3>=2.8.0,<3" "filelock>=4.2.1,<5" "waitress>=3.0.1,<4" \
 && ./venv/bin/python -m pip uninstall -y pip \
 && python -m pip uninstall -y pip setuptools wheel

USER libretranslate
EXPOSE 5000
ENTRYPOINT ["./scripts/entrypoint.sh"]
