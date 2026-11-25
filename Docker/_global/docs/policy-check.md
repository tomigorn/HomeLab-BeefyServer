# Policy check (beefy)
`policy_check.py`: the container rules (`docs/container-hardening.md`, `CLAUDE.md`) as a check over every compose project under `Docker/`. Same file on fastpi and beefy: keep them identical.

| Rule | Violated when |
|---|---|
| `ipv4_address` | a network without a fixed `ipv4_address` (`network_mode` host/none/container:/service: skipped) |
| `user` | `user:` missing, root/0, uid or gid 1000, gid 100 |
| `user_unique` | the uid is also used in another project folder (sharing inside one project is fine) |
| `cap_drop` | `cap_drop` without `ALL` (`cap_add` is fine) |
| `no_new_privileges` | no `security_opt: no-new-privileges:true` |
| `healthcheck` | no compose healthcheck and none in the image (`docker image inspect`), or `disable: true` |
| `image_pinned` | no tag or `latest`, without digest; `build:` and `*-local` / `:local` images skipped |
| `env_secret` | env name with PASSWORD/SECRET/TOKEN/KEY and a plain value (also from `.env`); `*_FILE`/`_PATH`/`_URL`, paths, URLs, true/false/numbers skipped. Values are never printed |

| | |
|---|---|
| Run | `python3 Docker/_global/scripts/policy_check.py [PROJECT ...]` (all by default); prints `project/service: rule (detail)`, exit 0 clean, 1 violation or stale exception, 2 error |
| Stacks | `docker-compose.yaml` (+ `.override.yaml`); a `docker-compose.<v>.yaml` is its own stack `<Project>:<v>`; all profiles |
| Exception | add to `Docker/_global/policy-exceptions.toml`, reviewed like code; remove it with the fix (a stale entry fails):<br>`[[exception]]` `project = "Traefik"` `service = "traefik"` `rule = "healthcheck"` `reason = "…"` (all four required) |
| Wired | fastpi's `smoke-test.sh` (HomeLab-FastPi): one line per host (gate). `post-update-check.sh <Project>`: `== policy (report only)`, never fails or rolls back an update; skipped for `all` |
| Completion | `source Docker/_global/scripts/completions/policy_check.bash` (bash) |
| Test | `python3 test_policy_check.py` |
