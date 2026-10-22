#!/usr/bin/with-contenv bash
# The linuxserver base image puts abc into "users", which is gid 100 here (the host group buntu is in).
if id -nG abc | tr " " "\n" | grep -qx users; then
    gpasswd -d abc users >/dev/null
fi
