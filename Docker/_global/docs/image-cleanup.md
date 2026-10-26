# Image cleanup

| | |
|---|---|
| What | `scripts/image-cleanup.sh`: removes images no container (running or stopped) uses, and build cache, both after 14 days. Never volumes, networks or containers. |
| When | `image-cleanup.timer`, Sun 04:20 inside the maintenance wake (a night asleep is skipped, never a wake of its own). Install: `_global/install-image-cleanup.sh` |
| Unused clock | own state `/var/lib/image-cleanup/first-seen` (image ID, first seen unused); `prune --filter until` dates by build time and would delete an image the day it was superseded |
| Rollback | `update-project.sh` re-tags the previous image ID, so a superseded image stays 14 days. Kept always: images of any container, tags containing `rollback`, images referenced in compose files and scripts of this repo. First run: dangling images and older tags of a repo in use start their 14 days then |
| Metrics | `docker_image_cleanup_*` in `/var/lib/auto-update/textfile/image_cleanup.prom` |
| By hand | `scripts/image-cleanup.sh --dry-run`; `sudo systemctl start image-cleanup.service`; `KEEP_DAYS=7` env changes the age |
| Test | `scripts/test_image_cleanup.sh` (fake docker) |
