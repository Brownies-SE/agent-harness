from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPBearer

from ..models.status import SystemStatus
from ..services.database import ChatError

router = APIRouter(prefix="/api", tags=["Models"], dependencies=[Depends(HTTPBearer())])


@router.get("/models", response_model=SystemStatus)
def models(request: Request):
    service = request.app.state.status_service
    if service is None:
        raise ChatError("model_status_unavailable")
    return service.get()
