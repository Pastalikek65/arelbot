# Arelbot Linux VDS Deployment

Production runs as separate Docker Compose services: `bot` receives Telegram
updates and `worker` consumes media, AI, quote, sticker and chart queues. MongoDB
and Redis are persistent dependencies. A single systemd bot process is not a
valid production topology for the current architecture.

## Deploy

On the VDS, provision `/opt/arel/shared/.env` with mode `0600` (or place the
initial file at `/opt/arel/.env`; the deployer copies it into shared state). Set
`APP_ENV=production`, a numeric `OWNER_ID`, `TELEGRAM_MODE=webhook`, an HTTPS
`WEBHOOK_URL`, and a strong `WEBHOOK_SECRET`. Add the verified VDS host key to
`~/.ssh/known_hosts` before using the SSH deployer.

From a clean, committed checkout:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-deploy.txt
.\.venv\Scripts\python.exe scripts\deploy_release.py `
  --host VDS_HOST `
  --key-file $env:USERPROFILE\.ssh\arelbot_ed25519 `
  --version "v1.0.0"
```

The deployer builds an immutable archive from `HEAD`, creates a per-file
SHA-256 manifest, verifies every file on the VDS, and uses the production
preflight before touching the running stack. Docker Compose runs a stable
`arelbot-prod` project with separate `bot` and `worker` services. It waits for
health checks before atomically switching `/opt/arel/current`; if the new
release fails health, it reactivates the previous release using the same
persistent MongoDB/Redis/Chroma volumes. SSH host keys are rejected unless
already present in the selected known-hosts file. Remote stderr is not echoed
because arbitrary container diagnostics may include sensitive values.

The manual `deploy_ubuntu.sh` path applies the same Compose production profile
to an already checked-out release. Run `./deploy_ubuntu.sh --dry-run` before
`./deploy_ubuntu.sh`. For HTTPS termination and exact webhook routing see
`deploy/nginx/arelbot.conf.example`.

Use SSH keys in regular operation. Secrets remain in the untracked VDS `.env`
and are never written to release manifests or terminal output.

## Media pipeline prerequisites

Install `ffmpeg`, `ffprobe`, and `aria2` on a Linux host. The Docker image and
`deploy_ubuntu.sh` install them automatically. Set `REDIS_URL` for production
and use `MEDIA_REDIS_REQUIRED=true` so a missing Redis server stops startup
instead of using the process-local development queue.

The `yt-dlp[default]` dependency is used for current extractor support. Keep a
JavaScript runtime available for YouTube extraction; the Docker image includes
Node.js, and Windows development should have Node.js on `PATH`.

For administrator-only authenticated downloads, set a random `ENCRYPTION_KEY`
of at least 32 characters, keep `MEDIA_COOKIE_ENABLED=false` until a Netscape
cookie file is uploaded with `/medya_cookie yükle <platform>`, then enable it
with `/medya_cookie on`.

Windows development uses `TELEGRAM_MODE=polling`. A Linux host with a public
HTTPS endpoint can use `TELEGRAM_MODE=webhook` and `WEBHOOK_URL`.

On Windows, install FFmpeg before testing non-MP4 downloads and ensure both
`ffmpeg.exe` and `ffprobe.exe` are on `PATH`:

```powershell
winget install Gyan.FFmpeg.Shared
ffmpeg -version
ffprobe -version
```

Chocolatey users can use `choco install ffmpeg`. The bot reports missing
transcoding binaries in `/health` and rejects formats that cannot be converted.

## Docker Compose profiles

The repository includes `docker-compose.yml` with persistent MongoDB and Redis
volumes. For local development, set `APP_ROLE=combined` (the default) and run
the `dev` profile after copying `.env.example` to `.env`:

```bash
docker compose --profile dev up --build dev
```

For production, the bot process accepts Telegram updates and the separate
worker consumes Redis media jobs:

```bash
docker compose --profile prod up -d --build
```

The bot container uses `APP_ROLE=bot` and does not consume media jobs itself;
the worker uses `APP_ROLE=worker`. Set `MONGODB_REQUIRED=true` and
`MEDIA_REDIS_REQUIRED=true` in production so a missing dependency stops startup
instead of silently degrading to process-local storage.

Useful Telegram administration commands include `/health`, `/metrics`,
`/providers`, `/providers reset`, `/cache clear`, `/maintenance on|off`,
`/jobs <id>`, `/audit`, and the administrator-only `/medya_cookie` flow.


## Nginx reverse proxy

`deploy/nginx/arelbot.conf.example` is a restricted template: HTTP redirects to
HTTPS, only the exact webhook path is proxied, unknown paths return 404, and the
Telegram secret header is forwarded to python-telegram-bot for validation. The
bot Compose service binds its webhook port to VDS loopback only. Replace
`BOT_DOMAIN`, install a real certificate (for example with certbot), and keep
the template path synchronized with `WEBHOOK_PATH` if it is customized.
