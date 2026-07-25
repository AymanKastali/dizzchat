# Notes — decisions & deferred work

A ledger of deliberate scope choices and the follow-ups a production hardening pass would pick up.
Nothing here is a known bug; these are conscious trade-offs for an assignment-scoped build.

## Deliberate decisions

### No domain events / CQRS / event sourcing
The two contexts coordinate through direct use-case calls, not a domain-event bus, and reads share
the write model. The assignment needs neither cross-context reactions nor an audit/history rebuild,
so an event backbone would be complexity without a paying use case. Revisit if requirements grow a
real consumer — an activity feed, an audit log, or asynchronous cross-context workflows.

### Modular monolith, not microservices
One deployable with two bounded contexts kept behind module boundaries (hexagonal layering, ports
and adapters). This preserves a clean split-point later without paying distributed-systems cost now.

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

### Security choices worth recording
- **Refresh tokens are opaque** `<jti>.<secret>` strings; only a SHA-256 hash of the secret is
  stored, so a database read cannot reconstruct a usable token.
- **Access tokens are re-validated on every WebSocket send**, so an expired token can't keep sending
  on an already-open socket.
- **Password length is capped** (1024 chars) to bound argon2 CPU cost against a long-input DoS.
- **CORS credentials** are enabled only for explicitly configured origins, never the `*` wildcard.
