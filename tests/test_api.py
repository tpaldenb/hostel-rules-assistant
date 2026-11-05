"""HTTP tests, plus the real Anthropic SDK talking to a local fake of the Messages API."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
import openai
import pytest
from fastapi.testclient import TestClient

from app import main
from app.answer import Assistant
from conftest import TODAY

client = TestClient(main.app)


def read_events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def chat(**body):
    body.setdefault("as_of", TODAY.isoformat())
    return client.post("/api/chat", json=body)


def test_offline_chat_stream(monkeypatch):
    monkeypatch.setattr(main.assistant, "_client", None)
    monkeypatch.setattr("app.config.MODE", "offline")
    events = read_events(chat(question="What time does the gate close on weekdays?"))
    assert [e["type"] for e in events] == ["meta", "delta", "done"]
    assert events[2]["mode"] == "offline"
    assert "HN-2026-19.1" in events[2]["cited"]
    statuses = {s["id"]: s["status"] for s in events[0]["sources"]}
    assert statuses["HM-2.1"] == "superseded"


def test_input_validation():
    assert client.post("/api/chat", json={"question": ""}).status_code == 422
    assert client.post("/api/chat", json={"question": "x" * 501}).status_code == 422
    assert chat(question="gate", as_of="not-a-date").status_code == 422
    assert chat(question="   ").status_code == 422


def test_clause_endpoints():
    body = client.get("/api/clauses/HM-2.1", params={"as_of": TODAY.isoformat()}).json()
    assert [h["id"] for h in body["history"]] == ["HM-2.1", "HN-2026-04.1", "HN-2026-19.1"]
    assert client.get("/api/clauses/NOPE").status_code == 404
    assert client.get("/api/clauses/HN-2026-19.1", params={"as_of": "2026-01-01"}).status_code == 404


def sse_body(text, stop_reason="end_turn"):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 9}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events).encode()


@pytest.fixture
def fake_api():
    state = {"requests": [], "status": 200, "text": "It closes at 9:15 PM [HN-2026-19.1].", "stop": "end_turn"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state["requests"].append({"path": self.path, "headers": dict(self.headers), "body": body})
            if state["status"] != 200:
                payload = json.dumps({"type": "error", "error": {"type": "authentication_error", "message": "bad"}}).encode()
                self.send_response(state["status"])
                self.send_header("content-type", "application/json")
            else:
                payload = sse_body(state["text"], state["stop"])
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()


def run(corpus, fake_api, question="What time does the gate close on weekdays?"):
    sdk = anthropic.Anthropic(api_key="test", base_url=fake_api["url"], max_retries=0)
    return list(Assistant(corpus, client=sdk).answer(question, [], TODAY))


def test_claude_path_request_and_stream(corpus, fake_api):
    events = run(corpus, fake_api)
    done = events[-1]
    assert done["mode"] == "claude" and done["cited"] == ["HN-2026-19.1"] and not done["warnings"]
    request = fake_api["requests"][0]
    body = request["body"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert body["thinking"] == {"type": "adaptive"} and body["stream"] is True
    headers = {k.lower(): v for k, v in request["headers"].items()}
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    prompt = body["messages"][-1]["content"]
    assert 'id="HN-2026-19.1" status="current"' in prompt
    assert 'id="HM-2.1" status="superseded"' in prompt


def test_auth_failure_falls_back_to_offline(corpus, fake_api):
    fake_api["status"] = 401
    done = run(corpus, fake_api)[-1]
    assert done["mode"] == "offline" and "rejected" in done["notes"][0]
    assert "HN-2026-19.1" in done["cited"]


def test_refusal_falls_back_to_offline(corpus, fake_api):
    fake_api["text"], fake_api["stop"] = "", "refusal"
    done = run(corpus, fake_api)[-1]
    assert done["mode"] == "offline"


def test_hallucinated_citation_is_flagged(corpus, fake_api):
    fake_api["text"] = "It closes at 8 PM [HN-2030-1.1]."
    done = run(corpus, fake_api)[-1]
    assert done["cited"] == [] and "HN-2030-1.1" in done["warnings"][0]


def chat_sse(text, finish="stop"):
    chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": word}, "finish_reason": None}]} for word in text.split(" ") if word]
    chunks = [{**c, "choices": [{**c["choices"][0], "delta": {"content": c["choices"][0]["delta"]["content"] + " "}}]} for c in chunks]
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    base = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m"}
    return ("".join(f"data: {json.dumps({**base, **c})}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


@pytest.fixture
def fake_openai():
    state = {"requests": [], "status": 200, "text": "It closes at 9:15 PM [HN-2026-19.1].", "finish": "stop"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state["requests"].append({"path": self.path, "body": body})
            if state["status"] != 200:
                payload = json.dumps({"error": {"message": "limit", "type": "rate_limit"}}).encode()
                self.send_response(state["status"])
                self.send_header("content-type", "application/json")
            else:
                payload = chat_sse(state["text"], state["finish"])
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/v1"
    yield state
    server.shutdown()


def run_free(corpus, fake):
    sdk = openai.OpenAI(api_key="test", base_url=fake["url"], max_retries=0)
    assistant = Assistant(corpus, client=sdk, provider="gemini")
    return list(assistant.answer("What time does the gate close on weekdays?", [], TODAY))


def test_free_provider_request_and_stream(corpus, fake_openai):
    events = run_free(corpus, fake_openai)
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    done = events[-1]
    assert "9:15 PM" in text and done["mode"] == "gemini" and done["label"] == "Gemini"
    assert done["cited"] == ["HN-2026-19.1"] and not done["warnings"]
    request = fake_openai["requests"][0]
    assert request["path"].endswith("/chat/completions")
    body = request["body"]
    assert body["model"] == "gemini-flash-lite-latest" and body["stream"] is True
    assert body["messages"][0]["role"] == "system"
    assert 'id="HM-2.1" status="superseded"' in body["messages"][-1]["content"]


def test_free_provider_rate_limit_falls_back_to_offline(corpus, fake_openai):
    fake_openai["status"] = 429
    done = run_free(corpus, fake_openai)[-1]
    assert done["mode"] == "offline" and "rate limit" in done["notes"][0]
    assert "HN-2026-19.1" in done["cited"]


def test_free_provider_content_filter_falls_back(corpus, fake_openai):
    fake_openai["text"], fake_openai["finish"] = "", "content_filter"
    assert run_free(corpus, fake_openai)[-1]["mode"] == "offline"


def test_auto_mode_picks_free_provider_from_key(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "MODE", "auto")
    monkeypatch.setattr(config, "has_credentials", lambda: False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert config.active_provider() == "offline"
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert config.active_provider() == "groq"
