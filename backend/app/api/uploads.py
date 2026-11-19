"""tus 1.0.0 endpoints (plan 1.1): resumable uploads of recordings.

OPTIONS /api/uploads          capabilities
POST    /api/uploads          create (Upload-Length, Upload-Metadata: filename)
HEAD    /api/uploads/{id}     current offset (to resume)
PATCH   /api/uploads/{id}     append bytes at Upload-Offset
DELETE  /api/uploads/{id}     cancel
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import APIRouter, Request, Response
from starlette.requests import ClientDisconnect

from app import uploads
from app.api.deps import AppSettings
from app.uploads import UploadError

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
log = logging.getLogger(__name__)

TUS_VERSION = "1.0.0"
_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _tus_headers(**extra: str) -> dict[str, str]:
    return {"Tus-Resumable": TUS_VERSION, "Cache-Control": "no-store", **extra}


def _error(e: UploadError) -> Response:
    return Response(
        str(e), status_code=e.status, headers=_tus_headers(), media_type="text/plain; charset=utf-8"
    )


def _check_version(request: Request) -> Response | None:
    if request.headers.get("tus-resumable") != TUS_VERSION:
        return Response(
            "Tus-Resumable 1.0.0 required",
            status_code=412,
            headers={"Tus-Version": TUS_VERSION},
        )
    return None


@router.options("")
def options(settings: AppSettings) -> Response:
    return Response(
        status_code=204,
        headers=_tus_headers(
            **{
                "Tus-Version": TUS_VERSION,
                "Tus-Extension": "creation,termination",
                "Tus-Max-Size": str(settings.max_upload_bytes),
            }
        ),
    )


@router.post("")
def create_upload(request: Request, settings: AppSettings) -> Response:
    if bad := _check_version(request):
        return bad
    try:
        length = int(request.headers.get("upload-length", ""))
    except ValueError:
        return _error(UploadError(400, "Upload-Length가 필요합니다"))
    try:
        if length <= 0:
            raise UploadError(400, "빈 파일은 올릴 수 없습니다")
        if length > settings.max_upload_bytes:
            limit_gb = settings.max_upload_bytes / 1024**3
            raise UploadError(413, f"파일이 너무 큽니다 (최대 {limit_gb:.0f}GB)")
        metadata = uploads.parse_metadata(request.headers.get("upload-metadata"))
        filename = metadata.get("filename", "")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in settings.allowed_extension_set:
            allowed = ", ".join(sorted(settings.allowed_extension_set)).upper()
            raise UploadError(415, f"지원하지 않는 형식입니다: {filename or '(이름 없음)'} (가능: {allowed})")
        info = uploads.create(length, metadata)
    except UploadError as e:
        return _error(e)
    log.info("upload created", extra={"upload_id": info.id, "upload_name": filename, "length": length})
    return Response(status_code=201, headers=_tus_headers(Location=f"/api/uploads/{info.id}"))


@router.head("/{upload_id}")
def upload_offset(upload_id: str) -> Response:
    try:
        info = uploads.load(upload_id)
    except UploadError as e:
        return Response(status_code=e.status, headers=_tus_headers())
    return Response(
        status_code=200,
        headers=_tus_headers(**{"Upload-Offset": str(info.offset), "Upload-Length": str(info.length)}),
    )


@router.patch("/{upload_id}")
async def append(upload_id: str, request: Request) -> Response:
    if bad := _check_version(request):
        return bad
    if request.headers.get("content-type") != "application/offset+octet-stream":
        return _error(UploadError(415, "Content-Type must be application/offset+octet-stream"))
    try:
        claimed = int(request.headers.get("upload-offset", ""))
    except ValueError:
        return _error(UploadError(400, "Upload-Offset가 필요합니다"))
    lock = _locks[upload_id]
    if lock.locked():
        return _error(UploadError(423, "같은 업로드가 이미 진행 중입니다"))
    async with lock:
        try:
            info = uploads.load(upload_id)
            if claimed != info.offset:
                raise UploadError(409, f"Upload-Offset 불일치 (서버: {info.offset})")
            offset = info.offset
            path = uploads.data_path(upload_id)
            with path.open("ab") as f:
                async for chunk in request.stream():
                    if offset + len(chunk) > info.length:
                        raise UploadError(413, "선언한 길이보다 많은 데이터를 받았습니다")
                    f.write(chunk)
                    offset += len(chunk)
        except UploadError as e:
            return _error(e)
        except ClientDisconnect:
            # Bytes written so far are kept; the client resumes from HEAD's offset.
            log.info("upload interrupted", extra={"upload_id": upload_id})
            return Response(status_code=400, headers=_tus_headers())
        finally:
            _locks.pop(upload_id, None)
    if offset >= info.length:
        log.info("upload complete", extra={"upload_id": upload_id, "length": info.length})
    return Response(status_code=204, headers=_tus_headers(**{"Upload-Offset": str(offset)}))


@router.delete("/{upload_id}")
def cancel(upload_id: str, request: Request) -> Response:
    if bad := _check_version(request):
        return bad
    try:
        uploads.delete(upload_id)
    except UploadError as e:
        return _error(e)
    return Response(status_code=204, headers=_tus_headers())
