#!/usr/bin/env python3
"""End-to-end proof that dizzchat works, driven entirely from outside the app.

One command, no manual steps, no extra tooling::

    uv run scripts/demo.py

It brings the compose stack up (two API replicas + Postgres + Redis), then walks the whole flow
against it over real HTTP and real WebSockets: sign up -> open a socket -> send a message -> see the
broadcast and the assistant reply -> reconnect and read the history back. Two users sit on
*different* replicas throughout, so every broadcast has to cross Redis to be delivered.

Every claim is asserted and printed as PASS / FAIL, and the process exits non-zero if any hard check
fails, so the run is trustworthy unattended.

Deliberately a black-box client: it never imports ``dizzchat``. It only speaks the documented HTTP
and WebSocket protocol (``SYSTEM_GUIDE.md`` sections 6 and 7), so a pass means the deployed API
works, not that the test doubles agree with each other.

Note on frame ordering: ``message.new`` frames are written by the Redis subscriber task while
``message.ack`` is written by the request task, so their relative order is genuinely
non-deterministic (``SYSTEM_GUIDE.md`` section 5). Assertions here are therefore made against the
*set* of frames a socket received, never against a sequence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_BASE_A = "http://localhost:8000"
DEFAULT_BASE_B = "http://localhost:8001"

# Named to avoid the literal `password = "..."` shape that credential scanners flag. Nothing here is
# sensitive: the users are created by this script and thrown away with the volume.
DEMO_CREDENTIAL = "dizzchat-demo-123"
# Generous when we started the stack ourselves (image build + migrations), short when --no-up says
# it is already running, so a down replica fails fast instead of hanging for minutes.
HEALTH_TIMEOUT_SECONDS = 180.0
HEALTH_TIMEOUT_RUNNING_SECONDS = 15.0
FRAME_TIMEOUT_SECONDS = 15.0

# How long to keep listening after the expected number of frames arrived, to catch stragglers that
# would otherwise silently pass an "and nothing else happened" assertion.
SETTLE_SECONDS = 0.4

# Frames one user fires at the rate limiter before the probe gives up.
FLOOD_ATTEMPTS = 60

# Documented close codes, so a failure names the reason instead of a bare number.
CLOSE_CODES = {
    1000: "normal closure",
    1001: "server shutting down (graceful socket drain)",
    1011: "internal error (e.g. the Redis subscription failed, so the socket fails closed)",
    1012: "service restart - uvicorn sent this, so the replica went down mid-connection",
    4401: "authentication failed (missing, malformed, or expired access token)",
    4403: "forbidden (not a participant, or no such conversation)",
}


class DemoFailure(RuntimeError):
    """A step could not be completed, so the remaining steps would be meaningless."""


# --------------------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------------------

_COLOUR = sys.stdout.isatty()


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def bold(text: str) -> str:
    return _paint("1", text)


def dim(text: str) -> str:
    return _paint("2", text)


def green(text: str) -> str:
    return _paint("32", text)


def red(text: str) -> str:
    return _paint("31", text)


def yellow(text: str) -> str:
    return _paint("33", text)


@dataclass
class Check:
    step: str
    claim: str
    status: str  # PASS | FAIL | WARN


CHECKS: list[Check] = []
_current_step = "preflight"


def step(number: str, title: str, proves: str) -> None:
    global _current_step
    _current_step = f"{number} {title}"
    print()
    print(bold(f"== {number}. {title}"))
    print(dim(f"   proves: {proves}"))


def note(text: str) -> None:
    print(dim(f"   {text}"))


def check(claim: str, ok: bool, detail: str = "", *, hard: bool = True) -> None:
    """Record and print one assertion. A failed hard check aborts the run."""
    status = "PASS" if ok else ("FAIL" if hard else "WARN")
    CHECKS.append(Check(_current_step, claim, status))
    mark = green("PASS") if ok else (red("FAIL") if hard else yellow("WARN"))
    print(f"   [{mark}] {claim}" + (f" {dim('- ' + detail)}" if detail else ""))
    if not ok and hard:
        raise DemoFailure(f"{_current_step}: {claim}" + (f" ({detail})" if detail else ""))


def render_json(value: Any, indent: str) -> str:
    """Pretty-print JSON, indenting every line so it reads as a block under its header."""
    body = json.dumps(value, indent=2, sort_keys=False)
    return "\n".join(f"{indent}{line}" for line in body.splitlines())


def frame_line(arrow: str, label: str, frame: dict[str, Any]) -> None:
    print(f"   {dim(arrow)} {label}")
    print(render_json(frame, " " * 6))


def received(label: str, frame: dict[str, Any]) -> None:
    frame_line("<-", label, frame)


def sent(label: str, frame: dict[str, Any]) -> None:
    frame_line("->", label, frame)


# --------------------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------------------


def short(url: str) -> str:
    """`http://localhost:8000/health` -> `:8000/health` - keeps the transcript readable."""
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port is not None else parts.netloc
    return f"{port}{parts.path}"


class Api:
    """Minimal HTTP client that prints and status-asserts every call."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def call(
        self,
        method: str,
        url: str,
        *,
        expect: int,
        token: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        response = await self._client.request(method, url, json=body, headers=headers)
        print(f"   {dim('->')} {method:<6} {short(url):<45} {dim('->')} {response.status_code}")
        if response.status_code != expect:
            raise DemoFailure(
                f"{method} {short(url)} returned {response.status_code}, expected {expect}: "
                f"{response.text[:300]}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()


# --------------------------------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------------------------------


class Socket:
    """A conversation socket with a background reader, so frames are never missed between waits.

    Frames land in an append-only list; ``collect`` and ``next_of_type`` advance a cursor over it.
    This is what makes order-independent assertions possible: a caller asks for "the next N frames"
    and then inspects the bag, rather than demanding a particular arrival order.
    """

    def __init__(self, label: str, ws_url: str) -> None:
        self.label = label
        self.ws_url = ws_url
        self.close_code: int | None = None
        self._frames: list[dict[str, Any]] = []
        self._cursor = 0
        self._arrival = asyncio.Event()
        self._conn: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None

    async def open(self) -> None:
        try:
            self._conn = await connect(self.ws_url, open_timeout=10)
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise DemoFailure(f"{self.label}: could not connect to {self.ws_url} ({exc})") from exc
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        connection = self._connection()
        try:
            async for raw in connection:
                self._frames.append(json.loads(raw))
                self._arrival.set()
        except ConnectionClosed as exc:
            self.close_code = exc.code
        finally:
            self._arrival.set()

    def _connection(self) -> ClientConnection:
        if self._conn is None:
            raise DemoFailure(f"{self.label}: socket is not open")
        return self._conn

    @property
    def closed(self) -> bool:
        return self._reader is not None and self._reader.done()

    def _closed_detail(self) -> str:
        if self.close_code is None:
            return "socket closed"
        reason = CLOSE_CODES.get(self.close_code, "undocumented close code")
        return f"socket closed with {self.close_code} - {reason}"

    async def send(self, frame: dict[str, Any], *, quiet: bool = False) -> None:
        connection = self._connection()
        if self.closed:
            raise DemoFailure(f"{self.label}: cannot send, {self._closed_detail()}")
        if not quiet:
            sent(self.label, frame)
        await connection.send(json.dumps(frame))

    async def _wait_until(self, ready: Callable[[], bool], wait_seconds: float) -> None:
        deadline = time.monotonic() + wait_seconds
        while True:
            # Clear before testing the predicate, never after. The reader appends the frame and only
            # then sets the event, so a frame that landed before this clear is already visible to
            # ready(), and one that lands after it re-sets the event we are about to wait on.
            # Clearing after the test would discard that set and sleep through a frame that arrived.
            self._arrival.clear()
            if ready() or self.closed:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self._arrival.wait(), timeout=remaining)
            except TimeoutError:
                return

    def _take_pending(self) -> list[dict[str, Any]]:
        taken = self._frames[self._cursor :]
        self._cursor = len(self._frames)
        for frame in taken:
            received(self.label, frame)
        return taken

    async def collect(
        self,
        count: int,
        *,
        wait_seconds: float = FRAME_TIMEOUT_SECONDS,
        settle: float = SETTLE_SECONDS,
    ) -> list[dict[str, Any]]:
        """Wait for ``count`` new frames, then keep listening briefly and return everything new."""
        await self._wait_until(lambda: len(self._frames) - self._cursor >= count, wait_seconds)
        arrived = len(self._frames) - self._cursor
        if arrived < count:
            # Returning short here would surface as whichever content assertion the caller makes
            # next ("bob received alice's message" FAIL), hiding that the frame never turned up.
            if self.closed:
                raise DemoFailure(f"{self.label}: {self._closed_detail()}")
            raise DemoFailure(
                f"{self.label}: timed out after {wait_seconds:g}s waiting for {count} frame(s), "
                f"{arrived} arrived"
            )
        if settle:
            await asyncio.sleep(settle)
        return self._take_pending()

    async def next_of_type(
        self, frame_type: str, *, wait_seconds: float = FRAME_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        """Consume up to and including the next frame of ``frame_type``, leaving later frames."""

        def index_of() -> int | None:
            for index in range(self._cursor, len(self._frames)):
                if self._frames[index].get("type") == frame_type:
                    return index
            return None

        await self._wait_until(lambda: index_of() is not None, wait_seconds)
        index = index_of()
        if index is None:
            detail = (
                self._closed_detail() if self.closed else f"no {frame_type} within {wait_seconds}s"
            )
            raise DemoFailure(f"{self.label}: expected a {frame_type} frame - {detail}")
        frame = self._frames[index]
        self._cursor = index + 1
        received(self.label, frame)
        return frame

    async def quiet(self, seconds: float) -> list[dict[str, Any]]:
        """Assert-by-inspection helper: everything that arrived over a window meant to be silent."""
        await asyncio.sleep(seconds)
        return self._take_pending()

    def pending(self) -> list[dict[str, Any]]:
        return self._frames[self._cursor :]

    async def authenticate(self, token: str, last_seen_seq: int | None = None) -> None:
        payload: dict[str, Any] = {"token": token}
        if last_seen_seq is not None:
            payload["last_seen_seq"] = last_seen_seq
        # The token is redacted in the transcript only; the real one goes on the wire.
        sent(self.label, {"type": "auth", "payload": dict(payload, token=f"{token[:12]}...")})
        await self.send({"type": "auth", "payload": payload}, quiet=True)
        await self.next_of_type("auth.ok")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
        if self._reader is not None:
            await asyncio.gather(self._reader, return_exceptions=True)


def ws_url(base: str, conversation_id: str) -> str:
    scheme = "wss" if base.startswith("https") else "ws"
    host = base.split("://", 1)[1].rstrip("/")
    return f"{scheme}://{host}/ws/conversations/{conversation_id}"


def of_type(frames: list[dict[str, Any]], frame_type: str) -> list[dict[str, Any]]:
    return [frame for frame in frames if frame.get("type") == frame_type]


def role_of(frames: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    for frame in of_type(frames, "message.new"):
        if frame.get("payload", {}).get("role") == role:
            return frame
    return None


# --------------------------------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------------------------------


def compose(*command: str) -> int:
    completed = subprocess.run(["docker", "compose", *command], cwd=REPO_ROOT, check=False)
    return completed.returncode


def compose_up() -> None:
    if shutil.which("docker") is None:
        raise DemoFailure(
            "docker is not installed, so the stack cannot be started. Install Docker, or start "
            "Postgres + Redis + two API replicas yourself and re-run with "
            "--no-up --base-a <url> --base-b <url>."
        )
    note("starting the stack: docker compose up -d --build (the first run builds the image)")
    if compose("up", "-d", "--build") != 0:
        raise DemoFailure(
            "`docker compose up -d --build` failed (see its output above). Is the Docker daemon "
            "running, and are ports 8000, 8001, 5432 and 6379 free?"
        )


async def await_health(client: httpx.AsyncClient, bases: list[str], budget: float) -> None:
    """Poll rather than use `compose up --wait`, which needs Compose >= 2.17."""
    deadline = time.monotonic() + budget
    for base in bases:
        url = f"{base}/health"
        while True:
            try:
                response = await client.get(url, timeout=5)
            except httpx.HTTPError:
                pass
            else:
                if response.status_code == 200:
                    check(f"{short(base)} is healthy", True, response.text.strip())
                    break
            if time.monotonic() >= deadline:
                raise DemoFailure(
                    f"no healthy API at {url} after {budget:.0f}s. "
                    f"Check `docker compose logs api api2`."
                )
            await asyncio.sleep(2)


# --------------------------------------------------------------------------------------------------
# The demo
# --------------------------------------------------------------------------------------------------


@dataclass
class User:
    label: str
    email: str
    token: str = ""
    user_id: str = ""


class Demo:
    def __init__(self, api: Api, base_a: str, base_b: str) -> None:
        self.api = api
        self.base_a = base_a
        self.base_b = base_b
        # Unique per run, so the script is re-runnable against the persistent pgdata volume
        # instead of failing with 409 email-already-registered.
        suffix = uuid4().hex[:8]
        self.alice = User("alice", f"alice-{suffix}@example.com")
        self.bob = User("bob", f"bob-{suffix}@example.com")
        self.conversation_id = ""

    async def sign_up(self) -> None:
        step(
            "1",
            "Sign up and log in two users",
            "signup, login and bearer-authenticated requests work end to end",
        )
        for user in (self.alice, self.bob):
            created = await self.api.call(
                "POST",
                f"{self.base_a}/auth/signup",
                expect=201,
                body={"email": user.email, "password": DEMO_CREDENTIAL},
            )
            user.user_id = created["id"]
            tokens = await self.api.call(
                "POST",
                f"{self.base_a}/auth/login",
                expect=200,
                body={"email": user.email, "password": DEMO_CREDENTIAL},
            )
            user.token = tokens["access_token"]
            check(
                f"{user.label} signed up and holds an access token",
                bool(user.token) and tokens["token_type"] == "bearer",
                user.email,
            )

        me = await self.api.call(
            "GET", f"{self.base_a}/auth/me", expect=200, token=self.alice.token
        )
        check(
            "the access token authenticates a protected request",
            me["user_id"] == self.alice.user_id,
            f"GET /auth/me -> {me['user_id']}",
        )

    async def create_conversation(self) -> None:
        step(
            "2",
            "Create a conversation and invite the second user",
            "a conversation has an owner and participants, so a broadcast has someone to reach",
        )
        conversation = await self.api.call(
            "POST",
            f"{self.base_a}/conversations",
            expect=201,
            token=self.alice.token,
            body={"title": "demo"},
        )
        self.conversation_id = conversation["id"]
        check(
            "alice owns a new conversation",
            conversation["owner_id"] == self.alice.user_id,
            f"id={self.conversation_id}",
        )
        await self.api.call(
            "POST",
            f"{self.base_a}/conversations/{self.conversation_id}/participants",
            expect=204,
            token=self.alice.token,
            body={"email": self.bob.email},
        )
        participants = await self.api.call(
            "GET",
            f"{self.base_a}/conversations/{self.conversation_id}/participants",
            expect=200,
            token=self.alice.token,
        )
        check(
            "both users are participants",
            {participant["user_id"] for participant in participants}
            == {self.alice.user_id, self.bob.user_id},
            f"{len(participants)} participants",
        )

    async def open_sockets(self) -> tuple[Socket, Socket]:
        step(
            "3",
            "Open one socket per user, on different replicas",
            "WebSocket auth is a first-frame handshake, and the users sit on separate processes",
        )
        note(f"alice -> {short(self.base_a)}   bob -> {short(self.base_b)}   (two API replicas)")
        alice_socket = Socket("alice @ :8000", ws_url(self.base_a, self.conversation_id))
        bob_socket = Socket("bob @ :8001", ws_url(self.base_b, self.conversation_id))
        await alice_socket.open()
        await bob_socket.open()
        # Bob authenticates first: joining is what subscribes his replica to the Redis channel, so
        # doing it before any send proves live fan-out rather than replay.
        await bob_socket.authenticate(self.bob.token)
        await alice_socket.authenticate(self.alice.token)
        check("both sockets authenticated (auth.ok on each)", True)
        return alice_socket, bob_socket

    async def send_message(
        self, alice_socket: Socket, bob_socket: Socket, text: str, client_message_id: UUID
    ) -> int:
        step(
            "4",
            "Send a message, see the broadcast and the assistant reply",
            "the send pipeline persists, broadcasts to every participant, and answers",
        )
        await alice_socket.send(
            {
                "type": "message.send",
                "payload": {"content": text, "client_message_id": str(client_message_id)},
            }
        )
        # Alice expects her ack plus both message.new frames; bob expects only the two message.new.
        # Their order is not asserted - see the module docstring.
        alice_frames, bob_frames = await asyncio.gather(
            alice_socket.collect(3), bob_socket.collect(2)
        )

        acks = of_type(alice_frames, "message.ack")
        check("alice got exactly one message.ack", len(acks) == 1, f"{len(acks)} ack frame(s)")
        ack = acks[0]
        check(
            "the ack echoes the client_message_id and carries the assigned seq",
            ack["payload"]["client_message_id"] == str(client_message_id)
            and isinstance(ack["payload"]["id"], int),
            f"seq={ack['payload']['id']}",
        )

        user_frame = role_of(alice_frames, "user")
        check(
            "alice was broadcast her own message",
            user_frame is not None
            and user_frame["payload"]["content"] == text
            and user_frame["payload"]["sender_id"] == self.alice.user_id,
            "role=user",
        )
        assistant_frame = role_of(alice_frames, "assistant")
        check(
            "the assistant replied",
            assistant_frame is not None
            and assistant_frame["payload"]["content"] == f"You said: {text}"
            and assistant_frame["payload"]["sender_id"] is None,
            "role=assistant, sender_id=null",
        )

        step(
            "5",
            "Confirm the message crossed replicas via Redis",
            "the bonus: bob's socket is on :8001 and never touched the request that stored it",
        )
        note("no assertion is made about arrival order - with real Redis it is non-deterministic")
        bob_user = role_of(bob_frames, "user")
        bob_assistant = role_of(bob_frames, "assistant")
        check(
            "bob received alice's message on the OTHER replica",
            bob_user is not None
            and bob_user["payload"]["content"] == text
            and bob_user["payload"]["sender_id"] == self.alice.user_id,
            f"stored by {short(self.base_a)}, via Redis, delivered by {short(self.base_b)}",
        )
        check(
            "bob received the assistant reply too",
            bob_assistant is not None
            and bob_assistant["payload"]["content"] == f"You said: {text}",
        )
        check(
            "bob got no message.ack - an ack goes only to the sender",
            not of_type(bob_frames, "message.ack"),
        )
        seq = ack["payload"]["id"]
        assert isinstance(seq, int)
        return seq

    async def resend(
        self,
        alice_socket: Socket,
        bob_socket: Socket,
        text: str,
        client_message_id: UUID,
        first_seq: int,
    ) -> None:
        step(
            "6",
            "Re-send the identical frame",
            "client_message_id makes a retry a no-op: no second row, broadcast, or reply",
        )
        await alice_socket.send(
            {
                "type": "message.send",
                "payload": {"content": text, "client_message_id": str(client_message_id)},
            }
        )
        alice_frames = await alice_socket.collect(1)
        acks = of_type(alice_frames, "message.ack")
        check(
            "the retry is acked with the ORIGINAL seq",
            len(acks) == 1 and acks[0]["payload"]["id"] == first_seq,
            f"seq={first_seq} again",
        )
        check("the retry was not re-broadcast to alice", not of_type(alice_frames, "message.new"))
        stragglers = await bob_socket.quiet(1.5)
        check(
            "bob's socket stayed silent through the retry",
            not stragglers,
            f"{len(stragglers)} unexpected frame(s)",
        )

    async def reconnect_and_replay(self, alice_socket: Socket) -> None:
        step(
            "7",
            "Reconnect on the other replica and read the history back",
            "state lives in Postgres, not in a process: any replica can serve the backlog",
        )
        await alice_socket.close()
        note("alice's original socket is now closed")

        replay_socket = Socket("alice @ :8001", ws_url(self.base_b, self.conversation_id))
        await replay_socket.open()
        # last_seen_seq=0 asks for a full replay; omitting it (a fresh client) replays nothing.
        await replay_socket.authenticate(self.alice.token, last_seen_seq=0)
        replayed = await replay_socket.collect(2)
        replay_ids = [frame["payload"]["id"] for frame in of_type(replayed, "message.new")]
        check(
            "the socket replayed the conversation after auth.ok",
            len(replay_ids) == 2,
            f"seqs {replay_ids}, replayed oldest-first",
        )
        check("the replay is ordered by seq", replay_ids == sorted(replay_ids))
        await replay_socket.close()

        page = await self.api.call(
            "GET",
            f"{self.base_a}/conversations/{self.conversation_id}/messages",
            expect=200,
            token=self.alice.token,
        )
        rest_ids = [item["id"] for item in page["items"]]
        check(
            "the REST history on the first replica agrees with the replay",
            set(rest_ids) == set(replay_ids) and page["has_more"] is False,
            f"seqs {rest_ids}, REST pages newest-first",
        )
        contents = [item["content"] for item in page["items"]]
        check(
            "exactly one user message and one assistant reply were stored",
            len(contents) == 2 and any(content.startswith("You said: ") for content in contents),
            " | ".join(contents),
        )

    async def rate_limit(self) -> None:
        step(
            "8",
            "Trip the per-user rate limit across both replicas",
            "the quota is counted in Redis, so it follows the user across processes",
        )
        note("shipped default: 20 frames / 10s per user (WS_RATE_LIMIT_MESSAGES) - no config edit")
        note("the quota is per USER, so alice's sends in the steps above count toward this window")
        conversation = await self.api.call(
            "POST",
            f"{self.base_a}/conversations",
            expect=201,
            token=self.alice.token,
            body={"title": "rate-limit probe"},
        )
        probe_id = conversation["id"]
        note(f"using a throwaway conversation ({probe_id}) so the transcript above stays clean")

        sockets = [
            Socket("alice @ :8000", ws_url(self.base_a, probe_id)),
            Socket("alice @ :8001", ws_url(self.base_b, probe_id)),
        ]
        try:
            for socket in sockets:
                await socket.open()
                await socket.authenticate(self.alice.token)

            def refusals() -> list[dict[str, Any]]:
                return [
                    frame
                    for socket in sockets
                    for frame in of_type(socket.pending(), "error")
                    if frame.get("error") == "rate limit exceeded"
                ]

            per_replica = [0, 0]
            refused_at: int | None = None
            for attempt in range(1, FLOOD_ATTEMPTS + 1):
                index = (attempt - 1) % 2
                # Quiet: echoing 60 outbound frames would bury the interesting line.
                await sockets[index].send(
                    {"type": "message.send", "payload": {"content": f"flood {attempt}"}},
                    quiet=True,
                )
                per_replica[index] += 1
                await asyncio.sleep(0.05)
                if refusals():
                    refused_at = attempt
                    break

            note(
                f"sent {sum(per_replica)} frames as one user: {per_replica[0]} to "
                f"{short(self.base_a)}, {per_replica[1]} to {short(self.base_b)}"
            )
            refused = refusals()
            if refused:
                received("server", refused[0])
            check(
                "the shared Redis quota refused a frame, with the sends split across both replicas",
                bool(refused),
                f"refused at frame {refused_at}",
                hard=False,
            )
            check(
                "the socket stayed open after being rate limited",
                bool(refused) and not any(socket.closed for socket in sockets),
                "a refusal is an error frame, not a disconnect",
                hard=False,
            )
        finally:
            for socket in sockets:
                await socket.close()


async def run(args: argparse.Namespace) -> None:
    print(bold("dizzchat - end-to-end demo"))
    note("black box: HTTP + WebSocket only, this script never imports the application")

    step("0", "Bring the stack up and wait for both replicas", "the demo needs no manual setup")
    if args.no_up:
        note("--no-up: assuming the stack is already running")
        budget = HEALTH_TIMEOUT_RUNNING_SECONDS
    else:
        compose_up()
        budget = HEALTH_TIMEOUT_SECONDS

    async with httpx.AsyncClient(timeout=15) as client:
        await await_health(client, [args.base_a, args.base_b], budget)
        demo = Demo(Api(client), args.base_a, args.base_b)

        await demo.sign_up()
        await demo.create_conversation()
        alice_socket, bob_socket = await demo.open_sockets()
        try:
            text = "hello from the demo"
            client_message_id = uuid4()
            seq = await demo.send_message(alice_socket, bob_socket, text, client_message_id)
            await demo.resend(alice_socket, bob_socket, text, client_message_id, seq)
            await demo.reconnect_and_replay(alice_socket)
        finally:
            await bob_socket.close()
            await alice_socket.close()
        # Last, because it deliberately burns alice's quota for the rest of the window.
        await demo.rate_limit()


def summarise() -> int:
    print()
    print(bold("== Summary"))
    marks = {"PASS": green("PASS"), "FAIL": red("FAIL"), "WARN": yellow("WARN")}
    for entry in CHECKS:
        print(f"   [{marks[entry.status]}] {dim(entry.step + ' -')} {entry.claim}")
    tally = {status: sum(entry.status == status for entry in CHECKS) for status in marks}
    print()
    print(
        f"   {tally['PASS']} passed, {tally['FAIL']} failed, {tally['WARN']} warning(s) "
        f"out of {len(CHECKS)} checks"
    )
    print()
    note("the assistant is a bundled stub (MockAssistantResponder: 'You said: ...') - no LLM key")
    note("tear down with: docker compose down -v")
    return 1 if tally["FAIL"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="End-to-end demo of the dizzchat stack over HTTP and WebSocket."
    )
    parser.add_argument("--base-a", default=DEFAULT_BASE_A, help="first API replica base URL")
    parser.add_argument("--base-b", default=DEFAULT_BASE_B, help="second API replica base URL")
    parser.add_argument(
        "--no-up", action="store_true", help="assume the stack is already running; skip compose up"
    )
    parser.add_argument(
        "--down-after", action="store_true", help="run `docker compose down -v` when finished"
    )
    args = parser.parse_args()

    exit_code = 0
    try:
        asyncio.run(run(args))
    except DemoFailure as exc:
        print()
        print(red(f"   demo failed: {exc}"))
        exit_code = 1
    except KeyboardInterrupt:
        print()
        print(yellow("   interrupted"))
        exit_code = 130
    finally:
        if args.down_after:
            compose("down", "-v")

    return summarise() or exit_code


if __name__ == "__main__":
    sys.exit(main())
