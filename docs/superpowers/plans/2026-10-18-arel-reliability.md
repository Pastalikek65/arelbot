# Arel Reliability Implementation Plan

> **For agentic workers:** Use executing-plans inline, task by task. No GitHub changes until local and production acceptance.

**Goal:** Repair the existing bot's command paths, useful output and presentation locally, then deploy the same verified package with rollback.
**Architecture:** Preserve existing service/port/provider boundaries. Fix each observed defect at its existing boundary; do not replace the application or claim unavailable features work.
**Tech Stack:** Python 3.11, python-telegram-bot, MongoDB, Redis, Chroma, Docker Compose.
**Spec:** User request in this chat and `C:/chatgpt/ss/AREL-KAPSAMLI-INCELEME-20261018.md`.

## Global constraints

- Work in `C:/Vscode/arelbot`; preserve pre-existing edits (baseline clean, dbcc3bc).
- No secret/cookie/user-data output or inclusion in source packages.
- Test external effects with fixtures; run target tests in isolated source copies with no production environment or network.
- Preserve handler/domain/storage separation and current callback compatibility.
- Do not advertise registry entries as proven functionality.
- Production: source/config hash preflight, backup, immutable release, readiness and rollback.
- User authorized local fixes and deployment; GitHub work comes last.

## Review focus

- Wrong-user/wrong-chat model arguments must never select another resource.
- Registered commands and aliases must reach an implemented handler or a useful, explicit response.
- Subprocess timeouts/cancellation must release worker capacity and clean output.
- Menus must work in private/group contexts and stale callbacks must not mutate newer state.
- A healthy port is not a healthy datastore or working bot.

## Tasks

### 1. Runtime collection and protocol readiness
Files: `src/games/card_engine.py`, `scripts/healthcheck.py`, matching tests.
- [ ] Reproduce existing card collection failure and add isolated-default regression.
- [ ] Add false-TCP-health regressions; run red.
- [ ] Apply minimal fixes, run focused tests, record result.

### 2. AI tool identity and ownership
Files: `src/tool_manager.py`, `src/chat_engine.py`, handler context, tool tests.
- [ ] Add real registered-tool tests for non-admin, cross-chat, missing-user and allowed-current-chat paths; run red.
- [ ] Bind explicit tool identity/permission; override resource IDs from trusted context.
- [ ] Supply verified group-admin context at the actual AI boundary; preserve private tools and provider execution.
- [ ] Run tool/AI/handler regressions and record result.

### 3. Command dispatch and feature usability
Files: `main.py`, `src/telegram/command_registry.py`, Guardian handler/domain, matching tests.
- [ ] Measure registry-to-handler binding and replay ordinary canonical/alias commands.
- [ ] Reproduce silent `/del` and other alias/dispatcher gaps at the actual handler seam.
- [ ] Fix routing without weakening authorization; unsupported controls must explain their limitation.
- [ ] Replay category/command entry points and verify the whole callback graph.

### 4. Media lifecycle
Files: `src/media/transcoder.py`, job/service policy and tests.
- [ ] Add timeout/cancellation/cleanup tests; run red.
- [ ] Bound FFmpeg process lifetime, terminate/reap on cancellation, preserve safe failure codes.
- [ ] Diagnose observed download error from safe codes; repair reproducible provider/delivery defects.
- [ ] Run media/sticker/attachment pipeline regressions.

### 5. Existing presentation and integration
Files: existing menu, feature-presentation, handler help/result copy and tests.
- [ ] Capture actual menu/help/feature result outputs from replay.
- [ ] Improve existing layout and Turkish messages: clear title, short useful result, relevant actions, consistent back navigation.
- [ ] Remove misleading dead-end actions from primary menus or provide explicit useful guidance.
- [ ] Full suite plus architecture/catalog and bounded integration checks from the exact package.

### 6. Deployment and live verification
Files: release manifest/evidence and operations notes.
- [ ] Freeze/hash source package; independently review changes before production.
- [ ] Verify current release and shared configuration, create rollback and data backups.
- [ ] Stage/build verified images; switch only after preflight.
- [ ] Verify app readiness, queue/worker, safe provider reads and published command/menu contracts; rollback on failure.
- [ ] Record what worked, what remains limited, and only then consider GitHub synchronization.

## Decision ledger

- Baseline: local dbcc3bc is clean and matched production in the preceding audit; refresh before deployment.
- Ruling: begin with existing reproducible defects while the full command replay is constructed; a green import or service status alone cannot complete this task.
- Ruling: unavailable Guardian surfaces are not automatically enabled without implementing and testing their enforcement consumers.
