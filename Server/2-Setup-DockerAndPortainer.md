# How to install Docker

## 1. Uninstall all conflicting packages
```bash
$ for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do sudo apt-get remove $pkg; done
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
Package 'docker.io' is not installed, so not removed
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
Package 'docker-doc' is not installed, so not removed
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
Package 'docker-compose' is not installed, so not removed
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
Package 'docker-compose-v2' is not installed, so not removed
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
Package 'podman-docker' is not installed, so not removed
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
Package 'containerd' is not installed, so not removed
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
Package 'runc' is not installed, so not removed
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
```

## 2. Setup Docker's apt repository
Without $ sign or output sample, because the formatting here is actually important:
```bash
sudo apt-get update
sudo apt-get install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
```

## 3. Install the latest Version of Docker
```bash
$ sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 4. Verify the installation
```bash
$ sudo docker run hello-world
Unable to find image 'hello-world:latest' locally
latest: Pulling from library/hello-world
17eec7bbc9d7: Pull complete 
Digest: sha256:54e66cc1dd1fcb1c3c58bd8017914dbed8701e2d8c74d9262e26bd9cc1642d31
Status: Downloaded newer image for hello-world:latest

Hello from Docker!
This message shows that your installation appears to be working correctly.

To generate this message, Docker took the following steps:
 1. The Docker client contacted the Docker daemon.
 2. The Docker daemon pulled the "hello-world" image from the Docker Hub.
    (amd64)
 3. The Docker daemon created a new container from that image which runs the
    executable that produces the output you are currently reading.
 4. The Docker daemon streamed that output to the Docker client, which sent it
    to your terminal.

To try something more ambitious, you can run an Ubuntu container with:
 $ docker run -it ubuntu bash

Share images, automate workflows, and more with a free Docker ID:
 https://hub.docker.com/

For more examples and ideas, visit:
 https://docs.docker.com/get-started/
```

## 5. Post-installation

> **We deliberately do _not_ add our user to the `docker` group on this host.**
> Run Docker with `sudo docker …` instead.

### Why not join the `docker` group?

The Docker daemon runs as **root**, and its socket (`/var/run/docker.sock`) is the daemon's
full control API. The `docker` group grants **password-free** access to that socket — which is
the same as handing out **passwordless root**. Any member can trivially take over the host, e.g.:

```bash
docker run -v /:/host -it alpine chroot /host   # now root on the real filesystem
```

So adding yourself to the group (the common `sudo usermod -aG docker $USER` step you'll see in
most tutorials) is the **risky** option, because:

- It is effectively **passwordless `sudo`** that **never prompts** and is easy to forget you granted.
- **Any** process running as your user — a shell script, a dev tool, a compromised dependency —
  inherits that root-equivalent access silently, with no password gate to slow an attacker down.
- The privilege is **invisible**: it doesn't show up in `sudoers`, so it's easy to overlook when
  auditing who can become root.

Using `sudo docker …` instead is **better** because:

- Every privileged Docker action goes through the **normal `sudo` path** — password prompt,
  logging in the auth log, and the usual sudo policy/timeout all apply.
- Privilege is **explicit and auditable**: root access for Docker lives in `sudoers` like
  everything else, not in a hidden group membership.
- A stray process running as your user **cannot** silently drive the daemon — it would have to
  pass `sudo` first.

The only cost is typing `sudo` before `docker`/`docker compose`. On a single-admin homelab
server that's a cheap, worthwhile trade.

> Regardless of the above: **never expose the docker socket (`/var/run/docker.sock`) to an
> untrusted container or over the network.**
>
> If you later want rootless operation without `sudo`, the right answer is **rootless Docker**
> (daemon runs as your unprivileged user) — *not* joining the `docker` group.

### Verify (with `sudo`)

Step 4 already verified the daemon with `sudo docker run hello-world`. Because we are not
joining the `docker` group, the **same `sudo` form is the normal way to run Docker here** — a
plain `docker …` (no `sudo`) will fail with a permission error on the socket, which is expected.

```bash
$ sudo systemctl enable docker.service
$ sudo systemctl enable containerd.service
Synchronizing state of docker.service with SysV service script with /usr/lib/systemd/systemd-sysv-install.
Executing: /usr/lib/systemd/systemd-sysv-install enable docker
```

> Note: the official Docker `apt` package already **enables and starts** `docker.service` and
> `containerd.service` on install, so these commands are usually a no-op confirmation. Use
> `sudo systemctl enable --now docker.service` if you ever want to enable *and* start in one go.
> Verify with `systemctl is-enabled docker containerd` (both should print `enabled`).

## 5b. Recommended: cap container log size

By default Docker uses the `json-file` log driver with **no size limit**, so container logs
grow unbounded and can eventually fill the disk on an always-on server. Set sane defaults for
**all** containers once, in `/etc/docker/daemon.json`:

```bash
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
sudo systemctl restart docker
```

This keeps at most 3 × 10 MB of logs per container. Existing containers pick up the new policy
when they are recreated. Validate the file before restarting with `sudo dockerd --validate`
(or just check that `docker info` still works after the restart).

### 6. Cleanup
```bash
$ sudo docker container ls --all
CONTAINER ID   IMAGE         COMMAND    CREATED         STATUS                     PORTS     NAMES
f4f155f661ef   hello-world   "/hello"   2 minutes ago   Exited (0) 2 minutes ago             beautiful_kilby
0f5dd81371f2   hello-world   "/hello"   5 minutes ago   Exited (0) 5 minutes ago             objective_banzai
$ sudo docker rm beautiful_kilby
beautiful_kilby
$ sudo docker rm objective_banzai
objective_banzai
$ sudo docker container ls --all
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
```

# Add this host to an existing Portainer

We are **not installing a Portainer server** on this host. There is already a Portainer
**server** running on another host (here: the RaspberryPi), and we only want to add this Ubuntu
server to it as a new **environment** to manage from that one Portainer Web UI.

This is done with the **Portainer Agent**: a small container that runs here and exposes this
host's Docker to the existing Portainer over the LAN. The same steps work for *any* existing
Portainer server, not just the Pi — just point the Web UI at whichever host is running it.

### 1. In the existing Portainer's Web UI

- Left sidebar → **Environments** (under "Environment-related").
- **Add environment** (top right).
- Select **Docker Standalone** → **Start Wizard**.
- Note the **agent `docker run` command** it shows. The command below is the equivalent — make
  sure the **image tag matches your server's version** (see the note under the command).

### 2. On this host — run the Agent

```bash
sudo docker run -d \
  -p 9001:9001 \
  --name portainer_agent \
  --restart=always \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/docker/volumes:/var/lib/docker/volumes \
  -v /:/host \
  portainer/agent:2.33.1
```

> Run with `sudo` (we don't join the `docker` group on this host — see §5).

> **Pin the agent to your Portainer *server* version.** The tag `2.33.1` must match (or be
> compatible with) the Portainer **server** you're attaching to — mismatched major/minor versions
> can fail to connect. Check the server's version in its Web UI footer and bump this tag to match
> when you upgrade the server.
>
> **What this agent can do / security:** the command mounts the Docker socket and `-v /:/host`,
> i.e. the agent has **full root-level control of this host** by design — that's how Portainer
> manages it. Therefore:
> - Port `9001` must stay on the **trusted LAN only** — never expose it to the internet or route
>   it through the Cloudflare Tunnel.
> - For defence-in-depth, set a shared secret on both ends: add `-e AGENT_SECRET=<random>` to this
>   `docker run` and enter the same secret when adding the environment in Portainer.

### 3. Back in the existing Portainer's Web UI — connect

- Set a **Name** (here: `beefy`) and the **Environment address** = this host's
  `LAN-IP:9001` (here: `192.168.1.102:9001`).
- Press **Connect**.

You should now see the new environment under **Home** in Portainer; opening it shows this host's
Stacks, Images, Networks, Containers and Volumes.