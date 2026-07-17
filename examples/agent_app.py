#!/usr/bin/env python3
"""Multi-turn agent with a tool loop, instrumented with ctxlineage.

A two-turn agent that answers questions about an (imaginary) team's engineering
notebook by calling a `search_notes` tool until it has enough evidence. Each
user turn is one span; every tool result is tagged with source="tool:search_notes"
before being fed back, so the report attributes the tool_result segments and
links the calls of one turn through same-span edges.

Usage:
    OPENAI_API_KEY=sk-... uv run python examples/agent_app.py    # real API (gpt-4o-mini)
    uv run python examples/agent_app.py --mock                   # keyless: respx-mocked, offline

Then: ctxlineage report --open
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys

import ctxlineage

SYSTEM_PROMPT = (
    "You are notebook-bot. Use the search_notes tool to gather evidence from the team's "
    "engineering notebook before answering. Cite notes like [note-2]. If no note covers "
    "the question, say so."
)

NOTES = [
    "[note-1] DB migrations run only through migrate.py in CI - never by hand on prod.",
    "[note-2] Rollback plan: keep the previous release image for 48h, restore with "
    "`deploy --rollback`.",
    "[note-3] The weekly deploy window is Tuesday 10:00 JST; migrations ship first.",
]

QUESTIONS = [
    "What did we decide about running database migrations?",
    "And what was the rollback plan we wrote down?",
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Search the team's engineering notebook. Returns matching notes.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
]

MAX_STEPS = 4  # LLM calls per user turn before the agent gives up


def search_notes(query: str) -> str:
    """The tool itself: toy keyword-overlap search over NOTES."""
    words = set(re.findall(r"\w+", query.lower()))
    hits = [n for n in NOTES if words & set(re.findall(r"\w+", n.lower()))]
    return "\n".join(hits) if hits else "No matching notes."


def run_turn(client, model: str, history: list[dict], question: str) -> str:
    """One user turn = one span. Inside it the agent loops: ask the model, run
    any requested tools (tagging each result before it re-enters the context),
    and stop at the first plain answer."""
    with ctxlineage.span("agent_turn") as span:
        span.tag("system", SYSTEM_PROMPT, source="agent_app.py:SYSTEM_PROMPT")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": question},
        ]
        answer = "(the agent hit its step limit without answering)"
        for _ in range(MAX_STEPS):
            response = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
            message = response.choices[0].message
            if not message.tool_calls:
                answer = message.content or ""
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [tc.model_dump() for tc in message.tool_calls],
                }
            )
            for tool_call in message.tool_calls:
                arguments = json.loads(tool_call.function.arguments)
                result = search_notes(**arguments)
                print(f"  [tool] search_notes({arguments['query']!r})")
                # tag the exact string that re-enters the context, so the
                # report can attribute the tool_result segment to its tool
                span.tag("tool_result", result, source="tool:search_notes")
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
    history += [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]
    return answer


def _mock_response(sent: dict, step: dict) -> dict:
    """One canned chat.completions response: either a tool call or an answer."""
    if "tool_query" in step:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"tc-{step['id']}",
                    "type": "function",
                    "function": {
                        "name": "search_notes",
                        "arguments": json.dumps({"query": step["tool_query"]}),
                    },
                }
            ],
        }
        finish_reason = "tool_calls"
    else:
        message = {"role": "assistant", "content": step["answer"]}
        finish_reason = "stop"
    prompt_tokens = sum(len(str(m.get("content"))) // 4 for m in sent["messages"])
    return {
        "id": f"chatcmpl-mock-{step['id']}",
        "object": "chat.completion",
        "created": 1784160000,
        "model": sent["model"],
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 20,
            "total_tokens": prompt_tokens + 20,
        },
    }


# The scripted agent trajectory served in --mock mode: each turn searches once,
# then answers from the note the tool returned.
MOCK_STEPS = [
    {"id": 1, "tool_query": "database migrations policy"},
    {
        "id": 2,
        "answer": "Migrations run only through migrate.py in CI - never by hand on prod "
        "[note-1]; they ship first in the Tuesday deploy window [note-3].",
    },
    {"id": 3, "tool_query": "rollback plan"},
    {
        "id": 4,
        "answer": "Keep the previous release image for 48 hours and restore with "
        "`deploy --rollback` [note-2].",
    },
]


@contextlib.contextmanager
def mock_openai(steps: list[dict]):
    """Intercept the OpenAI API with respx and replay the scripted trajectory."""
    try:
        import httpx
        import respx
    except ImportError:
        sys.exit("--mock needs the dev dependencies: uv sync  (or: pip install respx httpx)")

    step_iter = iter(steps)

    def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        return httpx.Response(200, json=_mock_response(sent, next(step_iter)))

    with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/chat/completions").mock(side_effect=respond)
        yield


def run_conversation(client, model: str) -> None:
    history: list[dict] = []
    for question in QUESTIONS:
        print(f"\nYou: {question}")
        print(f"Bot: {run_turn(client, model, history, question)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--mock", action="store_true", help="run offline against a scripted agent")
    parser.add_argument("--model", default="gpt-4o-mini")
    args = parser.parse_args(argv)

    if not args.mock and not os.environ.get("OPENAI_API_KEY"):
        print(
            "No OPENAI_API_KEY set. Either export one to run against the real API,\n"
            "or re-run keyless:  uv run python examples/agent_app.py --mock",
            file=sys.stderr,
        )
        return 2

    ctxlineage.init()  # records to $CTXLINEAGE_DIR or ./.ctxlineage from here on

    import openai

    if args.mock:
        with mock_openai(MOCK_STEPS):
            # Pin base_url so an exported OPENAI_BASE_URL can't bypass the respx routes.
            client = openai.OpenAI(api_key="ctxlineage-mock", base_url="https://api.openai.com/v1")
            run_conversation(client, args.model)
    else:
        run_conversation(openai.OpenAI(), args.model)

    out_dir = os.environ.get("CTXLINEAGE_DIR", ".ctxlineage")
    print(f"\nRecorded events to {os.path.join(out_dir, 'events.jsonl')}")
    print("Next: ctxlineage report --open")
    print("      ctxlineage test -c examples/ctxlineage.toml   # gate the same run in CI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
