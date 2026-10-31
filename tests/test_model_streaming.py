import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_harness.core.errors import SecurityError
from agent_harness.core.policy import Policy
from agent_harness.model import LocalModel


def frame(content=None, finish=None):
    return (
        "data: "
        + json.dumps(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": content} if content else {},
                        "finish_reason": finish,
                    }
                ]
            }
        )
        + "\n\n"
    ).encode()


def test_provider_stream_arrives_before_completion_and_decodes_unicode():
    received = threading.Event()
    payloads = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payloads.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(frame('{"final":"Hello '))
            self.wfile.flush()
            assert received.wait(3), "client buffered provider output"
            # Split escapes, an emoji surrogate pair, and UTF-8 across HTTP chunks.
            data = frame('\\ud83d\\ude00\\nworld"}') + frame(finish="stop") + b"data: [DONE]\n\n"
            for byte in data:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    pieces = []
    try:
        model = LocalModel(
            Policy(), endpoint=f"http://127.0.0.1:{server.server_port}", model="test"
        )

        def delta(text):
            pieces.append(text)
            received.set()

        decision = model.decide_stream([{"role": "system", "content": "test"}], [], delta)
        assert decision == {"final": "Hello 😀\nworld"}
        assert "".join(pieces) == decision["final"]
        assert payloads[0]["stream"] is True
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize(
    "content",
    [
        '{"tool":"write_file","arguments":{"content":"secret"}}',
        '{"final":"ok","final":"malicious"}',
        '{"final":"bad\\q"}',
    ],
)
def test_streamed_provider_tools_are_not_exposed_and_invalid_json_fails_closed(
    monkeypatch, content
):
    def transport(target, **kw):
        data = frame(content) + frame(finish="stop") + b"data: [DONE]\n\n"
        kw["response_headers"]["content-type"] = "text/event-stream"
        kw["on_chunk"](data)
        return b""

    monkeypatch.setattr("agent_harness.model.transport", transport)
    model = LocalModel(Policy(), endpoint="http://127.0.0.1:1", model="test")
    pieces = []
    if '"tool"' in content:
        assert model.decide_stream([], [], pieces.append)["tool"] == "write_file"
        assert pieces == []
    else:
        with pytest.raises(SecurityError, match="invalid_model_response"):
            model.decide_stream([], [], pieces.append)


def test_truncated_provider_stream_cannot_become_a_final_answer(monkeypatch):
    def transport(target, **kw):
        kw["response_headers"]["content-type"] = "text/event-stream"
        kw["on_chunk"](frame('{"final":"looks valid"}'))
        return b""

    monkeypatch.setattr("agent_harness.model.transport", transport)
    with pytest.raises(SecurityError, match="invalid_model_response"):
        LocalModel(Policy(), endpoint="http://127.0.0.1:1", model="test").decide_stream(
            [], [], lambda text: None
        )


def test_cancelling_live_provider_stream_interrupts_a_stalled_socket():
    from agent_harness.core.runtime import RunControl, RunLimits, RunStopped

    cancel = threading.Event()
    stopped = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(frame('{"final":"partial'))
            self.wfile.flush()
            stopped.wait(3)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = LocalModel(
            Policy(), endpoint=f"http://127.0.0.1:{server.server_port}", model="test"
        )
        control = RunControl(RunLimits(timeout_seconds=2), cancel=cancel)
        with pytest.raises(RunStopped, match="run_cancelled"), control.activate():
            model.decide_stream([], [], lambda text: cancel.set())
        assert control.status == "cancelled"
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join()
