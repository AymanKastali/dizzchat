# Notes

What this file is for, quoted from the assignment's Deliverables section:

> **NOTES.md (short):** how it's structured, your WebSocket auth + message protocol decisions, what
> you'd do next, and an honest self-critique of what you cut.

Those four, in that order, plus the requirements I read as ambiguous — which the FAQ asks to be noted
here ("Found something ambiguous or think a requirement is wrong? Note it in NOTES.md and make a
reasonable call"). Nothing else: the alternatives ruled out are in
[ARCHITECTURE.md](./ARCHITECTURE.md), how the system behaves is
[SYSTEM_GUIDE.md](./SYSTEM_GUIDE.md), and anything that doesn't work yet is in
[README.md](./README.md#known-issues).

## How it's structured

A DDD hexagonal modular monolith: one deployable run as two replicas, split into two bounded contexts —
`identity` (users, JWT, argon2) and `messaging` (conversations, participants, messages, **and** real-time
delivery) — plus a `shared/` kernel for clock, DB session factory, migration runner, Redis client, and
`/health`. Each context repeats the same `domain / application / infrastructure(inbound|outbound)` shape,
and the dependency arrow points inward: the domain imports no framework, and infrastructure depends on
the core by implementing the ports the core declares.

Real-time lives *inside* `messaging` rather than in a third context, because delivery is *how* messages
reach clients, not a separate domain — it shares the `Conversation`/`Message` aggregates, so splitting it
out would have put an artificial boundary through one model. Annotated file tree in
[SYSTEM_GUIDE.md § 5](./SYSTEM_GUIDE.md#5-codebase-map).

## WebSocket auth — why first-message auth

The assignment allows a query param, a subprotocol header, or an auth frame as the first message. **I
chose the first-message `auth` frame**, for four reasons:

- The token never appears in a URL, so it can't leak into access logs, proxy logs, referrers, or history.
- Rejection is a clean *application* close code (`4401`) saying exactly what went wrong; a handshake
  rejection can only be an HTTP status the client often can't inspect.
- It behaves identically across clients — no reliance on how one WebSocket implementation exposes headers
  or subprotocols.
- It gives `last_seen_seq` a home next to the token, which makes reconnect replay one round trip instead
  of a follow-up request.

**Rejected — query param** (`?token=…`): simplest, but the token lands in access and proxy logs and in
browser history. Kept only as a documented fallback for a client that genuinely can't send a first frame.

**Rejected — `Sec-WebSocket-Protocol`:** keeps the token out of the URL, but that argument exists for
*protocol negotiation*, not credentials, so smuggling a token through it is a misuse some proxies
normalise away — and rejection is again only a handshake failure, with no application close code.

**The cost, stated plainly:** the socket is accepted *before* it is authenticated, so an unauthenticated
socket exists briefly. It's bounded by `WS_AUTH_TIMEOUT_SECONDS` (default 5s), then closed `4401`.
`accept()` must come first because a close *code* can't be sent on a connection that was never accepted;
the alternative is rejecting the handshake, which loses the diagnosable code.

## Message protocol decisions

**Envelope.** `{"type": ..., "payload": {...}}` for data, `{"type": "error", "error": "<detail>"}` for
failures — one shape to parse, and a type field a client can switch on.

**Only two inbound types**, `auth` and `message.send`. Every other client need is a REST call or was cut,
so the socket's inbound surface stays small enough to validate exhaustively with Pydantic.

**History over REST, not a socket frame** — the split the brief asks me to justify. Paginated reads are
request/response by nature: cacheable, they page cleanly with a cursor, testable with plain HTTP. On the
socket they'd mean inventing request/response correlation (request ids, matching replies) inside a
protocol that otherwise only pushes. So `GET /conversations/{id}/messages` serves history and the socket
stays a pure live channel; auth and conversation CRUD are REST for the same reason. Reconnect catch-up
needs no `history.request` round trip either — the `auth` frame's `last_seen_seq` triggers replay.

**Auth failure is a close code, not an `auth.error` frame.** A connection that cannot authenticate
shouldn't stay open, so no state exists in which such a frame would help.

**`message.new` and `message.ack` are separate.** `new` is the broadcast every subscriber gets; `ack`
confirms *your* send and echoes `client_message_id` with the server-assigned `id`. Merged, clients would
have to tell "my message came back" from "someone else's arrived" by inspecting the sender.

**The bigserial `id` *is* the sequence number** — one value is both identity and order, so a client can't
mismatch them.

## Ambiguities I read differently

**1. "All connected clients of a conversation."** Either several clients of *one* user (phone, laptop, two
tabs) or several *users* sharing a room. The mandated sections say nothing either way, and conversation
REST is listed as "create, list, rename, delete" with no invite endpoint. **I built the multi-user
reading:** conversations have a participant set, any participant may connect, send, and read history,
while rename, delete, and membership stay with the owner (a participant from creation, not removable).
Membership is owner-granted by email; rejected alternatives in
[ARCHITECTURE.md](./ARCHITECTURE.md#alternatives-considered-ruled-out). It cost less than it looks: the
delivery path never changed, because `ConnectionManager` already keyed sockets by conversation rather
than by user. Only the authorization gate moved, plus one join table and three endpoints.

**2. "A client can fetch message history (paginated) on join."** Either pushed over the socket at join, or
available to fetch once joined. **I built the second.** Pushing an unbounded page on every join couples
live delivery to bulk read on one channel and re-sends data the client may already hold; instead a fresh
client reads history over REST and a *reconnecting* one sends `last_seen_seq` for only what it missed. If
a push-on-join `history` frame was the intent it's a small addition — the replay machinery exists and
would need only a `last_seen_seq: 0` default.

## What's built, and an honest self-critique of what I cut

**Built — all four "must build" sections, in full:**

- **Auth + sessions.** Email/password signup and login, argon2 hashing, short-lived JWT access tokens
  plus rotating refresh tokens (stored as a SHA-256 hash of the secret, so a database read can't
  reconstruct one). Every WebSocket authenticates, and the access token is re-validated on *every* send.
- **Conversations + persistence.** REST create/list/rename/delete, messages persisted per conversation
  with sender, role, content, and timestamp, cursor-paginated history, schema via Alembic.
- **Real-time messaging.** WebSocket endpoint per conversation; persist → broadcast → mock assistant
  reply → broadcast, fanned out over Redis pub/sub so a message sent to one replica reaches clients on
  the other. Two replicas run by default. Disconnects, reconnects, and dead sockets are cleaned up, and
  shutdown drains live sockets with close code `1001`.
- **Robustness.** Routing / use case / repository separation, Pydantic at the boundary, domain errors
  mapped to status codes centrally, and a failed AI or DB call answered with an `error` frame that never
  kills the socket or the worker.

**Also built, beyond the required scope:** both Req-2 nice-to-haves (soft-delete + restore, cursor
pagination over offset), one of the three Req-3 nice-to-haves (per-user rate limiting via Redis, keyed
so a user's quota holds across every socket and both replicas), and **one bonus** — message delivery
guarantees: dedupe via a client-supplied message id enforced by a unique constraint, plus at-least-once
redelivery on reconnect via `last_seen_seq`. 196 tests, including a cross-instance fan-out test that
stands up two replicas against one real Redis.

**Cut knowingly, with the reason:**

- **Streamed assistant replies and typing/presence** — the two Req-3 nice-to-haves not taken. Streaming
  needs chunk framing plus an answer for what replay sends; presence needs cross-replica ephemeral state
  (TTL heartbeats, and reconciliation when a replica dies holding sockets).
- **Exactly-once, ordered delivery at the replay/live seam.** The socket joins live delivery *before*
  replay runs, so nothing is missed — but a live frame can arrive ahead of a lower-`seq` replay frame, so
  the client must dedupe by `seq`. The sharpest edge in the build, since it pushes work onto the client.
- **Bounded replay.** `last_seen_seq` replays the full tail, so a long-absent client can trigger a large
  replay. Fine at this scale, an availability concern at real scale.
- **Rate limiting on REST.** The limit guards the socket, as Req-3 asks; `/auth/login` is unthrottled, so
  nothing slows a password-guessing loop. The socket's window is also fixed, which allows 2× the limit
  across a boundary.
- **Retention and an audit trail for delete/restore.** A soft-deleted conversation stays restorable
  forever, and `deleted_at` records state, not who changed it.
- **Revoking a live socket.** Access is checked at connect, so a removed participant keeps *receiving*
  until they disconnect — their sends stop immediately, and a reconnect is refused `4403`.
- **The other three bonuses** (`/metrics`, OAuth, load-test numbers) — the brief allows at most one.
- **REST correlation ids.** Sockets tag their logs with a `connection_id`; HTTP requests don't.

## What I'd do next

In the order I'd actually pick them up:

1. **Close the exactly-once gap at the replay/live seam** — buffer live frames during replay, release in
   `seq` order. Buys the most, since it's the one item pushing real work onto every client; costs
   server-side state and back-pressure.
2. **Bound replay** — page it with a deadline, falling back to REST history past a threshold, so one
   long-absent client can't monopolize a socket.
3. **Rate limit REST**, `/auth/login` first: request middleware keyed on the caller, reusing the same
   Redis counter. The largest remaining security gap.
4. **Retention + audit trail for delete/restore** — purge after N days, record who did what.
5. **Close live sockets on participant removal**, via a cross-replica `participant.removed` publish each
   replica turns into a close.
6. **Gate the assistant on an `@ai` mention**, so a multi-user room isn't answered three times.
7. **REST correlation ids**, matching the socket path's `connection_id`.
8. **Streamed replies, then typing/presence** — that order, since streaming needs only chunk framing
   while presence needs cross-replica ephemeral state.

Two operational follow-ups worth naming: clients should reconnect with exponential backoff and jitter
(the server mandates no policy), and on a large live table the dedupe index wants building
`CONCURRENTLY` out-of-band rather than inside the advisory-lock transaction — see
[SYSTEM_GUIDE.md § 15](./SYSTEM_GUIDE.md#15-key-decisions--trade-offs).
