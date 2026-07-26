# dizzchat — System Guide

> **The *how*.** How the system actually behaves, written against the code that shipped, and the
> source of truth where the docs disagree. Details cite the real file so you can jump to it. To run
> it: [README.md](./README.md). Why it's shaped this way: [ARCHITECTURE.md](./ARCHITECTURE.md).
> Scope decisions and self-critique: [NOTES.md](./NOTES.md).
>
> **Start at [§ 5](#5-how-a-message-flows)** — six diagrams trace one message end to end. Everything
> else fills them in.

---

## 1. What dizzchat is

A real-time AI chat backend. Authenticated users open a WebSocket per conversation and exchange
messages with a bundled **mock assistant** that echoes the input back (`"You said: …"` —
`messaging/infrastructure/outbound/assistant/mock_assistant_responder.py:16`). There is no external
LLM call.

A conversation holds **many participants**. Its owner invites others by email; from then on every
message any participant sends is broadcast to all of them, on whichever replica their sockets live.

It runs as **two identical API replicas** sharing one PostgreSQL database and one Redis instance. A
message sent to a socket on replica A reaches sockets on replica B via **Redis pub/sub**. Messages
persist in Postgres (they survive restarts), are idempotent per a client-supplied key, and are
replayed on reconnect so a client that dropped off catches up.

---

## 2. Architecture & codebase map

A **DDD hexagonal modular monolith**: one deployable, run as N replicas, split into two bounded
contexts — `identity` (supporting) and `messaging` (core, and where the real-time layer lives) — plus
a `shared` kernel. The reasoning is in [ARCHITECTURE.md](./ARCHITECTURE.md); the mechanics are here.

The dependency arrow points **inward**. The domain and application core depend on nothing external;
infrastructure depends on the core by implementing the **ports** the core declares.

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

- **Port** — a `Protocol`/ABC interface declared in `application` (or a repository port in `domain`).
- **Adapter** — a concrete implementation. *Inbound* adapters (`api`) drive the app; *outbound* ones
  (`infrastructure/outbound`) are driven by it.
- **Composition root** — where adapters are wired into use-cases: split between the app
  factory/lifespan (`app.py`, process-level singletons) and per-context `api/dependencies.py`
  (per-request and per-socket wiring via FastAPI `Depends`).

Each context repeats the same `domain / application / infrastructure(inbound|outbound)` shape:

```
src/dizzchat/
  app.py            FastAPI factory + lifespan = composition root (migrates, wires infra, routers)
  main.py           uvicorn console entry point ("dizzchat")
  config.py         pydantic-settings Settings + @lru_cache get_settings()
  logging.py        structured JSON logging + per-connection connection_id correlation (contextvar)

  contexts/identity/                    signup / login / refresh / JWT auth
    domain/user/                        User aggregate; Email, PasswordHash, UserId; UserRepository
                                        + PasswordHasher ports; InvalidEmail, InvalidCredentials,
                                        EmailAlreadyRegistered
    domain/refresh_token/               RefreshToken aggregate (issue/is_active/revoke/rotate),
                                        repository port, InvalidRefreshToken
    application/                        TokenService port; AccessClaims / TokenPair DTOs;
                                        RegisterUser, AuthenticateUser, RefreshAccessToken
    infrastructure/inbound/api/         /auth router, controllers, schemas, DI (get_current_user),
                                        error → HTTP status mapping
    infrastructure/outbound/            Argon2PasswordHasher, JwtTokenService (HS256 + opaque
                                        refresh), SQLAlchemy models + repositories

  contexts/messaging/                   conversations + messages + realtime delivery
    domain/conversation/                Conversation aggregate (lifecycle, ensure_owned_by /
                                        ensure_participant / add_participant / remove_participant);
                                        ConversationId, OwnerId, ParticipantId, ConversationTitle;
                                        Participant read-side VO; repository port; errors
    domain/message/                     Message aggregate (immutable, id = seq); MessageRole,
                                        MessageId, SenderId, ClientMessageId, MessageContent;
                                        repository port; InvalidMessageContent
    application/ports.py                ConversationAccess, UserDirectory, AssistantResponder,
                                        MessageBroadcaster, MessageWriter, MessageReplayer
    application/services/               create/list/rename/delete/restore_conversation,
                                        get_conversation_history, ensure_conversation_access,
                                        post_message, message_exchange, replay_messages,
                                        add/list/remove_participant
    infrastructure/inbound/api/         /conversations router, controllers, schemas, DI, errors
    …/api/realtime/websocket.py         the WS endpoint handler (the core flow)
    …/api/realtime/protocol.py          inbound frame models + outbound frame builders
    …/api/realtime/connection_manager.py        Connection + ConnectionManager (local delivery)
    …/api/realtime/conversation_registry.py     ConversationSubscriber port + ConversationRegistry
    …/api/realtime/rate_limit.py                RateLimiter port (declared beside its consumer)
    infrastructure/outbound/assistant/  MockAssistantResponder ("You said: …")
    infrastructure/outbound/redis/      channels (conv:{id}), message_codec, RedisMessageBroadcaster
                                        (PUBLISH only), RedisConversationSubscriber (per-replica
                                        reader), RedisRateLimiter (per-user fixed window)
    infrastructure/outbound/identity/   IdentityUserDirectory — email → user id (anti-corruption)
    infrastructure/outbound/persistence/ SQLAlchemy models + repositories, and the session-scoped
                                        per-operation UoW adapters (access, message writer, replayer)

  shared/                               the shared kernel
    application/clock.py                Clock port
    infrastructure/inbound/api/         /health router; get_session/SessionDep, get_clock/ClockDep
    infrastructure/outbound/            database (Base, engine, session factory), migrations runner,
                                        redis_client, SystemClock (UTC)
```

---

## 3. Domain model

Aggregates are the consistency boundaries. **Value objects** are immutable, validate once at
construction, and are equal by value — an invalid value raises a domain error instead of being
constructed, so invalid state is unrepresentable past the boundary.

**Identity.** `User` (`domain/user/user.py`) — `User.register(...)` hashes the password by
double-dispatch through the `PasswordHasher` port, so the domain never imports argon2. `Email`
normalizes to lowercase; `PasswordHash` is guaranteed to hold only a hash. `RefreshToken` is a
persisted credential identified by `jti` that stores **only the hash** of its secret; `rotate` revokes
the current token and mints a successor, raising `InvalidRefreshToken` if it isn't active — which is
what rejects replay of a revoked or expired token.

**`Conversation`** (`domain/conversation/conversation.py`) — lifecycle `start` / `rename` / `delete`
(soft) / `restore`, plus **two levels of authorization it enforces itself**:

- `ensure_owned_by(owner_id)` → `NotConversationOwner` — administration: rename, delete, restore, and
  changing the membership.
- `ensure_participant(participant_id)` → `NotConversationParticipant` — taking part: joining the live
  channel, sending, reading history.

Membership lives on the aggregate as `participant_ids: frozenset[ParticipantId]`, mutated by
`add_participant` (idempotent; returns whether it was new) and `remove_participant` (raises
`CannotRemoveConversationOwner` for the owner). `start` seeds the owner, so the owner is always a
participant and can never be locked out. `delete` and `restore` are both **idempotent** — each is a
no-op in the state it leads to, so neither can be corrupted by a retry.

**`Participant`** is a read-side VO pairing a `ParticipantId` with `joined_at`. The aggregate
deliberately holds **ids only**: identity is all it needs to decide access, and `joined_at` carries no
invariant, so it is served from the `list_participants` projection rather than loaded into the
aggregate.

**`Message`** (`domain/message/message.py`) — an **immutable** record and a **separate aggregate** from
`Conversation`. Its identity is `MessageId`, the persisted **bigserial** that is also the ordering key.
`SenderId` is null for assistant messages; `ClientMessageId` is the client's idempotency key;
`MessageContent` rejects blank content; `ConversationTitle` is trimmed and ≤ 200 chars.

> **Why `OwnerId`/`ParticipantId` aren't Identity's `UserId`.** `messaging` defines its own rather than
> importing `identity.UserId`, so the contexts stay decoupled — it stores the user's id as its own
> concept. The values are the same UUID; the type boundary is deliberate. `OwnerId` and `ParticipantId`
> are separate because they name different *roles*, and the call sites read better for it:
> `ensure_owned_by(OwnerId(...))` versus `ensure_participant(ParticipantId(...))` says which rule
> applies. The one question Messaging must ask Identity — resolving an invite email — goes through the
> `UserDirectory` port and its `IdentityUserDirectory` adapter, in infrastructure, where cross-context
> coupling belongs.

---

## 4. Database schema

Five Alembic migrations in a linear chain (`migrations/versions/`). Final schema:

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
| `token_hash` | String | SHA-256 of the secret, never the secret itself |
| `expires_at` | DateTime(tz) | |
| `revoked_at` | DateTime(tz) | nullable — set on rotation/revocation |

### `conversations` (0002)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `owner_id` | UUID | indexed (`ix_conversations_owner_id`) |
| `title` | String(200) | |
| `created_at` / `updated_at` | DateTime(tz) | |
| `deleted_at` | DateTime(tz) | nullable — **soft-delete** marker; clearing it is restore |

### `conversation_participants` (0005)
| Column | Type | Notes |
|---|---|---|
| `conversation_id` | UUID | FK → `conversations.id`; **composite PK** |
| `user_id` | UUID | **composite PK**, indexed (`ix_conversation_participants_user_id`) |
| `joined_at` | DateTime(tz) | |

The composite PK *is* the uniqueness rule — a user cannot be admitted twice, enforced at the database
as well as in the aggregate. The `user_id` index backs `list_for_participant` ("the conversations I'm
in"). The ORM loads the set with `relationship(lazy="selectin")`, which is required rather than
stylistic: the default lazy loader emits I/O on attribute access and raises under asyncio, and
`selectin` batches, so listing N conversations costs one extra query rather than N.

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

- `ix_messages_conversation_id_id` on `(conversation_id, id)` — backs **keyset pagination** and ordered
  replay.
- **`uq_messages_conversation_id_client_message_id`** unique on
  `(conversation_id, client_message_id)` — the dedupe backstop. Postgres treats NULLs as distinct, so
  keyless sends and assistant rows never collide.

### Migration chain
`0001_identity` → `0002_conversations` → `0003_message_role` → `0004_client_message_id` →
`0005_conversation_participants`.

- **0003** adds `role` with a temporary `server_default='user'` to backfill existing rows, then drops
  the default so the app must supply it on every insert; also makes `sender_id` nullable.
- **0004** adds `client_message_id` + the unique constraint. Building the backing index takes an
  `ACCESS EXCLUSIVE` lock and **cannot** use `CREATE UNIQUE INDEX CONCURRENTLY`, because migrations
  run inside a transaction and `CONCURRENTLY` isn't allowed in one. Acceptable on a small table.
- **0005** creates `conversation_participants` and **backfills the owner of every existing
  conversation** as its first participant. The backfill is load-bearing: access is now decided by
  membership, so a conversation without a row would leave its own owner unable to connect, post, or
  read history. It has no `WHERE` clause, so soft-deleted conversations are backfilled too and stay
  restorable.

**Migrations run on boot, serialized** (`migrations/env.py`). Every replica runs `alembic upgrade head`
during startup; inside the migration transaction it first executes `SELECT pg_advisory_xact_lock(721103)`.
The first replica takes the lock and migrates, later starters block and then find the schema already at
head and no-op. Postgres auto-releases the transaction-scoped lock on commit. The runner
(`shared/infrastructure/outbound/migrations.py`) builds the Alembic `Config` **in code** rather than
from `alembic.ini`, specifically so `logging.fileConfig` doesn't reset the structured logger.

---

## 5. How a message flows

**In one sentence:** a client opens a WebSocket to *one* replica; that replica saves each message to
Postgres, publishes it to a Redis channel named after the conversation, and *every* replica — the
publisher included — reads it back off Redis and writes it to its own sockets.

### A. Topology

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

Both API containers are the same image with the same env — only the published host port differs, and
no load balancer sits in front. A replica's live sockets and its set of subscribed channels are
in-memory and private to it: it knows nothing about sockets on the other replica, and doesn't need to.
Redis is the only thing that closes that gap.

### B. Boot — what each replica builds (`app.py:44-83`)

```
  run_migrations() in asyncio.to_thread     ← Alembic is sync; advisory lock 721103 serializes replicas
        ▼
  engine (pool_pre_ping) + session_factory  → app.state
        ▼
  create_redis_client()                     → app.state.redis   (5s socket/connect timeouts, raw bytes)
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
  RedisRateLimiter(redis, SystemClock(), …) → app.state.rate_limiter
        ▼
  ══ yield: serve traffic ══
```

`create_app()` configures logging, builds `FastAPI(lifespan=…)`, adds CORS (credentials enabled only
when `*` is **not** in the origin list), registers both contexts' error handlers, and includes the
health, identity, conversations, and WS routers. `main.py` runs uvicorn with `log_config=None` so it
doesn't clobber the JSON logger.

### C. A client connects

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

- **② comes before ③** because a close *code* can't be sent on a connection that was never accepted.
  A `uuid4` `connection_id` is set on a contextvar here and reset on exit, so every log line for this
  socket carries the same correlation id.
- **⑤ passes for any participant, not only the owner** — this one check is what makes a conversation
  multi-user. It runs **once**, at connect; see [§ 10](#10-security-model) for what that means when a
  membership is revoked.
- **⑦ happens only for the *first* socket** on that conversation on this replica.
  `ConnectionManager.register` reports the 0→1 transition and `ConversationRegistry` turns it into the
  `SUBSCRIBE`; a second socket reuses the existing subscription. If the `SUBSCRIBE` fails, `join`
  rolls back the local registration and the handler closes **1011** — a socket that isn't subscribed
  would silently miss every message from the other replica, so the code **fails closed**.
- **⑦ comes before ⑨ on purpose.** The socket is already receiving live messages before replay reads
  the backlog, so nothing can be lost in between. The cost is ordering at the seam — see
  [§ 8](#8-delivery-guarantees).

### D. One `message.send`, inside replica 1

```
  message.send frame arrives in the receive loop
        ▼
  ⓪ rate limit checked BEFORE parsing ───► over ─────► error frame, socket STAYS OPEN
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

- **⓪ is before the JSON check on purpose**, so an unparseable flood costs quota too. A refused frame
  is never parsed, persisted, broadcast, or shown to the assistant.
- **`from_user` re-checks membership** (`post_message.py:57`) before anything else, so a revoked user
  stops posting at once even on an already-open socket. It then does a pre-flight
  `find_by_client_message_id`; a hit returns `(existing, False)` and `exchange` returns early — **no
  new row, no re-broadcast, no second assistant reply**.
- **Persist before broadcast:** each COMMIT happens before its PUBLISH, so no client is shown a
  message a failed transaction would have erased.
- Notice what's *missing*: the replica never writes to its own sockets here. Every delivery goes
  through Redis — diagram E.
- Any exception from `exchange` (DB or mock-AI) becomes `error("failed to handle message")` and the
  loop continues. A failure never drops the socket.

### E. Fan-out — reaching sockets on both replicas

Alice and Bob are two different users, both participants, connected to different replicas. Alice
sends; both receive.

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
  devices or to two different participants. Adding multiple users changed only the **authorization**
  gate at ⑤ in diagram C — not one line of the delivery path.
- **The sender gets its own message back through Redis.** Replica 1 isn't special-cased: it receives
  its own `PUBLISH` on its own subscription (②) and delivers from there. That leaves exactly one
  delivery path to any socket — `subscriber → ConnectionManager.broadcast` — and no double-delivery.
  It's also why the `SUBSCRIBE` in diagram C must complete before the receive loop starts.
- **Between ② and ④:** the subscriber's reader task takes the message off Redis under a lock, decodes
  the JSON back into a domain `Message`, and hands it to the local `ConnectionManager`, which writes a
  `message.new` frame to every socket in that conversation.
- **⑥ can arrive before ④.** The `message.new` frames are written by the subscriber task and the ack by
  the receive-loop task — two independent tasks with no ordering between them. A client must not assume
  the ack comes first.
- **Replica 2 never reads Postgres here.** The whole message travels inside the Redis payload, so
  fan-out costs one `PUBLISH` plus one decode per replica.

### F. Teardown

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

While running, the subscriber also **repairs itself**: if a read from Redis fails it logs, waits
500 ms, rebuilds the pub/sub connection, and re-`SUBSCRIBE`s every channel it still needs. A brief
Redis outage therefore doesn't cost the replica its subscriptions. Messages published *during* the
outage are lost by pub/sub and recovered instead by the client's next `last_seen_seq` replay. A socket
whose send raises during a broadcast is dropped and unregistered (dead-socket cleanup) without
affecting the rest of the conversation.

### Where each diagram lives in the code

| Diagram | Files |
|---|---|
| B — boot wiring | `app.py:44-83` |
| C — connect, auth, join, replay | `realtime/websocket.py:59-164`; `realtime/conversation_registry.py:38-53` |
| D — validate, persist, publish, ack | `realtime/websocket.py:167-217`; `application/services/message_exchange.py:38-67` |
| E — publish, fan-out, local delivery | `outbound/redis/` (all four files); `realtime/connection_manager.py:83-98` |
| F — leave, unsubscribe, drain | `realtime/conversation_registry.py:55-59`; `app.py:72-83` |

---

## 6. REST endpoints

Request sessions are request-scoped: `shared/.../api/dependencies.py:get_session` opens one per
request, publishes it on `request.state`, and rolls back on error. It deliberately does **not**
commit — a `yield` dependency's teardown runs on the request exit stack, which unwinds only after the
response has been sent, so committing there would acknowledge a write before it was durable. The
commit lives in `shared/.../api/transactional_route.py:TransactionalRoute`, which runs in the window
between producing the response and sending it; routers opt in with `route_class=TransactionalRoute`,
so use-cases just add to the session. Because the commit sits on the router, a session-taking route
registered without it would discard its writes behind a `2xx` — `create_app` calls
`assert_session_routes_are_transactional` and refuses to boot rather than fail silently. Domain errors
map to status codes centrally, one handler per error type, so services never construct HTTP responses.

### `/auth`

- **`POST /signup` → 201** — `RegisterUser`: build `Email` (→ 422 on bad shape); duplicate check →
  `EmailAlreadyRegistered` (**409**); hashing off-loaded via `asyncio.to_thread` (argon2 is CPU-bound);
  `users.add`.
- **`POST /login` → 200** — `AuthenticateUser`: a malformed email yields the same generic
  `InvalidCredentials` as a wrong password (anti-enumeration); a **missing user still runs a dummy
  hash**, so response time doesn't leak existence; on success mint an access JWT plus a freshly
  persisted refresh token.
- **`POST /refresh` → 200** — rotation + revocation. `parse_refresh` splits `"<jti>.<secret>"`;
  `verify_refresh` compares `SHA-256(secret)` with `hmac.compare_digest`; `stored.rotate(...)` raises
  if the token isn't active (rejecting replay), the revocation is persisted, and a **new pair** is
  returned.
- **`GET /me` → 200** — `get_current_user` uses `HTTPBearer(auto_error=False)`; missing creds → 401
  with `WWW-Authenticate: Bearer`; a bad token → 401.

### `/conversations`

All routes require a Bearer token. Reads and sends are open to any **participant**; rename, delete,
restore, and membership changes are **owner-only**.

- **Create → 201** — `Conversation.start(...)` seeds the owner as the first participant, then
  `conversations.create(..., participant_ids=...)`, so the row and its owner membership land in one
  unit of work.
- **List** — `list_for_participant` joins `conversation_participants` on `user_id = ?` and filters
  `deleted_at IS NULL`, newest first: the conversations the caller **owns or was invited to**.
- **Rename / Delete** — `get` (→ 404), `ensure_owned_by` (→ 403), then `rename` or `delete(now)`
  (which sets `deleted_at`). Because `get` already filters deleted rows, deleting an
  already-deleted conversation returns 404 — idempotent from the client's view.
- **`POST /{id}/restore` → 200** — the inverse, and the **only** caller of `get_including_deleted`.
  Then `ensure_owned_by` (→ 403), `restore(now)`. `404` only if the id never existed. Restoring an
  **active** conversation returns 200 and changes nothing, including `updated_at`, so a retried undo is
  safe — `409 "not deleted"` would fail the retry. Delete never touched the message or participant
  rows, so clearing `deleted_at` brings the full history and membership back with no data repair.
- **`GET /{id}/messages`** — cursor pagination. Query `before` (int ≥ 1, optional) + `limit` (default
  50, 1–100). `get` (→ 404), `ensure_participant` (→ 403), then **over-fetch by one**, compute
  `has_more = fetched > limit`, trim, and set `next_cursor = items[-1].id if has_more else None`. The
  query is **keyset** — `WHERE conversation_id = ? [AND id < before] ORDER BY id DESC LIMIT ?` —
  backed by `ix_messages_conversation_id_id`.
- **`POST /{id}/participants` → 204** — `ensure_owned_by` (→ **403**, only the owner invites),
  `users.find_id_by_email` via the `UserDirectory` port (`None` → `ParticipantUserNotFound`, **404**),
  then `add_participant` and — **only if the membership is new** — the row insert. Re-inviting is a
  successful no-op, so a retrying client cannot duplicate; the composite PK is the backstop if two
  invites race past the in-aggregate check.
- **`GET /{id}/participants` → 200** — `ensure_participant` (→ 403), then `[{user_id, joined_at}]`,
  oldest first.
- **`DELETE /{id}/participants/{user_id}` → 204** — one rule covers both kick and leave: permitted if
  the actor **is the owner** or is removing **themselves**, else 403. `remove_participant` then refuses
  the owner (**409**) and rejects someone who never joined (403), so a refusal is distinguishable from
  a no-op.

---

## 7. WebSocket protocol

**Endpoint:** `ws://<host>/ws/conversations/{conversation_id}`.
**Envelope:** `{"type": ..., "payload": {...}}` for data; `{"type": "error", "error": "<detail>"}` for
failures. Inbound frames are Pydantic-validated; the only two inbound types are `auth` and
`message.send` (`realtime/protocol.py`).

```jsonc
// inbound — first frame, authenticate the connection
{ "type": "auth", "payload": { "token": "<access_token>", "last_seen_seq": null } }

// inbound — send a message
{ "type": "message.send",
  "payload": { "content": "hello", "client_message_id": "<uuid or null>" } }
```

```jsonc
// outbound
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
| 4401 | auth failed — timeout, non-JSON, invalid frame, or a bad/expired token (including on a later send) |
| 4403 | not a participant of the conversation, or it doesn't exist |
| 1011 | internal — e.g. the Redis fan-out subscription couldn't be established (fail closed) |
| 1001 | server shutting down (graceful socket drain) |

Bad JSON, an invalid `message.send`, or exceeding the rate limit returns an `error` frame and **keeps
the socket open**; only auth and access failures close it.

---

## 8. Delivery guarantees

Two guarantees ship — the assignment's one chosen bonus.

**Idempotent send.** `client_message_id` is a client UUID acting as an idempotency key. Re-sending the
same value returns the existing message's `id` in a `message.ack` — no second row, no re-broadcast, no
second assistant reply. Enforced in two layers: the pre-flight `find_by_client_message_id` in
`PostMessage.from_user`, and the unique constraint
`uq_messages_conversation_id_client_message_id` as the race backstop. NULL client ids are distinct in
Postgres, so keyless sends never collide.

**Reconnect replay (`last_seen_seq`).** On (re)connect the client may include it in the `auth` payload:
`null` — no replay, load history over REST; `0` — full replay; `N` — messages with `seq > N`,
oldest-first, as `message.new` frames. `ReplayMessages` paginates `list_since` 500 at a time,
ascending, so a long backlog streams in order.

**The seam trade-off.** Replay begins **after** the socket has joined live delivery, so no message is
missed — but the contract is **at-least-once and *not* ordered at the seam**: a live frame can arrive
ahead of a lower-`seq` replay frame. So the client must apply each `seq` **at most once** — a
*seen-set*, not a high-water mark, which would drop the later lower-`seq` frames — and order by the
`id` each frame carries. There is no server-side exactly-once buffering; see
[NOTES.md](./NOTES.md#what-i-cut--an-honest-read).

---

## 9. Rate limiting

`RedisRateLimiter` (`outbound/redis/redis_rate_limiter.py`) implements the `RateLimiter` port
(`realtime/rate_limit.py`) — the second, independent use of Redis, unrelated to pub/sub.

- **Fixed window.** `window = int(clock.now().timestamp()) // window_seconds`, key
  `ratelimit:ws:{user_id}:{window}`, then `INCR` + `EXPIRE` **in one transaction** — as two round
  trips, a crash between them would leave a key with no TTL and lock that user out for good. Allowed
  while the resulting count is `<= limit`.
- **The window number is part of the key**, so each window has its own self-expiring key: no sweeper,
  and re-applying the TTL can't slide the window forward and starve a busy client.
- **Per user, shared across replicas.** The counter lives in the Redis both replicas share, so one
  quota covers every socket that user holds on either instance.
- `WS_RATE_LIMIT_MESSAGES` (default 20) per `WS_RATE_LIMIT_WINDOW_SECONDS` (default 10). A limit of `0`
  disables the check and never touches Redis.
- **Fails open** — any Redis error is logged at WARNING and the frame is allowed. Largely theoretical:
  `registry.join` already fails *closed* with `1011` when Redis is down, so a socket can't reach the
  receive loop without Redis.
- **Accepted flaw:** a fixed window allows up to `2 × limit` frames back to back across a boundary.

---

## 10. Security model

- **First-message WebSocket auth**, within `WS_AUTH_TIMEOUT_SECONDS` or close `4401`. Keeps tokens out
  of URLs and access logs and gives a clean rejection close code.
- **The token is re-validated on every send.** `decode_access` runs again per `message.send`; an
  expired token closes the socket — a socket can't outlive its access token.
- **Two-level authorization.** Reading and sending require **membership**; rename, delete, restore, and
  membership changes require **ownership**. The owner is seeded as a participant and cannot be removed,
  so ownership always implies access. Membership is re-checked on **every** send
  (`PostMessage.from_user`), not only at connect.
- **Known limitation — revocation doesn't close live sockets.** The connect-time check runs once, so a
  removed participant keeps *receiving* broadcasts until their socket drops. Their *sends* are already
  refused and a reconnect is closed `4403`.
- **Invited users are resolved, never asserted.** An invite names an email that must belong to a
  registered user (`404` otherwise), so a mistyped identifier cannot become a phantom participant.
- **Per-user rate limit on the socket**, counted before parsing, so an authenticated client cannot
  spend the server's DB/AI budget in a loop and unparseable floods are capped too. It fails open if
  Redis is unreachable, and it counts inbound socket frames only.
- **Passwords:** argon2id, verified with the library's constant-time check, always hashed via
  `asyncio.to_thread` (off the loop). `max_length=1024` on the password (and 320 on the email) bounds
  argon2 CPU cost against a long-input DoS on the unauthenticated endpoints.
- **Refresh tokens are opaque** `"<jti>.<secret>"` strings; only `SHA-256(secret)` is stored, so a DB
  read can't reconstruct a usable token. Refresh rotates and rejects replay.
- **CORS** credentials are enabled only for explicitly configured origins, never `*`.
  **`JWT_SECRET_KEY` min length 32** — a weak key fails fast at settings construction.
- **No secrets in the repo:** `.env` is gitignored, `.env.example` is the template, and the compose
  `JWT_SECRET_KEY` is a labeled dev-only placeholder.

---

## 11. Concurrency & resilience

- **Nothing CPU-bound or blocking runs on the event loop.** argon2 hashing and the Alembic migration
  run are dispatched with `asyncio.to_thread`.
- **`Connection` serializes sends.** Each socket is wrapped in a `Connection` guarding `send` and
  `close` with an `asyncio.Lock`, so a broadcast frame and an ack or error frame can't interleave on
  the same socket.
- **One transaction per message (session-scoped UoW).** A WebSocket outlives any single request, so it
  can't reuse the request-scoped `get_session`. The session-scoped outbound adapters
  (`SessionScopedMessageWriter`, `SessionScopedConversationAccess`, `SessionScopedMessageReplayer`)
  each open a fresh session per operation, run the use-case, and commit — the writer per message.
- **A failed AI or DB call never crashes the socket or worker**; it becomes an `error` frame and the
  receive loop continues.
- **Fail closed on fan-out setup, self-heal afterwards.** A failed subscribe on join closes the socket
  `1011`; a failed read reconnects with backoff and resubscribes.
- **Graceful shutdown** drains live sockets (close `1001`) within a bounded timeout — a timeout is
  logged and swallowed — then stops the subscriber and disposes Redis and the DB pool.
- **Migrations are safe under concurrent boots** via the advisory lock ([§ 4](#4-database-schema)).

---

## 12. Testing

- **Layout mirrors the source.** `tests/` follows `contexts/{identity,messaging}` down through
  `domain/`, `application/`, `infrastructure/{api,security,redis}`, plus root `test_health.py` and
  `test_logging.py`.
- **Scale.** 190 tests across 35 files. Heaviest: `test_conversation_routes.py` (24),
  `test_websocket_routes.py` (18), `test_conversation.py` (15).
- **The multi-user proof.**
  `test_websocket_routes.py::test_a_message_from_one_user_is_broadcast_to_every_other_user_in_the_conversation`
  drives **two sockets authenticated as two different users** on one conversation and asserts both
  receive the `message.new` while only the sender receives the `message.ack`.
- **Fakes over infrastructure.** Almost every test uses in-memory fakes (`tests/contexts/*/fakes.py`)
  — repositories, hasher, token service, broadcaster, responder, `FixedClock` — so unit tests need no
  DB or network.
- **Two real-infra suites** on a real `redis:7-alpine` via **testcontainers** (session-scoped
  `redis_url` fixture; `pytest.skip`s when Docker or testcontainers is unavailable):
  - `test_redis_fanout_integration.py` stands up **two independent replicas** (each a
    `ConnectionManager` + `RedisConversationSubscriber` + `ConversationRegistry`) on one Redis and
    asserts (a) a message published from replica A reaches a socket on replica B **and** loops back to
    A's own socket through the same subscribe path, and (b) a replica only receives conversations it
    subscribed to.
  - `test_redis_rate_limiter.py` covers the counter against real Redis — allow-up-to-the-limit,
    per-user isolation, window rollover (via an injected movable clock, so no sleeping), the key's TTL,
    `limit=0` disabling the check, fail-open on an unreachable Redis, and **two limiter instances
    sharing one quota**, the cross-replica claim a fake could not prove.
- **WebSocket route tests** use Starlette's sync `TestClient.websocket_connect(...)` with
  `dependency_overrides` on the `provide_*` deps, covering the happy path, all close codes, and the
  "error frame keeps the socket open" cases.
- **CI = pre-commit.** `.github/workflows/ci.yml` runs `uv sync --locked` then
  `uv run pre-commit run --all-files` — ruff, ruff-format, mypy, pytest, the identical set that runs on
  every local commit.

---

## 13. Glossary

- **Bounded context** — an independent model and vocabulary with its own boundary (`identity`,
  `messaging`).
- **Aggregate** — a consistency boundary loaded and saved as a unit (`User`, `Conversation`,
  `Message`, `RefreshToken`).
- **Value object** — an immutable, self-validating value equal by content (`Email`, `MessageContent`,
  `MessageId`).
- **Port** — an interface the core declares (`UserRepository`, `MessageBroadcaster`, `TokenService`).
- **Adapter** — a concrete implementation of a port (SQLAlchemy repo, Redis broadcaster, JWT service).
- **Composition root** — where adapters are wired into use-cases (`app.py` lifespan +
  `api/dependencies.py`).
- **Keyset pagination** — paging by a cursor on an indexed column (`id < before`), not `OFFSET`.
- **At-least-once** — a message may be delivered more than once; the receiver must dedupe.
- **Idempotency key** — `client_message_id`; a repeat send with the same key is a no-op ack.
- **seq** — the message's monotonic bigserial `id`; both its identity and its order.
- **Correlation id (`connection_id`)** — a `uuid4` set per WebSocket connection on a contextvar and
  auto-attached to every JSON log record, so all log lines for one socket share an id.
