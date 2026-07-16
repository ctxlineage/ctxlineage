#!/usr/bin/env python3
"""Generate a realistic, keyless demo events.jsonl for report development.

Simulates a small RAG assistant ("docs-bot") over two sessions without calling
any real API: events are written through ctxlineage's own EventWriter in the
exact shape the openai instrumentation produces.

Usage: python examples/generate_demo_events.py [output_dir]   (default: .ctxlineage)
"""

from __future__ import annotations

import sys

from ctxlineage._events import EventWriter, make_event

SYSTEM_PROMPT = (
    "You are docs-bot, the assistant for the ctxlineage documentation. "
    "Answer strictly from the provided context chunks. Cite the chunk id like [chunk-3]. "
    "If the context does not contain the answer, say so."
)

CHUNKS = [
    "[chunk-1] ctxlineage.init() auto-instruments the openai and anthropic SDKs and records "
    "every LLM call to .ctxlineage/events.jsonl as append-only JSON lines.",
    "[chunk-2] The report command (ctxlineage report --open) renders a single self-contained "
    "HTML file with no server and no CDN dependencies.",
    "[chunk-3] Events follow a versioned JSON Schema; the envelope is strict while payload "
    "is open so unknown SDK fields pass through.",
    "[chunk-4] Streaming calls are recorded once, when the stream is exhausted or closed, "
    "with the assembled output text and final usage.",
    "[chunk-5] The span/tag API lets you label context elements (system, rag_chunks, history) "
    "so the report can show real segment boundaries instead of role heuristics.",
]

_TS = 1781222400  # 2026-06-12T00:00:00Z, advanced manually per call


def _usage(prompt: int, completion: int) -> dict:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _chat_response(call_no: int, text: str, prompt_tokens: int, completion_tokens: int) -> dict:
    return {
        "id": f"chatcmpl-demo{call_no}",
        "object": "chat.completion",
        "created": _TS + call_no * 30,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage(prompt_tokens, completion_tokens),
    }


class _Demo:
    def __init__(self, writer: EventWriter, session_id: str):
        self.writer = writer
        self.session_id = session_id
        self.call_no = 0

    def llm_call(self, payload: dict) -> None:
        self.call_no += 1
        event = make_event(
            "llm_call", self.session_id, payload, call_id=f"call-{self.session_id}-{self.call_no}"
        )
        # deterministic timestamps so the report is stable across runs
        event["timestamp"] = f"2026-06-12T09:{self.call_no:02d}:00+00:00"
        self.writer.write(event)

    def chat(self, messages, answer, prompt_tokens, completion_tokens, stack=None, **kwargs):
        request = {"model": "gpt-4o-mini", "messages": messages, **kwargs}
        self.llm_call(
            {
                "provider": "openai",
                "api": "chat.completions",
                "request": request,
                "stream": False,
                "duration_ms": 420.0 + self.call_no * 130,
                "call_stack": stack or ["rag_app.py:answer_query:57", "rag_app.py:main:21"],
                "response": _chat_response(self.call_no, answer, prompt_tokens, completion_tokens),
                "usage": _usage(prompt_tokens, completion_tokens),
            }
        )


def _rag_turn(demo: _Demo, history: list, question: str, chunk_ids: list[int], answer: str):
    """One user turn = a query-rewrite call + a RAG answer call (typical 2-call pattern)."""
    demo.chat(
        messages=[
            {
                "role": "system",
                "content": "Rewrite the user question into a standalone search query. "
                "Return only the query.",
            },
            *history[-4:],
            {"role": "user", "content": question},
        ],
        answer=f"search: {question.lower().rstrip('?')}",
        prompt_tokens=90 + 18 * len(history),
        completion_tokens=12,
        stack=["rag_app.py:rewrite_query:43", "rag_app.py:handle_turn:21"],
        temperature=0.0,
    )
    context = "\n\n".join(CHUNKS[i] for i in chunk_ids)
    demo.chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
        answer=answer,
        prompt_tokens=240 + 95 * len(chunk_ids) + 30 * len(history),
        completion_tokens=15 + len(answer) // 4,
        stack=["rag_app.py:answer_query:57", "rag_app.py:handle_turn:22"],
    )
    history += [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]


def generate(directory) -> None:
    writer = EventWriter(directory)

    # Session 1: three-turn RAG conversation, growing history
    demo = _Demo(writer, "demo-session-rag")
    history: list = []
    _rag_turn(
        demo,
        history,
        "How do I start recording LLM calls?",
        [0, 2],
        "Call ctxlineage.init() once at startup; every openai/anthropic call is then recorded "
        "to .ctxlineage/events.jsonl automatically [chunk-1].",
    )
    _rag_turn(
        demo,
        history,
        "And how do I actually see what was recorded?",
        [1, 0, 3],
        "Run ctxlineage report --open: it builds a single self-contained HTML report, "
        "no server needed [chunk-2].",
    )
    _rag_turn(
        demo,
        history,
        "Can I label my RAG chunks so they show up separately?",
        [4, 2, 1, 0],
        "Yes - wrap the call in ctxlineage.span() and tag the chunks; the report then shows "
        "real segment boundaries instead of role heuristics [chunk-5].",
    )

    # Session 2: a streamed call and a failed call
    demo2 = _Demo(writer, "demo-session-stream")
    demo2.llm_call(
        {
            "provider": "openai",
            "api": "chat.completions",
            "request": {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "You are a terse changelog writer."},
                    {"role": "user", "content": "Summarize this week's commits in one line."},
                ],
                "stream": True,
            },
            "stream": True,
            "duration_ms": 1310.0,
            "call_stack": ["changelog.py:summarize:33"],
            "response": {
                "object": "chat.completion.assembled",
                "id": "chatcmpl-demostream",
                "model": "gpt-4o-mini",
                "content": {"0": "Capture core landed: openai instrumentation, JSONL writer, CI."},
                "finish_reasons": {"0": "stop"},
                "usage": _usage(64, 14),
                "chunk_count": 9,
            },
            "usage": _usage(64, 14),
        }
    )
    demo2.llm_call(
        {
            "provider": "openai",
            "api": "chat.completions",
            "request": {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "This one will hit a rate limit."}],
            },
            "stream": False,
            "duration_ms": 902.0,
            "call_stack": ["changelog.py:summarize:41"],
            "error": {"type": "RateLimitError", "message": "Rate limit reached for gpt-4o-mini"},
        }
    )

    # Session 3: agent tool-call loop (search twice, then answer) — exercises
    # role=tool segments and the repeated input→output chain the report shows.
    demo3 = _Demo(writer, "demo-session-agent")
    agent_system = (
        "You are repo-bot. Use the search_docs tool to gather evidence before answering. "
        "Loop until you have enough context."
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_docs",
                "description": "Search the project documentation.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]
    question = {"role": "user", "content": "Does ctxlineage need a database server?"}
    call1_out = 'Calling search_docs("database server requirement")'
    demo3.chat(
        messages=[{"role": "system", "content": agent_system}, question],
        answer=call1_out,
        prompt_tokens=130,
        completion_tokens=14,
        stack=["agent.py:run_step:88", "agent.py:run:31"],
        tools=tools,
    )
    tool_result_1 = {
        "role": "tool",
        "name": "search_docs",
        "tool_call_id": "tc-1",
        "content": CHUNKS[0] + "\n" + CHUNKS[2],
    }
    call2_out = 'Calling search_docs("report generation server")'
    demo3.chat(
        messages=[
            {"role": "system", "content": agent_system},
            question,
            {"role": "assistant", "content": call1_out},
            tool_result_1,
        ],
        answer=call2_out,
        prompt_tokens=310,
        completion_tokens=13,
        stack=["agent.py:run_step:88", "agent.py:run:31"],
        tools=tools,
    )
    tool_result_2 = {
        "role": "tool",
        "name": "search_docs",
        "tool_call_id": "tc-2",
        "content": CHUNKS[1],
    }
    demo3.chat(
        messages=[
            {"role": "system", "content": agent_system},
            question,
            {"role": "assistant", "content": call1_out},
            tool_result_1,
            {"role": "assistant", "content": call2_out},
            tool_result_2,
        ],
        answer="No. Events are appended to a local JSONL file and the report is a single "
        "self-contained HTML - no database or server is required [chunk-1][chunk-2].",
        prompt_tokens=455,
        completion_tokens=38,
        stack=["agent.py:run_step:88", "agent.py:run:31"],
        tools=tools,
    )

    print(f"wrote {writer.path}")


if __name__ == "__main__":
    generate(sys.argv[1] if len(sys.argv) > 1 else ".ctxlineage")
