# The Intel OpenCL runtime that the jellyfin-opencl-intel Docker Mod would install on every start. Mods do not run in linuxserver non-root mode, so its debs are baked in here.
# Bump: pull lscr.io/linuxserver/mods:jellyfin-opencl-intel, take its new digest.
FROM ghcr.io/linuxserver/mods@sha256:693fb2e01ad3d6280cda67a0b9022d892020f7cf2b89a2e58cc0111208556b32 AS opencl

FROM lscr.io/linuxserver/jellyfin:12.4ubu2604-ls57
COPY --from=opencl /opencl-intel-legacy1 /tmp/opencl-intel-legacy1
COPY --from=opencl /opencl-intel /tmp/opencl-intel
RUN dpkg -i /tmp/opencl-intel-legacy1/*.deb \
 && dpkg -i /tmp/opencl-intel/*.deb \
 && rm -rf /tmp/opencl-intel-legacy1 /tmp/opencl-intel
