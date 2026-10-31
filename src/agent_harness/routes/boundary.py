"""Bounded, authenticated local HTTP boundary. This is not an OS sandbox."""

import json
import re
import secrets

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from agent_harness.model import strict_json
from agent_harness.routes.documentation import PUBLIC_DOCUMENTATION_PATHS
from agent_harness.services.database import ChatError

MAX_BODY_BYTES = 64 * 1024
MAX_UPLOAD_BODY_BYTES = 8 * 1024 * 1024


class LocalHTTPBoundary:
    def __init__(self, app, *, token, auth_service=None):
        self.app = app
        self.auth_service = auth_service
        self.authorization = ("Bearer " + token).encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
            await send(message)

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, private_send)

        headers = scope.get("headers", [])
        hosts = [v for k, v in headers if k == b"host"]
        if len(hosts) != 1 or not re.fullmatch(
            rb"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?", hosts[0]
        ):
            await reject(400, "invalid_host")
            return
        auth = [v for k, v in headers if k == b"authorization"]
        public_docs = scope["method"] == "GET" and scope["path"] in PUBLIC_DOCUMENTATION_PATHS
        authenticated = len(auth) == 1 and secrets.compare_digest(auth[0], self.authorization)
        if not authenticated and not public_docs and len(auth) == 1 and self.auth_service:
            try:
                bearer = auth[0].decode("ascii")
                if bearer.startswith("Bearer "):
                    authenticated = await run_in_threadpool(
                        self.auth_service.valid_session, bearer[7:]
                    )
            except (UnicodeError, ChatError):
                authenticated = False
        if not public_docs and not authenticated:
            await reject(401, "authentication_required")
            return

        body_limit = (
            MAX_UPLOAD_BODY_BYTES
            if scope["method"] == "POST" and scope["path"] == "/api/files"
            else MAX_BODY_BYTES
        )
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > body_limit:
                await reject(413, "payload_too_large")
                return
            if not message.get("more_body", False):
                break
        if body:
            content_types = [v for k, v in headers if k == b"content-type"]
            if (
                len(content_types) != 1
                or content_types[0].split(b";")[0].strip() != b"application/json"
            ):
                await reject(415, "json_required")
                return
            try:
                json.dumps(strict_json(bytes(body)), allow_nan=False)
            except (ValueError, UnicodeError, RecursionError):
                await reject(422, "invalid_request")
                return

        consumed = False

        async def replay():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, private_send)
