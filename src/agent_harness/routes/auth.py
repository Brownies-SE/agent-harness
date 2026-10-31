import secrets

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPBearer

from ..models.auth import AuthSession, AuthStatus, LoginCredentials, SetupCredentials
from ..services.database import ChatError

router = APIRouter(
    prefix="/api/auth", tags=["Authentication"], dependencies=[Depends(HTTPBearer())]
)


def host_auth(request: Request):
    authorization = request.headers.get("authorization", "").encode("utf-8")
    if not secrets.compare_digest(authorization, request.app.state.host_authorization):
        raise ChatError("authentication_required", 401)


def auth_service(request: Request):
    service = request.app.state.auth_service
    if service is None:
        raise ChatError("authentication_unavailable")
    return service


@router.get("/status", response_model=AuthStatus, dependencies=[Depends(host_auth)])
def status(request: Request):
    return auth_service(request).status()


@router.post("/setup", response_model=AuthSession, dependencies=[Depends(host_auth)])
def setup(body: SetupCredentials, request: Request):
    return auth_service(request).setup(body)


@router.post("/login", response_model=AuthSession, dependencies=[Depends(host_auth)])
def login(body: LoginCredentials, request: Request):
    return auth_service(request).login(body)


@router.post("/logout", status_code=204)
def logout(request: Request):
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    service = auth_service(request)
    if not service.valid_session(token):
        raise ChatError("authentication_required", 401)
    service.logout(token)
    return Response(status_code=204)
