"""Resolve current-run host chart references into ordered, provisional display blocks."""

import re

from ..core.errors import SecurityError
from ..models.chat import TextBlock
from .visual_wording import VisualWording

PREFIX = "[[chart:"


class ResponseBlocks:
    def __init__(self, charts, *, on_text=None, on_chart=None):
        self.charts = charts
        self.on_text = on_text
        self.on_chart = on_chart
        self.raw = ""
        self.buffer = ""
        self.blocks = []
        self.used = set()
        self.wording = VisualWording(self._append_text)

    def _text(self, text):
        if self.charts:
            self.wording.feed(text)
        else:
            self._append_text(text)

    def _append_text(self, text):
        if not text:
            return
        if self.blocks and isinstance(self.blocks[-1], TextBlock):
            self.blocks[-1] = TextBlock(text=self.blocks[-1].text + text)
        else:
            self.blocks.append(TextBlock(text=text))
        if self.on_text is not None:
            self.on_text(text)

    def _chart(self, chart_id):
        self.wording.finish()
        if chart_id not in self.charts or chart_id in self.used:
            raise SecurityError("invalid_model_response")
        self.used.add(chart_id)
        block = self.charts[chart_id]
        self.blocks.append(block)
        if self.on_chart is not None:
            self.on_chart(block)

    def feed(self, text):
        self.raw += text
        if len(self.raw.encode("utf-8")) > 256 * 1024:
            raise SecurityError("response_too_large")
        self.buffer += text
        while self.buffer:
            start = self.buffer.find(PREFIX)
            if start < 0:
                keep = 0
                for size in range(1, min(len(PREFIX), len(self.buffer) + 1)):
                    if self.buffer.endswith(PREFIX[:size]):
                        keep = size
                if keep:
                    self._text(self.buffer[:-keep])
                    self.buffer = self.buffer[-keep:]
                else:
                    self._text(self.buffer)
                    self.buffer = ""
                return
            self._text(self.buffer[:start])
            self.buffer = self.buffer[start:]
            end = self.buffer.find("]]", len(PREFIX))
            if end < 0:
                candidate = self.buffer[len(PREFIX) :]
                # At most 32 lowercase hex characters and one pending closing bracket.
                if not re.fullmatch(r"[a-f0-9]{0,32}\]?", candidate):
                    raise SecurityError("invalid_model_response")
                return
            chart_id = self.buffer[len(PREFIX) : end]
            if not re.fullmatch(r"[a-f0-9]{32}", chart_id):
                raise SecurityError("invalid_model_response")
            self._chart(chart_id)
            self.buffer = self.buffer[end + 2 :]

    def finish(self):
        if PREFIX in self.buffer:
            raise SecurityError("invalid_model_response")
        self._text(self.buffer)
        self.buffer = ""
        self.wording.finish()
        # Legacy answers without references still retain approved tool output.
        for chart_id in self.charts:
            if chart_id not in self.used:
                self._chart(chart_id)
        return self.blocks or [TextBlock(text="")]
