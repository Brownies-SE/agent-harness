"""Bounded host-owned stream lifecycle; only typed answer events leave this boundary."""

import asyncio
import queue
import threading
import time

from ..core.errors import SecurityError, safe_reason
from ..core.runtime import RunStopped, check_run
from ..models.chat import StreamChart, StreamComplete, StreamDelta, StreamError
from .database import ChatError


class ChatStream:
    def __init__(self, service, conversation_id, body):
        self.service = service
        self.conversation_id = conversation_id
        self.body = body
        self.started = False
        self.closed = False
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.events = queue.Queue(maxsize=32)
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _put(self, event):
        deadline = time.monotonic() + 5
        while not self.cancel.is_set():
            check_run()
            try:
                self.events.put(event, timeout=0.05)
                return
            except queue.Full:
                if time.monotonic() >= deadline:
                    raise SecurityError("network_timeout") from None
        raise RunStopped("run_cancelled")

    def _run(self):
        try:
            exchange = self.service.send(
                self.conversation_id,
                self.body,
                on_text=lambda text: self._put(StreamDelta(text=text)),
                on_chart=lambda chart: self._put(StreamChart(chart=chart)),
                cancel=self.cancel,
            )
            self._put(StreamComplete(exchange=exchange))
        except Exception as exc:
            detail = safe_reason(str(exc)) if isinstance(exc, SecurityError) else "operation_failed"
            try:
                self._put(StreamError(detail=detail))
            except SecurityError:
                pass
        finally:
            self.finished.set()
            self.service.stream_slots.release()

    def close(self):
        self.cancel.set()
        if not self.started and not self.closed:
            self.closed = True
            self.service.stream_slots.release()

    async def iterate(self):
        if self.closed:
            return
        self.started = True
        try:
            self.thread.start()
        except RuntimeError:
            self.service.stream_slots.release()
            self.closed = True
            raise
        heartbeat = time.monotonic()
        try:
            while True:
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    if self.finished.is_set():
                        return
                    if time.monotonic() - heartbeat >= 10:
                        yield b": keep-alive\n\n"
                        heartbeat = time.monotonic()
                    await asyncio.sleep(0.02)
                    continue
                yield ("data: " + event.model_dump_json() + "\n\n").encode()
                if isinstance(event, (StreamComplete, StreamError)):
                    return
        finally:
            # RunControl interrupts the pinned inference socket and rolls back unfinished turns.
            self.close()


def prepare_stream(service, conversation_id, body):
    service.get(conversation_id)
    if not service.stream_slots.acquire(blocking=False):
        raise ChatError("conversation_busy", 409)
    return ChatStream(service, conversation_id, body)
