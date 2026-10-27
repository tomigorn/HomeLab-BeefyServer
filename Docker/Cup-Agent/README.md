# Cup-Agent — beefy's containers in fastpi's Cup dashboard

Agent-mode Cup. It serves **only an API**; the web UI lives on fastpi at
`https://cup.holy-grail.ch`, which polls this and merges beefy's containers into the
same view.

## Why an agent rather than a remote socket

Pointing fastpi's Cup at beefy's Docker socket would mean exposing the Docker API
across the network, and **the Docker API is root on the host**. The agent reads
beefy's socket locally and publishes only update metadata — image names and whether
a newer tag exists — which is all fastpi needs.

Even locally it does not touch the real socket: `socket-proxy-ro` serves a read-only
endpoint set with `POST: 0`, and `socket-proxy-unix` bridges that back to a unix
socket because Cup cannot speak TCP. Mounting `/var/run/docker.sock:ro` would have
protected the socket *file*, not the API behind it.

## Security note — the firewall is the ONLY control

**Cup has no authentication of any kind.** Checked against `cup.schema.json`
(2026-10-09): the config accepts `agent`, `servers`, `socket`, `images`,
`registries`, `refresh_interval`, `ignore_update_type`, `theme`, `version` — there is
no token, password or allowlist option. So anything that can reach port `8000` gets a
full inventory of every image and container on beefy, including versions, which is a
ready-made list of what to try exploits against.

Port `8000` is reachable only from fastpi: the `DOCKER-USER` rule is an allowlist since 2026-10-24 (`../_global/docs/container-hardening.md`, Firewall), so nothing has to be added per port. Check: `sudo iptables -S DOCKER-USER`.

Why not drop the port publication entirely and keep it internal, as was done
elsewhere: fastpi is a *different host*, so it can only reach this over the LAN.
There is no loopback-only or Docker-network path available, which is exactly why the
six arr ports are handled the same way.

## Deploy

```bash
docker compose up -d
curl -s localhost:8000/api/v3/json | head -c 200     # should return JSON
```

## The fastpi side

`Docker/Cup/cup.json` carries:

```json
"servers": { "beefy": "http://192.168.1.102:8000" }
```

Restart fastpi's Cup after changing it. beefy's containers then appear tagged with
the server name.

## Upgrading

Keep `CUP_IMAGE_TAG` in step with fastpi's Cup — the agent API is versioned with the
server.

## Local rollback images

Tags like `rollback-4.0.20-20261021` are not versions: Cup logs "no remote version tags" and the agent restarts in a loop. List each such image under `images.exclude` in `cup.json` (and remove it there once the image is deleted).

## Locally built images

`jellyfin-local` and `clamav-local` have no registry copy, so they sit in `images.exclude` (no "Unauthorized" errors) and their bases are in `images.extra` (`lscr.io/linuxserver/jellyfin:12.2ubu2604-ls55`). An `extra` entry must be a versioned tag or already on the host: `alpine:latest` is refused and crash-loops the agent, so clamav's base is covered by the Trivy scan on fastpi only. Rebuild routine: the `Rebuild` line in each image's README. After editing `cup.json`: `sudo docker restart cup-agent`.
