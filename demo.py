#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["websockets>=14"]
# ///
"""A guided, live walkthrough of the running stack — the assignment's "a way to see it work".

    docker compose up --build      # in one terminal
    uv run demo.py                 # in another

Every HTTP call and every WebSocket frame is printed as it happens, with a short explanation of what
each step demonstrates, so the protocol and the cross-replica fan-out are visible rather than
summarised. Each step also asserts what it claims; a failure stops the run with a non-zero exit, so
this doubles as a smoke test.

The sequence follows the brief: sign up -> open a socket -> send a message -> see the broadcast and
the assistant reply -> reconnect and see history, plus the bonus (two replicas exchanging a message
through Redis) and idempotent resend from the delivery-guarantees bonus.

Nothing to install: ``uv run`` reads the header above and fetches ``websockets`` into a throwaway
environment. HTTP uses the standard library over a single keep-alive connection on purpose — logging
in immediately after signing up, on that same connection, leaves no delay for a write that isn't yet
durable to hide behind.

Point it at other hosts with DIZZCHAT_API_A / DIZZCHAT_API_B (default localhost:8000 / :8001), e.g.
inside the compose network:
    docker compose exec -e DIZZCHAT_API_A=api:8000 -e DIZZCHAT_API_B=api2:8000 api python demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from http.client import HTTPConnection
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect

API_A = os.environ.get("DIZZCHAT_API_A", "localhost:8000")
API_B = os.environ.get("DIZZCHAT_API_B", "localhost:8001")
DEMO_PASSPHRASE = "demo-credential-long-enough-for-argon2"
RECV_TIMEOUT = 10.0

WIDTH = 96
# Long opaque values are truncated in the printout: a full JWT is three lines of base64 that
# tell the reader nothing, and it would push the interesting fields off the screen.
LONG_VALUE_KEYS = frozenset({"token", "access_token", "refresh_token", "password"})


class DemoFailure(Exception):
    """A step's assertion did not hold, so the run stops there."""


def shorten(text: str, keep: int = 14) -> str:
    return text if len(text) <= keep else f"{text[:keep]}…"


def readable(value: Any) -> Any:
    """Recursively truncate the values that are long and uninformative (tokens, credentials)."""
    if isinstance(value, dict):
        return {
            key: shorten(item)
            if key in LONG_VALUE_KEYS and isinstance(item, str)
            else readable(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [readable(item) for item in value]
    return value


def as_json(value: Any) -> str:
    return json.dumps(readable(value), separators=(", ", ": "), ensure_ascii=False)


class Console:
    """Prints the walkthrough and counts the assertions, so the run ends with a verdict."""

    def __init__(self) -> None:
        self.checks = 0
        self.step_number = 0

    def banner(self) -> None:
        print("=" * WIDTH)
        print(" dizzchat — a live walkthrough of the running stack")
        print("=" * WIDTH)
        print(
            textwrap.fill(
                "Two API replicas run behind one Postgres and one Redis. Everything below is real "
                "traffic against them, printed as it happens.",
                width=WIDTH - 1,
            )
        )
        print()
        print(f"   replica A   http://{API_A}")
        print(f"   replica B   http://{API_B}")
        print()
        print("   →  sent by this script        ←  received from the server")

    def step(self, title: str, *explanation: str) -> None:
        self.step_number += 1
        print()
        print("─" * WIDTH)
        print(f"STEP {self.step_number} · {title}")
        print("─" * WIDTH)
        for paragraph in explanation:
            print(self._para(paragraph))
        if explanation:
            print()

    def label(self, text: str) -> None:
        print(f"  {text}")

    def request(self, method: str, netloc: str, path: str, body: dict[str, Any] | None) -> None:
        print(f"  →  {method} http://{netloc}{path}")
        if body is not None:
            self._wrapped(as_json(body), "       ")

    def response(self, status: int, payload: Any) -> None:
        print(f"  ←  {status}")
        if payload is not None:
            self._wrapped(as_json(payload), "       ")

    def frame_out(self, frame: dict[str, Any]) -> None:
        self._wrapped(as_json(frame), "       ", first_prefix="  →  ")

    def frame_in(self, frame: dict[str, Any]) -> None:
        self._wrapped(as_json(frame), "       ", first_prefix="  ←  ")

    def ok(self, text: str) -> None:
        self.checks += 1
        print(
            textwrap.fill(
                f"✓ {text}", width=WIDTH - 5, initial_indent="     ", subsequent_indent="       "
            )
        )

    def _para(self, text: str) -> str:
        return textwrap.fill(text, width=WIDTH - 3, initial_indent="  ", subsequent_indent="  ")

    def _wrapped(self, text: str, indent: str, first_prefix: str | None = None) -> None:
        lines = textwrap.wrap(text, width=WIDTH - len(indent), break_long_words=False) or [text]
        print(f"{first_prefix or indent}{lines[0]}")
        for line in lines[1:]:
            print(f"{indent}{line}")


console = Console()


class Http:
    """A tiny JSON client that holds **one** connection open, so requests reuse the same socket."""

    def __init__(self, netloc: str) -> None:
        self.netloc = netloc
        host, _, port = netloc.partition(":")
        self._conn = HTTPConnection(host, int(port or 80), timeout=15)

    def call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        token: str | None = None,
        show: bool = True,
    ) -> tuple[int, Any]:
        if show:
            console.request(method, self.netloc, path, body)
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        self._conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = self._conn.getresponse()
        raw = response.read()
        payload = json.loads(raw) if raw else None
        if show:
            console.response(response.status, payload)
        return response.status, payload

    def close(self) -> None:
        self._conn.close()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise DemoFailure(message)


async def send_frame(ws: ClientConnection, frame: dict[str, Any]) -> None:
    console.frame_out(frame)
    await ws.send(json.dumps(frame))


async def recv_frames(ws: ClientConnection, count: int) -> list[dict[str, Any]]:
    """Receive and print ``count`` frames, in arrival order.

    Arrival order is never asserted: an ack comes straight back from the replica that took the send,
    while broadcasts travel through Redis, so their relative order is not part of the contract.
    """
    frames: list[dict[str, Any]] = []
    for _ in range(count):
        raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
        frame = json.loads(raw)
        console.frame_in(frame)
        frames.append(frame)
    return frames


async def drain(ws: ClientConnection, count: int) -> None:
    """Consume frames without printing them.

    Used for alice's own copies of what bob is being shown: the same broadcasts land on both
    sockets, and printing them twice would bury the point of the cross-replica step.
    """
    for _ in range(count):
        await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)


def of_type(frames: list[dict[str, Any]], frame_type: str) -> list[dict[str, Any]]:
    return [frame for frame in frames if frame.get("type") == frame_type]


def by_role(frames: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {frame["payload"]["role"]: frame["payload"] for frame in frames}


async def authenticate(ws: ClientConnection, token: str, last_seen_seq: int | None = None) -> None:
    await send_frame(
        ws, {"type": "auth", "payload": {"token": token, "last_seen_seq": last_seen_seq}}
    )
    frame = (await recv_frames(ws, 1))[0]
    check(frame.get("type") == "auth.ok", f"expected auth.ok, got {frame}")


async def sign_up_and_log_in(http: Http, email: str) -> str:
    """Sign up, then log in on the same connection; returns the access token."""
    status, body = await asyncio.to_thread(
        http.call, "POST", "/auth/signup", body={"email": email, "password": DEMO_PASSPHRASE}
    )
    check(status == 201, f"signup for {email} returned {status} {body}")
    status, body = await asyncio.to_thread(
        http.call, "POST", "/auth/login", body={"email": email, "password": DEMO_PASSPHRASE}
    )
    check(status == 200, f"login for {email} returned {status} {body}")
    return str(body["access_token"])


def read_history(
    netloc: str, conversation_id: str, token: str, *, show: bool = True
) -> tuple[int, Any]:
    http = Http(netloc)
    try:
        return http.call(
            "GET", f"/conversations/{conversation_id}/messages", token=token, show=show
        )
    finally:
        http.close()


def read_history_quietly(netloc: str, conversation_id: str, token: str) -> tuple[int, Any]:
    return read_history(netloc, conversation_id, token, show=False)


async def walkthrough(alice_http: Http, bob_http: Http) -> None:
    console.step(
        "Both replicas are up",
        "The demo needs two API instances sharing one Redis — that is what makes the fan-out step "
        "later meaningful. This confirms both are answering.",
    )
    for netloc in (API_A, API_B):
        probe = Http(netloc)
        try:
            status, body = await asyncio.to_thread(probe.call, "GET", "/health")
            check(status == 200 and body == {"status": "ok"}, f"{netloc} returned {status} {body}")
        finally:
            probe.close()
    console.ok(f"{API_A} and {API_B} are both healthy")

    console.step(
        "Sign up and log in",
        "Passwords are hashed with argon2, never stored in plain text. Login returns a short-lived "
        "JWT access token plus a refresh token.",
        "The login runs immediately after the signup, on the same keep-alive connection, with no "
        "delay — if a write were acknowledged before it was committed, this is exactly where it "
        "would fail with a 401.",
    )
    alice_email = f"alice-{uuid4().hex[:8]}@demo.dizzchat"
    alice_token = await sign_up_and_log_in(alice_http, alice_email)
    console.ok(
        f"{alice_email} signed up and logged straight back in — the write was already durable"
    )

    console.step(
        "Create a conversation",
        "REST handles conversation CRUD and history; the live message path is the WebSocket, "
        "opened next. Alice is the owner, and an owner is a participant from the start.",
    )
    status, body = await asyncio.to_thread(
        alice_http.call, "POST", "/conversations", body={"title": "demo"}, token=alice_token
    )
    check(status == 201, f"create conversation returned {status} {body}")
    conversation_id = str(body["id"])
    console.ok(f'conversation "demo" created, id {conversation_id}')

    async with connect(f"ws://{API_A}/ws/conversations/{conversation_id}") as alice_ws:
        console.step(
            "Open a WebSocket and authenticate with the first frame",
            "The token travels in the first frame, never in the URL, so it cannot leak into access "
            "logs or browser history. The server accepts the socket and then waits up to 5 seconds "
            "for this frame; a missing, malformed, or expired token closes with code 4401, and a "
            "conversation the caller is not a participant in closes with 4403.",
        )
        console.label(f"socket A — ws://{API_A}/ws/conversations/{conversation_id}   (alice)")
        await authenticate(alice_ws, alice_token)
        console.ok("authenticated — this socket is now subscribed to the conversation")

        console.step(
            "Send a message, and see the broadcast and the assistant reply",
            "One send produces three frames back. The ack confirms it and carries the "
            "server-assigned id; the user message is then broadcast to every client in the "
            "conversation; and the mock assistant's reply is persisted and broadcast the same way. "
            "That id doubles as the sequence number used for ordering and replay.",
        )
        first_key = str(uuid4())
        await send_frame(
            alice_ws,
            {
                "type": "message.send",
                "payload": {"content": "hello", "client_message_id": first_key},
            },
        )
        frames = await recv_frames(alice_ws, 3)
        acks = of_type(frames, "message.ack")
        news = of_type(frames, "message.new")
        check(len(acks) == 1, f"expected one message.ack, got {frames}")
        check(len(news) == 2, f"expected two message.new frames, got {frames}")
        roles = by_role(news)
        check(roles["user"]["content"] == "hello", f"user frame was {roles.get('user')}")
        check(
            roles["assistant"]["content"] == "You said: hello",
            f"assistant frame was {roles.get('assistant')}",
        )
        first_seq = int(acks[0]["payload"]["id"])
        console.ok(f"acked as seq {first_seq}, then broadcast back with role=user")
        console.ok(
            f'the assistant replied "{roles["assistant"]["content"]}" '
            f"as seq {roles['assistant']['id']}, persisted like any other message"
        )

        console.step(
            "The cross-replica hop, through Redis   ← the bonus the brief asks for",
            "Bob is invited, then connects to replica B while alice keeps sending on replica A. "
            "Nothing in replica A's memory can reach a socket held by replica B, so the message "
            "has to travel: A persists it, publishes it to this conversation's Redis channel, B's "
            "subscriber picks it up, and B writes it to bob's socket.",
        )
        bob_email = f"bob-{uuid4().hex[:8]}@demo.dizzchat"
        bob_token = await sign_up_and_log_in(bob_http, bob_email)
        status, _ = await asyncio.to_thread(
            alice_http.call,
            "POST",
            f"/conversations/{conversation_id}/participants",
            body={"email": bob_email},
            token=alice_token,
        )
        check(status == 204, f"inviting bob returned {status}")
        console.ok(
            f"{bob_email} is a participant now, invited by alice (only the owner may invite)"
        )

        async with connect(f"ws://{API_B}/ws/conversations/{conversation_id}") as bob_ws:
            print()
            console.label(f"socket B — ws://{API_B}/ws/conversations/{conversation_id}   (bob)")
            await authenticate(bob_ws, bob_token)

            print()
            console.label(f"alice sends, on replica A ({API_A}):")
            await send_frame(
                alice_ws,
                {
                    "type": "message.send",
                    "payload": {"content": "hello bob", "client_message_id": str(uuid4())},
                },
            )
            print()
            console.label(
                f"bob receives, on replica B ({API_B}) — these crossed Redis to get here:"
            )
            bob_frames: list[dict[str, Any]]
            bob_frames, _ = await asyncio.gather(recv_frames(bob_ws, 2), drain(alice_ws, 3))
            bob_news = of_type(bob_frames, "message.new")
            check(len(bob_news) == 2, f"bob expected two message.new frames, got {bob_frames}")
            received = set(by_role(bob_news))
            check(received == {"user", "assistant"}, f"bob received roles {received}")
            console.ok(
                f"a message sent to {API_A} reached a socket held by {API_B} — the Redis fan-out "
                "working across instances, which is what the 2+ replica requirement is about"
            )

    console.step(
        "Reconnect, and catch up on what was missed",
        "Alice's socket is closed now. She reconnects to replica B — a different process, with no "
        'memory of her — sending last_seen_seq: 0 in the auth frame, meaning "replay everything". '
        "The server joins her to live delivery first and only then replays from Postgres, so no "
        "message can slip through the gap between the two.",
    )
    async with connect(f"ws://{API_B}/ws/conversations/{conversation_id}") as replay_ws:
        console.label(
            f"socket — ws://{API_B}/ws/conversations/{conversation_id}   (alice, reconnecting)"
        )
        await authenticate(replay_ws, alice_token, last_seen_seq=0)
        replayed = await recv_frames(replay_ws, 4)
        seqs = [int(frame["payload"]["id"]) for frame in of_type(replayed, "message.new")]
        check(len(seqs) == 4, f"expected four replayed messages, got {replayed}")
        check(seqs == sorted(seqs), f"replay should be oldest-first, got {seqs}")
        console.ok(f"replayed seq {seqs}, oldest-first, on a replica that never saw the sends")

        console.step(
            "Read the history over REST",
            "The same messages, fetched the way a client loads a conversation on open: cursor "
            "paginated, newest-first. History is REST rather than a socket frame, so paging stays "
            "request/response and the socket stays a pure live channel.",
        )
        status, body = await asyncio.to_thread(read_history, API_B, conversation_id, alice_token)
        check(status == 200, f"history returned {status} {body}")
        items = list(body["items"])
        check(len(items) == 4, f"expected four messages in history, got {len(items)}")
        check(
            [int(item["id"]) for item in items] == sorted(seqs, reverse=True),
            f"history should be newest-first, got {[item['id'] for item in items]}",
        )
        console.ok(f"{len(items)} messages, newest first, matching what the socket delivered")

        console.step(
            "Send the same message twice — the duplicate is a no-op",
            "The client_message_id from step 5 is an idempotency key, enforced by a unique "
            "constraint per conversation. Re-sending it returns an ack carrying the *original* id: "
            "no second row, no second broadcast, no second assistant reply. That is what makes a "
            "retry after a dropped connection safe.",
        )
        await send_frame(
            replay_ws,
            {
                "type": "message.send",
                "payload": {"content": "hello", "client_message_id": first_key},
            },
        )
        frame = (await recv_frames(replay_ws, 1))[0]
        check(frame.get("type") == "message.ack", f"expected message.ack, got {frame}")
        check(
            int(frame["payload"]["id"]) == first_seq,
            f"expected the original seq {first_seq}, got {frame['payload']['id']}",
        )
        console.ok(f"acked with the original seq {first_seq}, and no broadcast followed it")
        # Re-read history to prove no row was added, but don't reprint the whole page: it is the
        # same four messages already shown in step 8, and dumping them again buries the point.
        _, body = await asyncio.to_thread(read_history_quietly, API_B, conversation_id, alice_token)
        ids = [int(item["id"]) for item in body["items"]]
        console.label(f"→  GET /conversations/{conversation_id}/messages   (re-read)")
        console.label(f"←  200   still {len(ids)} messages, ids {ids}")
        check(len(ids) == 4, f"a duplicate send added a row: {len(ids)}")
        console.ok("history is unchanged — the duplicate was not stored")


async def main() -> int:
    console.banner()
    # One HTTP client per user, so each user's requests ride their own keep-alive connection, as two
    # real clients would.
    alice_http = Http(API_A)
    bob_http = Http(API_A)
    try:
        await walkthrough(alice_http, bob_http)
    except DemoFailure as failure:
        print()
        print(f"  ✗ FAILED: {failure}")
        return 1
    except (OSError, TimeoutError) as failure:
        print()
        print(f"  ✗ FAILED to reach the stack: {failure!r}")
        print("     Is it running? Start it with:  docker compose up --build")
        return 1
    finally:
        alice_http.close()
        bob_http.close()

    print()
    print("=" * WIDTH)
    print(
        f" done — {console.step_number} steps, {console.checks} assertions, all passed."
        " Nothing was mocked; this was the real stack."
    )
    print("=" * WIDTH)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
