# Rave Gate Audit

Security test harness for the Rave Telegram Mini App gate.

The gate is a client-side page that forwards Telegram `init_data` to
`POST /api/connect`. This directory contains everything needed to answer one
question the client can never answer on its own:

> Is the authentication actually performed by the server, or does the server
> trust what the browser posts?

## Layout

| File | Role |
| --- | --- |
| `tg_init_data.py` | Reference implementation of the Telegram `init_data` spec: HMAC-SHA256 over `data_check_string`, Ed25519 signature check, freshness window, replay cache. Also signs synthetic payloads with a bot token you supply. |
| `lab_server.py` | Local replica of the gate. Ships every weakness on purpose, switched by `RAVE_VULN`. |
| `lab_index.html` | The gate page the lab serves. |
| `audit.py` | The battery. 21 checks, one verdict each, non-destructive. |
| `test_rave_gate_audit.py` | Proof that the battery detects what it claims to detect. |
| `path_inventory.py` | Fixed candidate path list, GET only, with a body fingerprint per path. |
| `auth_probe.py` | How the API and the admin route answer different credential shapes. |
| `session_forge.py` | Cookie names, forged JWTs, headers and query parameters against the session check. |
| `admin_probe.py` | Credential shapes and header trust on the admin route. |
| `exposure_sweep.py` | Source tree, config and backup files left next to the app. |
| `homepage_scan.py` | Public homepage keywords and outbound links. |

## The checks

| ID | Question | Severity when failing |
| --- | --- | --- |
| GATE-00 | Control: is a freshly signed payload accepted at all? | high |
| GATE-01 | Can `POST /api/connect` be called with no `init_data` at all? | critical |
| GATE-02 | Is a payload with no `hash` accepted? | critical |
| GATE-03 | Is a payload signed with the wrong bot token accepted? | critical |
| GATE-04 | Does the server trust the client-side `gate: true` flag? | critical |
| GATE-05 | Is a validly signed but stale `init_data` accepted? | high |
| GATE-06 | Can one signed `init_data` be replayed? | high |
| GATE-07 | Does malformed input cause a 5xx? | medium |
| GATE-08 | Does a 3 MB body crash the endpoint? | medium |
| GATE-09 | Do error bodies leak stack traces or framework internals? | medium |
| GATE-10 | Is there any throttle on repeated failed verifications? | medium |
| GATE-11 | Can the throttle be walked around by rotating the forwarded client identity? | high |
| GATE-12 | Does an anonymous caller get the app instead of the gate? | high |
| GATE-13 | Which API routes answer an anonymous caller? | info |
| GATE-14 | Is a captured payload refused once it is old? | high |
| GATE-15 | Does a captured payload work a second time? | high |
| HTTP-01 | Are HSTS, CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` set? | medium |
| HTTP-02 | Does CORS reflect arbitrary origins on the auth endpoint? | medium |
| HTTP-03 | Is the `TRACE` method enabled? | low |
| TLS-01 | Is the certificate valid and unexpired? | high |

GATE-00 is the control the rest of the signature checks lean on. Without it a
server that rejects every payload would score a perfect run for staleness and
replay. When the control fails, GATE-05 and GATE-06 are reported as
inconclusive rather than as passes.

GATE-14 and GATE-15 test a genuine `init_data` captured from a real Telegram
session. A captured payload is already old, so replaying it exercises the
freshness window, and sending it twice exercises the replay cache — neither
needs the bot token.

## Run it against the lab

```powershell
# terminal 1
$env:RAVE_VULN="ok"
python -m uvicorn lab_server:app --host 127.0.0.1 --port 8099

# terminal 2
python audit.py --target http://127.0.0.1:8099 --bot-token "123456:TEST-TOKEN-FOR-LOCAL-LAB"
```

The lab token above is a fake used only by the lab. Against a real deployment
you need the real bot token for the signature-level checks (GATE-03, GATE-05,
GATE-06); everything else runs without it.

## Run it against a deployment

```powershell
python audit.py --target https://<host> --bot-token <BOT_TOKEN> --ack-authorized --json
```

`--ack-authorized` is required for any non-local target: it is the switch that
says you own the deployment or have written permission to test it. The
battery is non-destructive — it sends malformed and forged auth payloads, never
a flood, never a resource-exhaustion attempt — but it is still someone else's
box until you say otherwise.

Exit code is `1` when any critical or high check fails, so it drops straight
into CI.

Two optional inputs sharpen the run:

| Flag | What it unlocks |
| --- | --- |
| `--bot-token <TOKEN>` | GATE-00, GATE-03, GATE-05, GATE-06 |
| `--captured-init-data <STR>` / `--captured-file <PATH>` | GATE-14, GATE-15 |

The token is the gate bot's own token. If the Mini App's bot enforces the
Ed25519 `signature` field, an HMAC-only control payload will be rejected and
the staleness and replay checks will be reported as inconclusive — the
captured-payload path is the way around that.

Keep the token in an environment variable, not in a file and not in a commit.

## Lab weaknesses

`RAVE_VULN` takes a comma-separated list:

| Value | Simulated flaw |
| --- | --- |
| `ok` | Correct validation, no findings expected |
| `no_hash_check` | The HMAC is never compared |
| `no_auth_date` | The freshness window is skipped |
| `no_replay` | No replay cache, a signed payload works forever |
| `trust_gate` | The client-side `gate: true` flag is believed |

```powershell
$env:RAVE_VULN="no_hash_check,trust_gate"
python -m uvicorn lab_server:app --port 8099
python audit.py --target http://127.0.0.1:8099
```

## Tests

```powershell
python -m pytest test_rave_gate_audit.py -q
```

Each test flips one weakness on and asserts the matching check flips to FAIL.
If the battery cannot fail, it proves nothing.

## Notes

- This directory is a standalone security tool. It is not wired into the bot's
  `FeatureRegistry`, adds no runtime dependency to `src/`, and imports nothing
  from the bot.
- `init_data` payloads carry a Telegram user id and display name. Treat every
  payload as personal data: keep them out of logs and out of commits.
