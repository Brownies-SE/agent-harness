"""Public, offline documentation shells; the schema remains bearer protected."""

from functools import lru_cache
from importlib.resources import files

from fastapi import FastAPI
from starlette.responses import HTMLResponse, Response

ASSETS = {
    "docs.js": "application/javascript",
    "docs.css": "text/css",
    "swagger-ui-bundle.js": "application/javascript",
    "swagger-ui.css": "text/css",
    "redoc.standalone.js": "application/javascript",
}
PUBLIC_DOCUMENTATION_PATHS = frozenset({"/docs", "/redoc"}) | frozenset(
    "/docs-assets/" + name for name in ASSETS
)
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; font-src 'self' data:; "
    "worker-src blob:; object-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)


@lru_cache(maxsize=len(ASSETS))
def _asset(name: str) -> bytes:
    # Callers only supply one of the fixed names registered below, never a URL path.
    return files("agent_harness").joinpath("static", name).read_bytes()


def _shell(view: str) -> HTMLResponse:
    library = "swagger-ui-bundle.js" if view == "swagger" else "redoc.standalone.js"
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Local Agent Harness API</title>"
        '<link rel="stylesheet" href="/docs-assets/docs.css">'
        '<link rel="stylesheet" href="/docs-assets/swagger-ui.css">'
        f'<script defer src="/docs-assets/{library}"></script>'
        '<script defer src="/docs-assets/docs.js"></script></head>'
        f'<body data-view="{view}"><header><h1>Local Agent Harness API</h1>'
        "<p>Enter your local API bearer token to load the protected schema. "
        "The token stays in this tab until you reload or clear it.</p>"
        '<nav><a href="/docs">Swagger / Try it out</a> · '
        '<a href="/redoc">ReDoc reference</a></nav>'
        '<form id="token-form"><label for="token">Local bearer token</label> '
        '<input id="token" type="password" autocomplete="off" required> '
        '<button type="submit">Load API</button> '
        '<button type="button" id="clear-token">Clear token</button></form>'
        '<p id="status" role="status"></p></header><main id="reference"></main></body></html>',
        headers={"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer"},
    )


def install_documentation(app: FastAPI) -> None:
    """Install only fixed GET shells/assets, all excluded from the API schema."""

    @app.get("/docs", include_in_schema=False)
    async def swagger():
        return _shell("swagger")

    @app.get("/redoc", include_in_schema=False)
    async def redoc():
        return _shell("redoc")

    def asset_handler(name, mime):
        async def asset():
            return Response(_asset(name), media_type=mime)

        return asset

    for name, mime in ASSETS.items():
        app.add_api_route(
            "/docs-assets/" + name,
            asset_handler(name, mime),
            methods=["GET"],
            include_in_schema=False,
        )
