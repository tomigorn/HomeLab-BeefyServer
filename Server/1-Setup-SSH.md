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

On modern Ubuntu (24.04), `/etc/ssh/sshd_config` ends with
`Include /etc/ssh/sshd_config.d/*.conf`, and packages such as cloud-init may drop a
`50-cloud-init.conf` that sets `PasswordAuthentication yes`. Because sshd uses the **first**
value it finds for each option, editing the main file lower down can be silently overridden.
The robust approach is a drop-in file whose name sorts *before* any existing one.

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

> Note the `TriggeredBy: ssh.socket` line — on Ubuntu 24.04 SSH is **socket-activated**.
> Auth changes apply to new connections automatically, but if you ever change the listening
> **Port**, you must also restart the socket:
> `sudo systemctl restart ssh.socket`.

## 6. Optional: Restrict which users or keys can connect

Limit who may log in with `AllowUsers` (or `AllowGroups`) — add it to your drop-in:

```bash
echo "AllowUsers buntu" | sudo tee -a /etc/ssh/sshd_config.d/10-hardening.conf
sudo sshd -t && sudo systemctl restart ssh
```

## 7. Test the connection

Without logging out of your current session, open a **new** PowerShell window. First confirm
that password login is now refused, then confirm the key works:

```powershell
# Default location has no matching key -> should be rejected
ssh buntu@beefy
# buntu@beefy: Permission denied (publickey).

# Explicit key -> should succeed
ssh -i $env:USERPROFILE\.ssh\beefy buntu@beefy
# Welcome to Ubuntu 24.04.3 LTS (GNU/Linux 6.8.0-79-generic x86_64)
```

> **Tip:** To avoid typing `-i` every time, add a host entry in `C:\Users\<you>\.ssh\config`:
>
> ```text
> Host beefy
>     HostName beefy
>     User buntu
>     IdentityFile ~/.ssh/beefy
>     IdentitiesOnly yes
> ```
>
> Then you can simply run `ssh beefy`.
