# Notes

## How it's structured

A hexagonal modular monolith: one deployable run as two replicas, split into two bounded contexts —
`identity` (users, JWT, argon2) and `messaging` (conversations, participants, messages, **and** the
real-time delivery layer) — plus a `shared/` kernel for the clock, session factory, migrations, Redis
client, and `/health`. Each context repeats `domain / application / infrastructure(inbound|outbound)`,
and the arrow points inward: the domain imports no framework, and infrastructure implements its ports.

Real-time lives *inside* `messaging` rather than in a third context, because delivery is *how*
messages reach clients, not a separate domain — it shares the `Conversation`/`Message` aggregates, so
splitting it out would put an artificial boundary through one model. The replicas are interchangeable
and hold no state of their own: Postgres is the source of truth, Redis pub/sub is fan-out only.
Annotated file tree in [SYSTEM_GUIDE.md § 2](./SYSTEM_GUIDE.md#2-architecture--codebase-map); ruled-out
alternatives in [ARCHITECTURE.md](./ARCHITECTURE.md).

## WebSocket auth

The token can travel as a query param, a subprotocol header, or an auth frame. **I chose the
first-message `auth` frame:**

- It never puts the token in a URL, so it can't leak into access logs, proxy logs, or history.
- Rejection is an *application* close code (`4401`) that says what went wrong; a handshake rejection
  is only an HTTP status the client often can't inspect.
- It behaves identically on every client, with no reliance on how a given implementation exposes
  headers or subprotocols.
- It gives `last_seen_seq` a home next to the token, making reconnect replay one round-trip.

**Rejected — query param** (`?token=…`): simplest, but the token lands in access and proxy logs and in
browser history. Kept only as a documented fallback for a client that can't send a first frame.
**Rejected — `Sec-WebSocket-Protocol`**: keeps the token out of the URL, but that argument exists for
protocol *negotiation*, not credentials, so some proxies normalise it away — and rejection is again a
bare handshake failure.

**The cost, stated plainly:** the socket is `accept()`ed *before* it is authenticated. That window is
bounded by `WS_AUTH_TIMEOUT_SECONDS` (default 5s), then the server closes `4401`. `accept()` must come
first because a close *code* can't be sent on a connection that was never accepted. The token is then
re-decoded on **every** `message.send`, so a socket never outlives its credential.

## Message protocol

- **One envelope.** `{"type": …, "payload": {…}}` for data, `{"type": "error", "error": "<detail>"}`
  for failures — one shape to parse, and a type field a client can switch on.
- **Only two inbound types**, `auth` and `message.send`. Every other client need is a REST call or was
  cut, so the inbound surface stays small enough to validate exhaustively with Pydantic.
- **History over REST, not a socket frame.** Paginated reads are request/response by nature; on the
  socket they'd need request/response correlation inside a protocol that otherwise only pushes.
  Reconnect catch-up needs no round-trip either — `last_seen_seq` triggers replay directly.
- **Auth failure is a close code, not an `auth.error` frame.** A connection that cannot authenticate
  should not stay open, so no state exists in which that frame would help: `4401` for the token,
  `4403` for a conversation the caller is not a *participant* of.
- **`message.new` and `message.ack` are separate.** `message.new` is the broadcast every subscriber
  gets; `message.ack` confirms *your* send and echoes `client_message_id` with the server-assigned
  `id`, so clients needn't tell "mine came back" from "someone else's arrived" by the sender.
- **The bigserial `id` *is* the sequence number** — one value for identity and order.

Frame-by-frame reference in [README.md § API surface](./README.md#api-surface).

## What I'd do next

1. **Bound the replay** — cap it and fall back to the paginated REST history endpoint, so one
   reconnect can't monopolize a socket.
2. **Streamed assistant reply** — token chunks over the socket.
3. **Typing indicators / presence.**
4. **`/metrics`**, with active-connection and message-throughput counters.
5. **OAuth login**, alongside email/password.
6. **Load-test proof** of fan-out across the two replicas, with numbers.

## What I cut — an honest read

I took one bonus, delivery guarantees. Each line below is the real consequence, not a reassurance.

- **Replay is unbounded.** A long-absent client can trigger an arbitrarily large replay on one socket.
- **The rate limit is a fixed window.** A client that spends its quota at the end of one window and
  the next window's immediately can send 2× the limit back to back.
- **No streamed assistant reply, and no typing/presence** — the two nice-to-haves I skipped. The mock
  returns one whole `message.new` rather than token chunks over the socket.
- **A replica dying drops its sockets**, and Redis publish is fire-and-forget, so a message sent during
  a Redis outage is persisted to Postgres but never fanned out live. Recovery is reconnect and refetch
  either way — Postgres is the source of truth, Redis only the fan-out.
- **`/metrics`, OAuth login, load-test numbers** — the three untaken bonuses; only one was allowed.
