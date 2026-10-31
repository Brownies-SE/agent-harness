from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from .errors import SecurityError, safe_reason
from .http_boundary import LocalHTTPBoundary
from .models.chat import ErrorResponse
from .routes.chat import router
from .services.database import ChatError


def validate_token(token):
    if (
        not isinstance(token, str)
        or len(token) < 32
        or not token.isascii()
        or not token.isprintable()
    ):
        raise ValueError("configure an ASCII local API token of at least 32 characters")


def create_app(service, *, token, model_name):
    validate_token(token)
    app = FastAPI(
        title="Local Agent Harness",
        docs_url=None,
        redoc_url=None,
        responses={code: {"model": ErrorResponse} for code in (401, 404, 409, 422, 502, 503)},
    )
    app.state.chat_service = service
    app.state.model_name = model_name
    app.add_middleware(LocalHTTPBoundary, token=token)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"detail": "invalid_request"}, status_code=422)

    @app.exception_handler(ChatError)
    async def chat_error(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(SecurityError)
    async def security_error(request, exc):
        status = 504 if str(exc) == "run_timeout" else 502
        return JSONResponse({"detail": safe_reason(str(exc))}, status_code=status)

    app.include_router(router)
    return app
