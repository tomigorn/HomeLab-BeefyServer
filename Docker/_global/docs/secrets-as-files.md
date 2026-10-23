# Secrets as files, not environment

Why: every reader of the Docker API (socket proxies, cup-agent, Portainer agent, autoheal) sees each container's env via `docker inspect`. A file mounted into one container is readable only by that container's uid.

Same recipe as fastpi (HomeLab-FastPi `Docker/_global/docs/secrets-as-files.md`); here always `sudo docker …`.

## Rules

| Rule | How |
|---|---|
| Where | `<project>/secrets/<name>` (gitignored by each project's `.gitignore`: `secrets/`, `*.secret`) |
| Owner / mode | uid (and gid) of the container that reads it, `0400`; shared by two uids → `owner:group` + `0440`; root containers → `root:root 0400`. Uids: `container-hardening.md` |
| Content | the bare value, no trailing newline (`printf %s`) |
| Mount | compose `secrets:` with `file:` (lands at `/run/secrets/<name>`, keeps host owner/mode) or a `:ro` bind |
| Read | the app's own file option (table below) |
| `.env` | non-secret config only; `.env` and `.env.example` name the secret files in a comment |
| Check | `sudo docker inspect <c> --format '{{range .Config.Env}}{{println .}}{{end}}' \| cut -d= -f1` shows no secret name with a value |

## Create a file from an existing `.env` value (never echoes it)

```bash
cd ~/Projects/Docker/<project> && mkdir -p secrets
v=$(grep -E '^VAR=' .env | tail -1 | cut -d= -f2-); umask 077
printf %s "$v" | sudo tee secrets/name >/dev/null; unset v
sudo chown <uid>:<gid> secrets/name && sudo chmod 0400 secrets/name
sed -i -E 's|^VAR=.*|# -> secrets/name|' .env   # same comment in .env.example
```

Compose:

```yaml
services:
  app:
    environment:
      - VAR_FILE=/run/secrets/name
    secrets:
      - name
secrets:
  name:
    file: ./secrets/name
```

## Native file options (verified)

| App | Option | Used in |
|---|---|---|
| postgres image | `POSTGRES_PASSWORD_FILE` | Ryot (`ryot-db`) |
| libpq / psycopg | `passfile=/run/secrets/<name>` in the DSN, file `*:*:*:<user>:<password>`, mode `0400` | Ryot (`ryot-go`) |
| gluetun | `WIREGUARD_PRIVATE_KEY_SECRETFILE` (default `/run/secrets/wireguard_private_key`) | Movie-Downloads |
| Recyclarr | `!secret <key>` in `recyclarr.yml`, values in `/config/secrets.yml` | Movie-Downloads |
| linuxserver images | `FILE__<VAR>=/run/secrets/<name>` | |

## No file option

| Case | How | Used in |
|---|---|---|
| Image has a shell | compose `entrypoint: [sh, -ec, …]` reads the files, exports the variables, then `exec`s the original entrypoint + cmd (overriding `entrypoint` drops the image `CMD`). The value is then only in the process env, not in `docker inspect` | Ryot (`ryot`) |
| No shell | stays in env, noted next to the variable in compose | Portainer-Agent `AGENT_SECRET` |

## Git

- `.githooks/pre-commit` refuses a file whose basename is `secrets`, `secrets.yml`, `.pgpass`, … or has a dot-separated part `secret`, `secrets`, `key`, `env`, … (`x.key`, `.env`); `*.example` passes. Secret files are never staged anyway; `secrets-as-files.md` passes (one part `secrets-as-files`).
- After a move: `gitleaks git --log-opts="origin/main..HEAD"` before a push.
