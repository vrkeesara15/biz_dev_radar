"""Upload pipeline (SPEC section 11): validate -> scan -> store -> files row.

    svc = UploadService(session, storage=storage, scanner=scanner)
    row = await svc.store(tenant_id=..., region=tenant.data_residency, filename=..., data=...)

Raises app.core.uploads.UploadRejected subclasses (status 413/415), InfectedUploadError
(422) or app.services.scanner.ScannerUnavailableError (the API answers 503: uploads fail
closed when the scanner is down). The API layer turns every rejection into an audit row.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Region
from app.core.paths import tenant_file_key
from app.core.uploads import UploadCheck, UploadRejected, validate_upload
from app.models import File
from app.services.scanner import Scanner
from app.services.storage import DEFAULT_EXPIRES_SECONDS, Storage

REJECTED_ACTION = "file.rejected"
UPLOAD_ACTION = "file.upload"


class InfectedUploadError(UploadRejected):
    status = 422

    def __init__(self, signature: str | None, scanner: str) -> None:
        super().__init__("file failed the virus scan", signature=signature, scanner=scanner)


class UploadService:
    def __init__(self, session: AsyncSession, *, storage: Storage, scanner: Scanner) -> None:
        self.session = session
        self.storage = storage
        self.scanner = scanner

    async def store(
        self,
        *,
        tenant_id: uuid.UUID,
        region: Region,
        filename: str,
        data: bytes,
        uploaded_by: uuid.UUID | None = None,
    ) -> File:
        check: UploadCheck = validate_upload(filename, data)
        result = await self.scanner.scan(data)
        if not result.clean:
            raise InfectedUploadError(result.signature, result.scanner)
        file_id = uuid.uuid4()
        key = tenant_file_key(tenant_id, file_id, check.extension)
        row = File(
            id=file_id,
            tenant_id=tenant_id,
            filename=filename.replace("\\", "/").rsplit("/", 1)[-1][:255],
            extension=check.extension,
            kind=check.kind,
            content_type=check.content_type,
            size_bytes=check.size,
            sha256="",  # set from the stored object below
            region=region,
            bucket=self.storage.bucket,
            key=key,
            scan_status="clean",
            scanner=result.scanner,
            uploaded_by=uploaded_by,
        )
        self.session.add(row)
        await self.session.flush()  # row first: a storage failure rolls the row back
        obj = await self.storage.put(key, data, check.content_type)
        row.sha256 = obj.sha256
        await self.session.flush()
        return row

    async def signed_url(self, row: File, expires_seconds: int = DEFAULT_EXPIRES_SECONDS) -> str:
        return await self.storage.signed_url(row.key, expires_seconds)

    async def read(self, row: File) -> bytes:
        return await self.storage.get(row.key)

    async def delete(self, row: File) -> None:
        await self.storage.delete(row.key)
        await self.session.delete(row)
        await self.session.flush()
