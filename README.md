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
`messaging` — plus a small shared kernel. See [Architecture](#architecture) below, or
[SYSTEM_GUIDE.md](./SYSTEM_GUIDE.md) for a full deep-dive (design, decisions, flows, testing).

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

All routes require a Bearer access token and operate only on the caller's own conversations.

| Method & path                          | Body                     | Response                                                     |
|----------------------------------------|--------------------------|--------------------------------------------------------------|
| `POST   /conversations`                | `{title}`                | `201` `ConversationResponse`                                 |
| `GET    /conversations`                | —                        | `200` `[ConversationResponse]`                               |
| `PATCH  /conversations/{id}`           | `{title}`                | `200` `ConversationResponse`                                 |
| `DELETE /conversations/{id}`           | —                        | `204` (soft-delete)                                          |
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

Bad JSON or an invalid `message.send` frame returns an `error` frame and keeps the socket open; only
auth/ownership failures close it.

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
                             connection_manager, conversation_registry, dependencies)
        outbound/            mock assistant, redis fan-out, SQLAlchemy repositories,
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

---

## Demo

A full round-trip using `curl` and [`websocat`](https://github.com/vi/websocat):

```bash
# 1. Sign up and log in.
curl -sX POST localhost:8000/auth/signup \
  -H 'content-type: application/json' \
  -d '{"email":"demo@example.com","password":"<a-demo-password>"}'

TOKEN=$(curl -sX POST localhost:8000/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"demo@example.com","password":"<a-demo-password>"}' | jq -r .access_token)

# 2. Create a conversation.
CID=$(curl -sX POST localhost:8000/conversations \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"title":"demo"}' | jq -r .id)

# 3. Open the socket, authenticate, and send a message.
#    Expect: auth.ok, then message.ack + message.new(user) + message.new(assistant).
{ echo "{\"type\":\"auth\",\"payload\":{\"token\":\"$TOKEN\"}}";
  echo '{"type":"message.send","payload":{"content":"hello","client_message_id":"11111111-1111-1111-1111-111111111111"}}';
  sleep 2; } | websocat "ws://localhost:8000/ws/conversations/$CID"
```

To see **idempotent send**, send the same `client_message_id` twice — the second yields only a
`message.ack` (no new row, no re-broadcast). To see **reconnect replay**, reconnect with
`"last_seen_seq": 0` in the `auth` payload and observe the missed `message.new` frames replayed in
order — including cross-replica, by connecting the second time to `ws://localhost:8001`.

---

## Scope & deliberate cuts

Everything in the assignment's four "must build" sections is implemented. The items below are
conscious scope decisions — optional *nice-to-haves*, the untaken bonuses, or documented nuances of
features that already meet spec — not defects. Each production follow-up is recorded in `NOTES.md`.

- **No conversation restore.** Delete is a soft-delete (`deleted_at`); there is no restore endpoint,
  and reads filter deleted rows out. (Soft-delete and cursor pagination — the two Req-2
  nice-to-haves — *are* built.)
- **Reconnect replay is at-least-once and unordered at the live/replay seam.** This is the bonus as
  specified ("at-least-once redelivery on reconnect"). Because the socket joins live delivery before
  replay runs (so nothing is missed), a live frame can arrive ahead of a lower-`seq` replay frame.
  The client applies each `seq` at most once (a seen-set) and orders by the `id` each frame carries;
  there is no server-side exactly-once buffering.
- **Replay is unbounded.** `last_seen_seq` replays the full tail with no page/deadline cap, so a
  long-absent client can trigger a large replay. Production would cap it and fall back to the REST
  history endpoint past a threshold.
- **No streamed assistant reply, typing indicators/presence, or per-user rate limiting.** All three
  are optional Req-3 nice-to-haves; the mock returns a single `message.new` (`"You said: …"`).
- **One bonus taken — delivery guarantees.** The assignment asks for at most one; no `/metrics`
  (Prometheus), OAuth login, or load-test harness.
- **Correlation ids cover the WebSocket path.** Each connection tags its log lines with a
  `connection_id`; REST requests are not yet correlated.
- **Migrations run automatically on each replica's boot,** serialized by a Postgres advisory lock
  (later starters find the schema at head and no-op). Fine at this scale; a large live table would
  want out-of-band index builds — see `NOTES.md`.
