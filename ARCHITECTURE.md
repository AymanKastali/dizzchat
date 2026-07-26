# Architecture

> The *why*: the decisions behind the shape of the system, and what was ruled out. For the *how* —
> flows, schema, protocol, guarantees — read [SYSTEM_GUIDE.md](./SYSTEM_GUIDE.md). To run it, see
> [README.md](./README.md).

## The shape

A **modular monolith** — one deployable run as N replicas — with **hexagonal layering** and two
bounded contexts. One service scaled horizontally meets the "2+ replicas fanning out over Redis"
requirement without microservice overhead, while the hexagonal boundaries keep a future split clean.

- **`identity`** (supporting) — users, signup/login, JWT access + refresh, password hashing.
- **`messaging`** (**core**) — conversation CRUD, membership, message persistence and history, **and**
  the real-time layer: the WebSocket endpoint, connection lifecycle, Redis fan-out, delivery
  guarantees. Real-time lives here rather than in a third context because delivery is *how* messages
  reach clients, not a separate domain — it shares the `Conversation`/`Message` aggregates, so
  splitting it out would run a boundary through one model. This is where the differentiating work is.
- **`shared/`** — the shared kernel: clock, session factory, migration runner, Redis client,
  `/health`. Cross-context technical concerns with no domain of their own.

**One cross-context dependency.** Admitting a participant by email needs `messaging` to resolve an
`identity` user. It goes through a `UserDirectory` port implemented by `IdentityUserDirectory` in
`messaging/infrastructure/outbound/identity/` — an anti-corruption layer that constructs Identity's
`Email` and hands back a bare `UUID`, so Identity's types never reach Messaging's domain or use
cases.

**The dependency rule.** This is hexagonal, not classic layered: the arrow points *inward*. The domain
and application core depend on nothing; infrastructure depends on the core by implementing the ports
the core declares. Ports are `Protocol`/ABC interfaces in `application` (repository ports in
`domain`); inbound adapters (`api`) drive the app, outbound adapters (`infrastructure/outbound`) are
driven by it. The domain imports no FastAPI, SQLAlchemy, or Redis — which is what makes swapping any
of them an adapter-level change. Diagram and annotated file tree in
[SYSTEM_GUIDE.md § 2](./SYSTEM_GUIDE.md#2-architecture--codebase-map).

## Decisions

- **SQLAlchemy 2.0 async + asyncpg, Alembic for migrations.** Chosen over SQLModel for a mature async
  story and an explicit split between persistence models and Pydantic API DTOs.
- **WebSocket auth via a first-message `auth` frame.** Keeps the token out of URLs and access logs,
  behaves identically on every client, and makes rejection an application close code (`4401`) rather
  than an opaque handshake failure. The two rejected alternatives and the cost of this one are argued
  in [NOTES.md § WebSocket auth](./NOTES.md#websocket-auth).
- **The token is re-validated on every privileged action**, not only at connect, so a socket never
  outlives its credential. Conversation membership is re-checked the same way, so revoking it stops
  that user posting immediately.
- **REST for request/response, the socket for pushes.** History is a paginated REST read; the only
  inbound frames are `auth` and `message.send`. Correlated request/response inside an
  otherwise push-only protocol would be a second protocol. Argued in
  [NOTES.md § Message protocol](./NOTES.md#message-protocol).
- **Every delivery goes through Redis, including the sender's own.** The producing replica is not
  special-cased: it publishes to `conv:{id}` and receives its own message back on its own
  subscription. That leaves one delivery path (`subscriber → ConnectionManager`) instead of one for
  local sockets and another for remote ones — and no double-delivery to reason about.
- **Persist before broadcast.** Each message is committed before it is published, so no subscriber can
  be shown a message a rollback would erase.
- **Fail closed on fan-out setup, fail open on the rate limit.** A socket whose Redis `SUBSCRIBE`
  fails is closed `1011` rather than served, because it would silently miss every cross-replica
  message. The rate limiter does the opposite when Redis is unreachable — it allows the frame, because
  a limit is a protection, not an authorization rule.
- **The rate limit guards the transport, not the use case.** It is checked in the socket receive loop
  *before* parsing, so malformed floods cost quota too; inside `MessageExchange` it could never see
  them. Its port sits beside its consumer (`realtime/rate_limit.py`) rather than in
  `application/ports.py` for the same reason. Exceeding it answers with an `error` frame, not a close —
  a burst shouldn't cost a legitimate client its connection.
- **Two levels of access control on one aggregate.** `conversations.owner_id` names the administrator;
  `conversation_participants` names who may take part. `Conversation` enforces both itself:
  `ensure_participant` gates joining the live channel, sending, and reading history, while
  `ensure_owned_by` gates rename, delete, restore, and membership changes. `Conversation.start` seeds
  the owner as a participant and the owner cannot be removed, so a conversation can never end up
  locking out its own owner.
- **One named exception to the soft-delete filter, not a flag.** Restore must read a deleted row;
  every other read must not. So the repository port grew `get_including_deleted` with exactly one
  caller — an exception you can find by grep, rather than a boolean a future caller could flip.
- **No domain events, CQRS, or event sourcing.** The two contexts coordinate through direct use-case
  calls, and reads share the write model. Nothing here needs cross-context reactions or a history
  rebuild, so an event backbone would be complexity without a paying consumer. Worth revisiting if
  one appears — an activity feed, an audit log, an async workflow.

## Ruled out

- **Microservices** (auth / chat / gateway) — operational overhead unjustified at this size;
  monolith-as-replicas meets the horizontal-scale requirement directly.
- **In-process pub/sub with no Redis** — fails the 2+ replica fan-out requirement outright.
- **The token as a query param** — ends up in access logs, proxy logs, and browser history. Kept only
  as a documented fallback for a client that cannot send a first frame.
- **Self-service conversation join**, where any authenticated user joins any conversation whose id
  they know. The cheapest way to get several users into a room, but it reduces authorization to a
  capability URL. Membership is owner-granted by email instead.
- **Sliding-window rate limiting** (a Redis sorted set trimmed on every frame) — exact, with no
  boundary burst, but it costs extra round trips and per-frame cleanup for precision a 20-per-10s
  limit doesn't need. A fixed-window `INCR` on a self-expiring key was chosen instead; the
  2×-across-a-boundary flaw is stated in [NOTES.md](./NOTES.md#what-i-cut--an-honest-read).
- **Rate limiting inside `MessageExchange`** — would put the rule in the core use case, where
  malformed frames never arrive, so garbage floods could not be counted.
- **Reusing the compose Postgres/Redis for tests.** Testcontainers won, for a self-contained suite:
  one real `redis:7-alpine` backs the fan-out and rate-limiter tests and skips automatically when
  Docker is unavailable. Everything else runs on in-memory fakes, so the suite stays fast and needs no
  network.
- **`docker compose up --scale api=2`** — two explicit services (`api` :8000, `api2` :8001) won,
  because fixed distinct ports make the cross-replica demo copy-pasteable. `--scale` remains the
  answer for a real deployment.
