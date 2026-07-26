#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["websockets>=14"]
# ///
"""One-command end-to-end demo of the running stack.

    docker compose up --build      # in one terminal
    uv run demo.py                 # in another

Walks the sequence the assignment asks to see — sign up, open a socket, send a message, watch the
broadcast and the assistant reply, reconnect and read history — and finishes with the bonus: two
replicas exchanging a message through Redis. Every step asserts something and prints what it saw;
the exit code is non-zero if any of them fails.

Nothing to install: ``uv run`` reads the header above and fetches ``websockets`` into a throwaway
environment. HTTP goes through the standard library, deliberately over a single keep-alive
connection, because step 3 depends on it — sign-up-then-log-in with no delay is what catches a
server that acknowledges a write before committing it.

Point it at other hosts with DIZZCHAT_API_A / DIZZCHAT_API_B (default localhost:8000 / :8001), e.g.
to run it inside the network: docker compose exec -e DIZZCHAT_API_A=api:8000 \
    -e DIZZCHAT_API_B=api2:8000 api python demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from http.client import HTTPConnection
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect

API_A = os.environ.get("DIZZCHAT_API_A", "localhost:8000")
API_B = os.environ.get("DIZZCHAT_API_B", "localhost:8001")
DEMO_PASSPHRASE = "demo-credential-long-enough-for-argon2"
RECV_TIMEOUT = 10.0


class DemoFailure(Exception):
    """A step's assertion did not hold, so the demo stops there."""


class Http:
    """A tiny JSON client holding **one** connection open, so requests reuse the same socket."""

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
    ) -> tuple[int, Any]:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        self._conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = self._conn.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else None

    def close(self) -> None:
        self._conn.close()


class Report:
    """Prints one numbered line per step and remembers whether every one of them passed."""

    def __init__(self) -> None:
        self._step = 0
        self.failures = 0

    def ok(self, label: str, detail: str) -> None:
        self._step += 1
        print(f"{self._step:>3}  {label:<15} {detail}")

    def failed(self, label: str, detail: str) -> None:
        self._step += 1
        self.failures += 1
        print(f"{self._step:>3}  {label:<15} FAILED: {detail}")

    def total(self) -> int:
        return self._step


def check(condition: bool, message: str) -> None:
    if not condition:
        raise DemoFailure(message)


async def recv_frames(ws: ClientConnection, count: int) -> list[dict[str, Any]]:
    """Collect ``count`` frames.

    Frames are gathered rather than matched in order on purpose: an ack comes straight back from
    the replica that took the send, while the broadcasts arrive via Redis, so their relative order
    is not part of the contract and asserting on it would be asserting on luck.
    """
    frames: list[dict[str, Any]] = []
    for _ in range(count):
        raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
        frames.append(json.loads(raw))
    return frames


def of_type(frames: list[dict[str, Any]], frame_type: str) -> list[dict[str, Any]]:
    return [frame for frame in frames if frame.get("type") == frame_type]


async def authenticate(ws: ClientConnection, token: str, last_seen_seq: int | None = None) -> None:
    await ws.send(
        json.dumps({"type": "auth", "payload": {"token": token, "last_seen_seq": last_seen_seq}})
    )
    frame = (await recv_frames(ws, 1))[0]
    check(frame.get("type") == "auth.ok", f"expected auth.ok, got {frame}")


async def send_message(ws: ClientConnection, content: str, client_message_id: str) -> None:
    await ws.send(
        json.dumps(
            {
                "type": "message.send",
                "payload": {"content": content, "client_message_id": client_message_id},
            }
        )
    )


async def run(report: Report) -> None:
    alice_http = Http(API_A)
    bob_http = Http(API_A)
    try:
        await _run(report, alice_http, bob_http)
    finally:
        alice_http.close()
        bob_http.close()


async def _run(report: Report, alice_http: Http, bob_http: Http) -> None:
    # 1 — both replicas are up. The demo needs two, so say so plainly rather than failing later
    #     with a confusing connection error.
    for netloc in (API_A, API_B):
        probe = Http(netloc)
        try:
            status, body = await asyncio.to_thread(probe.call, "GET", "/health")
            check(status == 200 and body == {"status": "ok"}, f"{netloc} returned {status} {body}")
        finally:
            probe.close()
    report.ok("health", f"{API_A} and {API_B} both report ok")

    # 2 — a fresh email each run, so the script is re-runnable against a live database.
    alice_email = f"alice-{uuid4().hex[:8]}@demo.dizzchat"
    status, body = await asyncio.to_thread(
        alice_http.call,
        "POST",
        "/auth/signup",
        body={"email": alice_email, "password": DEMO_PASSPHRASE},
    )
    check(status == 201, f"signup returned {status} {body}")
    report.ok("signup", f"{alice_email} created (201)")

    # 3 — the read-your-writes check, and the reason this client keeps one connection open: logging
    #     in immediately on the same socket leaves no room for a commit that happens after the
    #     signup response was already sent.
    status, body = await asyncio.to_thread(
        alice_http.call,
        "POST",
        "/auth/login",
        body={"email": alice_email, "password": DEMO_PASSPHRASE},
    )
    check(status == 200, f"login straight after signup returned {status} {body} (commit race?)")
    alice_token = str(body["access_token"])
    report.ok("read-your-write", "login on the same connection, zero delay -> 200")

    # 4 — the conversation every later step uses.
    status, body = await asyncio.to_thread(
        alice_http.call, "POST", "/conversations", body={"title": "demo"}, token=alice_token
    )
    check(status == 201, f"create conversation returned {status} {body}")
    conversation_id = str(body["id"])
    report.ok("conversation", f'"demo" created, id {conversation_id}')

    # 5-7 — the live path. Bob joins on the *other* replica so the fan-out is genuinely cross
    #       process, not a local broadcast that would work without Redis at all.
    async with connect(f"ws://{API_A}/ws/conversations/{conversation_id}") as alice_ws:
        await authenticate(alice_ws, alice_token)
        report.ok("connect", f"auth.ok on {API_A}")

        first_key = str(uuid4())
        await send_message(alice_ws, "hello", first_key)
        frames = await recv_frames(alice_ws, 3)
        acks = of_type(frames, "message.ack")
        news = of_type(frames, "message.new")
        check(len(acks) == 1, f"expected one message.ack, got {frames}")
        check(len(news) == 2, f"expected two message.new frames, got {frames}")
        by_role = {frame["payload"]["role"]: frame["payload"] for frame in news}
        check(by_role["user"]["content"] == "hello", f"user frame was {by_role.get('user')}")
        check(
            by_role["assistant"]["content"] == "You said: hello",
            f"assistant frame was {by_role.get('assistant')}",
        )
        first_seq = int(acks[0]["payload"]["id"])
        report.ok(
            "send",
            f"ack seq={first_seq} · message.new user + assistant "
            f'"{by_role["assistant"]["content"]}"',
        )

        bob_email = f"bob-{uuid4().hex[:8]}@demo.dizzchat"
        bob_token = await _register(bob_http, bob_email)
        status, _ = await asyncio.to_thread(
            alice_http.call,
            "POST",
            f"/conversations/{conversation_id}/participants",
            body={"email": bob_email},
            token=alice_token,
        )
        check(status == 204, f"inviting bob returned {status}")

        async with connect(f"ws://{API_B}/ws/conversations/{conversation_id}") as bob_ws:
            await authenticate(bob_ws, bob_token)
            await send_message(alice_ws, "hello bob", str(uuid4()))
            # Alice's own ack and broadcasts are drained too, so the next step starts clean.
            bob_frames: list[dict[str, Any]]
            bob_frames, _ = await asyncio.gather(recv_frames(bob_ws, 2), recv_frames(alice_ws, 3))
            bob_news = of_type(bob_frames, "message.new")
            check(len(bob_news) == 2, f"bob expected two message.new frames, got {bob_frames}")
            roles = {frame["payload"]["role"] for frame in bob_news}
            check(roles == {"user", "assistant"}, f"bob received roles {roles}")
            report.ok(
                "two replicas",
                f"alice sent on {API_A}; bob received it on {API_B} via Redis",
            )

    # 8 — reconnect on the other replica and ask for everything. The socket that sent the messages
    #     is closed by now, so this is genuinely a fresh connection reading shared state.
    async with connect(f"ws://{API_B}/ws/conversations/{conversation_id}") as replay_ws:
        await authenticate(replay_ws, alice_token, last_seen_seq=0)
        replayed = await recv_frames(replay_ws, 4)
        seqs = [int(frame["payload"]["id"]) for frame in of_type(replayed, "message.new")]
        check(len(seqs) == 4, f"expected four replayed messages, got {replayed}")
        check(seqs == sorted(seqs), f"replay should be oldest-first, got {seqs}")
        report.ok("reconnect", f"last_seen_seq=0 on {API_B} replayed seq {seqs}")

        # 9 — the same history over REST, from the other replica again.
        status, body = await asyncio.to_thread(_read_history, API_B, conversation_id, alice_token)
        check(status == 200, f"history returned {status} {body}")
        items = list(body["items"])
        check(len(items) == 4, f"expected four messages in history, got {len(items)}")
        check(
            [int(item["id"]) for item in items] == sorted(seqs, reverse=True),
            f"history should be newest-first, got {[item['id'] for item in items]}",
        )
        report.ok("history", f"GET /messages returned {len(items)} messages, newest first")

        # 10 — idempotency: the same key must not create a second row or a second reply.
        await send_message(replay_ws, "hello", first_key)
        frame = (await recv_frames(replay_ws, 1))[0]
        check(frame.get("type") == "message.ack", f"expected message.ack, got {frame}")
        check(
            int(frame["payload"]["id"]) == first_seq,
            f"expected the original seq {first_seq}, got {frame['payload']['id']}",
        )
        status, body = await asyncio.to_thread(_read_history, API_B, conversation_id, alice_token)
        check(len(body["items"]) == 4, f"a duplicate send added a row: {len(body['items'])}")
        report.ok("idempotent", f"same client_message_id -> ack seq={first_seq}, history still 4")


async def _register(http: Http, email: str) -> str:
    """Sign up and log in, returning the access token."""
    status, body = await asyncio.to_thread(
        http.call, "POST", "/auth/signup", body={"email": email, "password": DEMO_PASSPHRASE}
    )
    check(status == 201, f"signup for {email} returned {status} {body}")
    status, body = await asyncio.to_thread(
        http.call, "POST", "/auth/login", body={"email": email, "password": DEMO_PASSPHRASE}
    )
    check(status == 200, f"login for {email} returned {status} {body}")
    return str(body["access_token"])


def _read_history(netloc: str, conversation_id: str, token: str) -> tuple[int, Any]:
    http = Http(netloc)
    try:
        return http.call("GET", f"/conversations/{conversation_id}/messages", token=token)
    finally:
        http.close()


async def main() -> int:
    print("dizzchat demo · the assignment's deliverable 5, end to end")
    print(f"replica A http://{API_A}   replica B http://{API_B}\n")
    report = Report()
    try:
        await run(report)
    except DemoFailure as failure:
        report.failed("assertion", str(failure))
    except (OSError, TimeoutError) as failure:
        report.failed(
            "connection",
            f"{failure!r} — is the stack up? try: docker compose up --build",
        )
    print()
    if report.failures:
        print(f"{report.failures} of {report.total()} steps failed")
        return 1
    print(f"{report.total()}/{report.total()} steps passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
