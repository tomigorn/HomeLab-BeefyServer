# Adding an unprivileged user with key-only SSH and rootless Docker

How to add a second account to this host that is **least-privilege by design**:

- **No `sudo`** — it cannot administer the machine.
- **Key-only SSH** — it inherits the host's key-only policy (no password logins); see
  [`1-Setup-SSH.md`](./1-Setup-SSH.md).
- **Can run Docker without `sudo` and without the `docker` group** — via **rootless Docker**,
  so a container escape lands as this unprivileged user, never real root. This is the answer
  foreshadowed in [`2-Setup-DockerAndPortainer.md`](./2-Setup-DockerAndPortainer.md) §5
  ("the right answer is rootless Docker … *not* joining the `docker` group").

> Throughout this guide the new account is named **`pixlhero`** (uid `1001`) as a concrete
> worked example — substitute your own username everywhere. Where a command needs the numeric
> uid (e.g. `/run/user/1001`), get it with `id -u <username>`.

Prerequisites and assumptions
- You can `sudo` on the server as `buntu` (the admin account).
- Docker is already installed **from the Docker `apt` (deb) package** as per
  [`2-Setup-DockerAndPortainer.md`](./2-Setup-DockerAndPortainer.md). The deb-package install is
  what makes the rootless AppArmor profile "just work" — see §4.2.
- The host enforces key-only SSH with an `AllowUsers` whitelist (guide 1, §6).

---

## 1. Create the user (no sudo)

```bash
sudo useradd --create-home --shell /bin/bash pixlhero
```

`useradd` adds the account only to its own primary group — **not** `sudo`, not `docker`,
nothing else. Verify:

```bash
$ id pixlhero
uid=1001(pixlhero) gid=1001(pixlhero) groups=1001(pixlhero)

$ getent group sudo          # the new user must NOT appear here
sudo:x:27:buntu
```

The account is created **password-locked** (no password set). That's fine and intended here —
SSH on this host is key-only (§2–§3), so the user never authenticates with a password.

> **Forcing a password change on first login?** The usual trick is `sudo passwd <user>` to set a
> temporary password, then `sudo chage -d 0 <user>` to force a change at next login. **This only
> works on an interactive, password-capable login** (local console / GDM). It does **not** work —
> and will actually *break* login — for a key-only SSH account, because the hardened SSH config
> (`PasswordAuthentication no`, `KbdInteractiveAuthentication no`) has no channel to run the
> password-change dialog. For an SSH-key-only user, leave the account password-locked.

---

## 2. Allow the user through the SSH whitelist

Guide 1 (§6) set `AllowUsers buntu`, which is a **whitelist** — only listed users may SSH in,
regardless of keys. Add the new user to it (in the hardening drop-in):

```bash
sudo sed -i 's/^AllowUsers buntu$/AllowUsers buntu pixlhero/' /etc/ssh/sshd_config.d/10-hardening.conf
sudo sshd -t && sudo systemctl restart ssh.socket ssh.service   # validate, then RESTART (not reload)
sudo sshd -T | grep -i allowusers                               # confirm live: "allowusers buntu pixlhero"
```

> `sshd -t` validates first; with `&&` the restart only runs if validation passes, so a typo
> can't lock you out. Key-only / no-password policy is unchanged — we only widened *who* may log
> in, not *how*.

> **Restart — do NOT just `reload` (the gotcha that bit this setup).** On OpenSSH 9.8+
> (Ubuntu 24.04/26.04) the listener parses `sshd_config` **once at startup** and passes it to
> each per-connection `sshd-session`; it does not re-read the file per connection. On a
> socket-activated host a plain `sudo systemctl reload ssh` can leave the *running* daemon on the
> old config — so the new user still gets `Permission denied (publickey)` and the auth log shows
> *"not allowed because not listed in AllowUsers"* even though the file already lists them.
> **Restart the socket *and* the service, then confirm with `sudo sshd -T | grep -i allowusers`.**
> Diagnose any remaining rejection from the server side: `sudo grep pixlhero /var/log/auth.log | tail`.

If your host doesn't use an `AllowUsers` whitelist, skip this step — the user is already
permitted once it has a key (§3).

---

## 3. Install the user's SSH public key

Have the user generate a keypair **on their client** (so the private key never leaves it) — same
as guide 1, §1 — and give you the **public** key (`...pub`, one line starting with `ssh-ed25519`).
Install it into the user's `authorized_keys` with correct ownership and permissions:

```bash
KEY='ssh-ed25519 AAAA...the-user-public-key... comment'   # paste the PUBLIC key

sudo install -d -m 700 -o pixlhero -g pixlhero /home/pixlhero/.ssh
echo "$KEY" | sudo tee -a /home/pixlhero/.ssh/authorized_keys >/dev/null
sudo chown pixlhero:pixlhero /home/pixlhero/.ssh/authorized_keys
sudo chmod 600 /home/pixlhero/.ssh/authorized_keys
```

Verify perms/ownership and that the key parsed:

```bash
$ sudo ls -ld /home/pixlhero/.ssh                       # want: drwx------ pixlhero pixlhero
drwx------ 2 pixlhero pixlhero 4096 ... /home/pixlhero/.ssh
$ sudo ls -l /home/pixlhero/.ssh/authorized_keys        # want: -rw------- pixlhero pixlhero
-rw------- 1 pixlhero pixlhero 110 ... /home/pixlhero/.ssh/authorized_keys
$ sudo ssh-keygen -l -f /home/pixlhero/.ssh/authorized_keys
256 SHA256:Wdu9614JRWP9RpqOIWz5qhfmCBlVvbvx4OMNsXpLj6A user@client (ED25519)
```

> **`.ssh` must be `700` and `authorized_keys` `600`, both owned by the user** — sshd refuses
> keys in world/group-writable or wrong-owner files. The user must own these, **never** root.

The user can now log in from their client: `ssh pixlhero@192.168.1.102` (no password prompt).

---

## 4. Rootless Docker for the user

### Why rootless instead of the `docker` group

The `docker` group grants **passwordless root** (a member can `docker run -v /:/host …` and own
the host) — the whole reason we don't use it on this server (see
[`2-Setup-DockerAndPortainer.md`](./2-Setup-DockerAndPortainer.md) §5). Giving an *unprivileged*
user that group would make it **more** powerful than `buntu`, which defeats the point of a
least-privilege account.

**Rootless Docker** runs a separate daemon as the user, inside a user namespace: container "root"
maps to the user's host uid, so a breakout is contained to that unprivileged user. No `sudo`,
no `docker` group, no root-equivalent grant.

### 4.1 Prerequisites (run once, as admin)

```bash
sudo apt-get update
sudo apt-get install -y uidmap slirp4netns fuse-overlayfs    # newuidmap/newgidmap + rootless net/storage
# dbus-user-session is usually already installed (needed for `systemctl --user`)

sudo loginctl enable-linger pixlhero    # user's daemon runs at boot, without an active login
```

Confirm the user has subordinate UID/GID ranges (modern `useradd` adds these automatically) and
a live user-systemd:

```bash
$ grep '^pixlhero:' /etc/subuid /etc/subgid
/etc/subuid:pixlhero:165536:65536
/etc/subgid:pixlhero:165536:65536
$ systemctl is-active user@1001.service     # active = lingering started the user manager
active
```

### 4.2 The AppArmor user-namespace gotcha (read this)

Ubuntu 23.10+ (incl. 26.04) ships `kernel.apparmor_restrict_unprivileged_userns=1`, which blocks
`rootlesskit` from creating a user namespace **unless an AppArmor profile grants `userns`** to it.
You'll otherwise see:

```text
[rootlesskit:parent] error: failed to start the child: fork/exec /proc/self/exe: permission denied
```

**Because Docker here was installed from the deb package, the correct profile is already
bundled** and loaded: `/etc/apparmor.d/rootlesskit` (owned by the **`apparmor`** package; profile
`rootlesskit` attaching `/usr/bin/rootlesskit` with a `userns,` rule). **You do not need to add
anything — and you must NOT create a manual profile.**

> **Do NOT follow the `usr.bin.rootlesskit` hint** that `dockerd-rootless-setuptool.sh` prints on
> failure. That hint is for the **`get.docker.com` *script* install** (binary in `~/bin`). On a
> **deb install** it creates a *second* profile claiming `/usr/bin/rootlesskit`, which collides
> with the bundled one → `aa-status` shows two, AppArmor attaches **neither** → rootlesskit runs
> unconfined → the kernel forces it into the restrictive `unprivileged_userns` profile → the same
> `fork/exec /proc/self/exe` denial. The audit log names the cause exactly:
> `sudo dmesg | grep -i apparmor` → `operation="exec" info="conflicting profile attachments"`.

If the bundled profile is **missing** (e.g. it was deleted), restore it. It's a dpkg
**conffile**, so a plain `--reinstall` will *not* bring back a deleted one — force it:

```bash
sudo apt-get install --reinstall -o Dpkg::Options::="--force-confmiss" -y apparmor
sudo systemctl restart apparmor.service
# verify exactly ONE profile, and that it grants userns:
sudo aa-status | grep -i rootless          # -> only:  rootlesskit
sudo cat /etc/apparmor.d/rootlesskit       # -> profile rootlesskit /usr/bin/rootlesskit flags=(unconfined) { userns, ... }
```

### 4.3 Run the per-user install

The rootless setup must run **as the user**, in a real user session. Easiest: have the user SSH
in (the key login from §3) and run:

```bash
# in the user's own session (ssh pixlhero@<host>):
dockerd-rootless-setuptool.sh install
```

Alternatively, run it from the admin (`buntu`) console **as the user**, feeding it the user's
already-running systemd instance (this is handy when you don't want to hop into the user's SSH
session). Substitute the user's uid for `1001`:

```bash
sudo -u pixlhero \
  HOME=/home/pixlhero \
  XDG_RUNTIME_DIR=/run/user/1001 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus \
  PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  /usr/bin/dockerd-rootless-setuptool.sh install
```

> `sudo -u <user> …` alone does **not** create a systemd/D-Bus session, so plain `sudo -iu` fails
> with `Failed to connect to bus`. Pointing `XDG_RUNTIME_DIR`/`DBUS_SESSION_BUS_ADDRESS` at the
> lingering `user@<uid>` instance (from §4.1) is what lets the tool's `systemctl --user` calls
> work. The official alternative is `sudo machinectl shell pixlhero@` — but that needs the
> `systemd-container` package, which isn't installed on this host.

On success it creates and starts `~/.config/systemd/user/docker.service`, enables it, and prints
the env vars to set (next step).

### 4.4 Persist the environment and enable on boot

Add the daemon socket to the user's shell profile so `docker` "just works" in their sessions:

```bash
sudo -u pixlhero bash -c 'grep -q "DOCKER_HOST=unix:///run/user/1001" ~/.bashrc || \
  printf "\n# Rootless Docker\nexport PATH=/usr/bin:\$PATH\nexport DOCKER_HOST=unix:///run/user/1001/docker.sock\n" >> ~/.bashrc'
```

`systemctl --user enable docker` (done by the installer) + `enable-linger` (§4.1) together mean
the daemon starts at boot and keeps running when the user is logged out.

### 4.5 Verify

```bash
# As the user (DOCKER_HOST set). From buntu, prefix with the same sudo -u env block as §4.3.
$ docker info | grep -iE 'rootless|Storage Driver|Cgroup'
 Storage Driver: overlayfs
 Cgroup Driver: systemd
 Cgroup Version: 2
  rootless

$ docker run --rm hello-world
Hello from Docker!
...

# The security payoff — container uid 0 maps to the host's unprivileged user (1001):
$ docker run --rm alpine cat /proc/self/uid_map
         0       1001          1
         1     165536      65536
```

`Rootless: true`-equivalent (`rootless` under `info`) and the `0 1001 1` mapping confirm that a
container "root" is really host-uid `1001` — a breakout is confined to the unprivileged user.

---

## 5. Day-to-day use and limits

- Once logged in, the user runs `docker …` / `docker compose …` normally — **no `sudo`**.
- Data lives under `~/.local/share/docker/` (not `/var/lib/docker`); size the home dir
  accordingly.
- Rootless trade-offs: can't bind host ports `<1024` by default (use high ports, or see the
  Docker docs for `net.ipv4.ip_unprivileged_port_start` / `setcap`); userspace networking
  (`slirp4netns`) is slower than rootful; no privileged containers. For a homelab service user
  these rarely matter.
- The user's rootless daemon is **independent** of the host's rootful `sudo docker` daemon
  (guide 2). They have separate images, containers, and sockets.

> **Reference:** Docker's own rootless docs and troubleshooting —
> <https://docs.docker.com/engine/security/rootless/>. The Ubuntu userns restriction is
> documented at the "Distribution-specific hint → Ubuntu" section there.
