from __future__ import annotations

import mimetypes
import os
from functools import lru_cache

from database import current_request_username, workspace_storage_name


def object_storage_enabled() -> bool:
    backend = os.getenv("STUDYPACE_STORAGE_BACKEND", "").strip().lower()
    if backend and backend != "r2":
        return False
    return all([
        os.getenv("R2_ACCOUNT_ID"),
        os.getenv("R2_ACCESS_KEY_ID"),
        os.getenv("R2_SECRET_ACCESS_KEY"),
        os.getenv("R2_BUCKET"),
    ])


def workspace_object_key(*parts: str) -> str:
    safe_parts = [
        str(part).strip("/").replace("\\", "/")
        for part in parts
        if str(part).strip("/")
    ]
    return "/".join(["workspaces", workspace_storage_name(current_request_username()), *safe_parts])


def content_type_for_filename(filename: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or fallback


@lru_cache(maxsize=1)
def object_storage() -> R2Storage | None:
    if not object_storage_enabled():
        return None
    return R2Storage(
        account_id=os.environ["R2_ACCOUNT_ID"],
        access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        bucket=os.environ["R2_BUCKET"],
    )


class R2Storage:
    def __init__(self, account_id: str, access_key_id: str, secret_access_key: str, bucket: str):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError("Install boto3 to use Cloudflare R2 storage.") from exc

        endpoint = os.getenv("R2_ENDPOINT_URL", "").strip()
        if not endpoint:
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def put_bytes(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
        )

    def get_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete_object(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            code = str(response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            return False
