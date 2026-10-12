import json
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import settings


class DocumentError(Exception):
    pass


class FileStorageService:
    def save(self, upload: UploadFile, allow_images: bool = False) -> Path:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in ({".pdf", ".xlsx", ".csv", ".txt", ".jpg", ".jpeg", ".png"} if allow_images else {".pdf", ".xlsx", ".csv", ".txt"}):
            raise DocumentError("Formato não suportado. Anexa PDF com texto, XLSX, CSV ou TXT.")
        settings.upload_path.mkdir(parents=True, exist_ok=True)
        target = settings.upload_path / f"{uuid4().hex}{suffix}"
        size = 0
        try:
            with target.open("xb") as output:
                while chunk := upload.file.read(65536):
                    size += len(chunk)
                    if size > settings.upload_max_size_mb * 1024 * 1024:
                        raise DocumentError(f"O ficheiro excede {settings.upload_max_size_mb} MB.")
                    output.write(chunk)
            if size == 0:
                raise DocumentError("O ficheiro está vazio.")
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self.mark(target, "received")
        return target

    def mark(self, path: Path, status: str) -> None:
        path.with_suffix(path.suffix + ".json").write_text(
            json.dumps({"status": status}, ensure_ascii=False), encoding="utf-8"
        )
