"""The gateway, against stand-in provider servers that speak the real wire formats.

No real provider is called anywhere in this file. The stand-ins record exactly what NEXUS sent them, so the
tests can check where the briefing was placed, which credentials went upstream, and what was metered.
"""

import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from nexus.demo import PROJECT
from nexus.gateway import assert_public_upstream, GatewayError, inject
from tests.conftest import free_port, run_server, sign_up, start_nexus

OPENAI_KEY, ANTHROPIC_KEY = "sk-openai-stored-0123456789", "sk-ant-stored-0123456789"


class Upstream:
    """Minimal OpenAI- and Anthropic-shaped endpoints. Keeps every request it receives."""

    def __init__(self):
        self.seen: list[dict] = []
        self.fail_next = 0
        port = free_port()
        self.base = f"http://127.0.0.1:{port}"
        app = Starlette(routes=[Route("/v1/chat/completions", self.chat, methods=["POST"]),
                                Route("/v1/responses", self.responses, methods=["POST"]),
                                Route("/v1/messages", self.messages, methods=["POST"]),
                                Route("/v1/messages/count_tokens", self.count, methods=["POST"])])
        self.stop = run_server(app, port)

    async def _take(self, request: Request) -> dict:
        body = await request.json()
        self.seen.append({"path": request.url.path, "headers": dict(request.headers), "body": body})
        return body

    async def chat(self, request: Request):
        body = await self._take(request)
        if self.fail_next:
            self.fail_next -= 1
            return JSONResponse({"error": {"message": "Rate limit reached", "type": "rate_limit_error"}}, 429,
                                headers={"retry-after": "7", "x-ratelimit-remaining-requests": "0"})
        if body.get("stream"):
            async def events():
                for part in ("Hel", "lo"):
                    yield f'data: {json.dumps({"choices": [{"index": 0, "delta": {"content": part}}]})}\n\n'.encode()
                # the usage event deliberately arrives split across two network chunks
                usage = f'data: {json.dumps({"choices": [], "usage": {"prompt_tokens": 321, "completion_tokens": 2}})}\n\n'.encode()
                yield usage[:20]
                yield usage[20:]
                yield b"data: [DONE]\n\n"
            return StreamingResponse(events(), media_type="text/event-stream")
        return JSONResponse({"id": "chatcmpl-1", "object": "chat.completion", "model": body["model"],
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 300, "completion_tokens": 5, "total_tokens": 305}},
                            headers={"x-request-id": "req_upstream_1"})

    async def responses(self, request: Request):
        body = await self._take(request)
        return JSONResponse({"id": "resp_1", "object": "response", "status": "completed", "model": body["model"],
                             "output": [], "usage": {"input_tokens": 210, "output_tokens": 9, "total_tokens": 219}})

    async def messages(self, request: Request):
        body = await self._take(request)
        if body.get("stream"):
            async def events():
                yield (b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","usage":'
                       b'{"input_tokens":444,"output_tokens":0}}}\n\n')
                yield b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
                yield b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":12}}\n\n'
                yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            return StreamingResponse(events(), media_type="text/event-stream")
        return JSONResponse({"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                             "content": [{"type": "text", "text": "Hi"}], "stop_reason": "end_turn",
                             "usage": {"input_tokens": 400, "output_tokens": 3}})

    async def count(self, request: Request):
        await self._take(request)
        return JSONResponse({"input_tokens": 77})


@pytest.fixture
def gw(tmp_path):
    """NEXUS wired to a stand-in upstream, with a workspace holding the example project and stored keys."""
    up = Upstream()
    base, accounts, stop = start_nexus(
        tmp_path, upstreams={"openai": f"{up.base}/v1", "anthropic": f"{up.base}/v1", "google": f"{up.base}/v1"},
        allow_insecure_upstreams=True)
    owner = sign_up(base, "olga@example.com", workspace="Acme")
    owner.post("/api/workspace/demo", json={})
    owner.post("/api/workspace/providers", json={"provider": "openai", "secret": OPENAI_KEY})
    owner.post("/api/workspace/providers", json={"provider": "anthropic", "secret": ANTHROPIC_KEY})
    key = owner.post("/api/workspace/keys", json={"name": "agent", "role": "editor"}).json()["secret"]
    client = httpx.Client(base_url=base, timeout=20)
    yield {"base": base, "accounts": accounts, "up": up, "owner": owner, "key": key, "http": client,
           "auth": {"Authorization": f"Bearer {key}"}, "ws": accounts.get_workspace("acme")}
    client.close()
    owner.close()
    stop()
    up.stop()


def usage_rows(g):
    return [dict(r) for r in g["accounts"].conn.execute(
        "SELECT * FROM usage_events WHERE channel = 'gateway' ORDER BY at")]


def test_chat_completions_adds_the_briefing_after_the_clients_system_prompt(gw):
    body = {"model": "gpt-test", "messages": [{"role": "system", "content": "You are the client's own prompt."},
                                              {"role": "user", "content": "Write tests for the cart"}]}
    r = gw["http"].post("/v1/chat/completions", headers={**gw["auth"], "x-nexus-project": PROJECT}, json=body)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "Hello"
    assert r.headers["x-nexus-provider"] == "openai" and r.headers["x-request-id"] == "req_upstream_1"
    receipt_id = r.headers["x-nexus-receipt"]
    sent = gw["up"].seen[-1]
    roles = [m["role"] for m in sent["body"]["messages"]]
    assert roles == ["system", "system", "user"]
    assert sent["body"]["messages"][0]["content"] == "You are the client's own prompt."  # untouched and first
    briefing = sent["body"]["messages"][1]["content"]
    assert briefing.startswith("<nexus_project_context") and "(confirmed, from claude-code) Use Vitest" in briefing
    assert sent["body"]["messages"][2] == body["messages"][1]
    # The stored provider key went upstream; the NEXUS key did not.
    assert sent["headers"]["authorization"] == f"Bearer {OPENAI_KEY}"
    assert gw["key"] not in json.dumps(sent) and "x-nexus-project" not in sent["headers"]
    # The receipt names exactly the records in the block.
    receipt = gw["accounts"].get_receipt(gw["ws"]["id"], receipt_id)
    assert receipt["channel"] == "gateway" and receipt["actor"] == "key:agent" and receipt["model"] == "gpt-test"
    assert len(receipt["record_refs"]) == int(r.headers["x-nexus-context-records"]) == 18
    assert all(f"[{ref['id']}]" in briefing for ref in receipt["record_refs"])
    row = usage_rows(gw)[-1]
    assert (row["provider"], row["model"], row["input_tokens"], row["output_tokens"], row["status"]) == \
        ("openai", "gpt-test", 300, 5, 200)
    assert row["receipt_id"] == receipt_id and row["context_tokens"] == receipt["token_estimate"] > 0
    # Nothing of the conversation is kept: only counts and ids.
    assert "Write tests for the cart" not in "\n".join(gw["accounts"].conn.iterdump())


def test_briefing_is_pinned_so_the_prompt_prefix_stays_identical(gw):
    h = {**gw["auth"], "x-nexus-project": PROJECT}
    body = {"model": "gpt-test", "messages": [{"role": "user", "content": "one"}]}
    first = gw["http"].post("/v1/chat/completions", headers=h, json=body)
    # The ledger changes between two requests of the same session...
    pid = gw["owner"].get("/api/projects").json()["projects"][0]["id"]
    gw["owner"].post(f"/api/projects/{pid}/records", json={"kind": "decision", "title": "A brand new decision"})
    second = gw["http"].post("/v1/chat/completions", headers=h, json=body)
    a, b = gw["up"].seen[-2]["body"]["messages"][0]["content"], gw["up"].seen[-1]["body"]["messages"][0]["content"]
    assert a == b and first.headers["x-nexus-receipt"] == second.headers["x-nexus-receipt"]  # ...the block does not
    # ...until the client asks for a fresh one (or the pin expires).
    third = gw["http"].post("/v1/chat/completions", headers={**h, "x-nexus-refresh": "1"}, json=body)
    assert "A brand new decision" in gw["up"].seen[-1]["body"]["messages"][0]["content"]
    assert third.headers["x-nexus-receipt"] != first.headers["x-nexus-receipt"]
    assert len(gw["accounts"].receipts(gw["ws"]["id"])) == 2  # one receipt per briefing, not per request


def test_context_can_be_limited_to_confirmed_records_or_turned_off(gw):
    body = {"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]}
    h = {**gw["auth"], "x-nexus-project": PROJECT}
    r = gw["http"].post("/v1/chat/completions", headers={**h, "x-nexus-context": "confirmed"}, json=body)
    block = gw["up"].seen[-1]["body"]["messages"][0]["content"]
    assert "Use Vitest" in block and "idempotency key" not in block and int(r.headers["x-nexus-context-records"]) < 18
    r = gw["http"].post("/v1/chat/completions", headers={**h, "x-nexus-context": "off"}, json=body)
    assert gw["up"].seen[-1]["body"] == body and "x-nexus-receipt" not in r.headers
    r = gw["http"].post("/v1/chat/completions", headers=gw["auth"], json=body)  # no project named: plain pass-through
    assert gw["up"].seen[-1]["body"] == body and r.headers["x-nexus-context-records"] == "0"
    assert gw["http"].post("/v1/chat/completions", headers={**h, "x-nexus-context": "some"}, json=body).status_code == 400
    missing = gw["http"].post("/v1/chat/completions", headers={**gw["auth"], "x-nexus-project": "Nope"}, json=body)
    assert missing.status_code == 404 and "Nope" in missing.json()["error"]["message"]


def test_anthropic_messages_keeps_the_clients_cached_system_blocks_first(gw):
    system = [{"type": "text", "text": "Client system prompt", "cache_control": {"type": "ephemeral"}}]
    body = {"model": "claude-test", "max_tokens": 64, "system": system,
            "messages": [{"role": "user", "content": "Refactor the payment handler"}]}
    h = {"x-api-key": gw["key"], "anthropic-version": "2023-06-01", "anthropic-beta": "some-beta",
         "x-nexus-project": PROJECT}
    r = gw["http"].post("/v1/messages", headers=h, json=body)
    assert r.status_code == 200 and r.json()["content"][0]["text"] == "Hi" and r.headers["x-nexus-provider"] == "anthropic"
    sent = gw["up"].seen[-1]
    assert sent["path"] == "/v1/messages"
    assert sent["body"]["system"][0] == system[0]  # byte-identical, so the client's cache breakpoint still hits
    assert sent["body"]["system"][1]["text"].startswith("<nexus_project_context") and len(sent["body"]["system"]) == 2
    assert "cache_control" not in sent["body"]["system"][1]
    assert sent["headers"]["x-api-key"] == ANTHROPIC_KEY and sent["headers"]["anthropic-beta"] == "some-beta"
    row = usage_rows(gw)[-1]
    assert (row["provider"], row["input_tokens"], row["output_tokens"]) == ("anthropic", 400, 3)
    # A plain string system prompt becomes two blocks.
    gw["http"].post("/v1/messages", headers=h, json={**body, "system": "Plain"})
    assert [b["text"][:5] for b in gw["up"].seen[-1]["body"]["system"]] == ["Plain", "<nexu"]
    # Token counting is forwarded too, and is given the same block so the count is honest.
    n = gw["http"].post("/v1/messages/count_tokens", headers=h, json={"model": "claude-test", "messages": body["messages"]})
    assert n.json() == {"input_tokens": 77} and gw["up"].seen[-1]["path"] == "/v1/messages/count_tokens"
    assert gw["up"].seen[-1]["body"]["system"][0]["text"].startswith("<nexus_project_context")
    # An Anthropic-shaped error for an Anthropic-shaped endpoint.
    bad = gw["http"].post("/v1/messages", headers={"x-api-key": "nxk_wrong"}, json=body)
    assert bad.status_code == 401 and bad.json()["type"] == "error" and bad.json()["error"]["type"] == "authentication_error"


def test_responses_api_appends_to_instructions(gw):
    h = {**gw["auth"], "x-nexus-project": PROJECT}
    r = gw["http"].post("/v1/responses", headers=h, json={"model": "gpt-test", "instructions": "Be brief.",
                                                         "input": "Plan the refund flow"})
    assert r.status_code == 200
    sent = gw["up"].seen[-1]
    assert sent["path"] == "/v1/responses" and sent["body"]["input"] == "Plan the refund flow"
    assert sent["body"]["instructions"].startswith("Be brief.\n\n<nexus_project_context")
    assert usage_rows(gw)[-1]["input_tokens"] == 210
    gw["http"].post("/v1/responses", headers=h, json={"model": "gpt-test", "input": "x"})
    assert gw["up"].seen[-1]["body"]["instructions"].startswith("<nexus_project_context")


def test_streaming_is_relayed_untouched_and_still_metered(gw):
    h = {**gw["auth"], "x-nexus-project": PROJECT}
    with gw["http"].stream("POST", "/v1/chat/completions", headers=h,
                           json={"model": "gpt-test", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-nexus-receipt"].startswith("rcp_")
        text = "".join(r.iter_text())
    assert text.count("data: ") == 4 and text.endswith("data: [DONE]\n\n")
    assert '"content": "Hel"' in text and '"prompt_tokens": 321' in text
    row = usage_rows(gw)[-1]
    assert (row["input_tokens"], row["output_tokens"]) == (321, 2)  # read from an event split across chunks

    with gw["http"].stream("POST", "/v1/messages", headers={"x-api-key": gw["key"], "x-nexus-project": PROJECT},
                           json={"model": "claude-test", "max_tokens": 9, "stream": True,
                                 "messages": [{"role": "user", "content": "hi"}]}) as r:
        events = "".join(r.iter_text())
    assert events.count("event: ") == 4 and "message_stop" in events
    row = usage_rows(gw)[-1]
    assert (row["provider"], row["input_tokens"], row["output_tokens"]) == ("anthropic", 444, 12)


def test_pass_through_mode_forwards_the_clients_own_provider_key_and_stores_nothing(gw):
    mine = "sk-my-own-provider-key-9876543210"
    h = {"x-nexus-key": gw["key"], "Authorization": f"Bearer {mine}", "x-nexus-project": PROJECT}
    r = gw["http"].post("/v1/chat/completions", headers=h,
                        json={"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    sent = gw["up"].seen[-1]
    assert sent["headers"]["authorization"] == f"Bearer {mine}" and OPENAI_KEY not in json.dumps(sent)
    assert "x-nexus-key" not in sent["headers"]
    assert mine not in "\n".join(gw["accounts"].conn.iterdump())
    # Anthropic style: the client's x-api-key is forwarded.
    r = gw["http"].post("/v1/messages", headers={"x-nexus-key": gw["key"], "x-api-key": mine},
                        json={"model": "claude-test", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and gw["up"].seen[-1]["headers"]["x-api-key"] == mine
    none = gw["http"].post("/v1/chat/completions", headers={"x-nexus-key": gw["key"]},
                           json={"model": "gpt-test", "messages": []})
    assert none.status_code == 401 and "provider key" in none.json()["error"]["message"]


def test_provider_is_chosen_from_the_model_and_errors_pass_through(gw):
    body = {"messages": [{"role": "user", "content": "hi"}]}
    r = gw["http"].post("/v1/chat/completions", headers=gw["auth"], json={**body, "model": "gemini-test"})
    assert r.status_code == 400 and "no google key" in r.json()["error"]["message"]  # nothing stored for Google
    r = gw["http"].post("/v1/chat/completions", headers=gw["auth"], json={**body, "model": "claude-test"})
    assert r.headers["x-nexus-provider"] == "anthropic" and gw["up"].seen[-1]["headers"]["x-api-key"] == ANTHROPIC_KEY
    r = gw["http"].post("/v1/responses", headers={**gw["auth"], "x-nexus-provider": "anthropic"},
                        json={"model": "x", "input": "hi"})
    assert r.status_code == 400
    assert gw["http"].post("/v1/chat/completions", headers=gw["auth"], json={"messages": []}).status_code == 400
    assert gw["http"].post("/v1/chat/completions", headers=gw["auth"], content=b"not json").status_code == 400
    # A provider's own error reaches the client as the provider sent it, with its rate-limit headers.
    gw["up"].fail_next = 1
    r = gw["http"].post("/v1/chat/completions", headers=gw["auth"], json={**body, "model": "gpt-test"})
    assert r.status_code == 429 and r.json()["error"]["message"] == "Rate limit reached"
    assert r.headers["retry-after"] == "7" and r.headers["x-ratelimit-remaining-requests"] == "0"
    assert usage_rows(gw)[-1]["status"] == 429
    summary = gw["owner"].get("/api/workspace/usage").json()
    assert {m["model"]: m["errors"] for m in summary["by_model"]}["gpt-test"] == 1


def test_mock_model_answers_in_every_wire_format_without_a_provider(gw):
    seen_before = len(gw["up"].seen)
    h = {**gw["auth"], "x-nexus-project": PROJECT}
    chat = gw["http"].post("/v1/chat/completions", headers=h,
                           json={"model": "mock-1", "messages": [{"role": "user", "content": "Add Jest tests"}]}).json()
    text = chat["choices"][0]["message"]["content"]
    assert "No AI provider was called" in text and "18 records" in text and "Use Vitest for unit tests" in text
    msg = gw["http"].post("/v1/messages", headers=h, json={"model": "mock-1", "max_tokens": 5,
                                                          "messages": [{"role": "user", "content": "hi"}]}).json()
    assert msg["type"] == "message" and msg["usage"]["input_tokens"] > 0
    resp = gw["http"].post("/v1/responses", headers=h, json={"model": "mock-1", "input": "hi"}).json()
    assert resp["output"][0]["content"][0]["type"] == "output_text"
    with gw["http"].stream("POST", "/v1/chat/completions", headers=h,
                           json={"model": "mock-1", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as r:
        streamed = "".join(json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                           for line in r.iter_lines() if line.startswith("data: {") and json.loads(line[6:])["choices"])
    assert "No AI provider was called" in streamed
    with gw["http"].stream("POST", "/v1/messages", headers=h, json={"model": "mock-1", "max_tokens": 5, "stream": True,
                                                                   "messages": [{"role": "user", "content": "hi"}]}) as r:
        kinds = [line[7:] for line in r.iter_lines() if line.startswith("event: ")]
    assert kinds[0] == "message_start" and kinds[-1] == "message_stop" and "content_block_delta" in kinds
    assert len(gw["up"].seen) == seen_before  # nothing left this process
    assert gw["http"].get("/v1/models", headers=gw["auth"]).json()["data"][0]["id"] == "mock-1"
    assert gw["http"].get("/v1/models").status_code == 401


def test_playground_runs_through_the_same_path_and_shows_what_was_sent(gw):
    pid = gw["owner"].get("/api/projects").json()["projects"][0]["id"]
    r = gw["owner"].post("/api/workspace/playground", json={"project": pid, "message": "Should I use Jest?"})
    out = r.json()
    assert r.status_code == 200 and out["provider"] == "mock" and out["receipt_id"].startswith("rcp_")
    assert out["request_sent"]["messages"][0]["content"].startswith("<nexus_project_context")
    assert out["request_sent"]["messages"][1] == {"role": "user", "content": "Should I use Jest?"}
    detail = gw["owner"].get(f"/api/workspace/receipts/{out['receipt_id']}").json()
    assert detail["actor"] == "olga@example.com" and not any(x["changed_since"] for x in detail["record_refs"])
    # Change a record: the receipt shows the model saw an older version.
    rid = next(x["id"] for x in detail["record_refs"] if x["title"] == "Use Vitest for unit tests")
    gw["owner"].patch(f"/api/records/{rid}", json={"reason": "tightened", "content": "Vitest 3 only."})
    changed = {x["id"]: x for x in gw["owner"].get(f"/api/workspace/receipts/{out['receipt_id']}").json()["record_refs"]}
    assert changed[rid]["changed_since"] and changed[rid]["current_version"] == changed[rid]["version"] + 1
    real = gw["owner"].post("/api/workspace/playground", json={"project": pid, "message": "hi", "model": "gpt-test"})
    assert real.json()["provider"] == "openai" and real.json()["response"]["choices"][0]["message"]["content"] == "Hello"


def test_monthly_quota_and_per_minute_limits(gw):
    accounts, ws = gw["accounts"], gw["ws"]
    body = {"model": "mock-1", "messages": [{"role": "user", "content": "hi"}]}
    accounts.set_plan(ws["id"], "free", {"gateway_requests_month": 2, "api_requests_minute": 1000})
    codes = [gw["http"].post("/v1/chat/completions", headers=gw["auth"], json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    over = gw["http"].post("/v1/chat/completions", headers=gw["auth"], json=body).json()
    assert over["error"]["code"] == "quota_exceeded"
    # Per minute, per key, counted once across the gateway and the REST API. Four requests are already counted.
    accounts.set_plan(ws["id"], "team", {"api_requests_minute": 7})
    codes = [gw["http"].post("/v1/chat/completions", headers=gw["auth"], json=body).status_code for _ in range(2)]
    assert codes == [200, 200]
    rest = [gw["http"].get("/v1/projects", headers=gw["auth"]) for _ in range(3)]
    assert [r.status_code for r in rest] == [200, 429, 429] and rest[-1].headers["retry-after"]
    limited = gw["http"].post("/v1/chat/completions", headers=gw["auth"], json=body)
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limit_error"


def test_private_provider_addresses_are_refused():
    for url in ("http://api.openai.com/v1", "https://127.0.0.1/v1", "https://localhost/v1",
                "https://169.254.169.254/latest", "https://10.1.2.3/v1", "https://[::1]/v1"):
        with pytest.raises(GatewayError):
            assert_public_upstream(url)


def test_injection_places_the_block_correctly_for_each_wire():
    chat = inject("chat", {"messages": [{"role": "developer", "content": "d"}, {"role": "system", "content": "s"},
                                        {"role": "user", "content": "u"}, {"role": "system", "content": "late"}]}, "B")
    assert [m["content"] for m in chat["messages"]] == ["d", "s", "B", "u", "late"]
    assert inject("chat", {}, "B")["messages"] == [{"role": "system", "content": "B"}]
    assert inject("messages", {"system": ""}, "B")["system"] == [{"type": "text", "text": "B"}]
    original = {"messages": [{"role": "user", "content": "u"}]}
    inject("chat", original, "B")
    assert original == {"messages": [{"role": "user", "content": "u"}]}  # the caller's body is not modified
