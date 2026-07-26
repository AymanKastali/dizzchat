# Architecture — Real-Time AI Chat Backend (dizzchat)

> **What this document is.** The architectural decisions — why the system is shaped this way, and what
> was ruled out. It's the *why*. For the *how* — flows, diagrams, schema, protocol, guarantees — read
> [SYSTEM_GUIDE.md](./SYSTEM_GUIDE.md); to run it, [README.md](./README.md).

## Context

A real-time chat backend where authenticated users exchange messages in conversations over
WebSockets. Messages persist in Postgres, and delivery fans out across **2+ app replicas** via Redis
pub/sub. The "AI" is a mock that echoes a canned assistant reply — the backend is the focus, not the
model.

**Scope:** all core requirements + the highest-signal nice-to-haves (cursor pagination,
soft-delete + restore, graceful shutdown, cross-instance fan-out test) + **one** bonus: **message delivery guarantees**
(client-`message_id` dedupe + at-least-once redelivery on reconnect). A conversation holds **many
participants**, so a message from any of them is broadcast to all of them, on any replica.

**Toolchain:** `uv` (deps/venv) · `ruff` (lint + format) · `mypy` (types) · `pytest` +
`pytest-asyncio` (tests).

**Constraints held throughout:** no blocking calls in the async event loop; no unauthenticated
WebSocket access; no plaintext passwords or committed secrets; a failed AI/DB call never crashes the
socket or worker; history survives a restart; the README runs on a clean checkout.

---

## The architecture

A **modular monolith** — a single deployable run as N replicas — with **hexagonal layering** and
two bounded contexts. One service scaled horizontally satisfies the "2+ replicas + Redis"
requirement without microservice overhead.

### Bounded contexts / subdomain map
- **Identity** (supporting) — users, signup/login, JWT access + refresh, password hashing.
- **Messaging** (**core**) — conversation CRUD, membership, and message persistence/history **and**
  the realtime layer: the WebSocket endpoint, connection lifecycle, Redis pub/sub fan-out, delivery
  guarantees. Conversations and realtime share the `Conversation`/`Message` aggregates, so they live
  in one context rather than being split behind an artificial boundary. Where the differentiating
  effort goes.
- **The one cross-context dependency** — admitting a participant by email needs Messaging to resolve
  an Identity user. That goes through a `UserDirectory` port implemented by `IdentityUserDirectory`
  in `messaging/infrastructure/outbound/identity/`: an anti-corruption layer that constructs
  Identity's `Email` and hands back a bare `UUID`, so Identity's types never reach Messaging's
  domain or use cases.
- **Shared kernel** (`shared/`) — clock, database/session factory, migration runner, Redis client,
  and the `/health` endpoint: cross-context technical concerns with no domain of their own.

### Layering — hexagonal (ports & adapters), per bounded context

This is **hexagonal**, not classic top-down layering: the dependency arrow points *inward*. The
domain + application core depends on nothing; infrastructure depends on the core by implementing
ports it declares. Swapping Postgres, Redis, or the web framework touches only adapters — never the
domain.

```text
                    ┌─────────────────── inbound ─────────────────────┐
                    │  api/  — FastAPI REST routers, WS endpoint,      │
                    │         Pydantic v2 DTOs, DI wiring              │
                    └───────────────────────┬─────────────────────────┘
                                             │ calls
                    ┌────────────────────────▼─────────────────────────┐
                    │  application/  — use-cases/services orchestrating │
                    │  the domain, and the PORTS (Protocol/ABC          │
                    │  interfaces): UserRepository, MessageRepository,  │
                    │  MessageBroadcaster, TokenService, PasswordHasher │
                    │                                                   │
                    │      domain/  — pure entities, value objects,     │
                    │      invariants. NO framework imports.            │
                    └────────────────────────▲─────────────────────────┘
                                             │ implements ports
                    ┌───────────────────────┴─────────────────────────┐
                    │  infrastructure/ (outbound adapters) —           │
                    │  SQLAlchemy repos, Redis pub/sub, JWT + argon2,  │
                    │  session/pool                                    │
                    └──────────────────────────────────────────────────┘
```

- **Ports** — Protocol/ABC interfaces — are declared in `application`; the domain and use-cases
  depend only on these abstractions.
- **Inbound adapters** (`api`) call the application services.
- **Outbound adapters** (`infrastructure`) implement the ports; `api` wires the concrete
  adapter into each service via FastAPI dependencies (composition root).
- The domain never imports FastAPI / SQLAlchemy / Redis. The dependency rule (everything points
  inward) is the difference from classic layered architecture, where the domain would sit on top of
  and depend on the data-access layer.

### Key technical choices
- **ORM:** SQLAlchemy 2.0 **async** + `asyncpg`, Alembic for migrations. Chosen over SQLModel for a
  mature async story and clean separation of persistence models from Pydantic API DTOs.
- **WebSocket auth — first-message auth.** The client connects, then must send an `auth` frame with
  the JWT within a short timeout; otherwise the server closes with application close code `4401`.
  Keeps tokens out of URLs/access logs (unlike query params), works uniformly across clients, and
  allows a clean rejection close code. Both rejected alternatives (query param,
  `Sec-WebSocket-Protocol` subprotocol) and the cost of this one are argued in
  [NOTES.md § WebSocket auth](./NOTES.md#websocket-auth--why-first-message-auth).
- **Token on every privileged action:** access token validated on every WS connect and re-checked
  for expiry on privileged frames; short-lived access + refresh via REST.
- **JSON message protocol** (envelope `{ "type", "payload" }`; errors are `{ "type": "error", "error" }`):
  - client→server: `auth` (carries the access token + optional `last_seen_seq`), `message.send`
    (carries `content` + optional `client_message_id`).
  - server→client: `auth.ok`, `message.new`, `message.ack` (echoes `client_message_id` + server
    `id` + `created_at`), `error`.
  - History is fetched over REST (`GET /conversations/{id}/messages`), not a WS frame; auth failure
    is signalled by close code `4401`, not an `auth.error` frame. There is no `typing` frame.
- **Redis fan-out** (`redis.asyncio`): each replica runs one subscriber task that (un)subscribes to
  per-conversation channels `conv:{id}` as local sockets join/leave. On a persisted message the
  producing replica `PUBLISH`es to `conv:{id}`; every replica delivers to its *local* sockets. A
  per-replica **`ConnectionManager`** holds `conversation_id → set[WebSocket]` and handles
  register/unregister, dead-socket cleanup, and local broadcast.
- **Delivery guarantees (bonus):**
  - *Idempotent send / dedupe:* messages carry a client `message_id` (UUID); unique constraint on
    `(conversation_id, client_message_id)`. A duplicate send returns the existing server id via
    `message.ack` and is not re-broadcast.
  - *At-least-once redelivery:* the message `bigserial` `id` **is** the monotonic ordering key
    (there is no separate `seq` field on the wire). On (re)connect the client sends `last_seen_seq`;
    the server replays persisted messages with `id > last_seen_seq` for that conversation. Because
    the socket joins live delivery *before* replay runs (so nothing is missed), delivery is
    at-least-once and unordered at the seam — the client applies each `id` at most once (a seen-set)
    and orders by it. Combines naturally with cursor pagination.
- **Rate limiting (Redis, second use):** the same Redis also carries a per-user fixed-window counter,
  `ratelimit:ws:{user_id}:{window}`, incremented for every inbound frame in the receive loop before
  parsing. Because the counter is shared, one user's quota holds across all of their sockets and both
  replicas; over the limit the server replies with an `error` frame and keeps the socket open. The
  port (`realtime/rate_limit.py`) sits beside its consumer rather than in `application/ports.py` — it
  guards the transport, not a use case. It fails **open** if Redis is unreachable, deliberately.
- **Transaction boundaries — two lifecycles, two mechanisms.** REST commits in `TransactionalRoute`,
  a route class that wraps the handler and commits *before* the response is sent; the request-scoped
  `get_session` dependency only publishes the session and rolls back on error. This is not stylistic:
  a `yield` dependency's teardown unwinds after the response has gone out, so committing there
  acknowledges a write before it is durable (see
  [NOTES.md](./NOTES.md#rest-writes-were-acknowledged-before-they-were-durable)). A WebSocket outlives
  any request, so the socket path instead commits one transaction per message in its own outbound
  adapter (`SessionScopedMessageWriter`). Because the REST commit now lives on the router, a router
  wired without `route_class` would silently discard writes — so `create_app` asserts at boot that
  every session-taking route is transactional.
- **Resilience:** mock-AI generation and each DB/Redis call are wrapped so a failure emits an
  `error` frame and is logged — never crashing the socket or worker. The mock reply is an `async`,
  non-blocking responder (no I/O) awaited inline in `MessageExchange`; the non-negotiable is that it
  never blocks the event loop, which inline async satisfies.
- **Graceful shutdown:** uvicorn translates SIGTERM into the lifespan shutdown, whose `finally`
  drains every live socket (close code `1001`), stops the Redis subscriber, closes Redis, and
  disposes the DB pool.
- **Config/secrets:** `pydantic-settings`, `.env` (gitignored) + committed `.env.example`; no
  secrets committed; configurable CORS allowlist.
- **Observability:** structured JSON logging; each WebSocket connection tags its log lines with a
  `connection_id` correlation id, plus a cheap `/health` endpoint. (Full `/metrics` is the
  observability bonus we are *not* taking — noted as a next step.)

### Data model (Alembic-managed)
- `users` (id, email unique, password_hash, created_at)
- `conversations` (id, owner_id, title, created_at, updated_at, deleted_at nullable → soft-delete;
  clearing it is restore, so no data is moved either way)
- `conversation_participants` (conversation_id, user_id, joined_at; composite PK on
  `(conversation_id, user_id)` so a user cannot be admitted twice; indexed on `user_id` for
  "conversations I'm in")
- `messages` (id `bigserial` = the ordering key, conversation_id, sender_id nullable for assistant,
  role `user|assistant`, content, client_message_id nullable, created_at; unique
  `(conversation_id, client_message_id)`)
- refresh tokens: store hashed token / jti for rotation + revocation.

### Access control — two levels, one aggregate
`conversations.owner_id` names the administrator; `conversation_participants` names who may take part.
`Conversation` enforces both itself: `ensure_participant` gates joining the live channel, sending,
and reading history, while `ensure_owned_by` gates rename, delete, restore, and membership changes.
The owner
is seeded as a participant by `Conversation.start` and cannot be removed, so a conversation can never
end up with an owner locked out of it. Membership is re-checked on **every** send, not only at
connect, so revoking it stops a user posting immediately.

### Alternatives considered (ruled out)
- **Microservices (auth / chat / gateway):** operational overhead unjustified; monolith-as-replicas
  meets the horizontal-scale requirement directly.
- **In-process pub/sub only (no Redis):** fails the explicit 2+ replica fan-out requirement.
- **SQLModel instead of SQLAlchemy 2.0:** thinner async story; we want explicit persistence ↔ DTO
  separation.
- **Auth token via query param (primary):** ends up in access logs; kept only as a documented
  fallback.
- **Self-service conversation join** (any authenticated user joins any conversation whose id they
  know): cheapest way to get multiple users into a room, but it reduces authorization to a capability
  URL. Membership is owner-granted by email instead — argued in
  [NOTES.md § Multi-user conversations](./NOTES.md#multi-user-conversations--the-reading-i-changed-my-mind-about).
- **Sliding-window rate limiting** (a Redis sorted set trimmed on every frame): exact, with no
  boundary burst, but it costs extra round trips and per-request cleanup for precision a 20-per-10s
  limit doesn't need. A fixed-window `INCR` on a self-expiring key was chosen instead; the 2×-across-a-
  boundary flaw is stated in [NOTES.md](./NOTES.md#deliberate-decisions).
- **Rate limiting inside `MessageExchange`:** would place the rule in the core use case, but malformed
  frames never reach a use case, so garbage floods could not be counted. The check sits in the socket
  receive loop instead.
- **Committing in each write controller** rather than in a route class: more obvious at the call site,
  but it is eight edits and a ninth endpoint would silently reinherit the acknowledge-before-durable
  bug. `route_class` covers every route on the router, including ones added later.

---

## Build order (slices)

The system was built in seven slices, each independently testable and each leaving the app runnable.

1. **Scaffold + tooling.** `uv` project, `pyproject.toml`, ruff/mypy/pytest config, package skeleton
   (layered per context: identity + messaging, plus a shared kernel), `docker-compose.yml`
   (api ×2, postgres, redis), Dockerfile, `.env.example`, `.gitignore`, structured logging +
   `pydantic-settings` config, `/health`. Verify: `docker compose up` boots; `/health` 200.
2. **Identity.** User model + migration, argon2 hashing, signup/login REST, JWT access+refresh
   issue/validate/refresh, auth dependency. Tests: signup→login→validate; password never plaintext.
3. **Conversations.** Conversation + Message models + migration, repositories, REST
   create/list/rename/delete (+ soft-delete/restore), cursor-paginated history. Tests: CRUD +
   pagination + persistence survives restart.
4. **Realtime core.** WS endpoint per conversation, first-message auth (+ `4401` close),
   `ConnectionManager`, JSON protocol, persist→broadcast→mock-reply→broadcast, per-socket error
   handling, disconnect/dead-socket cleanup. Tests: send→persist→broadcast; auth rejection;
   failed AI/DB does not kill the socket.
5. **Redis fan-out + graceful shutdown.** Per-replica subscriber, dynamic channel (un)subscribe,
   publish on persist, SIGTERM drain. Tests: **cross-instance fan-out** (a message published by one
   manager reaches a socket on another sharing one Redis) — the strong-signal test.
6. **Delivery guarantees (bonus).** Client `message_id` dedupe (unique constraint + idempotent ack),
   `last_seen_seq` reconnect replay. Tests: duplicate send → one row + ack; reconnect with stale
   `last_seen_seq` → missed messages replayed exactly, in order.
7. **Deliverables + hardening.** `README.md` (setup/run/test + honest "what doesn't work"),
   `NOTES.md` (structure, WS-auth + protocol rationale, next steps, self-critique), a documented
   Postman demo sequence, and `demo.py` — one file, one command, asserting the whole sequence
   (signup → socket → send → broadcast + assistant reply → cross-replica fan-out → reconnect replay →
   history → idempotent resend). Auditing the deliverables against the brief is also what surfaced and
   fixed the acknowledge-before-durable bug. Full ruff/mypy/pytest green.

---

## Verify (end-to-end)

- **Deps/build:** `uv sync` · `docker compose build`
- **Run:** `docker compose up` (api ×2 + postgres + redis; migrations auto-run on start)
- **Test:** `uv run pytest` (unit + integration; Postgres/Redis via testcontainers or the compose)
- **Lint:** `uv run ruff check .` · **Format:** `uv run ruff format --check .`
- **Types:** `uv run mypy` (strict; config-driven)
- **End-to-end proof:** `uv run demo.py` against a live stack — ten asserted steps including the
  two-replica Redis fan-out, with the same sequence available to click through in Postman
  ([README](./README.md#demo)).

**Definition of done, met:** clean checkout → `docker compose up` brings the stack live; a client can
sign up, open a socket, send a message, see the broadcast + mock assistant reply, reconnect and see
history (including messages missed while disconnected); a second replica receives messages via
Redis; ruff + mypy + pytest all green; no secret committed.

---

## Two decisions resolved during the build

- **Test infra for Postgres/Redis** — the choice was testcontainers vs reusing the compose services.
  **Testcontainers won**, for a self-contained suite: one real `redis:7-alpine` backs the fan-out
  integration test and skips automatically when Docker is unavailable. Everything else runs on
  in-memory fakes, so the suite stays fast and needs no network.
- **Two-replica ergonomics** — `docker compose up --scale api=2` vs two explicit services. **Two
  explicit services** (`api` :8000, `api2` :8001) won, because fixed distinct ports make the
  cross-replica demo copy-pasteable; `--scale` remains the alternative for real deployments.
