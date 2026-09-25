"""Offline HTTP contract tests; no TypeSafe credentials or paid requests."""

import asyncio
import copy
import json
import logging

import httpx
import pytest

from sage.domain.relevance import FileCandidate, RelevanceUnavailable
from sage.harness.jev.provider import TypeSafeProvider, SCORE_LEVELS, parse_response

CANDIDATES = (FileCandidate(id="c0", path="app.py", evidence="app.py:7: def solve"),)


def response():
    answer = {"type": "score", "score": 2.9, "confidence": .9,
        "legend": {str(i): value for i, value in enumerate(SCORE_LEVELS)},
        "probabilities": {"0": 0., "1": 0., "2": .1, "3": .9}}
    return {"model": "jev-1.13.0", "answers": {"c0": answer},
            "usage": {"input_tokens": 120, "output_tokens": 10}}


def test_http_contract_and_capture():
    requests = []

    def send(request):
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer secret"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    async def run():
        provider = TypeSafeProvider(api_key="secret", capture=True, transport=httpx.MockTransport(send))
        try:
            method = provider.score_files
            result = await method(state={"goal": "find implementation"}, candidates=CANDIDATES, timeout=1)
            assert result.input_tokens == 120
            assert provider.capture["request"] == requests[0]
            assert "secret" not in json.dumps(provider.capture)
            question = next(iter(requests[0]["questions"].values()))
            assert question["type"] == "score"
            assert "app.py" in json.dumps(question)
        finally:
            await provider.aclose()
        assert provider._client.is_closed
    asyncio.run(run())


@pytest.mark.parametrize("status,permanent", [(401, True), (422, True), (429, False), (529, False), (500, False)])
def test_errors_are_secret_safe_and_never_retried(status, permanent):
    calls = []

    async def run():
        def send(request):
            calls.append(request)
            return httpx.Response(status, text="secret source should not leak")
        provider = TypeSafeProvider(api_key="secret", transport=httpx.MockTransport(send))
        try:
            with pytest.raises(RelevanceUnavailable) as caught:
                await provider.score_files(state={}, candidates=CANDIDATES, timeout=1)
            assert caught.value.permanent is permanent
            assert "secret" not in str(caught.value)
            assert len(calls) == 1
        finally:
            await provider.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(model="other"),
    lambda d: d["answers"]["c0"].update(score=4),
    lambda d: d["answers"]["c0"].update(type="choice"),
    lambda d: d["answers"]["c0"].update(confidence=float("nan")),
    lambda d: d["answers"]["c0"].update(probabilities={"0": float("inf"), "1": 0, "2": 0, "3": 0}),
    lambda d: d["answers"]["c0"]["probabilities"].update({"3": .1}),
    lambda d: d["answers"].update(unexpected={}),
    lambda d: d["answers"].pop("c0"),
    lambda d: d["answers"]["c0"].update(score=1.5),
    lambda d: d["answers"]["c0"].update(legend={"0": "incorrect rubric"}),
    lambda d: d["answers"]["c0"].update(confidence=1.1),
    lambda d: d["usage"].update(input_tokens=-1),
    lambda d: d["usage"].update(input_tokens=True),
])
def test_malformed_responses_are_rejected(mutation):
    data = copy.deepcopy(response())
    mutation(data)
    with pytest.raises((ValueError, KeyError)):
        parse_response(data, model="jev-1.13.0", candidates=CANDIDATES)


def test_size_gate_prevents_network_and_timeout_cancels_transport():
    calls = []

    async def send(request):
        calls.append(request)
        await asyncio.sleep(1)
        return httpx.Response(200, json=response())

    async def run():
        provider = TypeSafeProvider(api_key="secret", transport=httpx.MockTransport(send))
        try:
            with pytest.raises(RelevanceUnavailable, match="request_size"):
                await provider.score_files(state={"source": "x" * 16000}, candidates=CANDIDATES, timeout=1)
            assert not calls
            with pytest.raises(RelevanceUnavailable, match="timeout"):
                await provider.score_files(state={}, candidates=CANDIDATES, timeout=.001)
            assert len(calls) == 1
        finally:
            await provider.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status", [200, 529])
def test_input_log_preserves_complete_wire_body_without_headers(caplog, status):
    caplog.set_level(logging.INFO)
    requests = []
    state = {"source": "source Ω\n\x1b[31m" + "x" * 1200, "goal": "Find callers"}

    def send(request):
        requests.append(json.loads(request.content))
        # Input is visible even when the request subsequently fails.
        assert any(r.message.startswith("Jev request ") for r in caplog.records)
        return httpx.Response(status, json=response())

    async def run():
        provider = TypeSafeProvider(api_key="private-typesafe-key", log_input=True, run_id="run-1",
                                    transport=httpx.MockTransport(send))
        try:
            method = provider.score_files
            if status == 200:
                await method(state=state, candidates=CANDIDATES, timeout=1)
            else:
                with pytest.raises(RelevanceUnavailable):
                    await method(state=state, candidates=CANDIDATES, timeout=1)
        finally:
            await provider.aclose()
    asyncio.run(run())
    message = next(r.message.removeprefix("Jev request ") for r in caplog.records
                   if r.message.startswith("Jev request "))
    assert json.loads(message) == {"run_id": "run-1", "request": 1, "input": requests[0]}
    assert "\n" not in message and "\x1b" not in message
    assert "private-typesafe-key" not in caplog.text and "Authorization" not in caplog.text


def test_input_log_redacts_key_without_changing_request(caplog):
    caplog.set_level(logging.INFO)
    secret = 'private-"key"'
    wire = []

    def send(request):
        wire.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    async def run():
        provider = TypeSafeProvider(api_key=secret, log_input=True, transport=httpx.MockTransport(send))
        try:
            await provider.score_files(state={"source": secret}, candidates=CANDIDATES, timeout=1)
        finally:
            await provider.aclose()
    asyncio.run(run())
    message = next(r.message.removeprefix("Jev request ") for r in caplog.records
                   if r.message.startswith("Jev request "))
    assert json.loads(message)["input"]["state"]["source"] == "[REDACTED]"
    assert wire[0]["state"]["source"] == secret


@pytest.mark.parametrize("oversize", [False, True])
def test_capture_does_not_enable_input_logging_and_size_gate_never_logs_body(caplog, oversize):
    caplog.set_level(logging.DEBUG)

    async def run():
        provider = TypeSafeProvider(api_key="secret", capture=True, log_input=oversize,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response())))
        try:
            if oversize:
                with pytest.raises(RelevanceUnavailable, match="request_size"):
                    await provider.score_files(state={"source": "x" * 16000}, candidates=CANDIDATES, timeout=1)
            else:
                await provider.score_files(state={"source": "private-source"}, candidates=CANDIDATES, timeout=1)
                assert provider.capture
        finally:
            await provider.aclose()
    asyncio.run(run())
    assert not any(r.message.startswith("Jev request ") for r in caplog.records)
    assert "private-source" not in caplog.text
