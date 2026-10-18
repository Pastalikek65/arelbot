# VDS Readiness Contract

This document describes the read-only production readiness probe for the Linux
VDS. The probe does not deploy, restart, mutate Telegram, change Docker, or
read secret values. SSH host keys must already exist in the operator's
`known_hosts` file and unknown keys are rejected.

## Checks

`scripts/vds_readiness.py` verifies the following bounded facts:

- verified SSH authentication and Linux host;
- Docker and Compose availability;
- `/opt/arel` presence and write access;
- `/opt/arel/shared/.env` presence (the contents are never archived);
- the active release's Compose file and `docker compose --profile prod config`;
- bot, worker, MongoDB, and Redis Docker health checks;
- listening ports, release count, target container count, and free disk space;
- Guardian mode and Unix socket presence/readability.

Guardian is expected to remain `disabled` until a real evaluator-backed bridge
exists. In `shadow` or `active` mode, a missing or unreadable socket is a hard
readiness blocker.

## Bounded live run

The live gate requires all three conditions: `--live`, `--confirm-live`, and
`ARELBOT_VDS_READINESS_LIVE=1`. The SSH password is supplied through an
ephemeral environment variable by the operator and is never written to this
repository or the evidence file.

```powershell
$env:AREL_SSH_PASSWORD = '<operator supplied secret>'
$env:ARELBOT_VDS_READINESS_LIVE = '1'
python scripts/vds_readiness.py `
  --host <vds-host> --user root --live --confirm-live --json `
  --report deploy/evidence/vds-readiness.json
```

The report contains only booleans, bounded counters, modes, and stable blocker
codes. It must be checked with `scripts/evidence_audit.py` before release
promotion. A successful read-only report is not proof of Telegram mutation,
media availability, provider approval, or Guardian enforcement.

## Current evidence

The latest bounded run against the configured VDS passed SSH host-key
verification, Docker/Compose checks, the active release Compose config, all
four production service health checks, and disk-space requirements. Guardian
was `disabled` and its socket was absent, which is expected while the bridge is
not deployed. The secret-free artifact is
`deploy/evidence/vds-readiness.json`.

The report does not promote Phase 9 by itself. Telegram mutation, full live
media matrix, monitoring delivery, provider governance, backup/restore, and a
real Guardian evaluator remain separate release gates.
