#!/usr/bin/env python3
"""Anthropic Messages app with a tool round trip, instrumented with ctxlineage.

A one-question deploy assistant that exercises the anthropic-specific shapes
the report renders: the top-level `system` kwarg, a tool_use / tool_result
round trip across two calls (the first call answers with a tool_use block, the
app runs the tool, tags the result, and feeds a tool_result block back), and a
streamed final answer. One span covers the whole question; the system prompt
and the tool result carry source=/transform= provenance.

Usage:
    ANTHROPIC_API_KEY=... uv run python examples/anthropic_app.py  # real API (claude-opus-4-8)
    uv run python examples/anthropic_app.py --mock                 # keyless: respx-mocked, offline

Then: ctxlineage report --open
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

import ctxlineage

SYSTEM_PROMPT = (
    "You are deploy-bot for the (fictional) meridian platform team. Before answering a "
    "deploy question, read the live status board with the check_service tool and answer "
    "strictly from what it returns. If the board does not cover the service, say so."
)

QUESTION = "Is it safe to deploy payments-api right now?"

STATUS_BOARD = {
    "payments-api": "green - last deploy 2h ago, error rate 0.02%, deploy window open",
    "search-api": "yellow - elevated latency since 09:40 JST, deploys frozen",
}

TOOLS = [
    {
        "name": "check_service",
        "description": "Look up one service's entry on the team's live status board.",
        "input_schema": {
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": ["service"],
        },
    }
]

# Deterministic final answer served in --mock mode, worded from the status
# board entry the tool returns - the report shows the flow, not canned lorem.
MOCK_ANSWER = (
    "The status board shows payments-api green: the last deploy was 2h ago with a "
    "0.02% error rate, and the deploy window is open - safe to deploy."
)


def check_service(service: str) -> str:
    """The tool itself: a lookup into the (toy) status board."""
    return STATUS_BOARD.get(service, f"unknown service: {service}")


def run_question(client, model: str) -> str:
    """One question = one span and (normally) two Messages calls.

    Call 1 sends the top-level `system` kwarg plus the tool definitions and
    comes back with a tool_use block. The app executes the tool, tags the exact
    string that re-enters the context, and feeds it back as a tool_result
    block. Call 2 streams the final answer through the patched stream helper.
    """
    with ctxlineage.span("deploy_check") as span:
        span.tag("system", SYSTEM_PROMPT, source="anthropic_app.py:SYSTEM_PROMPT")
        messages = [{"role": "user", "content": QUESTION}]
        first = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        tool_uses = [block for block in first.content if block.type == "tool_use"]
        if not tool_uses:  # a real model may answer directly; stay honest, don't crash
            return "".join(block.text for block in first.content if block.type == "text")

        # Re-serialize the content blocks so the recorded request (and the
        # report's assistant segment) carries plain dicts, not object reprs.
        messages.append(
            {
                "role": "assistant",
                "content": [block.model_dump(exclude_none=True) for block in first.content],
            }
        )
        results = []
        for block in tool_uses:
            result = check_service(**block.input)
            print(f"  [tool] check_service({block.input.get('service')!r})")
            # tag the exact string that re-enters the context, so the report
            # can attribute the tool_result segment to its tool
            span.tag(
                "tool_result",
                result,
                source="tool:check_service",
                transform="status_board[service]",
            )
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
        messages.append({"role": "user", "content": results})

        with client.messages.stream(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            return "".join(stream.text_stream)


def _tool_use_response(sent: dict) -> dict:
    """Canned call-1 response: a text block plus a tool_use block."""
    input_tokens = sum(len(json.dumps(m)) // 4 for m in sent["messages"])
    return {
        "id": "msg-mock-1",
        "type": "message",
        "role": "assistant",
        "model": sent["model"],
        "content": [
            {"type": "text", "text": "Let me check the status board for payments-api."},
            {
                "type": "tool_use",
                "id": "toolu-mock-1",
                "name": "check_service",
                "input": {"service": "payments-api"},
            },
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": 30},
    }


def _sse(*events) -> bytes:
    """Anthropic SSE frames are named events; the SDK dispatches on the name."""
    return b"".join(
        b"event: " + e["type"].encode() + b"\ndata: " + json.dumps(e).encode() + b"\n\n"
        for e in events
    )


def _answer_stream(model: str, text: str) -> bytes:
    """Canned call-2 response: the answer as two text deltas, then final usage."""
    head, tail = text[: len(text) // 2], text[len(text) // 2 :]
    return _sse(
        {
            "type": "message_start",
            "message": {
                "id": "msg-mock-2",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 90, "output_tokens": 1},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": head}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": tail}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": len(text) // 4},
        },
        {"type": "message_stop"},
    )


@contextlib.contextmanager
def mock_anthropic():
    """Intercept the Anthropic API with respx: one tool_use turn, one streamed answer.

    The example still drives the real patched anthropic SDK - only the HTTP
    transport is mocked, so the recorded events are exactly what a live run
    would produce.
    """
    try:
        import httpx
        import respx
    except ImportError:
        sys.exit("--mock needs the dev dependencies: uv sync  (or: pip install respx httpx)")

    def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        if sent.get("stream"):  # call 2: the app streams the final answer
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_answer_stream(sent["model"], MOCK_ANSWER),
            )
        return httpx.Response(200, json=_tool_use_response(sent))

    with respx.mock(base_url="https://api.anthropic.com") as router:
        router.post("/v1/messages").mock(side_effect=respond)
        yield


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--mock", action="store_true", help="run offline, respx-mocked")
    parser.add_argument("--model", default="claude-opus-4-8")
    args = parser.parse_args(argv)

    if not args.mock and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "No ANTHROPIC_API_KEY set. Either export one to run against the real API,\n"
            "or re-run keyless:  uv run python examples/anthropic_app.py --mock",
            file=sys.stderr,
        )
        return 2

    ctxlineage.init()  # records to $CTXLINEAGE_DIR or ./.ctxlineage from here on

    import anthropic

    print(f"\nYou: {QUESTION}")
    if args.mock:
        with mock_anthropic():
            # Pin base_url so an exported ANTHROPIC_BASE_URL can't bypass the respx routes.
            client = anthropic.Anthropic(
                api_key="ctxlineage-mock", base_url="https://api.anthropic.com"
            )
            print(f"Bot: {run_question(client, args.model)}")
    else:
        print(f"Bot: {run_question(anthropic.Anthropic(), args.model)}")

    out_dir = os.environ.get("CTXLINEAGE_DIR", ".ctxlineage")
    print(f"\nRecorded events to {os.path.join(out_dir, 'events.jsonl')}")
    print("Next: ctxlineage report --open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
