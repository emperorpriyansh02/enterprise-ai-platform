"""Knowledge-base API (Layer 3): collections, documents, and a search tester.

Reads are open. Writes need X-Admin-Key when ADMIN_API_KEY is set (until RBAC in Layer 5).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from app.api.deps import require_admin, require_rag_service
from app.core.errors import AppError
from app.rag.schemas import (
    CollectionCreate,
    CollectionDetail,
    CollectionList,
    DocumentList,
    DocumentOut,
    DocumentTextCreate,
    SearchRequest,
    SearchResponse,
    UploadError,
    UploadResult,
)
from app.rag.service import RagService

router = APIRouter(prefix="/v1/collections", tags=["knowledge"])

Rag = Annotated[RagService, Depends(require_rag_service)]
Admin = Depends(require_admin)


@router.get("", response_model=CollectionList)
async def list_collections(
    service: Rag, include_archived: Annotated[bool, Query()] = False
) -> CollectionList:
    return await service.list_collections(include_archived=include_archived)


@router.post("", response_model=CollectionDetail, status_code=201, dependencies=[Admin])
async def create_collection(body: CollectionCreate, service: Rag) -> CollectionDetail:
    return await service.create_collection(body)


@router.get("/{name}", response_model=CollectionDetail)
async def get_collection(name: str, service: Rag) -> CollectionDetail:
    return await service.get_collection(name)


@router.delete("/{name}", response_model=CollectionDetail, dependencies=[Admin])
async def archive_collection(name: str, service: Rag) -> CollectionDetail:
    return await service.archive_collection(name)


@router.post("/{name}/reindex", response_model=CollectionDetail, status_code=202, dependencies=[Admin])
async def reindex_collection(name: str, service: Rag) -> CollectionDetail:
    return await service.reindex_collection(name)


@router.get("/{name}/documents", response_model=DocumentList)
async def list_documents(name: str, service: Rag) -> DocumentList:
    return await service.list_documents(name)


@router.post("/{name}/documents", response_model=UploadResult, status_code=202, dependencies=[Admin])
async def upload_documents(
    name: str, files: Annotated[list[UploadFile], File()], service: Rag
) -> UploadResult:
    """Accept one or more files. Each is checked separately; refused files are listed in `errors`."""
    accepted: list[DocumentOut] = []
    errors: list[UploadError] = []
    for upload in files:
        data = await upload.read()
        try:
            accepted.append(await service.add_file(name, upload.filename, upload.content_type, data))
        except AppError as exc:
            if exc.status_code == 404 or len(files) == 1:
                raise  # unknown collection, or a single file: a plain error status is clearer
            errors.append(UploadError(filename=upload.filename or "file", code=exc.code, message=exc.message))
    return UploadResult(data=accepted, errors=errors)


@router.post("/{name}/documents/text", response_model=DocumentOut, status_code=202, dependencies=[Admin])
async def add_text_document(name: str, body: DocumentTextCreate, service: Rag) -> DocumentOut:
    return await service.add_text(name, body)


@router.get("/{name}/documents/{document_id}", response_model=DocumentOut)
async def get_document(name: str, document_id: uuid.UUID, service: Rag) -> DocumentOut:
    return await service.get_document(name, document_id)


@router.delete("/{name}/documents/{document_id}", status_code=204, dependencies=[Admin])
async def delete_document(name: str, document_id: uuid.UUID, service: Rag) -> Response:
    await service.delete_document(name, document_id)
    return Response(status_code=204)


@router.post(
    "/{name}/documents/{document_id}/reindex",
    response_model=DocumentOut,
    status_code=202,
    dependencies=[Admin],
)
async def reindex_document(name: str, document_id: uuid.UUID, service: Rag) -> DocumentOut:
    return await service.reindex_document(name, document_id)


@router.post("/{name}/search", response_model=SearchResponse)
async def search_collection(name: str, body: SearchRequest, service: Rag) -> SearchResponse:
    return await service.search_collection(name, body)
