"""Copilot pipeline: intent detection, evidence retrieval, filter propagation, grounding.
Gemini is replaced by a local mock of its streaming API (no network, no quota)."""
import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

import app.copilot_agent as agent
from app import gemini_pool
from app.store import store

mock = FastAPI()
CALLS: list[dict] = []
SCRIPT = {"fn": None}


def _sse(*chunks):
    async def gen():
        for parts in chunks:
            yield "data: " + json.dumps({"candidates": [{"content": {"role": "model", "parts": parts}}]}) + "\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@mock.post("/v1beta/models/{spec}")
async def _gen(spec: str, request: Request):
    body = await request.json()
    CALLS.append({"model": spec.split(":")[0], "body": body})
    return SCRIPT["fn"](body, len(CALLS))


@pytest.fixture(scope="module", autouse=True)
def mock_gemini(client):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    threading.Thread(target=lambda: uvicorn.run(mock, port=port, log_level="warning"), daemon=True).start()
    time.sleep(1.0)
    old_url, old_key = agent.STREAM_URL, agent.gemini_key
    agent.STREAM_URL = f"http://127.0.0.1:{port}/v1beta/models/{{model}}:streamGenerateContent?alt=sse"
    agent.gemini_key = lambda: "test-key"
    yield
    agent.STREAM_URL, agent.gemini_key = old_url, old_key


def _run(messages, filters=None):
    CALLS.clear()
    gemini_pool._blocked_until.clear()

    async def go():
        return [json.loads(e[6:]) async for e in agent.chat_stream(messages, filters)]
    return asyncio.run(go())


def test_intent_detection():
    assert agent.detect_intents("hi!") == ["greeting"]
    assert "pairs" in agent.detect_intents("What are the most dangerous configuration combinations?")
    assert "seeds" in agent.detect_intents("Which random seed is most unstable?")
    assert "hardware" in agent.detect_intents("Which hardware platform is least reliable?")
    assert "recommend" in agent.detect_intents("What should we change before the next test campaign?")


def test_greeting_needs_no_evidence():
    SCRIPT["fn"] = lambda body, n: _sse([{"text": "Hello!"}])
    ev = _run([{"role": "user", "content": "hi"}])
    assert [e for e in ev if e["type"] == "text"][0]["delta"] == "Hello!"
    assert not [e for e in ev if e["type"] == "evidence"]
    assert len(CALLS) == 1


def test_evidence_is_retrieved_and_filters_reach_the_prompt():
    hw = store.df["hardware"].unique().sort()[0]
    SCRIPT["fn"] = lambda body, n: _sse([{"text": "answer"}])
    ev = _run([{"role": "user", "content": "What are the most dangerous configuration combinations?"}], {"hardware": hw})
    system = CALLS[0]["body"]["systemInstruction"]["parts"][0]["text"]
    assert f'"hardware": "{hw}"' in system and "RETRIEVED EVIDENCE" in system and "toxic_combinations" in system
    assert "NEVER say a setting \"causes\"" in system
    cards = [e for e in ev if e["type"] == "evidence"]
    assert cards and cards[0]["items"][0]["evidence"]["runs"] > 0
    assert [e for e in ev if e["type"] == "intent"][0]["filters"] == {"hardware": hw}


def test_dashboard_filters_are_injected_into_tool_calls():
    hw = store.df["hardware"].unique().sort()[0]

    def script(body, n):
        if n == 1:
            return _sse([{"functionCall": {"name": "get_toxic_combinations", "args": {}, "id": "c1"}, "thoughtSignature": "S"}])
        result = body["contents"][-1]["parts"][0]["functionResponse"]["response"]["result"]
        script.runs = result["runs"]
        return _sse([{"text": "done"}])
    SCRIPT["fn"] = script
    _run([{"role": "user", "content": "hello there, analyse things"}], {"hardware": hw})
    assert script.runs == int((store.df["hardware"] == hw).sum()), "tool must analyse the filtered subset"


def test_grounding_check_traces_numbers():
    src = [{"failure_rate": 0.8801, "runs": 517, "lift": 3.378}]
    ok = agent.grounding_check("Failure rate **88.0%** over 517 runs (3.38x lift).", src)
    assert ok["figures"] == 3 and ok["matched"] == 3
    bad = agent.grounding_check("Failure rate 91.2% over 517 runs.", src)
    assert bad["matched"] == 1 and "91.2%" in bad["unmatched"]


def test_answer_numbers_from_evidence_are_grounded():
    def script(body, n):
        system = body["systemInstruction"]["parts"][0]["text"]
        rate = json.loads(system.split("RETRIEVED EVIDENCE for the latest question (detected intents: pairs):\n", 1)[1])["toxic_combinations"]["toxic"][0]
        return _sse([{"text": f"Top combination fails {rate['failure_rate'] * 100:.1f}% of {rate['runs']:,} runs."}])
    SCRIPT["fn"] = script
    ev = _run([{"role": "user", "content": "Which combinations are toxic?"}])
    g = [e for e in ev if e["type"] == "grounding"][0]
    assert g["figures"] == 2 and g["matched"] == 2


def test_quota_exhaustion_is_reported_not_faked():
    from fastapi.responses import JSONResponse
    SCRIPT["fn"] = lambda body, n: JSONResponse({"error": {"code": 429, "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "9999s"}]}}, status_code=429)
    ev = _run([{"role": "user", "content": "Which parameter is the biggest failure driver?"}])
    assert ev[-1]["type"] == "error" and not [e for e in ev if e["type"] == "text"]
