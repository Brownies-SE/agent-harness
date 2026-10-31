from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from agent_harness.core.errors import SecurityError, safe_reason
from agent_harness.routes.boundary import LocalHTTPBoundary

from .models.chat import ErrorResponse
from .routes.auth import router as auth_router
from .routes.chat import router
from .routes.documentation import install_documentation
from .routes.execution import router as execution_router
from .routes.files import router as files_router
from .routes.mcp import router as mcp_router
from .routes.memory import router as memory_router
from .routes.status import router as status_router
from .services.database import ChatError


def validate_token(token):
    if (
        not isinstance(token, str)
        or len(token) < 32
        or not token.isascii()
        or not token.isprintable()
    ):
        raise ValueError("configure an ASCII local API token of at least 32 characters")


def create_app(
    service,
    *,
    token,
    model_name,
    memory_service=None,
    file_service=None,
    mcp_service=None,
    execution_service=None,
    status_service=None,
    auth_service=None,
):
    validate_token(token)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if file_service is not None:
            file_service.close()

    app = FastAPI(
        title="Local Agent Harness",
        docs_url=None,
        redoc_url=None,
        telemetry={
            "tracing": False,
            "metrics": False,
            "logs": False,
            "operation_spans": False,
            "auto_configure": False,
        },
        lifespan=lifespan,
        responses={code: {"model": ErrorResponse} for code in (401, 404, 409, 422, 502, 503)},
    )
    app.state.auth_service = auth_service
    app.state.host_authorization = ("Bearer " + token).encode("ascii")
    app.state.chat_service = service
    app.state.model_name = model_name
    app.state.memory_service = memory_service
    app.state.file_service = file_service
    app.state.mcp_service = mcp_service
    app.state.execution_service = execution_service
    app.state.status_service = status_service
    app.add_middleware(LocalHTTPBoundary, token=token, auth_service=auth_service)

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

    app.include_router(auth_router)
    app.include_router(router)
    for resource_router in (
        memory_router,
        files_router,
        mcp_router,
        execution_router,
        status_router,
    ):
        app.include_router(resource_router)
    install_documentation(app)
    return app
