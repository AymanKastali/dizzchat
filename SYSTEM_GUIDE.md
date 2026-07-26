# dizzchat — System Guide

> **What this document is.** A single, self-contained deep-dive into how dizzchat actually works —
> system design, decisions, tools, tech stack, and codebase — written against the **code that
> shipped**, not an earlier plan. Read this end to end and you should be able to answer essentially
> any question about the app.
>
> **Where to start.** Read [§8.0](#80-the-whole-flow-in-one-picture) first — six diagrams that trace
> one message end to end. That gives you the shape of the system in a couple of minutes; everything
> else here fills it in. [§16](#16-glossary--quick-answer-index) is a "where do I look to answer X"
> index if you'd rather jump straight to a topic.
>
> **The four docs.** [`README.md`](./README.md) — how to run it, plus the REST + WebSocket API
> reference. [`ARCHITECTURE.md`](./ARCHITECTURE.md) — the decisions and what was ruled out (the *why*).
> **This guide** — how the system actually behaves (the *how*), and the source of truth where the docs
> disagree. [`NOTES.md`](./NOTES.md) — deliberate scope cuts and self-critique. Where a detail matters
> here, it cites the real file as `path:line` so you can jump straight to the code.

## Contents

1. [What dizzchat is](#1-what-dizzchat-is)
2. [Tech stack & why each choice](#2-tech-stack--why-each-choice)
3. [Run & verify](#3-run--verify)
4. [Architecture at a glance](#4-architecture-at-a-glance)
5. [Codebase map](#5-codebase-map)
6. [Domain model](#6-domain-model)
7. [Database schema](#7-database-schema)
8. [How it works — end-to-end flows](#8-how-it-works--end-to-end-flows)
9. [Real-time protocol reference](#9-real-time-protocol-reference)
10. [Delivery guarantees](#10-delivery-guarantees)
11. [Security model](#11-security-model)
12. [Concurrency & async correctness](#12-concurrency--async-correctness)
13. [Resilience & failure semantics](#13-resilience--failure-semantics)
14. [Testing strategy](#14-testing-strategy)
15. [Key decisions & trade-offs](#15-key-decisions--trade-offs)
16. [Glossary & quick-answer index](#16-glossary--quick-answer-index)

---

## 1. What dizzchat is

dizzchat is a **real-time AI chat backend**. Authenticated users open a WebSocket per conversation
and exchange messages with a bundled **mock assistant** that simply echoes the input back
(`"You said: …"` — see `contexts/messaging/infrastructure/outbound/assistant/mock_assistant_responder.py:16`).
There is **no external LLM call**: the assignment is about getting the backend right — auth,
persistence, real-time delivery, cross-replica fan-out, and delivery guarantees — not about the
model.

A conversation holds **many participants**. Its owner invites others by email; from then on every
message any participant sends is broadcast to **all** of them, on whichever replica their sockets
happen to live.

The system runs as **two identical API replicas** sharing one PostgreSQL database and one Redis
instance. A message sent to a socket on replica A is delivered to sockets on replica B via **Redis
pub/sub**. Messages **persist** in Postgres (they survive restarts), are **idempotent** per a
client-supplied key, and are **replayed** on reconnect so a client that dropped off catches up.

**Priorities the build optimized for** (from the assignment rubric): core works · real-time done
right · data layer · code quality/types/async · security · honest write-up.

**Non-negotiables the design holds to:** no blocking calls on the async event loop; no
unauthenticated WebSocket access; no plaintext passwords or committed secrets; a failed AI/DB call
never crashes the socket or the worker; history survives a restart; the app runs on a clean
checkout.

---

## 2. Tech stack & why each choice

**Language/runtime:** Python **3.13+** (`pyproject.toml` `requires-python = ">=3.13"`;
`.python-version` pins `3.13`). Fully `async`/`await`.

### Runtime dependencies (from `pyproject.toml`)

| Package | Floor | Role & why |
|---|---|---|
| `fastapi` | `>=0.115` | HTTP + WebSocket framework; Pydantic-native, ASGI, dependency injection. |
| `uvicorn[standard]` | `>=0.32` | ASGI server that runs the app. |
| `sqlalchemy[asyncio]` | `>=2.0` | Async ORM; mature async story + clean split of DB models from API DTOs (chosen over SQLModel for exactly that separation). |
| `asyncpg` | `>=0.30` | Fast async PostgreSQL driver used by SQLAlchemy. |
| `alembic` | `>=1.14` | Schema migrations, run automatically on boot. |
| `pydantic` | `>=2.9` | Validation + serialization for API DTOs and value parsing. |
| `pydantic-settings` | `>=2.6` | Typed config from environment / `.env`. |
| `argon2-cffi` | `>=23.1` | Password hashing (argon2id). |
| `pyjwt` | `>=2.9` | HS256 access-token signing/verification. |
| `redis` | `>=8.0.1` | `redis.asyncio` client for cross-replica pub/sub fan-out. |

### Dev dependencies & toolchain

| Tool | Floor | Role |
|---|---|---|
| `uv` | — | Dependency & virtualenv manager; `uv.lock` is committed and installs are `--frozen`/`--locked`. |
| `ruff` | `>=0.8` | Lint **and** format. Line length **100**, target `py313`, rules `E,F,I,UP,B,C4,SIM,ASYNC`. |
| `mypy` | `>=1.13` | Types, **`strict = true`**, `pydantic.mypy` plugin, `migrations/` excluded. |
| `pytest` | `>=8.3` | Tests, `asyncio_mode = "auto"`. |
| `pytest-asyncio` | `>=0.24` | Async test support. |
| `httpx` | `>=0.28` | Test HTTP client (via FastAPI/Starlette `TestClient`). |
| `pre-commit` | `>=4.6.1` | Runs ruff/format/mypy/pytest on every commit and in CI. |
| `testcontainers[redis]` | `>=4.15.0` | Spins up a real `redis:7-alpine` for the one fan-out integration test. |

**Console entry point:** `dizzchat = "dizzchat.main:main"` (`pyproject.toml`). Running `dizzchat`
serves `dizzchat.app:app` with uvicorn.

---

## 3. Run & verify

### With Docker (the intended path)

```bash
docker compose up --build
```

Four containers come up (`docker-compose.yml`):

| Service | Image | Host port | Notes |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 5432 | volume `pgdata`; healthcheck `pg_isready -U dizzchat` |
| `redis` | `redis:7-alpine` | 6379 | pub/sub backbone; healthcheck `redis-cli ping` |
| `api` | built from `Dockerfile` | 8000 | replica #1 |
| `api2` | same image | 8001 | replica #2 — shares Postgres + Redis |

- `api` and `api2` share config via a **YAML anchor** `x-api-base: &api-base` merged with
  `<<: *api-base`. Both wait for `postgres` and `redis` to be healthy (`depends_on … service_healthy`).
- **Migrations run automatically on boot.** There is no migration container; each replica runs
  `alembic upgrade head` during app startup, serialized across replicas by a Postgres advisory lock
  (details in [§7](#7-database-schema) and [§8](#8-how-it-works--end-to-end-flows)).
- The `Dockerfile` is `python:3.13-slim`, installs with a pinned `uv` in two cached layers
  (`uv sync --frozen --no-install-project --no-dev`, then `--no-dev`), runs as an unprivileged user
  `app`, and has `ENTRYPOINT ["dizzchat"]`.

Health check: `curl http://localhost:8000/health` → `{"status":"ok"}`.

### Locally with uv

```bash
uv sync                       # install deps into .venv
cp .env.example .env          # then edit (needs a reachable Postgres + Redis)
uv run dizzchat               # run the app
```

### Verify commands

```bash
uv run pytest                 # tests
uv run ruff check .           # lint
uv run ruff format --check .  # formatting
uv run mypy                   # types (strict)
```

CI (`.github/workflows/ci.yml`) does the same set via `uv run pre-commit run --all-files`.

### Configuration (`src/dizzchat/config.py`)

Settings load from env or `.env` via pydantic-settings; `get_settings()` is `@lru_cache`d.

| Env var | Type | Default |
|---|---|---|
| `ENVIRONMENT` | str | `development` |
| `LOG_LEVEL` | str | `INFO` |
| `HOST` | str | `0.0.0.0` |
| `PORT` | int | `8000` |
| `DATABASE_URL` | str | **required** — async SQLAlchemy URL (`postgresql+asyncpg://…`) |
| `REDIS_URL` | str | **required** |
| `JWT_SECRET_KEY` | str | **required**, **min length 32** (a weak key fails fast at startup) |
| `JWT_ALGORITHM` | str | `HS256` |
| `ACCESS_TOKEN_TTL_SECONDS` | int | `900` (15 min) |
| `REFRESH_TOKEN_TTL_SECONDS` | int | `1209600` (14 days) |
| `WS_AUTH_TIMEOUT_SECONDS` | float | `5.0` |
| `SHUTDOWN_DRAIN_TIMEOUT_SECONDS` | float | `10.0` |
| `CORS_ALLOW_ORIGINS` | list[str] | `[]` (JSON array; credentials only for explicit origins, never `*`) |

> **Gotcha:** `WS_AUTH_TIMEOUT_SECONDS` and `SHUTDOWN_DRAIN_TIMEOUT_SECONDS` exist in `config.py`
> but are **not** listed in `.env.example`. They fall back to their defaults unless you set them.
> The `JWT_SECRET_KEY` in `docker-compose.yml` is a dev-only placeholder — inject a strong random
> value anywhere real.

---

## 4. Architecture at a glance

dizzchat is a **DDD hexagonal modular monolith**: one deployable, run as N replicas, split into
**two bounded contexts** plus a small **shared kernel**.

- **`identity`** (supporting) — users, signup/login/refresh, JWT + argon2.
- **`messaging`** (core) — conversations, messages, **and** the real-time WebSocket delivery layer.
  This is where the differentiating work lives.
- **`shared`** — the shared kernel: `Clock`, DB engine/session factory, migration runner, Redis
  client factory, `/health`.

> The older plan described *three* contexts (splitting "Conversations" from "Realtime Messaging").
> In the shipped code they are **one** `messaging` context — real-time is *how* messages are
> delivered, not a separate domain, so it lives inside `messaging` rather than as its own context.

### Hexagonal layering (ports & adapters)

The dependency arrow points **inward**. The domain and application core depend on nothing external;
infrastructure depends on the core by implementing the **ports** (interfaces) the core declares.
Swapping Postgres, Redis, or the web framework touches only adapters — never the domain.

```
        ┌──────────────── inbound adapters (infrastructure) ────────────────┐
        │  api/  — FastAPI REST routers + WS endpoint, Pydantic DTOs, DI     │
        └───────────────────────────────┬───────────────────────────────────┘
                                         │ calls
        ┌────────────────────────────────▼──────────────────────────────────┐
        │  application/  — use-case services + PORTS (Protocol interfaces):   │
        │  repositories, TokenService, MessageBroadcaster, AssistantResponder │
        │                                                                     │
        │      domain/  — aggregates, value objects, invariants.              │
        │      No framework imports.                                          │
        └────────────────────────────────▲──────────────────────────────────┘
                                         │ implements ports
        ┌───────────────────────────────┴───────────────────────────────────┐
        │  infrastructure/ outbound adapters — SQLAlchemy repos, Redis        │
        │  pub/sub, JWT + argon2, session factory                             │
        └─────────────────────────────────────────────────────────────────────┘
```

Vocabulary as used here:

- **Port** — a `Protocol`/ABC interface declared in `application` (or a repository port in
  `domain`). The core depends only on these abstractions.
- **Adapter** — a concrete implementation of a port. *Inbound* adapters (`api`) drive the app;
  *outbound* adapters (`infrastructure/outbound`) are driven by it (DB, Redis, JWT, argon2).
- **Composition root** — the one place that wires concrete adapters into use-cases. Here it's split
  between the app factory/lifespan (`app.py`, process-level singletons) and per-context
  `api/dependencies.py` modules (per-request/per-socket wiring via FastAPI `Depends`).

Each context repeats the same `domain / application / infrastructure(inbound|outbound)` shape.

---

## 5. Codebase map

Annotated tree of `src/dizzchat/`. One line = one responsibility.

```
src/dizzchat/
  app.py            FastAPI factory + lifespan = composition root (migrates, wires infra, routers)
  main.py           uvicorn console entry point ("dizzchat")
  config.py         pydantic-settings Settings + @lru_cache get_settings()
  logging.py        structured JSON logging + per-connection connection_id correlation (contextvar)

  contexts/
    identity/                         signup / login / refresh / JWT auth
      domain/
        errors.py                     IdentityError base
        user/user.py                  User aggregate (registers, hashes via the port)
        user/value_objects.py         Email (lowercased), PasswordHash, UserId
        user/repository.py            UserRepository port (add, get_by_email)
        user/password_hasher.py       PasswordHasher port (domain-owned)
        user/errors.py                InvalidEmail, InvalidCredentials, EmailAlreadyRegistered
        refresh_token/refresh_token.py RefreshToken aggregate (issue/is_active/revoke/rotate)
        refresh_token/repository.py   RefreshTokenRepository port (add, get_by_jti, save)
        refresh_token/errors.py       InvalidRefreshToken
      application/
        ports.py                      TokenService technical port
        errors.py                     InvalidAccessToken
        dto/                          AccessClaims, GeneratedRefreshToken, TokenPair
        services/register_user.py     RegisterUser
        services/authenticate_user.py AuthenticateUser
        services/refresh_access_token.py RefreshAccessToken
      infrastructure/
        inbound/api/router.py         /auth router
        inbound/api/dependencies.py   Identity DI (builds use-cases, get_current_user bearer auth)
        inbound/api/authenticated_user.py AuthenticatedUser principal
        inbound/api/errors.py         identity error -> HTTP status mapping
        inbound/api/controllers/      signup, login, refresh, current_user
        inbound/api/schemas/          request/response Pydantic DTOs
        outbound/security/argon2_password_hasher.py   Argon2PasswordHasher
        outbound/security/jwt_token_service.py        JwtTokenService (HS256 + opaque refresh)
        outbound/persistence/models/                  user_model, refresh_token_model
        outbound/persistence/repositories/            SQLAlchemy user + refresh repos

    messaging/                        conversations + messages + realtime delivery
      domain/
        errors.py                     MessagingError base
        conversation/conversation.py  Conversation aggregate (lifecycle + ensure_owned_by /
                                      ensure_participant / add_participant / remove_participant)
        conversation/value_objects.py ConversationId, OwnerId, ParticipantId, ConversationTitle
        conversation/participant.py   Participant (read-side VO: id + joined_at)
        conversation/repository.py    ConversationRepository port
        conversation/errors.py        ConversationNotFound, NotConversationOwner,
                                      NotConversationParticipant, ParticipantUserNotFound,
                                      CannotRemoveConversationOwner, InvalidConversationTitle
        message/message.py            Message aggregate (immutable record, id = seq)
        message/value_objects.py      MessageRole, MessageId, SenderId, ClientMessageId, MessageContent
        message/repository.py         MessageRepository port
        message/errors.py             InvalidMessageContent
      application/
        ports.py                      ConversationAccess, UserDirectory, AssistantResponder,
                                      MessageBroadcaster, MessageWriter, MessageReplayer
        dto/message_page.py           MessagePage (items, next_cursor, has_more)
        services/create_conversation.py, list_conversations.py, rename_conversation.py,
                 delete_conversation.py (soft), get_conversation_history.py (cursor paging),
                 ensure_conversation_access.py, post_message.py, message_exchange.py,
                 replay_messages.py,
                 add_participant.py, list_participants.py, remove_participant.py
      infrastructure/
        inbound/api/router.py         /conversations REST router
        inbound/api/dependencies.py   Conversations DI
        inbound/api/errors.py         messaging error -> HTTP status mapping
        inbound/api/controllers/      create/list/rename/delete/get_conversation_history,
                                      add/list/remove_participant
        inbound/api/schemas/          request/response DTOs
        inbound/api/realtime/router.py       registers WS route /ws/conversations/{id}
        inbound/api/realtime/websocket.py    the WS endpoint handler (the core flow)
        inbound/api/realtime/protocol.py     inbound frame models + outbound frame builders
        inbound/api/realtime/connection_manager.py  Connection + ConnectionManager (local delivery)
        inbound/api/realtime/conversation_registry.py ConversationSubscriber port + ConversationRegistry
        inbound/api/realtime/dependencies.py realtime DI wiring
        outbound/assistant/mock_assistant_responder.py  MockAssistantResponder ("You said: …")
        outbound/redis/channels.py            conversation_channel() -> "conv:{id}"
        outbound/redis/message_codec.py       encode/decode a domain Message <-> JSON bytes
        outbound/redis/redis_message_broadcaster.py  RedisMessageBroadcaster (PUBLISH only)
        outbound/redis/redis_conversation_subscriber.py RedisConversationSubscriber (per-replica reader)
        outbound/identity/identity_user_directory.py  email -> user id (anti-corruption layer)
        outbound/persistence/models/          conversation_model, conversation_participant_model,
                                              message_model
        outbound/persistence/repositories/    SQLAlchemy conversation + message repos
        outbound/persistence/session_scoped_conversation_access.py   per-call UoW for access check
        outbound/persistence/session_scoped_message_writer.py        per-message UoW writer
        outbound/persistence/session_scoped_message_replayer.py      per-call UoW replayer

    shared/                           the shared kernel
      application/clock.py            Clock port
      infrastructure/inbound/api/health.py         /health router
      infrastructure/inbound/api/dependencies.py   get_session/SessionDep, get_clock/ClockDep
      infrastructure/outbound/database.py          Base, create_engine, create_session_factory
      infrastructure/outbound/migrations.py        run_migrations (alembic upgrade head)
      infrastructure/outbound/redis_client.py      create_redis_client
      infrastructure/outbound/system_clock.py      SystemClock (UTC)
```

---

## 6. Domain model

Aggregates are the consistency boundaries; **value objects** (VOs) are immutable, validate once at
construction, and are equal by value. An invalid value raises a **domain error** instead of being
constructed — so an invalid state is unrepresentable past the boundary.

### Identity

- **`User`** (`domain/user/user.py`) — a registered account. `User.register(...)` hashes the
  password by **double-dispatch** through the `PasswordHasher` port (the domain never imports
  argon2). Equal by `UserId`.
- **VOs:** `Email` (normalizes to lowercase, validates shape), `PasswordHash` (guaranteed to hold
  only a hash, never plaintext), `UserId`.
- **`RefreshToken`** (`domain/refresh_token/refresh_token.py`) — a persisted refresh credential
  identified by `jti`. It stores **only the hash** of the secret. Behaviors: `issue`, `is_active`,
  `revoke`, `rotate` (revoke current + mint successor; raises `InvalidRefreshToken` if not active —
  this rejects replay of a revoked/expired token).

### Messaging

- **`Conversation`** (`domain/conversation/conversation.py`) — lifecycle `start` / `rename` /
  `delete` (soft) and `is_deleted`, plus **two levels of authorization it enforces itself**:
  - `ensure_owned_by(owner_id)` (raises `NotConversationOwner`) — administration: rename, delete,
    and changing the membership.
  - `ensure_participant(participant_id)` (raises `NotConversationParticipant`) — taking part:
    joining the live channel, sending, reading history.

  Membership lives on the aggregate as `participant_ids: frozenset[ParticipantId]`, mutated by
  `add_participant` (idempotent, returns whether it was new) and `remove_participant` (raises
  `CannotRemoveConversationOwner` for the owner). `start` seeds the owner, so **the owner is always a
  participant** and can never be locked out of their own conversation. `delete` is idempotent (sets
  `deleted_at` + `updated_at`).
- **`Participant`** (`domain/conversation/participant.py`) — a read-side VO pairing a
  `ParticipantId` with `joined_at`. The aggregate deliberately holds **ids only**: identity is all it
  needs to decide access, and `joined_at` carries no invariant, so it is served from this projection
  (`ConversationRepository.list_participants`) rather than loaded into the aggregate.
- **`Message`** (`domain/message/message.py`) — an **immutable** persisted record and a **separate
  aggregate** from `Conversation`. Its identity is `MessageId`, which is also the ordering key.
- **VOs** (`domain/message/value_objects.py`):
  - `MessageRole` — `StrEnum` with `USER`/`ASSISTANT` (wire values `"user"`/`"assistant"`).
  - `MessageId` — the persisted **bigserial** sequence number (identity *and* order).
  - `SenderId` — reference to the Identity user who sent it; **null for assistant** messages.
  - `ClientMessageId` — client-generated idempotency key, unique per conversation.
  - `MessageContent` — required non-empty (rejects blank/whitespace via `InvalidMessageContent`).
  - `ConversationTitle` — trimmed, ≤ 200 chars.

> **Why `OwnerId`/`ParticipantId` are not Identity's `UserId`.** `messaging` defines its own
> (`domain/conversation/value_objects.py`) rather than importing `identity.UserId`. This keeps the
> two bounded contexts **decoupled** — `messaging` doesn't depend on Identity's model; it just
> stores the user's id as its own concept. The values happen to be the same UUID; the type boundary
> is deliberate. `OwnerId` and `ParticipantId` are separate because they name different *roles* in
> the aggregate, and the code reads better for it: `ensure_owned_by(OwnerId(...))` versus
> `ensure_participant(ParticipantId(...))` says which rule is being applied.
>
> The **one** place Messaging must ask Identity a question is admitting a participant by email. That
> goes through the `UserDirectory` port (`application/ports.py`), implemented by
> `IdentityUserDirectory` (`infrastructure/outbound/identity/`), which constructs Identity's `Email`
> and returns a bare `UUID`. An anti-corruption layer, in infrastructure — where cross-context
> coupling belongs — so the domain and use cases stay ignorant of Identity entirely.

---

## 7. Database schema

Five Alembic migrations, a linear chain, all in `migrations/versions/`. Final schema:

### `users` (0001)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `email` | String(320) | **unique**, indexed (`ix_users_email`) |
| `password_hash` | String | argon2id hash |
| `created_at` | DateTime(tz) | |

### `refresh_tokens` (0001)
| Column | Type | Notes |
|---|---|---|
| `jti` | String | PK |
| `user_id` | UUID | FK → `users.id` **ON DELETE CASCADE**, indexed |
| `token_hash` | String | SHA-256 of the secret (never the secret itself) |
| `expires_at` | DateTime(tz) | |
| `revoked_at` | DateTime(tz) | nullable — set on rotation/revocation |

### `conversations` (0002)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `owner_id` | UUID | indexed (`ix_conversations_owner_id`) |
| `title` | String(200) | |
| `created_at` / `updated_at` | DateTime(tz) | |
| `deleted_at` | DateTime(tz) | nullable — **soft-delete** marker |

### `conversation_participants` (0005)
| Column | Type | Notes |
|---|---|---|
| `conversation_id` | UUID | FK → `conversations.id`; **composite PK** |
| `user_id` | UUID | **composite PK**, indexed (`ix_conversation_participants_user_id`) |
| `joined_at` | DateTime(tz) | |

The composite PK on `(conversation_id, user_id)` *is* the uniqueness rule — a user cannot be admitted
twice, enforced at the database as well as in the aggregate. The `user_id` index backs
`list_for_participant` ("the conversations I'm in"). The ORM loads the set with
`relationship(lazy="selectin")`, which is required rather than stylistic: the default lazy loader
emits I/O on attribute access and raises under asyncio, and `selectin` batches, so listing N
conversations costs one extra query rather than N.

### `messages` (0002, extended by 0003 & 0004)
| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger, autoincrement | **PK; bigserial = the ordering key (`seq`)** |
| `conversation_id` | UUID | FK → `conversations.id` |
| `sender_id` | UUID | **nullable** (0003) — null for assistant messages |
| `role` | String(16) | **added in 0003** (`user`/`assistant`) |
| `content` | Text | |
| `created_at` | DateTime(tz) | |
| `client_message_id` | UUID | **nullable, added in 0004** — idempotency key |

Indexes/constraints on `messages`:
- `ix_messages_conversation_id_id` on `(conversation_id, id)` — backs **keyset pagination** and
  ordered replay.
- **`uq_messages_conversation_id_client_message_id`** unique on `(conversation_id, client_message_id)`
  (0004) — the dedupe backstop. Postgres treats NULLs as distinct, so keyless sends and assistant
  rows (both with `client_message_id = NULL`) never collide.

### Migration chain
`0001_identity` → `0002_conversations` → `0003_message_role` → `0004_client_message_id` →
`0005_conversation_participants`.
- **0003** adds `role` with a temporary `server_default='user'` to backfill existing rows, then
  drops the default so the app must supply role on every insert; also makes `sender_id` nullable.
- **0004** adds `client_message_id` + the unique constraint. Building the backing unique index takes
  an `ACCESS EXCLUSIVE` lock; it **cannot** use `CREATE UNIQUE INDEX CONCURRENTLY` because
  migrations run inside a transaction (see the advisory lock below) and `CONCURRENTLY` isn't allowed
  in a transaction. Acceptable on a small table; see [§15](#15-key-decisions--trade-offs) for the
  large-table plan.
- **0005** creates `conversation_participants` and **backfills the owner of every existing
  conversation** as its first participant. The backfill is load-bearing, not cosmetic: access is now
  decided by membership, so a conversation without a row would leave its own owner unable to connect,
  post, or read history. It has no `WHERE` clause, so soft-deleted conversations are backfilled too
  and remain restorable.

### Migrations run on boot, serialized (`migrations/env.py`)
Every replica runs `alembic upgrade head` during startup. Inside the migration transaction it first
executes `SELECT pg_advisory_xact_lock(721103)` (key `721103`). This **serializes concurrent replica
boots**: the first replica takes the lock and migrates; later starters block, then find the schema
already at head and no-op. Postgres auto-releases the transaction-scoped lock on commit. The runner
(`shared/infrastructure/outbound/migrations.py`) builds the Alembic `Config` **in code** (not from
`alembic.ini`) specifically so `logging.fileConfig` doesn't reset the structured logger.

---

## 8. How it works — end-to-end flows

### 8.0 The whole flow in one picture

**The whole design in one sentence:** a client opens a WebSocket to *one* replica; that replica saves
each message to Postgres, publishes it to a Redis channel named after the conversation, and *every*
replica — the publisher included — reads it back off Redis and writes it to its own sockets.

The six diagrams below walk through that step by step: the containers, what each replica builds at
boot, what happens when a client connects, what a replica does with an inbound message, how that
message reaches sockets on **both** replicas, and what teardown looks like. §8.1–8.5 cover the same
ground in prose, and the step index at the end links every numbered step to the code.

#### A. Topology — what talks to what

```
                          ┌──────────────────┐
   client sockets ───────►│   api    :8000   │───┐
                          │   (replica 1)    │   │       ┌────────────────┐
                          └──────────────────┘   ├──────►│    postgres    │  users · conversations
                                                 │       │   16-alpine    │  messages (id = seq)
                          ┌──────────────────┐   │       └────────────────┘
   client sockets ───────►│   api2   :8001   │───┤
                          │   (replica 2)    │   │       ┌────────────────┐
                          └──────────────────┘   └──────►│     redis      │  PUBLISH / SUBSCRIBE
                                                         │    7-alpine    │  conv:{conversation_id}
                                                         └────────────────┘
```

Both API containers are the **same image with the same env** — only the published host port differs,
and no load balancer sits in front (clients hit `:8000` / `:8001` directly, per `docker-compose.yml`).
What matters for the rest of this section: a replica's **live sockets** and its **set of subscribed
channels** are in-memory and private to that replica. It knows nothing about sockets on the other
replica, and it doesn't need to — Redis is the only thing that closes that gap.

#### B. Boot — what each replica builds (`app.py:44-83`)

```
  run_migrations() in asyncio.to_thread     ← Alembic is sync; advisory lock 721103 serializes replicas
        ▼
  engine (pool_pre_ping) + session_factory  → app.state
        ▼
  create_redis_client()                     → app.state.redis
        ▼
  ConnectionManager()                       → app.state.connection_manager   (local delivery half)
        ▼
  RedisConversationSubscriber(redis, mgr)
        .start()                            → reader task starts, idling (no channels subscribed yet)
        ▼
  RedisMessageBroadcaster(redis)            → app.state.message_broadcaster  (PUBLISH only)
        ▼
  ConversationRegistry(mgr, subscriber)     → app.state.conversation_registry (the glue)
        ▼
  ══ yield: serve traffic ══
```

#### C. A client connects

```
CLIENT A        REPLICA 1         POSTGRES            REDIS
(browser)      (api :8000)        (shared)          (pub/sub)
  │                 │                 │                 │
  ├────────────────►│                 │                 │   ① GET /ws/… + Upgrade
  │◄────────────────┤                 │                 │   ② accept() → 101 Switching
  ├────────────────►│                 │                 │   ③ auth frame (≤ 5s, else 4401)
  │                 ├────────────────►│                 │   ④ access.ensure() — own session
  │                 │◄────────────────┤                 │   ⑤ participant? (else 4403)
  │◄────────────────┤                 │                 │   ⑥ auth.ok
  │                 ├─────────────────┼────────────────►│   ⑦ SUBSCRIBE conv:{id}
  │                 │◄────────────────┼─────────────────┤   ⑧ subscribed → join() returns
  │                 ├────────────────►│                 │   ⑨ replay_since(last_seen_seq)
  │                 │◄────────────────┤                 │   ⑩ missed rows, oldest-first
  │◄────────────────┤                 │                 │   ⑪ message.new × N (replay)
  │                 │                 │                 │   ⑫ receive loop starts
```

Two things to notice:

- **⑦ happens only for the *first* socket** on that conversation on this replica. A second socket for
  the same conversation reuses the subscription that already exists — `ConnectionManager.register`
  reports the 0→1 transition, and `ConversationRegistry` turns that into the `SUBSCRIBE`. If the
  `SUBSCRIBE` fails, the socket is closed `1011` rather than served: a socket that isn't subscribed
  would silently miss every message sent from the other replica, so the code **fails closed**.
- **⑦ comes before ⑨ on purpose.** The socket is already receiving live messages *before* replay reads
  the backlog, so nothing can be lost in between. The cost is that a live message can arrive ahead of
  an older replayed one — see [§10](#10-delivery-guarantees).

#### D. One `message.send`, inside replica 1

```
  message.send frame arrives in the receive loop
        ▼
  ① frame + content validated ──────────► invalid ──► error frame, socket STAYS OPEN
        ▼
  ② decode_access(token) re-checked ────► expired ──► close 4401
        ▼
  ③ MessageExchange.exchange()
        │
        ├─► writer.from_user()  ─ own session ─► postgres: INSERT(role=user) + COMMIT
        │        └─ duplicate client_message_id? ─► return the existing row, skip ④–⑦
        │
        ├─► ④ broadcast(user_msg) ────────────► redis: PUBLISH conv:{id}
        │
        ├─► ⑤ responder.reply_to() → "You said: …"     (mock assistant; no external LLM)
        │
        ├─► ⑥ writer.from_assistant() ─ own session ─► postgres: INSERT(role=assistant) + COMMIT
        │
        └─► ⑦ broadcast(assistant_msg) ───────► redis: PUBLISH conv:{id}
        ▼
  ⑧ message.ack(user_msg) ──► CLIENT A
```

**Persist before broadcast:** each COMMIT happens before its PUBLISH, so no client is ever shown a
message that a failed transaction would have erased. Notice also what is *missing* here — the replica
never writes the message to its own sockets at this point. Every delivery goes through Redis, which
is diagram E.

#### E. Fan-out — how the message reaches sockets on both replicas

Alice and Bob are **two different users** who are both participants of this conversation, connected to
different replicas. Alice sends; both of them receive.

```
ALICE       REPLICA 1       REDIS       REPLICA 2        BOB
(socket)   (api :8000)     pub/sub     (api2 :8001)    (socket)
  │             │             │             │             │
  │             ├────────────►│             │             │   ① PUBLISH conv:{id}
  │             │◄────────────┤             │             │   ② loopback to replica 1
  │             │             ├────────────►│             │   ③ fan-out to replica 2
  │◄────────────┤             │             │             │   ④ message.new → alice
  │             │             │             ├────────────►│   ⑤ message.new → bob
  │             │             │             │             │   ══ ①–⑤ repeat for the reply
  │◄────────────┤             │             │             │   ⑥ message.ack → alice
```

- **This is the whole of multi-user broadcast.** `ConnectionManager` keys sockets by *conversation*,
  never by user, so ④ and ⑤ are the same code path whether the two sockets belong to one person on two
  devices or to two different participants. Adding multiple users to a conversation therefore changed
  only the **authorization** gate at ⑤ in diagram C — not one line of the delivery path here.
- **The sender gets its own message back through Redis.** Replica 1 is not treated specially: it
  receives its own `PUBLISH` on its own subscription (②) and delivers from there. That leaves exactly
  **one** delivery path to any socket — `subscriber → ConnectionManager.broadcast` — instead of one
  path for local sockets and a second for remote ones. It is also why the `SUBSCRIBE` back in diagram
  C must complete before the socket is allowed to send anything.
- **What happens between ② and ④:** the subscriber's reader task takes the message off Redis
  (`get_message`), decodes the JSON back into a domain `Message`, and hands it to the local
  `ConnectionManager`, which writes a `message.new` frame to every socket in that conversation.
- **⑥ can arrive before ④.** The `message.new` frames are written by the subscriber task and the ack
  by the receive-loop task — two independent tasks with no ordering between them. A client must not
  assume the ack comes first.
- **Replica 2 never reads Postgres here.** The entire message travels inside the Redis payload, so
  fan-out costs one `PUBLISH` plus one decode per replica — no extra database queries.

#### F. Teardown

```
  ── one socket goes away ──────────────────────────────────────────────────────
  WebSocketDisconnect (or an unexpected error → close 1011)
        ▼
  finally: registry.leave(conversation, connection)
        ▼
  manager.unregister() → was that the LAST local socket for this conversation?
        ├─ no  → keep the subscription; other local sockets still need the channel
        └─ yes → subscriber.unsubscribe() → UNSUBSCRIBE conv:{id}
        ▼
  connection_id contextvar reset

  ── the whole replica goes away (SIGTERM) ─────────────────────────────────────
  connection_manager.close_all() → every live socket closed 1001
        │                          (bounded by SHUTDOWN_DRAIN_TIMEOUT_SECONDS)
        ▼
  subscriber.stop() → reader task cancelled, pub/sub connection closed
        ▼
  redis.aclose() → engine.dispose()
```

While the replica is running, the subscriber also **repairs itself**: if a read from Redis fails, it
logs, waits 500 ms, rebuilds the pub/sub connection, and re-`SUBSCRIBE`s every channel it still needs.
A brief Redis outage therefore doesn't cost the replica its subscriptions. Messages published *during*
the outage are lost by pub/sub and recovered instead by the client's next `last_seen_seq` replay.

#### Where each diagram lives in the code

| Diagram | Files |
|---|---|
| B — boot wiring | `app.py:44-83` |
| C — connect, auth, join, replay | `realtime/websocket.py:59-164`; `realtime/conversation_registry.py:38-53` |
| D — validate, persist, publish, ack | `realtime/websocket.py:167-217`; `application/services/message_exchange.py:38-67` |
| E — publish, fan-out, local delivery | `outbound/redis/` (all four files); `realtime/connection_manager.py:83-98` |
| F — leave, unsubscribe, drain | `realtime/conversation_registry.py:55-59`; `app.py:72-83` |

### 8.1 Boot / lifespan / composition root

Entry: `main.py:main` loads settings and calls `uvicorn.run("dizzchat.app:app", …, log_config=None)`
(so uvicorn doesn't clobber the JSON logger). `app = create_app()` runs at import.

`create_app()` (`app.py`), in order: configure logging → build `FastAPI(lifespan=lifespan)` → add
CORS (credentials enabled only when `*` is **not** in the origin list) → register identity + messaging
error handlers → include routers in order: `health_router`, `identity_router`, `conversations_router`,
`ws_router`.

`lifespan` on startup, in exact order:
1. `await asyncio.to_thread(run_migrations)` — migrate **off the event loop**, advisory-lock
   serialized.
2. Build the async **engine** (`pool_pre_ping=True`) → `app.state.engine`.
3. Build the **session factory** (`async_sessionmaker(expire_on_commit=False)`) → `app.state.session_factory`.
4. Create the **Redis client** (5s socket/connect timeouts, raw bytes) → `app.state.redis`.
5. Create `ConnectionManager()` → `app.state.connection_manager`.
6. Create `RedisConversationSubscriber(redis, connection_manager)` and **`await subscriber.start()`**
   (launches the background reader task). (Held as a local; reachable via the registry.)
7. `RedisMessageBroadcaster(redis)` → `app.state.message_broadcaster`.
8. `ConversationRegistry(connection_manager, subscriber)` → `app.state.conversation_registry`.

On shutdown: drain sockets via `connection_manager.close_all()` bounded by
`SHUTDOWN_DRAIN_TIMEOUT_SECONDS` (a timeout is logged and swallowed), then `subscriber.stop()`,
`redis.aclose()`, `engine.dispose()`.

**`app.state` singletons:** `engine`, `session_factory`, `redis`, `connection_manager`,
`message_broadcaster`, `conversation_registry`.

### 8.2 REST auth (`/auth`)

Router: prefix `/auth`; `POST /signup` (201), `POST /login`, `POST /refresh`, `GET /me`. Request
sessions are request-scoped: `shared/.../api/dependencies.py:get_session` commits on success and
rolls back on error, so use-cases just add to the session and let teardown commit.

- **`POST /auth/signup` → 201** — `RegisterUser.execute`: build `Email` (→ 422 on bad shape);
  `get_by_email` duplicate check → `EmailAlreadyRegistered` (**409**); hashing off-loaded via
  `asyncio.to_thread(User.register, …)` (argon2 is CPU-bound); `users.add(user)`. Returns
  `{id, email, created_at}`.
- **`POST /auth/login` → 200** — `AuthenticateUser.execute`: parse `Email` (malformed → generic
  `InvalidCredentials`, anti-enumeration); `get_by_email`; if the user is missing it **still** runs
  a dummy hash (`asyncio.to_thread`) so timing doesn't reveal existence, then `InvalidCredentials`
  (**401**); verify off-loop; on success mint a **`TokenPair`**: an access JWT
  (`issue_access(user.id)`) + a freshly generated + persisted refresh token. Returns
  `{access_token, refresh_token, token_type: "bearer"}`.
- **`POST /auth/refresh` → 200 (rotation + revocation)** — `RefreshAccessToken.execute`:
  `parse_refresh` splits `"<jti>.<secret>"` (malformed → `InvalidRefreshToken`); `get_by_jti`;
  `verify_refresh` compares `SHA-256(secret)` against the stored hash with `hmac.compare_digest`;
  `stored.rotate(...)` (raises if not active → rejects replay), `refresh_tokens.save(stored)` (merge
  to persist the revocation), `refresh_tokens.add(rotated)`. Returns a **new** pair.
- **`GET /auth/me` → 200** — `get_current_user` uses `HTTPBearer(auto_error=False)`; missing creds →
  401 with `WWW-Authenticate: Bearer`; `decode_access` → `InvalidAccessToken` → 401; returns
  `{user_id}`.

### 8.3 Conversations REST (`/conversations`)

All routes require a Bearer token (reuses Identity's `get_current_user`). Reads and sends are open to
any **participant**; rename, delete, and membership changes are **owner-only**.

- **Create → 201** — `Conversation.start(...)` (which seeds the owner as the first participant) then
  `conversations.create(..., participant_ids=...)`, so the conversation row and its owner membership
  land in one unit of work.
- **List** — `list_for_participant` joins `conversation_participants` on `user_id = ?` and filters
  `deleted_at IS NULL`, newest first. Returns conversations the caller **owns or was invited to**.
- **Rename** — `get` (→ 404 if absent), `ensure_owned_by` (→ 403), `rename`, `update`.
- **Delete → 204 (soft)** — `get` (→ 404), `ensure_owned_by`, `delete(now)` sets `deleted_at`,
  `update`. Because `get` already filters `deleted_at IS NULL`, deleting an already-deleted
  conversation returns 404 (idempotent from the client's view).
- **`GET /{id}/messages` — cursor pagination** — query `before` (int ≥ 1, optional) + `limit`
  (default 50, 1–100). `GetConversationHistory.execute`: `get` (→ 404), `ensure_participant` (→ 403),
  then **over-fetch by one** (`list_history(limit=limit+1)`), compute `has_more = fetched > limit`,
  trim to `limit`, `next_cursor = items[-1].id if has_more else None`. Query is **keyset**:
  `WHERE conversation_id = ? [AND id < before] ORDER BY id DESC LIMIT ?` — newest-first, backed by
  `ix_messages_conversation_id_id`. Response: `{items, next_cursor, has_more}`; pass `next_cursor`
  as the next `before`.

**Participants** — the three routes that make a conversation multi-user:

- **`POST /{id}/participants` → 204** — `AddParticipant.execute`: `get` (→ 404),
  `ensure_owned_by` (→ **403**, only the owner invites), `users.find_id_by_email(email)` via the
  `UserDirectory` port (`None` → `ParticipantUserNotFound`, **404**), then
  `conversation.add_participant(...)` and — **only if the membership is new** —
  `conversations.add_participant(..., joined_at=now)`. Re-inviting an existing participant is a
  successful no-op, so a retrying client cannot create a duplicate. The composite PK is the backstop
  if two invites race past the in-aggregate check.
- **`GET /{id}/participants` → 200** — `ListParticipants.execute`: `get` (→ 404),
  `ensure_participant` (→ 403), then `list_participants` → `[{user_id, joined_at}]`, oldest first.
- **`DELETE /{id}/participants/{user_id}` → 204** — `RemoveParticipant.execute`: `get` (→ 404), then
  one rule covering both kick and leave — permitted if the actor **is the owner** or is removing
  **themselves**, else `NotConversationOwner` (403). `conversation.remove_participant` then refuses
  the owner (`CannotRemoveConversationOwner`, **409**) and rejects someone who never joined
  (`NotConversationParticipant`, 403), so a refusal is distinguishable from a no-op.

### 8.4 WebSocket lifecycle (`/ws/conversations/{id}`) — the core

Handler: `realtime/websocket.py:conversation_ws`. Close-code constants: `4401` auth, `4403`
forbidden, `1011` internal; drain uses `1001`.

1. `await websocket.accept()` — accept first (so we can send a close *code* on rejection). A `uuid4`
   `connection_id` is then set on the `connection_id_var` contextvar (reset on exit), so every log
   line emitted while serving this socket carries the same correlation id.
2. **First-frame auth with timeout** — `asyncio.wait_for(receive_json(), WS_AUTH_TIMEOUT_SECONDS)`.
   Timeout / non-JSON → **close 4401**. A plain disconnect before the frame → just return.
3. **Token decode** — validate the `AuthFrame`, then `tokens.decode_access(token)`. Invalid frame or
   bad/expired token → **close 4401**. The `auth` payload also carries an optional `last_seen_seq`.
4. **Access check** — `access.ensure(conversation_id, participant_id)` in its own session
   (`SessionScopedConversationAccess` → `EnsureConversationAccess` → `ensure_participant`).
   `ConversationNotFound` / `NotConversationParticipant` → **close 4403**. Any participant passes, not
   only the owner — this one check is what makes the conversation multi-user. It runs **once**, at
   connect; see [§11](#11-security-model) for what that means when a membership is revoked.
5. **`auth.ok`** — build a `Connection` (a lock-wrapped socket) and send `{"type":"auth.ok"}`.
6. **Join + subscribe (fail-closed)** — `registry.join(conversation, connection)`: under a lock,
   register the socket locally; if it's the **first** socket for this conversation on this replica,
   `await subscriber.subscribe(conv:{id})` on Redis. If the subscribe fails it rolls back the local
   registration and re-raises → the handler **closes 1011**. This is deliberate: a socket that
   couldn't subscribe would silently miss cross-replica messages, so we fail closed.
7. **`last_seen_seq` replay — after joining live delivery** — `_replay_missed`: if `last_seen_seq`
   is `None`, do nothing (a fresh client loads history over REST). Otherwise
   `replayer.replay_since(after=MessageId(last_seen_seq))` and send each missed message as a
   `message.new` frame. Replay happens **after** the socket is already receiving live traffic, so no
   message can slip through the gap — at the cost of ordering at the seam (see
   [§10](#10-delivery-guarantees)).
8. **Receive loop** — per inbound frame:
   - non-JSON → send `error("invalid JSON")`, keep the socket open.
   - not a valid `message.send` / blank content → `error("invalid message frame")`, keep open.
   - **Re-validate the access token on every send** (`decode_access(token)` again). Expired → close
     **4401**. A socket never outlives its token.
   - `exchange.exchange(...)`. Any exception (DB or mock-AI) → log + `error("failed to handle
     message")`, keep the socket open.
   - On success → send `message.ack` (`{id, client_message_id, created_at}`).
9. **`MessageExchange.exchange`** (`application/services/message_exchange.py`):
   1. `writer.from_user(...)` → `(user_message, created)`. The session-scoped writer opens its own
      session, runs `PostMessage.from_user`, and **commits** — **persist before broadcast**, so a
      rollback can never surface an unstored message.
   2. `PostMessage.from_user` re-checks **membership** (`ensure_participant`, so a revoked user stops
      posting at once even on an open socket), then does a **pre-flight** `find_by_client_message_id`:
      if the client id already exists, returns `(existing, False)` — **no new row**.
   3. If `not created` → return early: **no re-broadcast, no second assistant reply** (idempotent).
   4. Else `broadcaster.broadcast(conv, user_message)` → the mock `reply_to(content)` (`"You said:
      …"`) → persist+commit the assistant message → `broadcast(conv, assistant_message)`.
   5. Returns the **user** message (that's what the ack echoes).
10. **Disconnect / cleanup** — `WebSocketDisconnect` is caught; other errors → close 1011;
    `finally: registry.leave(...)` unregisters and, if it was the **last** local socket for the
    conversation, unsubscribes from Redis. During a broadcast, any socket whose send raises is
    dropped and unregistered (dead-socket cleanup).

### 8.5 Redis fan-out

- **Publish** — `RedisMessageBroadcaster.broadcast` does exactly one thing:
  `redis.publish("conv:{id}", encode(message))`. It never touches sockets.
- **Subscribe (one per replica)** — `RedisConversationSubscriber` runs a single background reader
  task that reads under a lock, decodes each frame, and hands the domain `Message` to the **local**
  `ConnectionManager.broadcast`, which serializes it as a `message.new` frame to that conversation's
  local sockets. It dynamically (un)subscribes to `conv:{id}` channels on 0↔1 local-socket
  transitions (driven by the registry) and reconnects-with-backoff + resubscribes on read failure.
- **Single uniform delivery path.** The producing replica is **not** special-cased: it publishes to
  Redis and receives its own message back through its own subscriber, just like every other replica.
  So there's exactly one code path to a socket (`subscriber → ConnectionManager.broadcast`) and no
  double-delivery. This is *why* `join` awaits the subscribe before the receive loop starts — even
  the sender's own `message.new` echo depends on that subscription being live.

---

## 9. Real-time protocol reference

**Endpoint:** `ws://<host>/ws/conversations/{conversation_id}`.
**Envelope:** `{"type": ..., "payload": {...}}` for data; `{"type": "error", "error": "<detail>"}`
for failures. Inbound frames are Pydantic-validated; the only two inbound types are `auth` and
`message.send` (`realtime/protocol.py`).

### Inbound (client → server)

```jsonc
// first frame — authenticate the connection
{ "type": "auth", "payload": { "token": "<access_token>", "last_seen_seq": null } }

// send a message
{ "type": "message.send",
  "payload": { "content": "hello", "client_message_id": "<uuid or null>" } }
```

### Outbound (server → client)

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

`id` is the message `seq` (the same value as `MessageResponse.id` over REST). There is **no** separate
`seq` field on the wire — the bigserial `id` *is* the sequence number.

### Close codes

| Code | Meaning |
|---|---|
| 4401 | auth failed — timeout, non-JSON, invalid frame, or bad/expired token (incl. on a later send) |
| 4403 | not the conversation owner, or the conversation doesn't exist |
| 1011 | internal — e.g. the Redis fan-out subscription couldn't be established (fail closed) |
| 1001 | server shutting down (graceful socket drain) |

Bad JSON or an invalid `message.send` returns an `error` frame and **keeps the socket open**; only
auth/ownership failures close it.

---

## 10. Delivery guarantees

Two guarantees ship (the assignment's chosen bonus):

### Idempotent send
`client_message_id` is a client UUID acting as an idempotency key. Re-sending the same value returns
the existing message's `id` in a `message.ack` — **no second row, no re-broadcast, no second
assistant reply**. Enforced in two layers: a pre-flight `find_by_client_message_id` in
`PostMessage.from_user`, and the DB unique constraint `uq_messages_conversation_id_client_message_id`
as the race backstop. NULL client ids are distinct in Postgres, so keyless sends never collide.

### Reconnect replay (`last_seen_seq`)
On (re)connect the client may include `last_seen_seq` in the `auth` payload:
- `null` — no replay; load prior history via `GET /conversations/{id}/messages`.
- `0` — full replay of the conversation.
- `N` — replay messages with `seq > N`, oldest-first, as `message.new` frames.

`ReplayMessages` paginates `list_since` (500 at a time, ascending) so a long backlog streams in
order.

### The seam trade-off (important)
Replay begins **after** the socket has joined live delivery, so no message is missed — but the
contract is **at-least-once and *not* ordered at the seam**: a live frame can arrive ahead of a
lower-`seq` replay frame. Therefore the **client must**:
- apply each `seq` **at most once** (track a *seen-set*, not a high-water mark), and
- order by the `id` each frame carries.

A stricter server-side exactly-once/ordered option (buffer live frames during replay, release in
`seq` order) is deferred — see [§15](#15-key-decisions--trade-offs).

---

## 11. Security model

- **First-message WebSocket auth.** The client connects, then must send an `auth` frame with the
  JWT within `WS_AUTH_TIMEOUT_SECONDS`, or the server closes with `4401`. This keeps tokens out of
  URLs/access logs (unlike a query param) and gives a clean rejection close code.
- **Token re-validated on every send.** `decode_access` runs again per `message.send`; an expired
  token closes the socket (`4401`) — a socket can't outlive its access token.
- **Two-level authorization on a conversation.** Reading and sending require **membership**
  (`ensure_participant`); rename, delete, and changing the membership require **ownership**
  (`ensure_owned_by`). The owner is seeded as a participant and cannot be removed, so ownership always
  implies access. Membership is re-checked on **every** send (`PostMessage.from_user`), not only at
  connect, so revoking it stops the user posting immediately.
- **Known limitation — revocation doesn't close live sockets.** The connect-time check runs once, so a
  removed participant keeps *receiving* broadcasts until their socket drops; their *sends* are already
  refused, and a reconnect is closed `4403`. Closing sockets on removal needs a cross-replica
  revocation event; deliberately not built (see `NOTES.md`).
- **Invited users are resolved, never asserted.** An invite names an email, which must belong to a
  registered user (`404` otherwise), so a mistyped identifier cannot become a phantom participant.
- **Passwords:** argon2id (`Argon2PasswordHasher`), verified with the library's constant-time check;
  failures are swallowed to a boolean. Hashing always runs via `asyncio.to_thread` (off the loop).
- **Anti-enumeration + timing defense** on login: malformed emails and missing users both yield the
  same generic `InvalidCredentials`, and a missing user still triggers a dummy hash so response time
  doesn't leak existence.
- **Refresh tokens are opaque** `"<jti>.<secret>"` strings; only `SHA-256(secret)` is stored, so a
  DB read can't reconstruct a usable token. Refresh **rotates** (old revoked, new issued) and
  **rejects replay** of a revoked/expired token.
- **Password length cap** (`max_length=1024`, email `320`) bounds argon2 CPU cost against a
  long-input DoS on the unauthenticated signup/login endpoints.
- **CORS** credentials are enabled **only** for explicitly configured origins, never `*`.
- **`JWT_SECRET_KEY` min length 32** — a weak key fails fast at settings construction.
- **No secrets in the repo**: `.env` is gitignored; `.env.example` is the template; the compose
  `JWT_SECRET_KEY` is a labeled dev-only placeholder.

---

## 12. Concurrency & async correctness

- **Nothing CPU-bound or blocking runs on the event loop.** argon2 hashing and the Alembic migration
  run are dispatched with `asyncio.to_thread`.
- **`Connection` serializes sends.** Each socket is wrapped in a `Connection` that guards `send` and
  `close` with an `asyncio.Lock`, so a broadcast frame and an ack/error frame can't interleave on the
  same socket.
- **One transaction per message (session-scoped UoW).** A WebSocket outlives any single request, so
  it can't reuse the request-scoped `get_session`. Instead the session-scoped outbound adapters
  (`SessionScopedMessageWriter`, `SessionScopedConversationAccess`, `SessionScopedMessageReplayer`)
  each open a fresh session per operation, run the relevant use-case, and commit. The message writer
  commits **per message**.
- **Migrations are safe under concurrent boots** via the advisory lock ([§7](#7-database-schema)).
- **Graceful shutdown** drains live sockets (close `1001`) within a bounded timeout, then stops the
  subscriber and disposes Redis + the DB pool.

---

## 13. Resilience & failure semantics

- **A failed AI or DB call never crashes the socket or worker.** In the receive loop, exceptions
  from `exchange.exchange` become an `error` frame and the loop continues.
- **Persist before broadcast.** The user message is committed before it's broadcast, so a rollback
  can't surface a message that isn't stored.
- **Fail closed on fan-out setup.** If the Redis subscribe can't be established on join, the socket
  is closed `1011` rather than left silently missing cross-replica messages.
- **Dead-socket cleanup.** A socket whose send raises during a broadcast is dropped and unregistered;
  the rest of the conversation is unaffected.
- **Subscriber self-heals.** The per-replica reader reconnects with backoff and resubscribes to its
  active channels after a read failure.
- **Idempotent migrations.** Concurrent replicas serialize on the advisory lock; late starters find
  the schema at head and no-op.

---

## 14. Testing strategy

- **Layout mirrors the source.** `tests/` follows `contexts/{identity,messaging}` down through
  `domain/`, `application/`, `infrastructure/{api,security,redis}`, plus root `tests/test_health.py`
  and `tests/test_logging.py`.
- **Scale.** 165 tests across 33 files. Heaviest: `test_conversation_routes.py` (19),
  `test_websocket_routes.py` (14), `test_conversation.py` (11).
- **The multi-user proof.**
  `test_websocket_routes.py::test_a_message_from_one_user_is_broadcast_to_every_other_user_in_the_conversation`
  drives **two sockets authenticated as two different users** on one conversation and asserts both
  receive the `message.new`, while only the sender receives the `message.ack`.
- **Fakes over infrastructure.** Almost every test uses in-memory fakes (`tests/contexts/*/fakes.py`)
  — fake repositories, hasher, token service, broadcaster, responder, `FixedClock`, etc. — so unit
  tests need no DB or network.
- **One real-infra integration test.** `test_redis_fanout_integration.py` uses a real
  `redis:7-alpine` via **testcontainers** (session-scoped fixture; `pytest.skip`s if Docker or
  testcontainers is unavailable). It stands up **two independent replicas** (each a
  `ConnectionManager` + `RedisConversationSubscriber` + `ConversationRegistry`) on one Redis and
  asserts (a) a message published from replica A reaches a socket on replica B **and** loops back to
  A's own socket via the same subscribe path, and (b) a replica only receives conversations it
  subscribed to.
- **WebSocket route tests** use Starlette's sync `TestClient.websocket_connect(...)` with
  `dependency_overrides` on the `provide_*` deps (fake writer/responder/broadcaster/token service),
  covering the happy path, all close codes, and the "error frame keeps the socket open" cases.
- **CI = pre-commit.** `.github/workflows/ci.yml` runs `uv sync --locked` then
  `uv run pre-commit run --all-files`, which runs ruff / ruff-format / mypy / pytest — the identical
  set that runs on every local commit.

---

## 15. Key decisions & trade-offs

**Made:**
- **Modular monolith, not microservices.** One deployable run as N replicas satisfies the "2+
  replicas + Redis" requirement without distributed-systems overhead, while hexagonal boundaries
  keep a future split-point clean.
- **No domain events / CQRS / event sourcing.** The two contexts coordinate through direct use-case
  calls and reads share the write model. The assignment needs no cross-context reactions or
  history-rebuild, so an event backbone would be complexity without a paying use case. Revisit if a
  real consumer appears (activity feed, audit log, async workflow).
- **SQLAlchemy 2.0 async over SQLModel.** Mature async story and an explicit split between
  persistence models and Pydantic API DTOs.
- **First-message WS auth over a token query param.** Keeps tokens out of logs; the query-param
  approach is only a documented fallback.

**Deferred (production-hardening follow-ups, from `NOTES.md`):**
- **Server-side exactly-once at the replay/live seam** — buffer live frames during replay and
  release them in `seq` order, for a true ordered exactly-once stream (more server state + back
  pressure). Today the client dedupes.
- **Bounded replay for large backlogs** — cap/paginate replay with a deadline and fall back to the
  REST history endpoint past a threshold, so one reconnect can't monopolize a socket.
- **`CREATE UNIQUE INDEX CONCURRENTLY`** for the dedupe constraint on a large live table — build it
  out-of-band, then `ALTER TABLE … ADD CONSTRAINT … USING INDEX` (can't run `CONCURRENTLY` inside
  the advisory-lock transaction).
- **Reconnect backoff/jitter** — a client concern; the server mandates no reconnect policy.
- **Expand/contract migrations** — prefer add-nullable → backfill → enforce → drop across releases
  so old and new code coexist during a rolling deploy.

---

## 16. Glossary & quick-answer index

**Glossary**
- **Bounded context** — an independent model + vocabulary with its own boundary (`identity`,
  `messaging`).
- **Aggregate** — a consistency boundary you load/save as a unit (`User`, `Conversation`,
  `Message`, `RefreshToken`).
- **Value object (VO)** — an immutable, self-validating value equal by content (`Email`,
  `MessageContent`, `MessageId`).
- **Port** — an interface the core declares (`UserRepository`, `MessageBroadcaster`, `TokenService`).
- **Adapter** — a concrete implementation of a port (SQLAlchemy repo, Redis broadcaster, JWT
  service).
- **Composition root** — where adapters are wired into use-cases (`app.py` lifespan +
  `api/dependencies.py`).
- **Keyset pagination** — paging by a cursor on an indexed column (`id < before`), not `OFFSET`.
- **At-least-once** — a message may be delivered more than once; the receiver must dedupe.
- **Idempotency key** — `client_message_id`; a repeat send with the same key is a no-op ack.
- **seq** — the message's monotonic bigserial `id`; both its identity and its order.
- **Correlation id (`connection_id`)** — a `uuid4` set per WebSocket connection on the
  `connection_id_var` contextvar and auto-attached to every JSON log record by `JsonFormatter`, so
  all log lines for one socket share an id.

**Where do I look to answer…**
- *How does auth work?* → [§8.2](#82-rest-auth-auth), [§11](#11-security-model);
  `contexts/identity/…`.
- *How do several users end up in one conversation?* → [§8.3](#83-conversations-rest-conversations)
  for the participant routes, [§6](#6-domain-model) for the aggregate rules,
  [§11](#11-security-model) for who may do what.
- *Show me the whole flow in one picture.* → [§8.0](#80-the-whole-flow-in-one-picture) (six diagrams,
  boot → connect → send → fan-out → teardown, each step cited to code).
- *How does a message travel end to end?* → [§8.0](#80-the-whole-flow-in-one-picture) for the diagrams,
  then [§8.4](#84-websocket-lifecycle-wsconversationsid--the-core)–[§8.5](#85-redis-fan-out) for the prose.
- *What's the wire format?* → [§9](#9-real-time-protocol-reference); `realtime/protocol.py`.
- *What are the delivery guarantees?* → [§10](#10-delivery-guarantees).
- *What's in the database?* → [§7](#7-database-schema); `migrations/versions/`.
- *Where does X live in the code?* → [§5](#5-codebase-map).
- *Why was X built this way / what's deferred?* → [§15](#15-key-decisions--trade-offs); `NOTES.md`.
- *How do I trace one connection's logs?* → [§8.4](#84-websocket-lifecycle-wsconversationsid--the-core); `logging.py` (`connection_id`).
- *How do I run/test it?* → [§3](#3-run--verify).
