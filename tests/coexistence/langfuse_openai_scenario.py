"""Subprocess scenario for the ctxlineage x langfuse.openai coexistence matrix (#26).

Runs one (wrap order, call mode) cell: both libraries patch process-global SDK
state at import/init time, so each cell needs a fresh interpreter. Langfuse
stays fully active — its OTLP export is pointed at a local sink server so the
test can assert langfuse *also* captured the call, without any real network.

argv: <order: ctxlineage-first|langfuse-first> <mode: plain|stream> <events_dir>
stdout: one JSON line consumed by tests/test_coexistence_langfuse.py.
"""

import gzip
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ORDER, MODE, EVENTS_DIR = sys.argv[1], sys.argv[2], sys.argv[3]

received = []  # (path, headers, body) of every request langfuse sends


class _SinkHandler(BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get("content-length") or 0)
        received.append((self.path, dict(self.headers), self.rfile.read(length)))
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST = _handle

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), _SinkHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()

# Must be set before anything langfuse is imported; dummy keys keep the
# wrapper fully active, the host points its exporter at the sink above.
os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-test"
os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-test"
os.environ["LANGFUSE_HOST"] = f"http://127.0.0.1:{server.server_port}"
os.environ["NO_PROXY"] = "127.0.0.1"

import httpx  # noqa: E402
import respx  # noqa: E402


def init_ctxlineage():
    import ctxlineage

    ctxlineage.init(EVENTS_DIR)


def init_langfuse():
    global OpenAI
    from langfuse.openai import OpenAI


if ORDER == "ctxlineage-first":
    init_ctxlineage()  # ctxlineage wraps first -> langfuse ends up outermost
    init_langfuse()
elif ORDER == "langfuse-first":
    init_langfuse()  # langfuse wraps first -> ctxlineage ends up outermost
    init_ctxlineage()
else:
    raise SystemExit(f"unknown order: {ORDER}")

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "Say hello"}]
RESPONSE = {
    "id": "chatcmpl-test1",
    "object": "chat.completion",
    "created": 1765500000,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello there!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
}


def _chunk(delta=None, finish_reason=None, usage=None):
    return {
        "id": "chatcmpl-test1",
        "object": "chat.completion.chunk",
        "created": 1765500000,
        "model": "gpt-4o-mini",
        "choices": []
        if delta is None and finish_reason is None
        else [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}],
        "usage": usage,
    }


STREAM_BODY = (
    b"".join(
        b"data: " + json.dumps(c).encode() + b"\n\n"
        for c in [
            _chunk(delta={"role": "assistant", "content": "Hello"}),
            _chunk(delta={"content": " world"}),
            _chunk(finish_reason="stop"),
            _chunk(usage={"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11}),
        ]
    )
    + b"data: [DONE]\n\n"
)

with respx.mock(assert_all_called=False) as router:
    # langfuse's own httpx traffic (if any) must reach the local sink
    router.route(host="127.0.0.1").pass_through()
    if MODE == "plain":
        router.post(CHAT_URL).mock(return_value=httpx.Response(200, json=RESPONSE))
    else:
        router.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=STREAM_BODY
            )
        )

    client = OpenAI(api_key="test-key")
    if MODE == "plain":
        resp = client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
        response_text = resp.choices[0].message.content
    else:
        chunks = list(
            client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES, stream=True)
        )
        response_text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)

    from langfuse import get_client

    get_client().flush()

with open(os.path.join(EVENTS_DIR, "events.jsonl")) as fh:
    ctx_events = [json.loads(line) for line in fh]

# Decode whatever langfuse exported to the sink: span count + a flat text blob
# of all span attributes (enough to prove langfuse captured the call content).
span_count = 0
attr_blob = ""
decode_errors = []
for path, headers, body in received:
    if "trace" not in path:
        continue
    try:
        if headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        for resource_spans in request.resource_spans:
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    span_count += 1
                    attr_blob += str(span.attributes)
    except Exception as exc:  # decoding problems must fail the parent assert loudly
        decode_errors.append(repr(exc))

print(
    json.dumps(
        {
            "response_text": response_text,
            "ctx_events": ctx_events,
            "langfuse_span_count": span_count,
            "langfuse_saw_input": "Say hello" in attr_blob,
            "sink_paths": sorted({p for p, _, _ in received}),
            "decode_errors": decode_errors,
        }
    )
)
