"""Offline HTTP contract tests; no TypeSafe credentials or paid requests."""

import asyncio
import copy
import json
import logging

import httpx
import pytest

from sage.domain.navigation import ActionCandidate, ReadAction, NavigationUnavailable
from sage.providers.typesafe import TypeSafeProvider, SCORE_LEVELS, parse_response

CANDIDATES = (ActionCandidate(id="c0", action=ReadAction(path="app.py", start_line=1, end_line=40),
                              evidence="app.py:7: def solve"),)


def response(actions=True):
    answer = {"type": "choice", "choice": "c0", "confidence": .9,
              "probabilities": {"c0": .95, "RETURN_TO_SOLVER": .05}} if actions else {
        "type": "score", "score": 2.9, "confidence": .9,
        "legend": {str(i): value for i, value in enumerate(SCORE_LEVELS)},
        "probabilities": {"0": 0., "1": 0., "2": .1, "3": .9}}
    return {"model": "jev-1.13.0", "answers": {"next" if actions else "c0": answer},
            "usage": {"input_tokens": 120, "output_tokens": 10}}


@pytest.mark.parametrize("actions", [False, True])
def test_http_contract_and_capture(actions):
    requests = []

    def send(request):
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer secret"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response(actions))

    async def run():
        provider = TypeSafeProvider(api_key="secret", capture=True, transport=httpx.MockTransport(send))
        try:
            method = provider.choose_action if actions else provider.rank_excerpts
            result = await method(state={"goal": "find implementation"}, candidates=CANDIDATES, timeout=1)
            assert result.input_tokens == 120
            assert provider.capture["request"] == requests[0]
            assert "secret" not in json.dumps(provider.capture)
            question = next(iter(requests[0]["questions"].values()))
            assert question["type"] == ("choice" if actions else "score")
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
            with pytest.raises(NavigationUnavailable) as caught:
                await provider.choose_action(state={}, candidates=CANDIDATES, timeout=1)
            assert caught.value.permanent is permanent
            assert "secret" not in str(caught.value)
            assert len(calls) == 1
        finally:
            await provider.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(model="other"),
    lambda d: d["answers"]["next"].update(choice="write_file"),
    lambda d: d["answers"]["next"].update(type="score"),
    lambda d: d["answers"]["next"].update(confidence=float("nan")),
    lambda d: d["answers"]["next"].update(probabilities={"c0": float("inf"), "RETURN_TO_SOLVER": 0}),
    lambda d: d["answers"]["next"]["probabilities"].update(c0=.1),
    lambda d: d["answers"].update(unexpected={}),
    lambda d: d["usage"].update(input_tokens=-1),
    lambda d: d["usage"].update(input_tokens=True),
])
def test_malformed_responses_are_rejected(mutation):
    data = copy.deepcopy(response())
    mutation(data)
    with pytest.raises((ValueError, KeyError)):
        parse_response(data, model="jev-1.13.0", candidates=CANDIDATES, actions=True)


def test_size_gate_prevents_network_and_timeout_cancels_transport():
    calls = []

    async def send(request):
        calls.append(request)
        await asyncio.sleep(1)
        return httpx.Response(200, json=response())

    async def run():
        provider = TypeSafeProvider(api_key="secret", transport=httpx.MockTransport(send))
        try:
            with pytest.raises(NavigationUnavailable, match="request_size"):
                await provider.choose_action(state={"source": "x" * 16000}, candidates=CANDIDATES, timeout=1)
            assert not calls
            with pytest.raises(NavigationUnavailable, match="timeout"):
                await provider.choose_action(state={}, candidates=CANDIDATES, timeout=.001)
            assert len(calls) == 1
        finally:
            await provider.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("actions", [False, True])
@pytest.mark.parametrize("status", [200, 529])
def test_input_log_preserves_complete_wire_body_without_headers(caplog, actions, status):
    caplog.set_level(logging.INFO)
    requests = []
    state = {"source": "source Ω\n\x1b[31m" + "x" * 1200, "goal": "Find callers"}

    def send(request):
        requests.append(json.loads(request.content))
        # Input is visible even when the request subsequently fails.
        assert any(r.message.startswith("Jev request ") for r in caplog.records)
        return httpx.Response(status, json=response(actions))

    async def run():
        provider = TypeSafeProvider(api_key="private-typesafe-key", log_input=True, run_id="run-1",
                                    transport=httpx.MockTransport(send))
        try:
            method = provider.choose_action if actions else provider.rank_excerpts
            if status == 200:
                await method(state=state, candidates=CANDIDATES, timeout=1)
            else:
                with pytest.raises(NavigationUnavailable):
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
            await provider.choose_action(state={"source": secret}, candidates=CANDIDATES, timeout=1)
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
                with pytest.raises(NavigationUnavailable, match="request_size"):
                    await provider.choose_action(state={"source": "x" * 16000}, candidates=CANDIDATES, timeout=1)
            else:
                await provider.choose_action(state={"source": "private-source"}, candidates=CANDIDATES, timeout=1)
                assert provider.capture
        finally:
            await provider.aclose()
    asyncio.run(run())
    assert not any(r.message.startswith("Jev request ") for r in caplog.records)
    assert "private-source" not in caplog.text
