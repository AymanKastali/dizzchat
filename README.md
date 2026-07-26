# dizzchat

[![CI](https://github.com/AymanKastali/dizzchat/actions/workflows/ci.yml/badge.svg)](https://github.com/AymanKastali/dizzchat/actions/workflows/ci.yml)

A real-time AI chat backend. Users sign up, open a per-conversation WebSocket, and exchange messages
with a bundled mock assistant that echoes the message back (`You said: …`) — no external LLM, no API
key. Messages persist to Postgres, fan out across API replicas over Redis pub/sub, survive reconnects
via sequence-based replay, and are idempotent per client-supplied key.

**Stack:** Python 3.13 · FastAPI + WebSockets · SQLAlchemy 2.0 async + asyncpg · Alembic ·
PostgreSQL · Redis pub/sub · JWT auth (argon2 password hashing) · Docker Compose.

This README is **set up, run, test, and [what doesn't work yet](#what-doesnt-work-yet)**. For
everything else:

| Doc | What's in it |
|---|---|
| **[SYSTEM_GUIDE.md](./SYSTEM_GUIDE.md)** | how the system behaves — flows, diagrams, schema, guarantees, the full API + protocol reference. The source of truth |
| **[ARCHITECTURE.md](./ARCHITECTURE.md)** | why it's shaped this way, and what was ruled out |
| **[NOTES.md](./NOTES.md)** | how it's structured, the WebSocket auth + message protocol decisions, and the self-critique |

> Want the whole picture first? [SYSTEM_GUIDE.md § 5](./SYSTEM_GUIDE.md#5-how-a-message-flows)
> traces one message end to end in six diagrams — topology, boot, connect + auth, the send pipeline,
> Redis fan-out across both replicas, teardown — every step cited to code.

---

## Set up

**Prerequisites:** Docker with Compose. That's all — no local Python, no API key, no LLM account.

```bash
git clone https://github.com/AymanKastali/dizzchat.git
cd dizzchat
docker compose up --build
```

Four containers come up:

| Service    | Image                   | Host port | Notes                                    |
|------------|-------------------------|-----------|------------------------------------------|
| `postgres` | `postgres:16-alpine`    | 5432      | volume `pgdata`; `pg_isready` healthcheck |
| `redis`    | `redis:7-alpine`        | 6379      | pub/sub fan-out backbone                 |
| `api`      | built from `Dockerfile` | 8000      | API replica #1                           |
| `api2`     | same image              | 8001      | API replica #2 — shares Postgres + Redis |

Two replicas run so cross-instance fan-out is exercised out of the box: a message sent to a socket on
`:8000` reaches a socket on `:8001`. Both wait for Postgres and Redis to report healthy before
starting.

**Migrations run automatically.** Each replica applies `alembic upgrade head` during application
startup, serialized across replicas by a Postgres advisory lock — no separate migration step, no
command to remember. (`app.py` lifespan → `shared/infrastructure/outbound/migrations.py`; the lock is
in `migrations/env.py`.)

**The mock assistant is bundled** — `messaging/infrastructure/outbound/assistant/mock_assistant_responder.py`
returns `You said: <your message>`. Nothing to install or start separately.

Confirm both replicas are live:

```bash
curl http://localhost:8000/health   # {"status":"ok"}
curl http://localhost:8001/health   # {"status":"ok"}
```

### Without Docker

Needs Python 3.13+, [uv](https://docs.astral.sh/uv/), and a reachable Postgres + Redis.

```bash
uv sync                       # install deps into .venv
cp .env.example .env          # then edit DATABASE_URL / REDIS_URL / JWT_SECRET_KEY
uv run dizzchat               # console entry point → serves dizzchat.app:app with uvicorn
```

Migrations still run on startup, so a fresh database needs no extra step.

---

## Configuration

Settings load from the environment (or a local `.env`) via pydantic-settings. `docker compose`
injects the container values directly; `.env.example` is the template for local runs.

| Env var                          | Default        | Notes                                                        |
|----------------------------------|----------------|--------------------------------------------------------------|
| `ENVIRONMENT`                    | `development`  |                                                              |
| `LOG_LEVEL`                      | `INFO`         |                                                              |
| `HOST` / `PORT`                  | `0.0.0.0` / `8000` |                                                          |
| `DATABASE_URL`                   | *(required)*   | async SQLAlchemy URL, asyncpg driver                         |
| `REDIS_URL`                      | *(required)*   |                                                              |
| `JWT_SECRET_KEY`                 | *(required)*   | HS256 signing key; **min length 32** — a weak key fails fast |
| `JWT_ALGORITHM`                  | `HS256`        |                                                              |
| `ACCESS_TOKEN_TTL_SECONDS`       | `900`          | 15 minutes                                                   |
| `REFRESH_TOKEN_TTL_SECONDS`      | `1209600`      | 14 days                                                      |
| `WS_AUTH_TIMEOUT_SECONDS`        | `5.0`          | time to send the first `auth` frame before close `4401`       |
| `WS_RATE_LIMIT_MESSAGES`         | `20`           | inbound frames a user may send per window; `0` disables       |
| `WS_RATE_LIMIT_WINDOW_SECONDS`   | `10`           | the window, counted in Redis so the quota spans replicas      |
| `SHUTDOWN_DRAIN_TIMEOUT_SECONDS` | `10.0`         | graceful socket-drain budget on shutdown                      |
| `CORS_ALLOW_ORIGINS`             | `[]`           | JSON array; credentials only for explicit origins, never `*`  |

> The `JWT_SECRET_KEY` baked into `docker-compose.yml` is a **dev-only placeholder**. Any non-local
> environment must inject a strong random value, e.g.
> `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

---

## Demo

One command proves the whole flow against the real stack — no manual steps, no extra tooling:

```bash
uv run scripts/demo.py
```

It starts the stack itself (`docker compose up -d --build`), waits for both replicas to report
healthy, then walks the flow end to end and prints a labelled transcript of every request, every
WebSocket frame, and a `PASS`/`FAIL` per claim. It **exits non-zero if any check fails**, so the run
can be trusted unattended.

| Step | What it proves |
|---|---|
| 1 · sign up + log in two users | signup, login, and bearer-authenticated requests |
| 2 · create a conversation, invite the second user | ownership and the participant list |
| 3 · open one socket per user, **on different replicas** | the first-frame `auth` handshake |
| 4 · send one message | persist → broadcast → mock assistant reply |
| 5 · check the second user's socket | **the message crossed replicas via Redis** — stored by `:8000`, delivered by `:8001` |
| 6 · re-send the identical frame | `client_message_id` idempotency: one `ack`, no second row, no re-broadcast |
| 7 · reconnect on the *other* replica with `last_seen_seq: 0` | replay + REST history agree — state is in Postgres, not in a process |
| 8 · flood one user across both replicas | the rate-limit quota is shared in Redis (reported as a warning, never a hard failure) |

Useful flags:

```bash
uv run scripts/demo.py --no-up        # the stack is already running; skip `compose up`
uv run scripts/demo.py --down-after   # `docker compose down -v` when finished
uv run scripts/demo.py --base-a http://host:8000 --base-b http://host:8001
```

The script is a deliberate **black-box client**: it never imports `dizzchat`, only speaks the
documented HTTP and WebSocket protocol, so a pass means the running API works rather than that the
test doubles agree with each other. It asserts on the *set* of frames each socket received, never on
their order — `message.new` is written by the Redis subscriber task while `message.ack` is written by
the request task, so their relative order is genuinely non-deterministic (see
[SYSTEM_GUIDE.md § 5](./SYSTEM_GUIDE.md#5-how-a-message-flows)).

---

## Test

### Automated

```bash
uv run pytest                 # tests
uv run ruff check .           # lint
uv run ruff format --check .  # formatting
uv run mypy                   # type check (strict)
```

CI runs the identical set via `uv run pre-commit run --all-files`.

190 tests across 35 files, mirroring the source layout. Most run against in-memory fakes
(`tests/contexts/*/fakes.py`), so they need no database or network. Two suites use a real
`redis:7-alpine` spun up via [testcontainers](https://testcontainers.com/):

- `test_redis_fanout_integration.py` stands up **two independent replicas** on one Redis and asserts
  a message published from replica A reaches a socket on replica B *and* loops back to A's own socket
  through the same subscribe path.
- `test_redis_rate_limiter.py` proves two limiter instances share one quota — the cross-replica claim
  a fake could not make.

Both **skip automatically** when Docker or testcontainers is unavailable.

### By hand (Postman)

The same walkthrough clicked through manually, if you want to poke at the API yourself rather than
run [the demo script](#demo).

[Postman](https://www.postman.com/) speaks both HTTP and WebSocket, so no extra tooling is needed.
Start the stack first (`docker compose up --build`).

**1 · Sign up, log in, create a conversation.** Three HTTP requests:

1. `POST http://localhost:8000/auth/signup` — `{"email":"alice@example.com","password":"demo-password-123"}`
2. `POST http://localhost:8000/auth/login` — same body → copy `access_token`
3. `POST http://localhost:8000/conversations` — header `Authorization: Bearer <access_token>`, body
   `{"title":"demo"}` → copy the conversation `id`

**2 · Send a message and see the broadcast + reply.** New → WebSocket Request,
`ws://localhost:8000/ws/conversations/<id>`. The server closes the socket if the `auth` frame doesn't
arrive within 5s, so paste it into the composer *before* clicking **Connect**, then **Send**
immediately. (Raise `WS_AUTH_TIMEOUT_SECONDS` in `docker-compose.yml` for a roomier demo.)

```json
{"type":"auth","payload":{"token":"<access_token>"}}
```

→ `{"type":"auth.ok"}`. Then send a message:

```json
{"type":"message.send","payload":{"content":"hello","client_message_id":"11111111-1111-1111-1111-111111111111"}}
```

→ `message.ack`, then `message.new` (your message) and `message.new` (`"You said: hello"`).

**3 · Idempotent send.** Send that exact frame again — you get only a `message.ack`. No second row,
no re-broadcast, no second assistant reply.

**4 · Reconnect replay across replicas.** Open a second WebSocket Request to the *other* replica,
`ws://localhost:8001/ws/conversations/<id>`, with a replay cursor:

```json
{"type":"auth","payload":{"token":"<access_token>","last_seen_seq":0}}
```

After `auth.ok` the server replays the conversation as `message.new` frames. The messages were sent
to `:8000` and read back from `:8001` — state is shared, not per-process.

**5 · Two users, one on each replica.** Sign up `bob@example.com`, log in, copy his token. As alice,
`POST /conversations/<id>/participants` with `{"email":"bob@example.com"}` → `204`. Open two sockets
to the same conversation on *different* replicas (alice on `:8000`, bob on `:8001`), each with its own
`auth` frame. Send from alice: **bob's socket receives `message.new`** with alice's `sender_id`,
followed by the assistant's reply; alice additionally gets her `message.ack`. It works the same way
from bob. The message crossed both users *and* replicas — persisted by `:8000`, published to Redis,
delivered by `:8001`.

**6 · Soft-delete, then restore.** As alice: `DELETE /conversations/<id>` → `204`; the conversation
vanishes from `GET /conversations` and its history returns `404`. `POST /conversations/<id>/restore` →
`200`, and `GET /conversations/<id>/messages` returns **every message from before the delete**.
Repeat the restore and you still get `200` — it's idempotent. As bob (a participant, not the owner)
it returns `403`, as does `PATCH /conversations/<id>`. A third user who was never invited gets `403`
from history and is closed with `4403` on connect.

**7 · Rate limiting, shared across replicas.** Set `WS_RATE_LIMIT_MESSAGES: 3` under **both** api
services in `docker-compose.yml`, then `docker compose up --build`. Send four frames quickly on one
socket: the fourth returns `{"type":"error","error":"rate limit exceeded"}` and the socket stays
connected. Now connect alice on *both* `:8000` and `:8001` and send two frames on each — the fourth
is still refused, even though it's only the second on that replica. The counter lives in Redis, not
in either process. Bob is unaffected: the quota is per user. Refused frames never appear in
`GET /conversations/<id>/messages`.

---

## API surface

Enough to drive the walkthrough above. Full reference — request/response shapes, status-code mapping,
per-frame semantics — in [SYSTEM_GUIDE.md § 6](./SYSTEM_GUIDE.md#6-rest-endpoints) and
[§ 7](./SYSTEM_GUIDE.md#7-websocket-protocol).

**REST.** All bodies JSON. Authenticated routes take `Authorization: Bearer <access_token>`; a
missing token is `401 "missing bearer token"`, an invalid or expired one
`401 "invalid or expired access token"`. Domain failures return `{"detail": "..."}`; request-shape
failures return FastAPI's structured `422` body naming the offending field.

| Method & path | Who | Notes |
|---|---|---|
| `POST /auth/signup` | anyone | `{email, password}` → `201 {id, email, created_at}` |
| `POST /auth/login` | anyone | → `{access_token, refresh_token, token_type}` |
| `POST /auth/refresh` | anyone | `{refresh_token}` → a rotated pair; the presented token is invalidated |
| `GET /auth/me` | any user | → `{user_id}` |
| `POST /conversations` | any user | `{title}` → `201` |
| `GET /conversations` | any user | conversations you own or were invited to |
| `PATCH /conversations/{id}` | owner | `{title}` |
| `DELETE /conversations/{id}` | owner | `204` — soft-delete, rows kept |
| `POST /conversations/{id}/restore` | owner | undo the soft-delete; idempotent |
| `GET /conversations/{id}/messages` | participant | cursor-paginated, newest-first: `before` (int ≥ 1), `limit` (1–100, default 50) → `{items, next_cursor, has_more}` |
| `POST /conversations/{id}/participants` | owner | `{email}` of a registered user → `204`, idempotent |
| `GET /conversations/{id}/participants` | participant | → `[{user_id, joined_at}]` |
| `DELETE /conversations/{id}/participants/{user_id}` | owner, or yourself (leaving) | `204`; removing the owner is `409` |
| `GET /health` | anyone | `{"status":"ok"}` |

A message's `id` **is** its monotonic sequence number (`seq`); `sender_id` is `null` for assistant
messages. Idempotent send is enforced by the per-conversation unique constraint
`uq_messages_conversation_id_client_message_id`.

**WebSocket.** `ws://localhost:8000/ws/conversations/{conversation_id}`. Envelope is
`{"type": ..., "payload": {...}}` for data and `{"type": "error", "error": "<detail>"}` for failures.

| | |
|---|---|
| Inbound | `auth` (first frame, within `WS_AUTH_TIMEOUT_SECONDS`; carries the token and optional `last_seen_seq`) · `message.send` (`content`, optional `client_message_id`) |
| Outbound | `auth.ok` · `message.new` (the broadcast every subscriber gets) · `message.ack` (confirms *your* send, echoing `client_message_id`) · `error` |
| Close codes | `4401` auth failed — timeout, non-JSON, invalid frame, bad or expired token · `4403` not a participant, or no such conversation · `1011` internal, e.g. the Redis fan-out subscription could not be established (fail closed) · `1001` graceful shutdown drain |

Bad JSON, an invalid `message.send`, or exceeding the rate limit returns an `error` frame and **keeps
the socket open** — a burst costs you the frame, not the connection. Only auth and access failures
close it. The access token is re-validated and conversation membership re-checked on **every** send,
so a socket never outlives its credential or its permission.

`last_seen_seq` on the `auth` frame controls replay: `null` — none, load history over REST; `0` — the
full conversation; any *N* — messages with `seq > N`, oldest-first, as `message.new` frames. Read the
delivery contract under [What doesn't work yet](#what-doesnt-work-yet) before writing a client.

---

## What doesn't work yet

Everything in the assignment's four "must build" sections is implemented, plus three optional
nice-to-haves — soft-delete + restore, cursor-based pagination, and per-user rate limiting via Redis.
The one bonus taken is **message delivery guarantees**. What follows is what is missing or rough.

**Not built**

- **Typing indicators / presence.** No presence frames at all. It needs cross-replica ephemeral state
  — TTL heartbeats plus reconciliation when a replica dies holding sockets.
- **Streamed assistant reply.** The mock returns one whole `message.new`, not token chunks over the
  socket.
- **`/metrics` (Prometheus), OAuth login, and load-test numbers** — the three untaken bonuses. The
  brief allows one.

**Built, with limits worth knowing**

- **Reconnect replay is at-least-once and not ordered at the live/replay seam.** The socket joins live
  delivery *before* replay runs, so nothing is missed — but a live frame can arrive ahead of a
  lower-`seq` replay frame. A client must apply each `seq` **at most once** (track a seen-set, not a
  high-water mark, which would drop the later lower-`seq` frames) and order by the `id` each frame
  carries. There is no server-side exactly-once buffering.
- **Replay is unbounded.** `last_seen_seq` replays the whole tail with no page or deadline cap, so a
  long-absent client can trigger an arbitrarily large replay on one socket.
- **The rate limit is a fixed window**, so a client that spends its quota at the end of one window and
  the next window's immediately can send 2× the limit back to back. It counts inbound socket frames
  only.
- **The rate limiter fails open.** If Redis is unreachable the frame is allowed and a warning logged —
  it is a protection, not an authorization rule.
- **Redis publish is fire-and-forget.** During a Redis outage a message is committed to Postgres but
  never fanned out live, and a replica dying drops the sockets it held. Recovery is reconnect and
  refetch either way — Postgres is the source of truth, Redis only the fan-out.
- **A removed participant keeps *receiving* until they disconnect.** Access is checked once at
  connect. Their sends are refused immediately and a reconnect is closed with `4403`, but the
  already-open socket is not.

**Setup caveats**

- `.env.example` omits `WS_AUTH_TIMEOUT_SECONDS`, `WS_RATE_LIMIT_MESSAGES`,
  `WS_RATE_LIMIT_WINDOW_SECONDS`, and `SHUTDOWN_DRAIN_TIMEOUT_SECONDS`. They fall back to the
  defaults listed under [Configuration](#configuration) unless you set them explicitly.
- The two testcontainers suites **skip silently** without a reachable Docker daemon, so a green
  `uv run pytest` on a machine without Docker has not proven Redis fan-out.
