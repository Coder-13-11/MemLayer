"""The gateway: a thin pass-through for AI clients that can change a base URL but cannot speak MCP.

It is deliberately not a router or a marketplace (see docs/research.md). It forwards a request to the
provider that natively speaks the same wire format, and does three things on the way:

  1. adds the project's briefing (``briefing.py``) to the request's system text;
  2. returns a context receipt id naming exactly which records the model was given;
  3. meters the call against the workspace.

Wire formats, each forwarded without translation:

    POST /v1/chat/completions        OpenAI Chat Completions  -> OpenAI, Google (OpenAI-compatible endpoint),
                                                                 Anthropic (compatibility endpoint), or any
                                                                 OpenAI-compatible base URL the workspace added
    POST /v1/responses               OpenAI Responses         -> OpenAI or an OpenAI-compatible base URL
    POST /v1/messages                Anthropic Messages       -> Anthropic
    POST /v1/messages/count_tokens   Anthropic token counting -> Anthropic

Two ways to supply the provider key:

    stored        Authorization: Bearer nxk_...   NEXUS uses the key the workspace stored under Providers.
    pass-through  x-nexus-key: nxk_...            The client's own Authorization / x-api-key header is forwarded
                                                  untouched and never stored or logged.

Request headers: ``x-nexus-project`` (name or id; otherwise the key's default project; with neither, nothing is
added), ``x-nexus-context`` (all | confirmed | off), ``x-nexus-context-budget`` (tokens), ``x-nexus-provider``,
``x-nexus-credential`` (label of a stored key), ``x-nexus-refresh: 1`` (rebuild a pinned briefing now).

The briefing is pinned: once built for a project it is reused unchanged for a few minutes, so the start of the
prompt stays byte-identical and provider prompt caches keep working. It is added after the client's own system
text, never before it.

The ``mock`` provider (model names starting with ``mock``) answers inside this process, in each wire format,
and says plainly that no model was called. It exists so the product can be tried and tested without keys.

Request and response bodies are never stored or logged. Only counts, model names and record ids are kept.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import anyio
import httpx
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from .briefing import DEFAULT_BUDGET, build_briefing, estimate_tokens
from .config import Settings
from .models import NotFound, ValidationError
from .tenancy import LimitReached, PermissionDenied

WIRES = {"chat": "/chat/completions", "responses": "/responses", "messages": "/messages",
         "count_tokens": "/messages/count_tokens"}
MAX_BODY = 12 * 1024 * 1024
FORWARD_REQUEST = ("anthropic-version", "anthropic-beta", "openai-organization", "openai-project", "openai-beta")
FORWARD_RESPONSE = ("request-id", "x-request-id", "retry-after", "openai-processing-ms")
TIMEOUT = httpx.Timeout(connect=10, read=600, write=60, pool=10)


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class GatewayError(Exception):
    def __init__(self, status: int, message: str, code: str = "invalid_request_error"):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


class MinuteLimiter:
    """Requests per key in a sliding minute. In memory: each instance counts for itself."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int) -> bool:
        cutoff = time.monotonic() - 60
        with self._lock:
            q = self._hits[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(time.monotonic())
            return True


@dataclass
class Call:
    """One gateway request after authentication, before forwarding."""
    workspace: dict[str, Any]
    actor: str
    wire: str
    body: dict[str, Any]
    provider: str
    upstream_url: str | None
    upstream_headers: dict[str, str]
    project_id: str | None = None
    receipt_id: str | None = None
    context_records: int = 0
    context_tokens: int = 0


def assert_public_upstream(url: str) -> None:
    """Refuse provider URLs that point at this machine or a private network (server-side request forgery)."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise GatewayError(400, "The provider base URL must be an https address.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise GatewayError(502, "The provider's address could not be resolved.", "upstream_error") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip in _NAT64:  # NAT64 addresses embed an IPv4 address, which may be private
            raise GatewayError(400, "The provider base URL points at a private or local address, which is not allowed.")


# ------------------------------------------------------------------ injection


def inject(wire: str, body: dict[str, Any], briefing: str) -> dict[str, Any]:
    """Add the briefing after the client's own system text, in the place each wire format keeps it."""
    body = dict(body)
    if wire == "chat":
        messages = list(body.get("messages") or [])
        at = 0
        while at < len(messages) and isinstance(messages[at], dict) and messages[at].get("role") in ("system", "developer"):
            at += 1
        messages.insert(at, {"role": "system", "content": briefing})
        body["messages"] = messages
    elif wire in ("messages", "count_tokens"):
        system = body.get("system")
        blocks = [{"type": "text", "text": system}] if isinstance(system, str) and system else list(system or [])
        body["system"] = blocks + [{"type": "text", "text": briefing}]
    elif wire == "responses":
        existing = body.get("instructions")
        body["instructions"] = f"{existing}\n\n{briefing}" if isinstance(existing, str) and existing else briefing
    return body


def last_user_text(wire: str, body: dict[str, Any]) -> str:
    def text_of(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
        return ""

    if wire == "responses":
        inp = body.get("input")
        if isinstance(inp, str):
            return inp
        items = [i for i in inp or [] if isinstance(i, dict) and i.get("role") == "user"]
        return text_of(items[-1].get("content")) if items else ""
    users = [m for m in body.get("messages") or [] if isinstance(m, dict) and m.get("role") == "user"]
    return text_of(users[-1].get("content")) if users else ""


def system_text(wire: str, body: dict[str, Any]) -> str:
    if wire == "responses":
        return body.get("instructions") or ""
    if wire in ("messages", "count_tokens"):
        s = body.get("system")
        return s if isinstance(s, str) else " ".join(b.get("text", "") for b in s or [] if isinstance(b, dict))
    return "\n".join(m["content"] for m in body.get("messages") or []
                     if isinstance(m, dict) and m.get("role") in ("system", "developer") and isinstance(m.get("content"), str))


def read_usage(wire: str, obj: Any, into: dict[str, int]) -> None:
    """Pull token counts out of a provider response object or stream event, whatever its shape."""
    if not isinstance(obj, dict):
        return
    usage = obj.get("usage")
    if wire == "messages" and isinstance(obj.get("message"), dict):
        usage = obj["message"].get("usage") or usage
    if wire == "responses" and isinstance(obj.get("response"), dict):
        usage = obj["response"].get("usage") or usage
    if not isinstance(usage, dict):
        return
    for ours, theirs in (("input", ("prompt_tokens", "input_tokens")), ("output", ("completion_tokens", "output_tokens"))):
        for name in theirs:
            if isinstance(usage.get(name), int):
                into[ours] = max(into.get(ours, 0), usage[name])


# ---------------------------------------------------------------- mock model


def mock_reply(wire: str, body: dict[str, Any]) -> str:
    import re

    system = system_text(wire, body)
    ids = list(dict.fromkeys(re.findall(r"\[(rec_[0-9a-f]{12})\]", system)))
    decisions = re.findall(r"^- \[rec_[0-9a-f]{12}\] \([^)]*\) (.+)$", system.split("DECISIONS", 1)[1].split("\n\n", 1)[0], re.M) \
        if "<nexus_project_context" in system and "DECISIONS" in system else []
    lines = ["NEXUS mock model. No AI provider was called, so this is not a real answer."]
    if "<nexus_project_context" in system:
        lines.append(f"I was given this project's record: {len(ids)} records.")
        if decisions:
            lines.append("Decisions I would have to follow: " + "; ".join(decisions[:5]) + ".")
    else:
        lines.append("No project record was added to this request.")
    said = " ".join(last_user_text(wire, body).split())
    if said:
        lines.append(f"You said: {said[:200]}")
    return "\n".join(lines)


def mock_json(wire: str, body: dict[str, Any]) -> dict[str, Any]:
    text = mock_reply(wire, body)
    n_in, n_out = estimate_tokens(json.dumps(body)), estimate_tokens(text)
    model = body.get("model") or "mock-1"
    if wire == "count_tokens":
        return {"input_tokens": n_in}
    if wire == "messages":
        return {"id": "msg_mock", "type": "message", "role": "assistant", "model": model,
                "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": n_in, "output_tokens": n_out}}
    if wire == "responses":
        return {"id": "resp_mock", "object": "response", "created_at": int(time.time()), "status": "completed",
                "model": model, "output": [{"type": "message", "id": "msg_mock", "status": "completed",
                                            "role": "assistant",
                                            "content": [{"type": "output_text", "text": text, "annotations": []}]}],
                "usage": {"input_tokens": n_in, "output_tokens": n_out, "total_tokens": n_in + n_out}}
    return {"id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": n_in, "completion_tokens": n_out, "total_tokens": n_in + n_out}}


def mock_stream(wire: str, body: dict[str, Any]) -> list[bytes]:
    full = mock_json(wire, body)
    model = full.get("model", "mock-1")

    def sse(data: Any, event: str | None = None) -> bytes:
        return ((f"event: {event}\n" if event else "") + f"data: {json.dumps(data)}\n\n").encode()

    if wire == "messages":
        text, usage = full["content"][0]["text"], full["usage"]
        parts = [text[i:i + 24] for i in range(0, len(text), 24)]
        return [sse({"type": "message_start", "message": {**full, "content": [], "stop_reason": None,
                                                          "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 0}}},
                    "message_start"),
                sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    "content_block_start"),
                *[sse({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}},
                      "content_block_delta") for p in parts],
                sse({"type": "content_block_stop", "index": 0}, "content_block_stop"),
                sse({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                     "usage": {"output_tokens": usage["output_tokens"]}}, "message_delta"),
                sse({"type": "message_stop"}, "message_stop")]
    if wire == "responses":
        text = full["output"][0]["content"][0]["text"]
        parts = [text[i:i + 24] for i in range(0, len(text), 24)]
        return [sse({"type": "response.created", "response": {**full, "status": "in_progress", "output": []}},
                    "response.created"),
                *[sse({"type": "response.output_text.delta", "item_id": "msg_mock", "output_index": 0,
                       "content_index": 0, "delta": p}, "response.output_text.delta") for p in parts],
                sse({"type": "response.completed", "response": full}, "response.completed")]
    text = full["choices"][0]["message"]["content"]
    base = {"id": full["id"], "object": "chat.completion.chunk", "created": full["created"], "model": model}
    parts = [text[i:i + 24] for i in range(0, len(text), 24)]
    return [sse({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}),
            *[sse({**base, "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]}) for p in parts],
            sse({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
            sse({**base, "choices": [], "usage": full["usage"]}), b"data: [DONE]\n\n"]


# ------------------------------------------------------------------- gateway


class Gateway:
    def __init__(self, accounts: Any, settings: Settings):
        self.accounts, self.settings = accounts, settings
        self.limiter = MinuteLimiter()
        self._pins: dict[tuple, tuple[float, dict[str, Any], str]] = {}
        self._pin_lock = threading.Lock()
        self._client: httpx.AsyncClient | None = None

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        return self._client

    # ---------------------------------------------------------- authentication

    def authenticate(self, headers: Any) -> tuple[dict[str, Any], bool]:
        """Returns the NEXUS key and whether the provider key is passed through by the client."""
        explicit = headers.get("x-nexus-key")
        bearer = headers.get("authorization", "")
        bearer = bearer[7:].strip() if bearer.lower().startswith("bearer ") else ""
        candidates = [(explicit, True)] if explicit else [(bearer, False), (headers.get("x-api-key"), False)]
        for secret, passthrough in candidates:
            key = self.accounts.lookup_api_key(secret or "")
            if key:
                if "gateway" not in key["scopes"]:
                    raise GatewayError(403, "This NEXUS key does not have the gateway scope.", "permission_error")
                return key, passthrough
        raise GatewayError(401, "Send a NEXUS API key: 'Authorization: Bearer nxk_...' to use the provider key "
                                "stored in the workspace, or 'x-nexus-key: nxk_...' next to your own provider key.",
                           "authentication_error")

    # -------------------------------------------------------------- preparation

    def briefing_for(self, workspace_id: str, project_ref: str, *, policy: str, budget: int, actor: str,
                     model: str | None, refresh: bool = False, channel: str = "gateway") -> tuple[dict[str, Any], str]:
        """The pinned briefing for a project and its receipt id. Rebuilt when the pin expires or on request."""
        store = self.accounts.memory_for(workspace_id)
        project = store.get_project(project_ref)
        pin = (workspace_id, project["id"], policy, budget)
        now = time.monotonic()
        with self._pin_lock:
            held = self._pins.get(pin)
            if held and held[0] > now and not refresh:
                return held[1], held[2]
        built = build_briefing(store.project_snapshot(project["id"]), budget_tokens=budget, policy=policy)
        receipt = self.accounts.save_receipt(
            workspace_id, project_id=project["id"], project_name=project["name"], channel=channel,
            briefing=built["text"], record_refs=built["record_refs"], token_estimate=built["token_estimate"],
            actor=actor, model=model)
        with self._pin_lock:
            if len(self._pins) > 2000:
                self._pins = {k: v for k, v in self._pins.items() if v[0] > now}
            self._pins[pin] = (now + self.settings.briefing_pin_seconds, built, receipt)
        return built, receipt

    def choose_provider(self, wire: str, model: str, requested: str | None, workspace_id: str, passthrough: bool) -> str:
        if requested:
            if requested not in ("openai", "anthropic", "google", "openai_compatible", "mock"):
                raise GatewayError(400, "x-nexus-provider must be openai, anthropic, google, openai_compatible or mock.")
            provider = requested
        elif model.startswith("mock"):
            provider = "mock"
        elif wire in ("messages", "count_tokens"):
            provider = "anthropic"
        elif model.startswith("gemini"):
            provider = "google"
        elif model.startswith("claude"):
            provider = "anthropic"
        else:
            stored = {c["provider"] for c in self.accounts.credentials(workspace_id)}
            provider = "openai" if passthrough or "openai" in stored or "openai_compatible" not in stored \
                else "openai_compatible"
        if provider == "mock" and not self.settings.mock_provider:
            raise GatewayError(400, "The mock provider is turned off on this server.")
        if wire in ("messages", "count_tokens") and provider not in ("anthropic", "mock"):
            raise GatewayError(400, "/v1/messages is the Anthropic wire format and is only forwarded to Anthropic.")
        if wire == "responses" and provider not in ("openai", "openai_compatible", "mock"):
            raise GatewayError(400, "/v1/responses is only forwarded to OpenAI or an OpenAI-compatible endpoint.")
        return provider

    def prepare(self, key: dict[str, Any], passthrough: bool, wire: str, body: dict[str, Any], headers: Any,
                *, actor: str | None = None, channel: str = "gateway") -> Call:
        """Everything that touches the database, done before the first byte goes upstream. Runs in a thread."""
        ws = self.accounts.get_workspace(key["workspace_id"])
        actor = actor or f"key:{key['name']}"
        if not self.limiter.allow(key["id"], ws["limits"]["api_requests_minute"]):
            raise GatewayError(429, "Too many requests on this key in the last minute.", "rate_limit_error")
        if self.accounts.month_gateway_requests(ws["id"]) >= ws["limits"]["gateway_requests_month"]:
            raise GatewayError(429, f"This workspace has used its {ws['limits']['gateway_requests_month']} gateway "
                                    "requests for the month.", "quota_exceeded")
        model = body.get("model")
        if not isinstance(model, str) or not model:
            raise GatewayError(400, "The request needs a model.")
        for name, kinds in (("messages", list), ("system", (str, list)), ("instructions", str)):
            if body.get(name) is not None and not isinstance(body[name], kinds):
                raise GatewayError(400, f"'{name}' has the wrong type for this endpoint.")
        provider = self.choose_provider(wire, model, headers.get("x-nexus-provider"), ws["id"], passthrough)

        # identity: the stream is relayed byte for byte, so it must not arrive compressed
        upstream_headers = {"content-type": "application/json", "accept-encoding": "identity"}
        for name in FORWARD_REQUEST:
            if headers.get(name):
                upstream_headers[name] = headers[name]
        url: str | None = None
        if provider != "mock":
            base = self.settings.upstreams.get(provider)
            if passthrough:
                for name in ("authorization", "x-api-key"):
                    # A NEXUS key placed in a provider header by mistake must never reach the provider.
                    if headers.get(name) and "nxk_" not in headers[name]:
                        upstream_headers[name] = headers[name]
                if not ({"authorization", "x-api-key"} & set(upstream_headers)):
                    raise GatewayError(401, "Pass-through mode needs your provider key in Authorization or x-api-key.",
                                       "authentication_error")
                if provider == "openai_compatible":
                    raise GatewayError(400, "An OpenAI-compatible provider needs its base URL stored under Providers.")
            else:
                if key.get("role") == "viewer":
                    raise GatewayError(403, "A read-only key cannot spend the workspace's stored provider key. Use a "
                                            "read-and-write key, or send your own provider key with x-nexus-key.",
                                       "permission_error")
                try:
                    cred = self.accounts.open_credential(ws["id"], provider, headers.get("x-nexus-credential") or "default")
                except NotFound as e:
                    raise GatewayError(400, str(e)) from None
                except ValidationError as e:
                    raise GatewayError(503, str(e), "configuration_error") from None
                base = cred["base_url"] or base
                if provider == "anthropic":
                    upstream_headers["x-api-key"] = cred["secret"]
                    if wire == "chat":
                        upstream_headers["authorization"] = f"Bearer {cred['secret']}"
                else:
                    upstream_headers["authorization"] = f"Bearer {cred['secret']}"
            if provider == "anthropic":
                upstream_headers.setdefault("anthropic-version", "2023-06-01")
            if not base:
                raise GatewayError(400, f"No base URL is known for provider {provider}.")
            url = base.rstrip("/") + WIRES[wire]
            if not self.settings.allow_insecure_upstreams:
                assert_public_upstream(url)

        call = Call(workspace=ws, actor=actor, wire=wire, body=body, provider=provider, upstream_url=url,
                    upstream_headers=upstream_headers)
        mode = (headers.get("x-nexus-context") or "all").lower()
        if mode not in ("all", "confirmed", "off"):
            raise GatewayError(400, "x-nexus-context must be all, confirmed or off.")
        project = headers.get("x-nexus-project") or key.get("default_project")
        if project and mode != "off":
            try:
                budget = int(headers.get("x-nexus-context-budget") or DEFAULT_BUDGET)
            except ValueError:
                raise GatewayError(400, "x-nexus-context-budget must be a number of tokens.") from None
            try:
                built, receipt = self.briefing_for(ws["id"], project, policy=mode, budget=budget, actor=actor,
                                                   model=model, refresh=headers.get("x-nexus-refresh") == "1",
                                                   channel=channel)
            except NotFound:
                raise GatewayError(404, f"This workspace has no project called {project!r}.") from None
            call.body = inject(wire, body, built["text"])
            call.project_id, call.receipt_id = built["project"]["id"], receipt
            call.context_records, call.context_tokens = len(built["record_refs"]), built["token_estimate"]
        return call

    def record(self, call: Call, status: int, usage: dict[str, int], started: float, channel: str = "gateway") -> None:
        try:
            self.accounts.record_usage(
                call.workspace["id"], channel=channel, operation=call.wire, actor=call.actor,
                project_id=call.project_id, provider=call.provider, model=str(call.body.get("model"))[:120],
                status=status, input_tokens=usage.get("input", 0), output_tokens=usage.get("output", 0),
                context_tokens=call.context_tokens, latency_ms=int((time.monotonic() - started) * 1000),
                receipt_id=call.receipt_id)
        except Exception:  # metering must never fail a request that already succeeded upstream
            pass

    def our_headers(self, call: Call) -> dict[str, str]:
        h = {"x-nexus-provider": call.provider, "x-nexus-context-records": str(call.context_records)}
        if call.receipt_id:
            h["x-nexus-receipt"] = call.receipt_id
        return h

    # ----------------------------------------------------------------- execute

    async def complete(self, call: Call, channel: str = "gateway") -> tuple[int, dict[str, Any], dict[str, str]]:
        """Run a non-streaming call and return (status, json, headers). Used by the in-app playground."""
        started, usage = time.monotonic(), {}
        if call.provider == "mock":
            data, status = mock_json(call.wire, call.body), 200
        else:
            try:
                r = await self.client().post(call.upstream_url, headers=call.upstream_headers, json=call.body)
            except httpx.HTTPError as e:
                await run_in_threadpool(self.record, call, 502, usage, started, channel)
                raise GatewayError(502, f"The provider could not be reached ({type(e).__name__}).", "upstream_error") from None
            status = r.status_code
            try:
                data = r.json()
            except ValueError:
                data = {"error": {"message": r.text[:500]}}
        read_usage(call.wire, data, usage)
        await run_in_threadpool(self.record, call, status, usage, started, channel)
        return status, data, self.our_headers(call)

    async def handle(self, request: Request, wire: str) -> Response:
        try:
            raw = await request.body()
            if len(raw) > MAX_BODY:
                raise GatewayError(413, "The request body is too large.")
            try:
                body = json.loads(raw)
            except (ValueError, RecursionError):
                raise GatewayError(400, "The request body must be JSON.") from None
            if not isinstance(body, dict):
                raise GatewayError(400, "The request body must be a JSON object.")
            key, passthrough = await run_in_threadpool(self.authenticate, request.headers)
            call = await run_in_threadpool(self.prepare, key, passthrough, wire, body, request.headers)
        except GatewayError as e:
            return error_response(wire, e)
        except (PermissionDenied, LimitReached, ValidationError) as e:
            return error_response(wire, GatewayError(400, str(e)))

        started, usage = time.monotonic(), {}
        ours = self.our_headers(call)
        stream = bool(call.body.get("stream")) and wire != "count_tokens"

        if call.provider == "mock":
            if not stream:
                data = mock_json(wire, call.body)
                read_usage(wire, data, usage)
                await run_in_threadpool(self.record, call, 200, usage, started)
                return JSONResponse(data, headers=ours)
            chunks = mock_stream(wire, call.body)
            for c in chunks:
                self._scan(wire, c, usage)

            async def play() -> AsyncIterator[bytes]:
                try:
                    for c in chunks:
                        yield c
                finally:
                    with anyio.CancelScope(shield=True):
                        await run_in_threadpool(self.record, call, 200, usage, started)

            return StreamingResponse(play(), media_type="text/event-stream", headers={**ours, "cache-control": "no-cache"})

        try:
            upstream = await self.client().send(
                self.client().build_request("POST", call.upstream_url, headers=call.upstream_headers, json=call.body),
                stream=True)
        except httpx.HTTPError as e:
            await run_in_threadpool(self.record, call, 502, usage, started)
            return error_response(wire, GatewayError(502, f"The provider could not be reached ({type(e).__name__}).",
                                                     "upstream_error"), ours)
        headers = {**ours, "content-type": upstream.headers.get("content-type", "application/json")}
        for name, value in upstream.headers.items():
            if name.lower() in FORWARD_RESPONSE or "ratelimit" in name.lower():
                headers[name.lower()] = value
        is_events = "text/event-stream" in headers["content-type"]
        if not is_events:
            try:
                content = await upstream.aread()
            except httpx.HTTPError as e:
                await run_in_threadpool(self.record, call, 502, usage, started)
                return error_response(wire, GatewayError(502, f"The provider's answer was cut off ({type(e).__name__}).",
                                                         "upstream_error"), ours)
            finally:
                await upstream.aclose()
            try:
                read_usage(wire, json.loads(content), usage)
            except ValueError:
                pass
            await run_in_threadpool(self.record, call, upstream.status_code, usage, started)
            return Response(content, status_code=upstream.status_code, headers=headers)

        async def relay() -> AsyncIterator[bytes]:
            tail = b""
            try:
                async for chunk in upstream.aiter_raw():
                    tail = self._scan(wire, tail + chunk, usage, keep_partial=True)
                    yield chunk
            finally:
                # Shielded: when the client goes away this task is cancelled, and the request still has to be
                # closed upstream and counted.
                with anyio.CancelScope(shield=True):
                    await upstream.aclose()
                    await run_in_threadpool(self.record, call, upstream.status_code, usage, started)

        return StreamingResponse(relay(), status_code=upstream.status_code, headers={**headers, "cache-control": "no-cache"})

    @staticmethod
    def _scan(wire: str, data: bytes, usage: dict[str, int], keep_partial: bool = False) -> bytes:
        """Read token counts from complete server-sent event lines; return any trailing partial line."""
        lines = data.split(b"\n")
        partial = lines.pop() if keep_partial else b""
        for line in lines:
            if line.startswith(b"data:") and b'"usage"' in line:
                try:
                    read_usage(wire, json.loads(line[5:]), usage)
                except ValueError:
                    pass
        return partial

    async def models(self, request: Request) -> Response:
        try:
            await run_in_threadpool(self.authenticate, request.headers)
        except GatewayError as e:
            return error_response("chat", e)
        data = [{"id": "mock-1", "object": "model", "created": 0, "owned_by": "nexus"}] if self.settings.mock_provider else []
        return JSONResponse({"object": "list", "data": data,
                             "note": "NEXUS forwards any model name to its provider. This list shows only the "
                                     "built-in mock model."})


def error_response(wire: str, e: GatewayError, headers: dict[str, str] | None = None) -> JSONResponse:
    if wire in ("messages", "count_tokens"):
        body: dict[str, Any] = {"type": "error", "error": {"type": e.code, "message": e.message}}
    else:
        body = {"error": {"message": e.message, "type": e.code, "code": e.code}}
    return JSONResponse(body, status_code=e.status, headers=headers)
