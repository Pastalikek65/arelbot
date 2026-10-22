# Rave Gate — Recon, Live Run And Risk Model

Target: `film.samildev.com` (Rave App, Telegram Mini App for `@RaveAppBot/raveapp`)
Owner: samildev — a Telegram bot shop on ikas.com, selling the "Film Botu"
product. The Mini App is the service `pixelfilm`.

Date: 2026-10-21
Method: passive surface collection, one non-destructive authenticated battery
run, a routing map, an exposure sweep, and a credential-shape sweep. About a
hundred small requests in total. No flood, no resource exhaustion, no attempt
to take the service down.

## Surface

| Item | Value |
| --- | --- |
| Host | `film.samildev.com` (bare `film.samildev` does not resolve) |
| Address | `46.224.180.197` |
| Server | `nginx` |
| TLS | Let's Encrypt `YE1`, `CN=film.samildev.com`, SAN `DNS:film.samildev.com` |
| Validity | 2026-08-30 → 2026-11-28 |
| Title | `Rave App` |
| Page weight | 2602 bytes, `Cache-Control: no-store` |
| Service identity | `pixelfilm`, version `3.1.0` (leaked by `/healthz`) |
| Bot behind the gate | `@RaveAppBot` (the token supplied for this run belongs to a different bot) |

Other hosts on the same domain, none of them the app:

| Host | Address | Note |
| --- | --- | --- |
| `samildev.com` | `75.2.120.200` | Next.js shop on ikas.com, marketing only |
| `www.samildev.com` | `99.83.189.248`, `75.2.120.200` | same |
| `api.samildev.com` | `193.111.125.92` | answers `404` on every path, TLS handshake fails |

Response headers on `/`:

```
x-content-type-options: nosniff
x-frame-options: SAMEORIGIN
referrer-policy: no-referrer
x-robots-tag: noindex, nofollow, noarchive
cache-control: no-store
```

Not present: `Strict-Transport-Security`, `Content-Security-Policy`.

## Service map

Three distinct answers behind one hostname, which is how the routing was
mapped:

| Response shape | Meaning |
| --- | --- |
| `404 {"error":"Not found"}` (21 bytes, nginx only) | nginx handled the path — dotfiles, `/docs`, `/openapi.json` |
| `404 {"detail":"Not Found"}` (22 bytes) | the FastAPI gate service, path not routed |
| `401 {"error":"Telegram oturumu gerekli"}` + `x-session-required: 1` | the application service, session middleware |
| `403 <h1>Yetkisiz</h1>` | the admin route on the gate service |
| `200 {"ok":true,"verified":false}` | `/api/connect` rejecting a bad `init_data` |

So nginx fronts two upstreams: the FastAPI gate that owns `/`, `/admin` and
`POST /api/connect`, and the application service that owns `/api/*`,
`/healthz` and the static paths.

## Gate architecture

```
GET  /                      -> client-side gate page (no server-side auth on the HTML)
     page loads telegram.org/js/telegram-web-app.js
     reads Telegram.WebApp.initData
     POST /api/connect  { init_data, gate: true }
         server -> validate -> { "ok": true, "verified": true }  -> reload -> app
                                 -> { "ok": true, "verified": false } -> deny panel
```

Two observations from the shipped client:

1. **The deny decision is client-side.** The `Dogrulaniyor...` spinner and the
   `Telegram uzerinden acin` panel are decided in JavaScript. Anyone can skip
   the panel. The only thing that counts is whether `/api/connect` verifies the
   signature itself.
2. **`sessionStorage` holds a one-shot flag.** `pf_gate` is set before the
   POST and cleared only on success, so a failed attempt burns the flag for
   that tab. It is a retry guard, not a security control.

A rejected verification still answers `HTTP 200` with
`{"ok":true,"verified":false}`. `ok` means the request parsed, `verified` means
the signature passed. Any monitoring, WAF rule or log alert has to key on the
body field, not on the status code.

## Live battery run

```powershell
python audit.py --target https://film.samildev.com --ack-authorized --json
```

| ID | Result | Observed |
| --- | --- | --- |
| GATE-01 | PASS | No `init_data` → `verified:false` |
| GATE-02 | PASS | Unsigned payload → `verified:false` |
| GATE-03 | PASS | Payload HMAC-signed with a different bot's token → `verified:false` |
| GATE-04 | PASS | `gate:true` flag alone → `verified:false` |
| GATE-07 | PASS | Malformed input → no 5xx |
| GATE-08 | PASS | 3 MB body → `HTTP 413` (nginx caps it) |
| GATE-09 | PASS | No stack traces or framework internals in error bodies |
| GATE-12 | PASS | Anonymous caller gets the gate page, not the app |
| GATE-13 | PASS | No common API route answers anonymously |
| HTTP-02 | PASS | No arbitrary CORS origin reflection |
| HTTP-03 | PASS | `TRACE` → `405` |
| TLS-01 | PASS | Certificate valid until 2026-11-28 |
| GATE-10 | **FAIL** | Ten consecutive failed verifications, ten `HTTP 200`, no `429`, no lockout |
| GATE-11 | **FAIL** | Same root cause as GATE-10: there is no throttle to bypass |
| HTTP-01 | **FAIL** | `Strict-Transport-Security` and `Content-Security-Policy` both missing |
| GATE-00 | SKIP | Needs the gate bot's own token |
| GATE-05 | SKIP | Needs the gate bot's own token |
| GATE-06 | SKIP | Needs the gate bot's own token |
| GATE-14/15 | SKIP | Needs a genuine captured `init_data` |

## Credential and bypass sweeps

### Session forgery — no deviation

The application answers every unauthenticated request with the same
`401 {"error":"Telegram oturumu gerekli"}`. Tested against `/api/me`: fifteen
cookie names, six forged JWTs signed with the secrets a deployment forgets to
change, `Authorization: Bearer`, `X-Session-Token`, `X-Telegram-Init-Data`,
four query parameters, and a POST body carrying an `init_data`. **Zero
deviations.** The session is opaque, server-side, and not forgeable from the
outside.

### Admin route — no deviation

`/admin` answers a uniform `403 <h1>Yetkisiz</h1>`. Tested: sixteen header
shapes (`X-Admin-Token`, `X-Internal-Token`, `X-Pixelfilm-Key`, `Authorization`
in three forms), seven cookie names, eight query parameters, and five nearby
paths. **Zero deviations** on the route itself. The check is not a guessable
header, cookie or query key.

### Client identity spoofing — no effect

`X-Forwarded-For`, `X-Real-IP`, `X-Original-URL` and `X-Forwarded-Host` set to
loopback and private addresses changed nothing, on both `/admin` and `/api/me`.

### Exposure sweep — nothing found

No `.git/HEAD`, no `.git/config`, no `.env`, no `Dockerfile`, no
`requirements.txt`, no `package.json`, no `backup.sql`, no `dump.sql`, no
`db.sqlite3`, no `nginx_status`, no `server-status`, no `metrics`, no
`actuator`. Dotfile paths are blocked at nginx with its own JSON 404.

## Findings

### F1 — No throttle on failed verifications (medium)

Ten consecutive forged verifications against `/api/connect` were answered with
`HTTP 200` and `verified:false`. Nothing was counted, nothing was locked out,
nothing was delayed. The GATE-10 burst and the GATE-11 identity-rotation burst
both ran unimpeded.

Severity is medium rather than high for a specific reason: the server verifies
an HMAC-SHA256 signature, and guessing an HMAC is not a brute-force problem, so
the missing throttle does not directly hand over the gate. What it does hand
over is everything else that leans on the endpoint being expensive to hammer:
log volume, alerting noise, and the absence of a second barrier if any other
weakness ever appears. It is also the cheapest thing to fix on the list.

### F2 — No HSTS, no CSP (medium)

`Strict-Transport-Security` is missing, so a browser keeps trying plain HTTP on
the first request unless the user manually upgrades. `Content-Security-Policy`
is missing on a page that loads remote script from `telegram.org` and holds
`init_data` in the JavaScript context. Any XSS on that origin inherits the
full WebApp object, `init_data` included.

### F3 — `/healthz` leaks internal service state (low)

`GET /healthz` is public and answers:

```json
{"ok":true,"service":"pixelfilm","version":"3.1.0","providers_known":22,
 "providers_loaded":15,"providers_available":7,"providers_maintenance":8,
 "providers_disabled":7}
```

Service name, exact version, and the live state of every upstream provider.
Useful to an attacker choosing a version to attack and to anyone tracking
maintenance windows. Nothing here is a credential, but none of it needs to be
public either.

### F4 — Authentication failures answer HTTP 200 (low, observation)

`{"ok":true,"verified":false}` with `HTTP 200` for every rejected verification.
Proxy logs, WAF rules and uptime monitors all treat this as success. Anything
watching for brute force has to parse the body.

### F5 — Two upstreams, uneven header hygiene (low, observation)

The gate service and the application service do not apply the same headers.
`/docs` and `/openapi.json` are blocked at nginx, but the FastAPI service
still answers its own `404` shape on unrouted paths, and the admin route
returns HTML while everything else returns JSON. Minor, but it means the
nginx rules and the application rules are maintained separately.

### F6 — Unresolved: freshness window and replay protection (unknown)

`GATE-05` (stale `init_data`) and `GATE-06` (replay) could not be judged. The
token supplied for this run belongs to `@Hahahahhahahahahhahahabot`, not to
`@RaveAppBot`, so the control payload was rejected for the wrong reason and
the battery marked both checks inconclusive rather than passing them on a
technicality. Two ways to close this:

1. Supply the token of the bot that owns the Mini App. Then the control passes
   and both checks become decisive.
2. Capture a real `init_data` from an actual Telegram session and pass it with
   `--captured-init-data`. The captured payload is already old, so replaying it
   tests the freshness window, and sending it twice tests the replay cache —
   no token needed.

One caveat on option 1: Telegram signs Mini App payloads with the bot's
Ed25519 key as well as the HMAC. If the server enforces the `signature`
field, an HMAC-only payload is rejected no matter how correct the HMAC is, and
the control will keep failing. That would make option 2 the reliable path.

### Token verification, two-sided

`verify_token.py` checks the token from both ends, because either end alone
can mislead.

Telegram side, `getMe`:

```json
{"id": 8883735434, "username": "Hahahahhahahahahhahahabot",
 "first_name": "Ayselayselaysel", "has_main_web_app": false}
```

Gate side, `POST /api/connect`:

| Payload | Result |
| --- | --- |
| correctly signed with this token | `{"ok":true,"verified":false}` |
| same payload with one character of the hash changed | `{"ok":true,"verified":false}` |
| empty `init_data` | `{"ok":true,"verified":false}` |

The gate rejects a correctly signed payload from this token, and it rejects a
tampered one the same way. The signature is being compared against a
different bot's token, and the gate does not leak which part of the payload
was wrong — the response is identical in every case, which is good practice.

The bot that owns the Mini App is `@RaveAppBot`; its public Telegram page
carries `Destek & Reklam: @Samildev`, the same developer behind
`samildev.com` and the "Film Botu" product. `has_main_web_app: false` on the
supplied token also rules it out on the Telegram side alone: a bot with no
Main Mini App cannot own this gate.

## Hardening checklist

Server side, in the order that matters:

1. Compute the expected HMAC server-side and compare with
   `hmac.compare_digest`. Never branch on a client-supplied boolean.
2. Validate the `hash` field against the `WebAppData`-derived secret key:
   `HMAC_SHA256(key=HMAC_SHA256("WebAppData", bot_token), msg=data_check_string)`.
   `data_check_string` is the `hash`-excluded parameters, sorted, joined with
   `\n`. If Telegram's Ed25519 `signature` is present, verify that too.
3. Reject `auth_date` older than a short window (300 s is typical).
4. Keep a replay cache keyed on the `hash` value so one signed payload opens
   one session, once.
5. Bind the session to the Telegram user id from `init_data`, and re-read that
   id server-side for every protected route. The gate must not be the only
   place authorisation is checked.
6. Throttle failed verifications on the real peer address as seen after
   nginx's `real_ip` handling, not on a header the caller can rewrite.
7. Return a uniform `401` for every verification failure. Do not distinguish
   "unknown hash" from "stale" in the body.
8. Add `Strict-Transport-Security` and a `Content-Security-Policy` that
   allows `https://telegram.org` for the script and nothing else.
9. Take the provider counts and the service version out of `/healthz`, or put
   the route behind the same session check as the rest of the API.

Client side:

10. The page is a convenience, not the control. Keep the deny panel, but assume
    it is bypassed.
11. Do not log `init_data`. It carries a Telegram user id and display name.

## Operational note

The bot token used for this run was pasted into a chat session. Treat it as
disclosed: rotate it through BotFather, and keep the gate bot's token out of
chat entirely. The battery reads it from `--bot-token` or an environment
variable so it never has to be written into a file or a commit.

## Running the verification

```powershell
cd security/rave_gate_audit

# without the bot token
python audit.py --target https://film.samildev.com --ack-authorized --json

# with the gate bot's own token
python audit.py --target https://film.samildev.com --bot-token <TOKEN> --ack-authorized --json

# with a genuine captured payload
python audit.py --target https://film.samildev.com --captured-file captured.txt --ack-authorized --json
```

The battery sends a few dozen small requests: forged, unsigned, stale and
replayed `init_data`, malformed JSON, one oversized body, one throttle burst.
Nothing destructive, nothing sustained. Exit code `1` means a critical or high
check failed.

Supporting sweeps, all read-only:

```powershell
python path_inventory.py --targets https://film.samildev.com
python auth_probe.py --target https://film.samildev.com
python session_forge.py --target https://film.samildev.com
python admin_probe.py --target https://film.samildev.com
python exposure_sweep.py --target https://film.samildev.com
```
