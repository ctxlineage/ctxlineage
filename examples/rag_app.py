#!/usr/bin/env python3
"""Minimal RAG app instrumented with ctxlineage — the span()/tag() exemplar.

A three-turn Q&A bot over the (fictional) "aurora" CLI docs. Every turn opens a
span and tags the three context elements it assembles — system prompt, retrieved
chunks (with source= and transform= provenance), conversation history — so the
report shows real, named segment boundaries and a lineage graph instead of role
heuristics.

Usage:
    OPENAI_API_KEY=sk-... uv run python examples/rag_app.py     # real API (gpt-4o-mini)
    uv run python examples/rag_app.py --mock                    # keyless: respx-mocked, offline

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
    "You are aurora-helper, the assistant for the aurora CLI documentation. "
    "Answer strictly from the provided context chunks and cite them like [doc-3]. "
    "If the context does not contain the answer, say so."
)

DOCS = [
    "[doc-1] aurora is installed with `pipx install aurora-cli`. Python 3.10+ is required.",
    "[doc-2] On first run aurora creates ~/.aurora/config.toml. Set AURORA_HOME to relocate it.",
    "[doc-3] `aurora new <name>` scaffolds a snippet; snippets are plain files under AURORA_HOME.",
    "[doc-4] `aurora sync --remote <git-url>` pushes and pulls snippets through any git remote.",
    "[doc-5] `aurora doctor` checks the installation and prints the resolved config path.",
]

QUESTIONS = [
    "How do I install aurora?",
    "Where does aurora keep its config, and can I move it somewhere else?",
    "How do I sync my snippets to another machine?",
]

# Deterministic answers used in --mock mode, one per question. Written so each
# answer flows verbatim into the next turn's history — the report then infers
# output→input lineage edges exactly as it would for a real conversation.
MOCK_ANSWERS = [
    "Install it with `pipx install aurora-cli`; Python 3.10+ is required [doc-1].",
    "The config lives at ~/.aurora/config.toml, and you can relocate it by setting "
    "AURORA_HOME [doc-2].",
    "Run `aurora sync --remote <git-url>` - snippets are plain files, so any git remote "
    "works [doc-4].",
]


def retrieve(query: str, k: int = 2) -> list[str]:
    """Toy keyword-overlap retrieval standing in for a real vector store."""
    words = set(re.findall(r"\w+", query.lower()))
    scored = sorted(DOCS, key=lambda d: -len(words & set(re.findall(r"\w+", d.lower()))))
    return scored[:k]


def answer_turn(client, model: str, history: list[dict], question: str) -> str:
    """One user turn. The span/tag calls are the whole point of this example:

    tag exactly the object you interpolate into `messages` (the chunk list, the
    sliced history, the system string) — that is what lets the report match
    segments and draw provenance (source=) and derivation (transform=) edges.
    """
    with ctxlineage.span("answer_query") as span:
        span.tag("system", SYSTEM_PROMPT, source="rag_app.py:SYSTEM_PROMPT")

        chunks = retrieve(question, k=2)
        span.tag("rag_chunks", chunks, source="keyword_index:aurora_docs", transform="top_k(2)")

        recent = history[-6:]
        if recent:
            span.tag("history", recent, transform="last_6_messages")

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *recent,
                {
                    "role": "user",
                    "content": "Context:\n" + "\n\n".join(chunks) + f"\n\nQuestion: {question}",
                },
            ],
        )
    answer = response.choices[0].message.content or ""
    history += [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]
    return answer


@contextlib.contextmanager
def mock_openai(answers: list[str]):
    """Intercept the OpenAI API with respx and serve canned answers, in order."""
    try:
        import httpx
        import respx
    except ImportError:
        sys.exit("--mock needs the dev dependencies: uv sync  (or: pip install respx httpx)")

    answer_iter = iter(answers)

    def respond(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        prompt_tokens = sum(len(str(m.get("content"))) // 4 for m in sent["messages"])
        text = next(answer_iter)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": 1784160000,
                "model": sent["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": len(text) // 4,
                    "total_tokens": prompt_tokens + len(text) // 4,
                },
            },
        )

    with respx.mock(base_url="https://api.openai.com") as router:
        router.post("/v1/chat/completions").mock(side_effect=respond)
        yield


def run_conversation(client, model: str) -> int:
    history: list[dict] = []
    for question in QUESTIONS:
        print(f"\nYou: {question}")
        print(f"Bot: {answer_turn(client, model, history, question)}")
    return len(QUESTIONS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--mock", action="store_true", help="run offline against canned answers")
    parser.add_argument("--model", default="gpt-4o-mini")
    args = parser.parse_args(argv)

    if not args.mock and not os.environ.get("OPENAI_API_KEY"):
        print(
            "No OPENAI_API_KEY set. Either export one to run against the real API,\n"
            "or re-run keyless:  uv run python examples/rag_app.py --mock",
            file=sys.stderr,
        )
        return 2

    ctxlineage.init()  # records to $CTXLINEAGE_DIR or ./.ctxlineage from here on

    import openai

    if args.mock:
        with mock_openai(MOCK_ANSWERS):
            # Pin base_url so an exported OPENAI_BASE_URL can't bypass the respx routes.
            client = openai.OpenAI(api_key="ctxlineage-mock", base_url="https://api.openai.com/v1")
            calls = run_conversation(client, args.model)
    else:
        calls = run_conversation(openai.OpenAI(), args.model)

    out_dir = os.environ.get("CTXLINEAGE_DIR", ".ctxlineage")
    print(f"\nRecorded {calls} LLM call(s) to {os.path.join(out_dir, 'events.jsonl')}")
    print("Next: ctxlineage report --open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
