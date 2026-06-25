# Setting up SSH with an ED25519 key and disabling password login

- Generate an ED25519 SSH key pair on a Windows client.
- Where to store the private key on Windows (client).
- Where to place the public key on an Ubuntu server (remote).
- Disable password authentication on the Ubuntu server to force key-only SSH logins.

Prerequisites and assumptions
- You have control of both the Windows client and the Ubuntu server.
- You can run commands as your Windows user and use `sudo` on the Ubuntu server.
- The server runs OpenSSH (`sshd`) — default on Ubuntu.

> Throughout this guide the key pair is named `beefy` (after the server). Keep that name
> consistent everywhere, or substitute your own — the `-i` path you use to connect must match
> the file you actually created.

## 1. Generate an ED25519 key on Windows (client)

Open PowerShell and run the following, replacing `Gamer` with [YourUserName], `Prime` with
[YourPcName] and `beefy` with [RemoteServerName]:

```powershell
cd $env:USERPROFILE\.ssh
ssh-keygen -t ed25519 -C "|| Gamer @ Prime -> beefy" -f beefy
```

Sample output (the randomart image will differ — it is derived from your key):

```text
Generating public/private ed25519 key pair.
Enter passphrase (empty for no passphrase):
Enter same passphrase again:
Your identification has been saved in beefy
Your public key has been saved in beefy.pub
The key fingerprint is:
SHA256:ZEpVpBpQt5S+pSx+LCKdhU1aEWVXK/Su+Q9IuZJ82CU || Gamer @ Prime -> beefy
The key's randomart image is:
+--[ED25519 256]--+
|        ...      |
|       . o .     |
|      . + +      |
|     . = S =     |
|      o B + o    |
|       = * .     |
|        o .      |
|                 |
|                 |
+----[SHA256]-----+
```

Notes:
- `-t ed25519` creates an Ed25519 keypair (modern, small, and secure).
- `-C "|| Gamer @ Prime -> beefy"` sets the key's comment. The format is
  `<user> @ <client-host> -> <server-host>`, with a leading `|| ` so the comment stands out from
  the key body in `authorized_keys` and key listings.
- `-f beefy` saves the private key to `C:\Users\Gamer\.ssh\beefy` and the public key to `beefy.pub`.

You'll be prompted for a passphrase — strongly recommended. If you prefer convenience over
security you can leave it empty, but that rather defeats the point of going through the hassle
of a key file. Use the ssh-agent (below) so you only type the passphrase once per session.

## 2. Where to keep keys on Windows (client)

- Private key: `C:\Users\<your-windows-user>\.ssh\beefy` — keep this file secret.
- Public key: `C:\Users\<your-windows-user>\.ssh\beefy.pub` — safe to share with remote servers.

Best practices (PowerShell):

Lock down the `.ssh` folder so only your user can read it (removes inherited permissions, then
grants your user full control):

```powershell
icacls "$env:USERPROFILE\.ssh" /inheritance:r
icacls "$env:USERPROFILE\.ssh" /grant:r "$($env:USERNAME):(OI)(CI)F"
```

Use the Windows OpenSSH agent so an unlocked (passphrase-protected) key is cached for the session:

```powershell
# One-time: have the agent start automatically
Get-Service ssh-agent | Set-Service -StartupType Automatic
Start-Service ssh-agent

ssh-add "$env:USERPROFILE\.ssh\beefy"
```

## 3. Copy the public key to the Ubuntu server (remote)

Choose one of these methods.

### a) Using `ssh-copy-id` (recommended if you have a Bash environment like WSL or Git Bash)

```bash
ssh-copy-id -i ~/.ssh/beefy.pub buntu@your.server.ip
```

This appends the key with the correct permissions and ownership for you.

### b) Manual copy (PowerShell + SSH)

1. Copy the public key's contents to the clipboard (PowerShell):

   ```powershell
   Get-Content "$env:USERPROFILE\.ssh\beefy.pub" | Set-Clipboard
   ```

   (It's a single short line beginning with `ssh-ed25519`. You can also open `beefy.pub` in any
   text editor and copy it — just never copy the private `beefy` file.)

2. On the server (after logging in over SSH with your password), append it to `authorized_keys`.
   **Do not use `sudo`** — these files live in your own home directory and must be owned by you,
   not root, or sshd will reject the key:

   ```bash
   mkdir -p ~/.ssh
   chmod 700 ~/.ssh
   echo "ssh-ed25519 AAAA...the key you copied... || Gamer @ Prime -> beefy" >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```

## 4. Verify key-based login

From your Windows client (PowerShell or WSL), **before** disabling passwords:

```powershell
ssh -i $env:USERPROFILE\.ssh\beefy buntu@beefy
```

You should be logged in ***without*** being asked for your account password. (If you set a
passphrase and added the key to ssh-agent, you won't be prompted for that either.)

## 5. Disable password authentication on the Ubuntu server (force key-only login)

> **Important:** Keep your working SSH session open while you change the config and test from a
> *second* window, so you can revert if something goes wrong and you get locked out.

On modern Ubuntu (22.04 and later, including 26.04), `/etc/ssh/sshd_config` has an
`Include /etc/ssh/sshd_config.d/*.conf` line **near the top**, and packages such as cloud-init
may drop a `50-cloud-init.conf` that sets `PasswordAuthentication yes`. Because the Include sits
at the top and sshd uses the **first** value it finds for each option, drop-ins are read before
the main file's own defaults — but a *later*-sorting drop-in still loses to an earlier one. So
editing the main file lower down can be silently overridden, and your drop-in must sort *before*
any conflicting one (lower number wins, e.g. `10-` beats `50-`).

### a) Check for conflicting drop-ins

```bash
ls -1 /etc/ssh/sshd_config.d/
sudo grep -ri "passwordauthentication" /etc/ssh/sshd_config /etc/ssh/sshd_config.d/
```

If a file like `50-cloud-init.conf` enables passwords, our file must sort earlier (e.g. `10-…`).

### b) Create the hardening drop-in

```bash
sudo tee /etc/ssh/sshd_config.d/10-hardening.conf > /dev/null <<'EOF'
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
# Disallow interactive root login (key-only root still possible if ever needed)
PermitRootLogin prohibit-password
EOF
```

(`KbdInteractiveAuthentication` replaces the old, now-deprecated `ChallengeResponseAuthentication`.)

To revert later, just delete the drop-in and restart: `sudo rm /etc/ssh/sshd_config.d/10-hardening.conf && sudo systemctl restart ssh`. Because we never touched the main `sshd_config`, there's no backup to restore.

### c) Test the config, then apply it

```bash
sudo sshd -t                 # validates syntax; no output = OK
sudo systemctl restart ssh
sudo systemctl status ssh --no-pager
```

Sample status output:

```text
● ssh.service - OpenBSD Secure Shell server
     Loaded: loaded (/usr/lib/systemd/system/ssh.service; disabled; preset: enabled)
     Active: active (running) since Sun 2025-09-14 22:53:33 UTC; 4s ago
TriggeredBy: ● ssh.socket
       Docs: man:sshd(8)
             man:sshd_config(5)
   Main PID: 4557 (sshd)
     CGroup: /system.slice/ssh.service
             └─4557 "sshd: /usr/sbin/sshd -D [listener] 0 of 10-100 startups"
```

> Note the `TriggeredBy: ssh.socket` line — on Ubuntu 22.04+ SSH is **socket-activated**.
>
> **Important — `restart`, not `reload`, and restart the socket too (OpenSSH 9.8+ / Ubuntu
> 24.04+26.04).** Since OpenSSH 9.8 the listener parses `sshd_config` **once at startup** and
> hands it to each per-connection `sshd-session` — it does **not** re-read the file on every
> connection like older versions did. On a socket-activated host a plain `sudo systemctl reload
> ssh` can therefore leave the *running* daemon on the **old** config. Always:
> `sudo systemctl restart ssh.socket ssh.service`, then confirm the change is live with
> `sudo sshd -T | grep -i <option>`. (Symptom of getting this wrong: a freshly-added
> `AllowUsers` user gets `Permission denied (publickey)` and the auth log says *"not allowed
> because not listed in AllowUsers"* even though the file is correct — see §6.) The same applies
> if you ever change the listening **Port**.

## 6. Optional: Restrict which users or keys can connect

Limit who may log in with `AllowUsers` (or `AllowGroups`) — add it to your drop-in:

```bash
echo "AllowUsers buntu" | sudo tee -a /etc/ssh/sshd_config.d/10-hardening.conf
sudo sshd -t && sudo systemctl restart ssh.socket ssh.service
sudo sshd -T | grep -i allowusers     # confirm the RUNNING daemon loaded it
```

> **Note:** `AllowUsers buntu` is a whitelist — once set, **only** `buntu` may log in over SSH;
> every other account (including `root`) is refused regardless of keys. List multiple users
> space-separated (`AllowUsers buntu alice`) if you need more.

> **Use `restart` (socket + service), not `reload`** — and verify with `sudo sshd -T | grep -i
> allowusers`. On OpenSSH 9.8+ a `reload` can silently leave the running daemon on the old
> whitelist, so the new user keeps getting `Permission denied (publickey)` even though the file
> is right. See the socket-activation note in §5.

## 7. Test the connection

Without logging out of your current session, open a **new** PowerShell window and test.

What proves password auth is disabled is the *kind* of failure: a client with no usable key
gets **`Permission denied (publickey)`** and is **never offered a password prompt**. If you were
ever asked for a password, password auth would still be on.

```powershell
# A name/user with no configured key -> rejected, and NOT offered a password
ssh someone@beefy
# someone@beefy: Permission denied (publickey).

# Your explicit key -> succeeds
ssh -i $env:USERPROFILE\.ssh\beefy buntu@beefy
# Welcome to Ubuntu 26.04 LTS (GNU/Linux 7.0.0-22-generic x86_64)
```

> **Don't use `ssh beefy` as your "should fail" test.** Once you add the `Host` block below,
> `ssh beefy` *succeeds* because it supplies the key automatically. The same is why an alias that
> resolves to the server but isn't in your config (e.g. `beefy.local`) fails with `publickey`:
> no key is offered for that name — the server did **not** fall back to passwords.

> **Tip:** To avoid typing `-i` every time, add a host entry in `C:\Users\<you>\.ssh\config`:
>
> ```text
> Host beefy beefy.local
>     HostName 192.168.1.102
>     User buntu
>     IdentityFile ~/.ssh/beefy
>     IdentitiesOnly yes
> ```
>
> List every alias you might type on the `Host` line — otherwise that alias (e.g. `beefy.local`)
> gets no key and fails with `publickey`. `IdentitiesOnly yes` stops ssh from offering every
> agent key. Then you can simply run `ssh beefy`.

## 8. Optional: further hardening

Beyond key-only login, you can tighten things further. Add any of these to the same
`10-hardening.conf` drop-in, then re-run `sudo sshd -t && sudo systemctl restart ssh`:

```bash
MaxAuthTries 3              # fewer auth attempts per connection
LoginGraceTime 20          # drop un-authenticated connections quickly
LogLevel VERBOSE           # log the key fingerprint used for each login (good for auditing)
AuthenticationMethods publickey   # make public key the only accepted method, explicitly
AddressFamily inet         # listen on IPv4 only (skip if you rely on IPv6 / beefy.local over v6)
```

None of these are required — steps 1–6 already enforce key-only logins. Note that
`AddressFamily inet` disables IPv6, which would stop link-local `beefy.local` connections that
resolve over IPv6 (as seen in step 7).
