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

Port `8000` must therefore be in the `DOCKER-USER` DROP rule alongside the six arr
ports. Verify:

```bash
sudo iptables -S DOCKER-USER
```

Expected — note `8000` at the end:

```
-A DOCKER-USER ! -s 192.168.1.2/32 -i enp6s0 -p tcp \
   -m multiport --dports 7878,9696,6767,8080,8081,8096,8000 -j DROP
```

If `8000` is missing, add it by *replacing* the rule (`-R`, so there is no moment
where the ports are open) and re-persist:

```bash
sudo iptables -R DOCKER-USER 1 ! -s 192.168.1.2/32 -i enp6s0 -p tcp \
  -m multiport --dports 7878,9696,6767,8080,8081,8096,8000 -j DROP
sudo netfilter-persistent save
```

`-R ... 1` assumes the DROP is the first rule in the chain, which it is — Docker only
ever appends its own `RETURN` after it. Confirm with `-S` before and after.

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
