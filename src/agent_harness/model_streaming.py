"""Project only a provisional final-answer string from bounded, untrusted provider SSE."""

import codecs
import json
import re

from .core.errors import SecurityError
from .model import strict_json


class ModelStream:
    def __init__(self, on_text, control):
        self.on_text = on_text
        self.control = control
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.buffer = ""
        self.content = ""
        self.published = ""
        self.stopped = False
        self.done = False
        self.size = 0

    def feed(self, chunk):
        self.size += len(chunk)
        if self.size > 1024 * 1024:
            raise SecurityError("response_too_large")
        try:
            self.buffer = (self.buffer + self.decoder.decode(chunk)).replace("\r\n", "\n")
            while "\n\n" in self.buffer:
                frame, self.buffer = self.buffer.split("\n\n", 1)
                if len(frame) > 131072:
                    raise ValueError("large event")
                data = "\n".join(
                    line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:")
                )
                if data:
                    self._event(data)
            if len(self.buffer) > 131072:
                raise ValueError("large event")
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError) as exc:
            raise SecurityError("invalid_model_response") from exc

    def _event(self, data):
        if self.done:
            raise ValueError("data after done")
        if data == "[DONE]":
            if not self.stopped:
                raise ValueError("missing finish")
            self.done = True
            return
        event = strict_json(data)
        if not isinstance(event, dict) or "error" in event:
            raise ValueError("invalid event")
        choices = event["choices"]
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError("invalid choices")
        if not choices:
            if self.control is not None:
                self.control.usage(event.get("usage", {}))
            return
        choice = choices[0]
        delta = choice["delta"]
        if not isinstance(delta, dict) or delta.get("tool_calls") or delta.get("function_call"):
            raise ValueError("unexpected tool protocol")
        text = delta.get("content")
        if text is not None:
            if not isinstance(text, str) or self.stopped:
                raise ValueError("invalid delta")
            self.content += text
            if len(self.content.encode("utf-8")) > 256 * 1024:
                raise ValueError("large answer")
            self._project()
        reason = choice.get("finish_reason")
        if reason is not None:
            if reason != "stop" or self.stopped:
                raise ValueError("incomplete answer")
            self.stopped = True
        if self.control is not None and event.get("usage"):
            self.control.usage(event["usage"])

    def _project(self):
        # Tool requests never match this prefix. This display text grants no authority;
        # the complete decision still passes strict JSON and Pydantic before execution/save.
        prefix = re.match(r'^\s*\{\s*"final"\s*:\s*(")', self.content)
        if prefix is None:
            return
        raw = self.content[prefix.start(1) :]
        decoder = json.JSONDecoder()
        for trim in range(min(len(raw), 13)):
            candidate = raw if trim == 0 else raw[:-trim]
            try:
                value, _ = decoder.raw_decode(candidate + '"')
                if value and 0xD800 <= ord(value[-1]) <= 0xDBFF:
                    value = value[:-1]
                value.encode("utf-8")
            except (ValueError, UnicodeError):
                continue
            if not value.startswith(self.published):
                raise ValueError("changed prefix")
            suffix = value[len(self.published) :]
            if suffix:
                self.on_text(suffix)
                self.published = value
            return

    def finish(self):
        try:
            self.buffer += self.decoder.decode(b"", final=True)
            if not self.done or self.buffer.strip():
                raise ValueError("incomplete stream")
            decision = strict_json(self.content)
            if isinstance(decision, dict) and set(decision) == {"final"}:
                answer = decision["final"]
                if not isinstance(answer, str) or not answer.startswith(self.published):
                    raise ValueError("invalid answer")
                answer.encode("utf-8")
                if answer != self.published:
                    self.on_text(answer[len(self.published) :])
            return decision
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SecurityError("invalid_model_response") from exc
