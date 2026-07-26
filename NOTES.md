# Notes — decisions & deferred work

The decisions behind the real-time layer, the one requirement I read as ambiguous, an honest account of
what I cut, and the follow-ups a production hardening pass would pick up. Nothing here is a known bug.

## How it's structured

A DDD hexagonal modular monolith: one deployable run as two replicas, split into two bounded contexts —
`identity` (users, JWT, argon2) and `messaging` (conversations, participants, messages, **and** the
real-time delivery layer) — plus a `shared/` kernel for clock, DB session factory, migration runner,
Redis client, and `/health`. Each context repeats the same
`domain / application / infrastructure(inbound|outbound)` shape, and the dependency arrow points
inward: the domain imports no framework, and infrastructure depends on the core by implementing the
ports the core declares.

Real-time lives *inside* `messaging` rather than in a third context, because delivery is *how* messages
reach clients, not a separate domain — it shares the `Conversation`/`Message` aggregates, so splitting it
out would have put an artificial boundary through one model.

Annotated file tree in [SYSTEM_GUIDE.md § 5](./SYSTEM_GUIDE.md#5-codebase-map); the reasoning and the
alternatives that were ruled out are in [ARCHITECTURE.md](./ARCHITECTURE.md).

## WebSocket auth — why first-message auth

The assignment allows a query param, a subprotocol header, or an auth frame as the first message. **I
chose the first-message `auth` frame.**

- The token never appears in a URL, so it can't leak into access logs, proxy logs, referrers, or browser
  history.
- Rejection is a clean *application* close code (`4401`) that says exactly what went wrong. A handshake
  rejection can only be an HTTP status the client often can't inspect.
- It behaves identically across every client — no reliance on how a particular WebSocket implementation
  exposes headers or subprotocols.
- It gives `last_seen_seq` a natural home next to the token, which is what makes reconnect replay a
  single round-trip instead of a follow-up request.

**Rejected — query param** (`?token=…`): simplest to implement, but the token lands in access and proxy
logs and in browser history. Kept only as a documented fallback if a client genuinely can't send a first
frame.

**Rejected — `Sec-WebSocket-Protocol` subprotocol:** keeps the token out of the URL, but the browser
`WebSocket` constructor's protocol argument exists for *protocol negotiation*, not credentials, so
smuggling a token through it is a misuse that some proxies and servers normalise away. Rejection is again
only a handshake failure, with no application close code.

**The cost, stated plainly:** the socket is accepted *before* it is authenticated, so an unauthenticated
socket exists for a moment. It's bounded by `WS_AUTH_TIMEOUT_SECONDS` (default 5s), after which the server
closes `4401`. `accept()` has to come first because a close *code* can't be sent on a connection that was
never accepted — the alternative is rejecting the handshake, which loses the diagnosable code.

## Message protocol decisions

**Envelope.** `{"type": ..., "payload": {...}}` for data, `{"type": "error", "error": "<detail>"}` for
failures — one shape to parse, and a type field a client can switch on.

**Only two inbound types**, `auth` and `message.send`. Every other client need is either a REST call or
was cut, so the socket's inbound surface stays small enough to validate exhaustively with Pydantic.

**History over REST, not a socket frame.** Paginated reads are request/response by nature: they're
cacheable, they page cleanly with a cursor, and they're testable with plain HTTP. Putting them on the
socket would mean inventing request/response correlation (request ids, matching replies) inside a
protocol that otherwise only pushes. So `GET /conversations/{id}/messages` serves history, and the socket
stays a pure live channel. Reconnect catch-up doesn't need a `history.request` round-trip either — the
`auth` frame's `last_seen_seq` triggers replay directly.

**Auth failure is a close code, not an `auth.error` frame.** A connection that cannot authenticate should
not stay open, so there's no state in which an `auth.error` frame would be useful. `4401` (and `4403` for
a conversation the caller doesn't own) is unambiguous to any client.

**`message.new` and `message.ack` are separate.** `message.new` is the broadcast every subscriber gets;
`message.ack` confirms *your* send and echoes `client_message_id` with the server-assigned `id`. Merging
them would force clients to distinguish "my message came back" from "someone else's arrived" by inspecting
the sender.

**The bigserial `id` *is* the sequence number.** No separate `seq` field on the wire: one value is both
identity and order, so a client can't mismatch them.

**No `typing`/presence frames.** A nice-to-have, cut — see below.

## Multi-user conversations — the reading I changed my mind about

The brief says users "**join conversations**" and that messages reach "all connected **clients** of a
conversation". That reads two ways: several clients of *one* user (their phone, laptop, two tabs), or
several *users* sharing a room. The four mandated sections say nothing either way — conversation REST
is "create, list, rename, delete", with no invite or share endpoint.

**I first built the single-user reading, then built the second.** Conversations now have a
participant set: any participant may open a socket, send, and read history, while rename, delete, and
the membership itself stay with the owner (who is a participant from creation and cannot be removed).

What this cost is worth recording, because it is *less* than it looks: **the delivery path did not
change at all.** `ConnectionManager` already keyed sockets by conversation rather than by user, and
the Redis subscriber already re-broadcast to every replica's local sockets, so "all connected clients"
was never single-user in the transport. The only thing standing in the way was the authorization
gate — `ensure_owned_by` on connect, send, and history. That is now `ensure_participant`, plus one
join table (`conversation_participants`, migration 0005) and three endpoints.

**Membership is granted by the owner, by email** (`POST /conversations/{id}/participants`). Two
alternatives were rejected:

- **Self-service join** (`POST /conversations/{id}/join`, anyone with the id) — much less code, but
  authorization degrades to "knows the UUID", which is a capability URL, not access control. That
  sits badly against the brief's "no unauthenticated WebSocket access" constraint.
- **Invite by user id** — no cross-context lookup needed, but nothing validates the user exists, so a
  mistyped UUID becomes a silent phantom participant that can never connect.

Resolving the email needs Messaging to ask Identity about a user it does not own. That goes through a
`UserDirectory` port whose only implementation, `IdentityUserDirectory`, lives in
`messaging/infrastructure/outbound/identity/`. It constructs Identity's `Email` and returns a bare
`UUID`, so Identity's types never reach Messaging's domain or use cases — an anti-corruption layer,
placed in infrastructure precisely because that is where cross-context coupling belongs.

**The assistant still replies to every user message.** `MessageExchange` is untouched, so in a
three-person room the mock answers all three. That matches the brief's AI-chat framing and kept the
send path and its tests stable, but it is the wrong behaviour for real group chat; gating on an `@ai`
mention is the small next step.

**The honest gap: a removed participant keeps *receiving* until they disconnect.** Access is checked
once, at connect, so revoking a membership does not close a socket that is already open. Their
*sends* stop at once — `PostMessage` re-checks membership on every message, deliberately — and a
reconnect is refused with `4403`. Closing live sockets on removal would need a cross-replica
revocation event (a `participant.removed` publish that each replica turns into a close), which is
real complexity for a case the assignment never raises. Not built, named here instead.

## An ambiguity I read differently

Requirement 2 says "a client can fetch message history (paginated) **on join**". That reads two ways:
history *pushed over the socket* at join time, or history *available to fetch* once a client joins.

**I built the second.** A join that automatically pushes an unbounded page of history couples two
concerns — live delivery and bulk read — onto one channel, and it means every reconnect re-sends data the
client may already hold. Instead a fresh client loads history over REST and a *reconnecting* client sends
`last_seen_seq` to get only what it missed. That covers the intent — no client can join and be unable to
see history — while keeping bulk reads off the live path.

If the intent really was a push-on-join `history` frame, it's a small addition: the replay machinery
already exists and would just need a `last_seen_seq: 0` default at join.

## Deliberate decisions

### No domain events / CQRS / event sourcing
The two contexts coordinate through direct use-case calls, not a domain-event bus, and reads share
the write model. The assignment needs neither cross-context reactions nor an audit/history rebuild,
so an event backbone would be complexity without a paying use case. Revisit if requirements grow a
real consumer — an activity feed, an audit log, or asynchronous cross-context workflows.

### Modular monolith, not microservices
One deployable with two bounded contexts kept behind module boundaries (hexagonal layering, ports
and adapters). This preserves a clean split-point later without paying distributed-systems cost now.

## What I cut, and the honest cost

I took **one** bonus, as instructed — message delivery guarantees. Everything below was skipped
knowingly, and each line is the real consequence rather than a reassurance:

- **Ordered exactly-once delivery.** Replay is at-least-once and *unordered at the seam*, so a client
  tracking a high-water mark instead of a seen-set will drop replayed messages, and one that doesn't dedupe
  will render duplicates. This is the sharpest edge in the build — it pushes real work onto the client.
  (Remedy below.)
- **Bounded replay.** A client away for a long time can trigger an arbitrarily large replay on one socket.
  Fine at assignment scale, an availability problem at real scale. (Remedy below.)
- **Per-user rate limiting.** An authenticated client can send as fast as it likes; the only gate on the
  send path is token validity. This is the most obvious production gap.
- **Conversation restore.** Delete is a soft-delete, but with no restore endpoint a deleted conversation is
  unreachable without direct DB access — so the `deleted_at` column currently buys auditability, not undo.
- **Streamed assistant replies and typing/presence indicators.** The mock returns one whole `message.new`.
  Streaming would need chunk framing and a terminal marker; presence would need cross-replica state, which
  nothing in the system tracks today.
- **`/metrics`, OAuth login, load-test numbers.** The other three bonuses, untaken — only one was allowed.
- **REST correlation ids.** Every WebSocket connection tags its logs with a `connection_id`; REST requests
  have no equivalent, so an HTTP-side investigation has less to grep on.
- **A replica dying drops its sockets.** There is no server-side session migration; clients reconnect (to
  either replica) and catch up with `last_seen_seq`. Redis pub/sub is also fire-and-forget, so a message
  published while Redis is down is committed to Postgres but never fanned out live — recovery is again
  reconnect + replay. This is why Postgres, not Redis, is the source of truth.

## Delivery-guarantee follow-ups (from the delivery-guarantees slice)

### Server-side exactly-once at the replay/live seam
Reconnect replay is **at-least-once and unordered at the seam**: because the socket joins live
delivery before replay runs (so no message is missed), a live frame can arrive ahead of a lower-`seq`
replay frame. The current contract pushes dedup/ordering to the client (apply each `seq` once via a
seen-set; order by the `id` each frame carries). A stricter server-side option would buffer live
frames during replay and release them in `seq` order for a true exactly-once, ordered stream — more
server state and back-pressure to manage, deferred until a client can't dedupe.

### Bounded replay for large backlogs
`_replay_missed` currently streams the full `seq > last_seen_seq` tail. A client that has been away a
long time could trigger a large replay. Production would cap this — page it with a deadline and fall
back to "catch up via the REST history endpoint" past a threshold — so a single reconnect can't
monopolize a socket.

### `CREATE UNIQUE INDEX CONCURRENTLY` for the dedupe constraint
Migration 0004 adds `uq_messages_conversation_id_client_message_id`. Building it takes an
`ACCESS EXCLUSIVE` lock, briefly blocking reads/writes on `messages`. It cannot use
`CREATE INDEX CONCURRENTLY`, because migrations run inside a transaction guarded by
`pg_advisory_xact_lock` (see `migrations/env.py`) and `CONCURRENTLY` is not allowed inside a
transaction. Acceptable on a small/empty table. On a large live table, build the index
`CONCURRENTLY` out-of-band, then `ALTER TABLE ... ADD CONSTRAINT ... USING INDEX`.

## Client-side follow-ups

### Reconnect backoff / jitter
The server exposes `last_seen_seq` replay but mandates no reconnect policy. A production client
should reconnect with exponential backoff and jitter to avoid a thundering herd after a shared
outage. Out of scope for the backend.

## Operational notes

### Migration strategy
Prefer expand/contract for schema changes so old and new code can run against the same schema during
a rolling deploy (add nullable column → backfill → enforce → drop, across separate releases).
Concurrent replica boots all run `upgrade head`; `pg_advisory_xact_lock` serializes them, so later
starters block, then find the schema already at head and no-op.

Migration 0005 (`conversation_participants`) is expand-only and **backfills the owner of every
existing conversation as its first participant**. That backfill is not cosmetic: access is now decided
by membership, so without a row per conversation its own owner would lose the ability to connect,
post, or read history. It has no `WHERE` clause, so soft-deleted conversations are backfilled too and
stay restorable. Old code tolerates the new table, but new code requires it, so the table must exist
before the new image serves traffic — which the boot-time `upgrade head` guarantees.

### Security choices worth recording
- **Refresh tokens are opaque** `<jti>.<secret>` strings; only a SHA-256 hash of the secret is
  stored, so a database read cannot reconstruct a usable token.
- **Access tokens are re-validated on every WebSocket send**, so an expired token can't keep sending
  on an already-open socket.
- **Password length is capped** (1024 chars) to bound argon2 CPU cost against a long-input DoS.
- **CORS credentials** are enabled only for explicitly configured origins, never the `*` wildcard.
