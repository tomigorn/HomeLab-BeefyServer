# HomeLab-BeefyServer
Everything that runs on beefy: Docker stacks under `Docker/`, server docs under `Server/`, scripts. The repo is public: no secrets, no mail addresses, no real names.

## Git commits
- `Project Name: short description`. The project is the folder or service changed, written as it is normally written: `Jellyfin: …`, `Movie-Downloads: …`, `Server: …` for the docs.

## Projects
- One folder per project under `Projects/`, named as normally written (PascalCase, `Portainer-Agent`). Any type: Docker stacks, scripts, docs.

## Docker projects (`Docker/`)
- Run Docker with `sudo docker …`; `buntu` is not in the `docker` group on purpose (see `Server/2-Setup-DockerAndPortainer.md`). The Portainer agent here is managed from fastpi.
- `COMPOSE_PROJECT_NAME` in `.env` is the folder name in lowercase.
- The sample files in `Docker/` are the template. Every project has these four files:
  - `docker-compose.yaml`: the sectioned layout with banner blocks for Services, Networks, Volumes and a `# NAME` banner above each service.
  - `.env.example`: identical to `.env` except secret values are blank. Same variables, order and comments. Starts with `COMPOSE_PROJECT_NAME=`.
  - `.env`: the real values, gitignored.
  - `.gitignore`: ignores `.env` and every sensitive file or folder, per the sample.
- Media containers share one `/srv/video` → `/data` mount so Radarr can hardlink; see `Docker/Movie-Downloads/README.md`.
- `Docker/MEDIA-STACK-TODO.md` is the open-items list for the media stack.

## Project review checklist
- `_global/checklist.md`, created with the first project that needs it. Columns: `Project | Reviewed | Deployed | Image / Tag Version | my update performed date | Tag`.
- A new project gets a row with `❌` in Reviewed and Deployed and the other columns empty. Never set a `✅` and never fill the version, date or tag columns on your own.
