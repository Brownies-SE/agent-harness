from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPBearer

from ..models.files import FileUpload, StoredFile
from ..services.files import FileService
from .common import require_service

router = APIRouter(prefix="/api", tags=["Files"], dependencies=[Depends(HTTPBearer())])


def file_service(request: Request) -> FileService:
    return require_service(request, "file_service")


Service = Annotated[FileService, Depends(file_service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]


@router.post("/files", response_model=StoredFile, status_code=201)
def upload_file(body: FileUpload, service: Service):
    return service.upload(body)


@router.get("/files", response_model=list[StoredFile])
def list_files(service: Service, limit: Limit = 50, offset: Offset = 0):
    return service.list(limit=limit, offset=offset)


@router.get("/files/{file_id}", response_model=StoredFile)
def get_file(file_id: UUID, service: Service):
    return service.get(file_id)


@router.get(
    "/files/{file_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def download_file(file_id: UUID, service: Service):
    metadata, content = service.download(file_id)
    return Response(
        content,
        media_type=metadata.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(metadata.filename, safe=""),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: UUID, service: Service):
    service.delete(file_id)
    return Response(status_code=204)
