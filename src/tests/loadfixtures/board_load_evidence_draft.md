# Legacy board load acceptance — evidence draft

Status: **PASS on isolated test MongoDB** (2026-10-22). Telegram transport was
synthetic; this result does not measure Telegram service capacity.

Run only with `APP_ENV=test`, database path `/arel_bot_test`, and MongoDB user
`arel_test`. `scripts/board_load_test.py` creates random `board_load_games_*` and
`board_load_actions_*` collections and drops them in `finally`. It refuses any
other profile before connecting or writing.

The script opens 25 legal tables each of Connect Four, XOX, Gomoku, and chess.
Two service instances share the same MongoDB repository. Each of 100 tables
receives 10 legal domain transitions, and every transition is submitted twice
with one durable action key. Synthetic Telegram calls add bounded latency;
targeted tests inject rate limiting, ambiguous timeout, and deleted canonical
messages, then restart delivery processing. The script requires 1,000 committed
transitions, 1,000 duplicate rejections, no lost moves or stale revisions,
settled panel delivery, and nonzero canonical message IDs.
Two service instances also submit a legal join from the same stale snapshot:
only one write succeeds, and retrying the losing snapshot is rejected by CAS.
It then runs actual `GameRuntime.tick` delegation against 140 additional due
rows: the oldest 100 use a synthetic failed processor, and the next 40 use a
healthy processor. The first batch must be deferred in MongoDB; the next tick
must reach all 40 later rows despite the due query's 100-row limit.

Command (from `C:\Vscode\arelbot`):

```powershell
docker run --rm --network arelbot-gate_default --env-file .env.test -e PYTHON_DOTENV_DISABLED=1 -v C:/Vscode/arelbot:/app -w /app arelbot-gate-test:latest python scripts/board_load_test.py
```

Measured script result:

```json
{"ok": true, "profile": "isolated_synthetic_mongo_and_telegram", "real_telegram_load": false, "tables": 100, "transitions": 1000, "duplicate_rejections": 1000, "failures": 0, "duration_seconds": 22.039, "throughput_transitions_per_second": 45.37, "action_latency_ms": {"p50": 399.08, "p95": 1007.87, "p99": 1065.11}, "synthetic_transport_faults": {"rate_limit": 1, "timeout": 2, "deleted": 1}, "runtime_fairness": {"unhealthy_deferred": 100, "later_healthy_processed": 40, "tick_limit": 100}, "stale_cas_rejections": 1}
```

The two timeout warnings in stdout were intentionally injected and recovered.
Latency and throughput cover the script's full run, including synthetic
transport faults and runtime fairness canary; they are not a Telegram quota
or whole-application capacity measurement.
An authenticated post-run read of `/arel_bot_test` found
`board_load_residual_collections=0`.
Windows-host execution with the same profile previously returned
`ServerSelectionTimeoutError` before a write because `mongo` resolves only in
the Docker test network; that attempt is not counted as load evidence.
