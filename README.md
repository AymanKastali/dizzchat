# dizzchat

[![CI](https://github.com/AymanKastali/dizzchat/actions/workflows/ci.yml/badge.svg)](https://github.com/AymanKastali/dizzchat/actions/workflows/ci.yml)

A real-time AI chat backend. Users sign up, open per-conversation WebSocket connections, and
exchange messages with a bundled mock assistant that echoes the message back (`You said: …`) — no
external LLM call.
Messages fan out across API replicas over Redis pub/sub, survive reconnects via sequence-based
replay, and are idempotent per client-supplied key.

**Stack:** Python 3.13 · FastAPI + WebSockets · SQLAlchemy 2.0 async + asyncpg · Alembic ·
PostgreSQL · Redis pub/sub · JWT auth (argon2 password hashing) · Docker Compose.

**Architecture:** a DDD hexagonal modular monolith with two bounded contexts — `identity` and
`messaging` — plus a small shared kernel. Summarised in [Architecture](#architecture) below.

### The docs

| Doc | What's in it |
|---|---|
| **README.md** (you are here) | how to run it, configuration, and the full REST + WebSocket API reference |
| **[SYSTEM_GUIDE.md](./SYSTEM_GUIDE.md)** | how the system behaves — flows, diagrams, schema, guarantees. The source of truth |
| **[ARCHITECTURE.md](./ARCHITECTURE.md)** | why it's shaped this way, and what was ruled out |
| **[NOTES.md](./NOTES.md)** | deliberate scope cuts and self-critique |

> **Start here:** [SYSTEM_GUIDE.md § 8.0 — The whole flow in one picture](./SYSTEM_GUIDE.md#80-the-whole-flow-in-one-picture).
> Six diagrams trace one message end to end — topology, boot, connect + auth, the send pipeline,
> Redis fan-out across both replicas, teardown — with every step cited to code.

---

## Quick start (Docker)

```bash
docker compose up --build
```

This starts four containers:

| Service    | Image               | Host port | Notes                                            |
|------------|---------------------|-----------|--------------------------------------------------|
| `postgres` | `postgres:16-alpine`| 5432      | volume `pgdata`                                  |
| `redis`    | `redis:7-alpine`    | 6379      | pub/sub fan-out backbone                         |
| `api`      | built from `Dockerfile` | 8000  | API replica #1                                   |
| `api2`     | same image          | 8001      | API replica #2 — shares Postgres + Redis         |

Two API replicas run so cross-instance fan-out is exercised out of the box: a message sent to a
socket on `:8000` reaches a socket on `:8001`. Both replicas wait for Postgres and Redis to be
healthy before starting.

**Migrations run automatically on boot** — each replica applies `alembic upgrade head` during
application startup, serialized across replicas by a Postgres advisory lock, so there is no separate
migration step. (See `src/dizzchat/app.py` lifespan → `shared/infrastructure/outbound/migrations.py`
and the advisory lock in `migrations/env.py`.)

Health check:

```bash
curl http://localhost:8000/health   # {"status":"ok"}
```

Then see the whole thing work in one command:

```bash
uv run demo.py
```

That is the [Demo](#demo) below, automated — sign up, open a socket, send, watch the broadcast and
the assistant reply, reconnect on the *other* replica and read history back. Nothing to install.

If you would rather apply migrations yourself, the automatic run is idempotent, so the equivalent
manual command is `uv run alembic upgrade head` (or `docker compose run --rm api alembic upgrade
head`).

---

## Local development

Dependencies and the virtualenv are managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install deps into .venv
cp .env.example .env          # then edit values (see Configuration)
uv run dizzchat               # run the app locally (needs a reachable Postgres + Redis)
```

`dizzchat` is the console entry point (`dizzchat.main:main`), which serves `dizzchat.app:app` with
uvicorn.

### Verify

```bash
uv run pytest                 # tests
uv run ruff check .           # lint
uv run ruff format --check .  # formatting
uv run mypy                   # type check (strict)
```

Tests run against fakes and (for the Redis fan-out integration test) a real `redis:7-alpine`
spun up via [testcontainers](https://testcontainers.com/); that test skips automatically when Docker
or testcontainers is unavailable.

---

## Configuration

Settings load from the environment (or a local `.env`) via pydantic-settings. `docker compose`
injects the container values directly; `.env.example` is the template for local runs.

| Env var                          | Default        | Notes                                                        |
|----------------------------------|----------------|--------------------------------------------------------------|
| `ENVIRONMENT`                    | `development`  |                                                              |
| `LOG_LEVEL`                      | `INFO`         |                                                              |
| `HOST`                           | `0.0.0.0`      |                                                              |
| `PORT`                           | `8000`         |                                                              |
| `DATABASE_URL`                   | *(required)*   | async SQLAlchemy URL, asyncpg driver                         |
| `REDIS_URL`                      | *(required)*   |                                                              |
| `JWT_SECRET_KEY`                 | *(required)*   | HS256 signing key; **min length 32** — a weak key fails fast |
| `JWT_ALGORITHM`                  | `HS256`        |                                                              |
| `ACCESS_TOKEN_TTL_SECONDS`       | `900`          | 15 minutes                                                   |
| `REFRESH_TOKEN_TTL_SECONDS`      | `1209600`      | 14 days                                                      |
| `WS_AUTH_TIMEOUT_SECONDS`        | `5.0`          | time to send the first `auth` frame before close 4401        |
| `WS_RATE_LIMIT_MESSAGES`         | `20`           | inbound frames a user may send per window; `0` disables      |
| `WS_RATE_LIMIT_WINDOW_SECONDS`   | `10`           | the rate-limit window, counted in Redis so it spans replicas |
| `SHUTDOWN_DRAIN_TIMEOUT_SECONDS` | `10.0`         | graceful socket-drain budget on shutdown                     |
| `CORS_ALLOW_ORIGINS`             | `[]`           | JSON array; credentials only for explicit origins, never `*` |

> The `JWT_SECRET_KEY` baked into `docker-compose.yml` is a **dev-only placeholder**. Any non-local
> environment must inject a strong random value, e.g.
> `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

---

## REST API

All request/response bodies are JSON. Authenticated routes expect
`Authorization: Bearer <access_token>`; a missing token returns `401 "missing bearer token"` and an
invalid or expired one returns `401 "invalid or expired access token"`.

### Error responses

Domain and auth failures return a single consistent envelope:

```jsonc
{ "detail": "conversation not found: 8f1c…" }        // 400 · 401 · 403 · 404 · 409 · 422
```

Request-shape failures caught by Pydantic at the boundary return FastAPI's structured validation body,
which names the offending field:

```jsonc
{ "detail": [ { "type": "missing", "loc": ["body", "password"],
                "msg": "Field required" } ] }                // 422
```

Domain errors are mapped to status codes centrally, one handler per error type, so services raise
domain errors and never construct HTTP responses
(`contexts/identity/infrastructure/inbound/api/errors.py`, same pattern in `messaging`).

### Identity — `/auth`

| Method & path      | Auth | Body                              | Response                                                            |
|--------------------|------|-----------------------------------|--------------------------------------------------------------------|
| `POST /auth/signup`| none | `{email, password}`               | `201` `{id, email, created_at}`                                    |
| `POST /auth/login` | none | `{email, password}`               | `200` `{access_token, refresh_token, token_type: "bearer"}`        |
| `POST /auth/refresh`| none | `{refresh_token}`                | `200` `{access_token, refresh_token, token_type: "bearer"}`        |
| `GET  /auth/me`    | yes  | —                                 | `200` `{user_id}`                                                  |

Refresh rotates the token: `/auth/refresh` returns a fresh pair and invalidates the presented
refresh token.

### Messaging — `/conversations`

All routes require a Bearer access token and operate only on conversations the caller **takes part
in**.

| Method & path                          | Body                     | Response                                                     |
|----------------------------------------|--------------------------|--------------------------------------------------------------|
| `POST   /conversations`                | `{title}`                | `201` `ConversationResponse`                                 |
| `GET    /conversations`                | —                        | `200` `[ConversationResponse]`                               |
| `PATCH  /conversations/{id}`           | `{title}`                | `200` `ConversationResponse`                                 |
| `DELETE /conversations/{id}`           | —                        | `204` (soft-delete)                                          |
| `POST   /conversations/{id}/restore`   | —                        | `200` `ConversationResponse` (undo the soft-delete)          |
| `GET    /conversations/{id}/messages`  | — (query params below)   | `200` `MessagePageResponse`                                  |

`GET /conversations/{id}/messages` is cursor-paginated, newest-first:

- Query: `before` (int, ≥ 1, optional cursor) and `limit` (int, default 50, 1–100).
- Response: `{items: [MessageResponse], next_cursor: int | null, has_more: bool}`. When `has_more`
  is true, pass `next_cursor` as the next `before`.

Shapes:

```jsonc
ConversationResponse { id, owner_id, title, created_at, updated_at }
MessageResponse      { id, conversation_id, sender_id, role, content, created_at }
```

`MessageResponse.id` is the message's monotonic sequence number (`seq`). `sender_id` is `null` for
assistant messages.

#### Soft-delete and restore

`DELETE` sets `deleted_at` rather than removing rows; every read filters deleted conversations out,
so a deleted conversation returns `404` from history and disappears from `GET /conversations`. Its
messages and participants are untouched, so `POST /conversations/{id}/restore` brings all of it
back:

- **Owner only.** A participant who isn't the owner gets `403`, as with rename and delete.
- **Idempotent.** Restoring an already-active conversation returns `200` and changes nothing (not
  even `updated_at`), so a retried request is harmless.
- `404` only if the conversation never existed — being soft-deleted is precisely what makes it
  restorable.

### Participants — many users in one conversation

A conversation has an **owner** and a set of **participants**. Every participant may open a socket,
send, and read history; only the owner may rename, delete, or change the membership. The owner is a
participant from the moment the conversation is created, and cannot be removed.

| Method & path                                        | Body        | Who        | Response                          |
|------------------------------------------------------|-------------|------------|-----------------------------------|
| `POST   /conversations/{id}/participants`            | `{email}`   | owner      | `204` (idempotent)                |
| `GET    /conversations/{id}/participants`            | —           | any member | `200` `[{user_id, joined_at}]`    |
| `DELETE /conversations/{id}/participants/{user_id}`  | —           | owner, or the user themselves (leaving) | `204`  |

- The invited `email` must belong to a registered user, otherwise `404`.
- Re-inviting an existing participant is a no-op that still returns `204`, so a client can retry
  safely.
- Removing the owner returns `409`.
- Someone who is not a participant gets `403` on history and participants, and their WebSocket is
  closed with `4403`.

Once two users are participants, **every message either of them sends is broadcast to both** — and
to their sockets on any replica, via Redis. See the [Demo](#demo) for a two-user walkthrough.

---

## WebSocket protocol

Connect per conversation:

```
ws://localhost:8000/ws/conversations/{conversation_id}
```

The envelope is `{"type": ..., "payload": {...}}` for data frames and
`{"type": "error", "error": "<detail>"}` for failures.

### Handshake

1. The client connects and must send an `auth` frame within `WS_AUTH_TIMEOUT_SECONDS` (default 5s).
2. On success the server replies `auth.ok` and begins live delivery.
3. The access token is re-validated on **every** send, so a socket never outlives its token.

### Inbound frames (client → server)

```jsonc
// authenticate the connection (first frame)
{ "type": "auth", "payload": { "token": "<access_token>", "last_seen_seq": null } }

// send a message
{ "type": "message.send",
  "payload": { "content": "hello", "client_message_id": "<uuid or null>" } }
```

### Outbound frames (server → client)

```jsonc
{ "type": "auth.ok" }

{ "type": "message.new",
  "payload": { "id": 42, "conversation_id": "<uuid>", "sender_id": "<uuid or null>",
               "role": "user" | "assistant", "content": "...", "created_at": "<iso8601>",
               "client_message_id": "<uuid or null>" } }

{ "type": "message.ack",
  "payload": { "id": 42, "client_message_id": "<uuid or null>", "created_at": "<iso8601>" } }

{ "type": "error", "error": "<detail>" }
```

`id` is the message `seq` (same value as `MessageResponse.id` over REST).

### Close codes

| Code | Meaning                                                                             |
|------|-------------------------------------------------------------------------------------|
| 4401 | auth failed — timeout, non-JSON, invalid frame, or a bad/expired token (incl. per-send) |
| 4403 | not the conversation owner, or the conversation does not exist                      |
| 1011 | internal error — e.g. the Redis fan-out subscription could not be established (fail closed) |
| 1001 | server shutting down (graceful socket drain)                                        |

Bad JSON, an invalid `message.send` frame, or exceeding the rate limit returns an `error` frame and
keeps the socket open; only auth/ownership failures close it.

### Rate limiting

Each user may send `WS_RATE_LIMIT_MESSAGES` inbound frames per `WS_RATE_LIMIT_WINDOW_SECONDS`
(default **20 per 10s**). Over the limit:

```json
{"type": "error", "error": "rate limit exceeded"}
```

The socket **stays open** — a burst costs you the frame, not the connection, so there is nothing to
reconnect and replay. Details worth knowing:

- **Per user, not per socket or per conversation.** The counter lives in Redis, so the quota holds
  across every socket that user has open *and* across both replicas — reconnecting to `:8001` does
  not buy a fresh allowance.
- **Every inbound frame counts**, including malformed JSON, so unparseable floods aren't free.
- A refused frame is never persisted, never broadcast, and never reaches the mock assistant.
- Set `WS_RATE_LIMIT_MESSAGES=0` to disable the check.
- If Redis is unreachable the limiter **fails open** (allows the frame) and logs a warning — it is a
  protection, not an authorization rule.

### Idempotent send

`client_message_id` is a client-generated UUID and an idempotency key. Re-sending with the same
`client_message_id` returns the existing message's `id` in a `message.ack` — it does **not** store a
second row or re-broadcast, and does not trigger a second assistant reply. Enforced by the
per-conversation unique constraint `uq_messages_conversation_id_client_message_id` (migration 0004).

### Reconnect replay (`last_seen_seq`)

On (re)connect the client may include `last_seen_seq` on the `auth` frame:

- `null` — no replay; load prior history via `GET /conversations/{id}/messages`.
- `0` — full replay of the conversation.
- any N — the server replays messages with `seq > N`, oldest-first, as `message.new` frames.

Replay begins **after** the socket has joined live delivery, so no message can slip through the gap.
The delivery contract is **at-least-once and not ordered at the seam**: a live frame may interleave
ahead of a lower-`seq` replay frame. The client must therefore apply each `seq` **at most once**
(track a seen-set, not a high-water mark) and order by the `id` each frame carries. See
[NOTES.md](./NOTES.md) for the rationale and the exactly-once follow-up.

---

## Architecture

DDD hexagonal modular monolith. Two bounded contexts, each in the same layered shape, plus a shared
kernel:

```
src/dizzchat/
  app.py                     FastAPI factory + lifespan (composition root; runs migrations, wires infra)
  main.py                    uvicorn entry point (console script "dizzchat")
  config.py  logging.py
  contexts/
    identity/                signup / login / refresh / JWT auth
      domain/                user + refresh_token aggregates, value objects, repository ports, errors
      application/           use-case services, DTOs, technical ports
      infrastructure/
        inbound/api/         FastAPI routers, controllers, schemas, dependency wiring
        outbound/            argon2 hasher, JWT service, SQLAlchemy repositories
    messaging/               conversations + messages + realtime delivery
      domain/                conversation + message aggregates, value objects, repository ports, errors
      application/           create/list/rename/delete, history, post_message, message_exchange,
                             replay_messages, ensure_conversation_access; ports.py
      infrastructure/
        inbound/api/         REST routers/controllers + realtime/ (websocket, protocol,
                             connection_manager, conversation_registry, rate_limit, dependencies)
        outbound/            mock assistant, redis fan-out + rate limiter, SQLAlchemy repositories,
                             session-scoped per-message unit-of-work adapters
  shared/                    clock, database/session factory, migration runner, redis client, health
```

**Layering** follows ports-and-adapters: `domain` holds aggregates and repository ports;
`application` holds use-cases and technical ports (`MessageBroadcaster`, `AssistantResponder`,
`MessageWriter`, `MessageReplayer`); `infrastructure` provides the FastAPI/SQLAlchemy/Redis adapters
and the composition root that wires them.

**Cross-replica fan-out (Redis pub/sub):** `MessageExchange` persists and commits the user message,
then broadcasts, then generates the mock assistant reply, persists it, and broadcasts again —
**persist before broadcast**, so a rollback can never surface a message that isn't stored. Broadcast
goes through the `MessageBroadcaster` port; the WebSocket composition injects
`RedisMessageBroadcaster`, which `PUBLISH`es to channel `conv:{conversation_id}`. Each replica runs a
`RedisConversationSubscriber` that decodes the frame and hands it to the local `ConnectionManager`
for delivery. Every replica — the publisher included — delivers only via its own subscriber, so
there is a single uniform delivery path and no double-delivery.

Because a WebSocket outlives any single request, message writes use session-scoped outbound adapters
that open one transaction per message rather than reusing a request-scoped session.

For the same flow as diagrams — client → replica → Postgres → Redis → the other replica → both
clients, step by step — see
[SYSTEM_GUIDE.md § 8.0](./SYSTEM_GUIDE.md#80-the-whole-flow-in-one-picture).

---

## Demo

### Run it — one file, one command

With the stack up, from a second terminal:

```bash
uv run demo.py
```

```
dizzchat demo · the assignment's deliverable 5, end to end
replica A http://localhost:8000   replica B http://localhost:8001

  1  health          localhost:8000 and localhost:8001 both report ok
  2  signup          alice-d90e34d2@demo.dizzchat created (201)
  3  read-your-write login on the same connection, zero delay -> 200
  4  conversation    "demo" created, id 35b7250d-7d1e-4aa8-8131-6cc3968f6f81
  5  connect         auth.ok on localhost:8000
  6  send            ack seq=31 · message.new user + assistant "You said: hello"
  7  two replicas    alice sent on localhost:8000; bob received it on localhost:8001 via Redis
  8  reconnect       last_seen_seq=0 on localhost:8001 replayed seq [31, 32, 33, 34]
  9  history         GET /messages returned 4 messages, newest first
 10  idempotent      same client_message_id -> ack seq=31, history still 4

10/10 steps passed
```

Every step asserts something and the exit code is non-zero if any fails, so it is a smoke test as
well as a demo. There is nothing to install: a [PEP 723](https://peps.python.org/pep-0723/) header
declares its one dependency and `uv run` fetches it into a throwaway environment — no `uv sync`, no
project virtualenv. HTTP goes over a single keep-alive connection on purpose, which is what makes
step 3 meaningful (see [Known issues](#known-issues)). Fresh emails are generated per run, so it is
re-runnable against a live database.

It also ships inside the image, if you would rather not have `uv` on the host:

```bash
docker compose exec -e DIZZCHAT_API_A=api:8000 -e DIZZCHAT_API_B=api2:8000 api python demo.py
```

### Or click through it manually

The same round-trip in [Postman](https://www.postman.com/), which speaks both HTTP and WebSocket —
no extra tooling. Start the stack first (`docker compose up --build`).

### 1. REST — auth and a conversation

Send these as HTTP requests (all bodies are JSON):

1. `POST http://localhost:8000/auth/signup` — body
   `{"email":"demo@example.com","password":"demo-password-123"}`.
2. `POST http://localhost:8000/auth/login` — same body → copy `access_token` from the response.
3. `POST http://localhost:8000/conversations` — header `Authorization: Bearer <access_token>`, body
   `{"title":"demo"}` → copy the conversation `id`.

### 2. WebSocket — send, broadcast, assistant reply

In Postman: **New → WebSocket Request**, URL `ws://localhost:8000/ws/conversations/<id>`.

The server closes the socket if the first `auth` frame doesn't arrive within 5s, so paste the `auth`
frame into the message composer *before* clicking **Connect**, then **Send** it immediately. (Raise
`WS_AUTH_TIMEOUT_SECONDS` in `docker-compose.yml` for a roomier manual demo.)

1. Send the auth frame → expect `{"type":"auth.ok"}`:

   ```json
   {"type":"auth","payload":{"token":"<access_token>"}}
   ```

2. Send a message → expect `message.ack`, then `message.new` (your user message) and `message.new`
   (the assistant's `"You said: hello"`):

   ```json
   {"type":"message.send","payload":{"content":"hello","client_message_id":"11111111-1111-1111-1111-111111111111"}}
   ```

### 3. Idempotent send

Send the same `message.send` frame again (same `client_message_id`) → you get only a `message.ack`;
no new row, no re-broadcast, no second assistant reply.

### 4. Cross-replica reconnect replay (Redis fan-out)

Open a **second** WebSocket Request to the *other* replica,
`ws://localhost:8001/ws/conversations/<id>`, and send an `auth` frame carrying a replay cursor:

```json
{"type":"auth","payload":{"token":"<access_token>","last_seen_seq":0}}
```

After `auth.ok` the server replays the conversation's `message.new` frames. The messages were sent
to replica `:8000` and read back from `:8001` — proving fan-out state is shared across replicas over
Redis.

### 5. Two users in one conversation, one on each replica

This is the multi-user broadcast, end to end. Keep the first user (call them **alice**) and her
conversation from the steps above.

1. Sign a second user up: `POST http://localhost:8000/auth/signup` with
   `{"email":"bob@example.com","password":"demo-password-456"}`, then `POST /auth/login` with the
   same body → copy **bob's** `access_token`.
2. As **alice**, invite bob:
   `POST http://localhost:8000/conversations/<id>/participants` with header
   `Authorization: Bearer <alice_token>` and body `{"email":"bob@example.com"}` → `204`.
3. Confirm the room: `GET http://localhost:8001/conversations/<id>/participants` as either user →
   two entries. `GET http://localhost:8001/conversations` as bob now lists the conversation he was
   invited to.
4. Open **two** WebSocket Requests to the *same* conversation on *different* replicas — alice on
   `ws://localhost:8000/ws/conversations/<id>`, bob on `ws://localhost:8001/ws/conversations/<id>` —
   and send each user's own `auth` frame.
5. Send a `message.send` from alice. **Bob's socket receives `message.new`** with alice's
   `sender_id`, followed by the assistant's reply; alice additionally receives her own
   `message.ack`. Send from bob and alice receives it the same way.

The message crossed users *and* replicas: persisted by `:8000`, published to Redis, delivered to a
socket held by `:8001`.

### 6. Soft-delete, then restore

Still as **alice**, on the conversation from the steps above:

1. `DELETE http://localhost:8000/conversations/<id>` → `204`.
2. `GET http://localhost:8000/conversations` → the conversation is gone;
   `GET /conversations/<id>/messages` → `404`.
3. `POST http://localhost:8000/conversations/<id>/restore` → `200` with the conversation.
4. `GET /conversations` lists it again, and `GET /conversations/<id>/messages` returns **every
   message from before the delete** — soft-delete never removed them.

Repeat step 3 and you still get `200`: restore is idempotent. As **bob** (a participant, not the
owner) it returns `403`.

Two negative checks worth showing: a third user who was never invited gets `403` from
`GET /conversations/<id>/messages` and is closed with `4403` on connect; and bob, a participant but
not the owner, gets `403` from `PATCH /conversations/<id>`.

### 7. Rate limiting, shared across replicas

Set `WS_RATE_LIMIT_MESSAGES: 3` and `WS_RATE_LIMIT_WINDOW_SECONDS: 10` under **both** api services in
`docker-compose.yml`, then `docker compose up --build`.

1. As alice, open one socket on `ws://localhost:8000/ws/conversations/<id>` and send four
   `message.send` frames quickly. The first three behave normally; the fourth returns
   `{"type":"error","error":"rate limit exceeded"}` and the socket stays connected. Wait ten seconds
   and a send succeeds again.
2. **The shared-counter proof:** with alice connected on *both* `:8000` and `:8001`, send two frames
   on each. The fourth is refused even though it is only the second on that replica — the count lives
   in Redis, not in either process.
3. Bob sending at the same time is unaffected: the quota is per user.
4. `GET /conversations/<id>/messages` shows only the accepted sends — a refused frame is never
   persisted.

The full frame and close-code reference is in [WebSocket protocol](#websocket-protocol) above.

---

## Known issues

Nothing outstanding. Every REST route, WebSocket frame, and close code documented above behaves as
described, and `uv run demo.py` asserts the end-to-end path on every run.

One real bug was found and fixed while preparing the handoff, recorded here because it is the kind of
thing this section exists for:

- **REST writes used to be acknowledged before they were durable.** The request-scoped session
  committed in a FastAPI `yield` dependency's teardown, which unwinds *after* the response has been
  sent, so a client that read straight back could be served a snapshot without its own write — an
  intermittent `401 invalid credentials` on sign-up-then-log-in. The transaction boundary is now
  `TransactionalRoute`, which commits before the response goes out; the ordering is asserted at the
  raw ASGI level in `tests/shared/infrastructure/api/test_transactional_route.py`, and `demo.py`
  step 3 exercises it live over one keep-alive connection. Full write-up in
  [NOTES.md](./NOTES.md#rest-writes-were-acknowledged-before-they-were-durable).

Everything in the next section is a deliberate scope decision, not a defect.

---

## Scope & deliberate cuts

Everything in the assignment's four "must build" sections is implemented. The items below are
conscious scope decisions — optional *nice-to-haves*, the untaken bonuses, or documented nuances of
features that already meet spec — not defects. Each production follow-up is recorded in `NOTES.md`.

- **Restore has no time limit and no audit trail.** Both Req-2 nice-to-haves are built —
  soft-delete + restore, and cursor pagination — but a conversation stays restorable forever, and
  nothing records who deleted or restored it. A retention window (purge after N days) and an audit
  log are the production follow-ups.
- **Reconnect replay is at-least-once and unordered at the live/replay seam.** This is the bonus as
  specified ("at-least-once redelivery on reconnect"). Because the socket joins live delivery before
  replay runs (so nothing is missed), a live frame can arrive ahead of a lower-`seq` replay frame.
  The client applies each `seq` at most once (a seen-set) and orders by the `id` each frame carries;
  there is no server-side exactly-once buffering.
- **Replay is unbounded.** `last_seen_seq` replays the full tail with no page/deadline cap, so a
  long-absent client can trigger a large replay. Production would cap it and fall back to the REST
  history endpoint past a threshold.
- **No streamed assistant reply and no typing indicators/presence.** Two of the three Req-3
  nice-to-haves; the third, per-user rate limiting, **is** built (see
  [Rate limiting](#rate-limiting)). The mock returns a single `message.new` (`"You said: …"`) rather
  than token chunks, and there are no presence frames — presence needs cross-replica ephemeral state
  (TTL heartbeats, plus reconciliation when a replica dies holding sockets), which is a larger piece
  of work than it looks.
- **The rate limit is a fixed window, and covers the socket only.** A client can send up to 2× the
  limit back to back across a window boundary; a sliding window would be exact but costs extra Redis
  ops per frame. The REST endpoints are not rate limited — that needs request middleware, which is a
  different mechanism from the socket's per-frame check.
- **The assistant replies to every user message, including in a multi-user room.** With three people
  in a conversation the mock answers each of them, which is noisy. Gating the reply on an `@ai`
  mention is the obvious refinement; see `NOTES.md`.
- **A removed participant keeps *receiving* until they disconnect.** Access is checked once at
  connect, so revoking a membership does not close an already-open socket. Their *sends* are blocked
  immediately (every message re-checks membership), and a reconnect is refused with `4403`. Closing
  live sockets on removal needs a cross-replica revocation signal — recorded in `NOTES.md`, not
  built.
- **One bonus taken — delivery guarantees.** The assignment asks for at most one; no `/metrics`
  (Prometheus), OAuth login, or load-test harness.
- **Correlation ids cover the WebSocket path.** Each connection tags its log lines with a
  `connection_id`; REST requests are not yet correlated.
- **Migrations run automatically on each replica's boot,** serialized by a Postgres advisory lock
  (later starters find the schema at head and no-op). Fine at this scale; a large live table would
  want out-of-band index builds — see `NOTES.md`.
