"""Private document-storage backends for Arbiter."""
from __future__ import annotations

import os
import hashlib
from pathlib import Path


class StorageError(RuntimeError):
    pass


class LocalStorage:
    def __init__(self, root: Path): self.root = root
    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try: self.root.chmod(0o700)
        except OSError: pass  # Windows uses inherited NTFS ACLs; downloads remain API-authorized.
    def put(self, key: str, content: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
        except FileExistsError:
            try:
                if path.read_bytes() == content:
                    return
            except OSError as exc:
                raise StorageError("Stored document is unavailable") from exc
            raise StorageError("Refusing to replace an existing document object")
        except OSError as exc:
            try: path.unlink(missing_ok=True)
            except OSError: pass
            raise StorageError("Unable to store document") from exc
    def exists(self, key: str) -> bool: return (self.root / key).is_file()
    def get(self, key: str) -> bytes:
        try: return (self.root / key).read_bytes()
        except FileNotFoundError as exc: raise StorageError("Stored document is unavailable") from exc


class S3Storage:
    def __init__(self, bucket: str, prefix: str, region: str | None, endpoint_url: str | None, encryption: str, kms_key_id: str | None):
        if not bucket: raise StorageError("APP_S3_BUCKET is required when APP_STORAGE_BACKEND=s3")
        try: import boto3
        except ImportError as exc: raise StorageError("Install boto3 to use the S3 storage backend") from exc
        self.bucket, self.prefix = bucket, prefix.strip("/")
        self.encryption, self.kms_key_id = encryption.lower(), kms_key_id
        self.client = boto3.client("s3", region_name=region or None, endpoint_url=endpoint_url or None)
    def _key(self, key: str) -> str: return f"{self.prefix}/{key}" if self.prefix else key
    def initialize(self) -> None:
        # The app must not create buckets or relax their policy as a side effect.
        self.client.head_bucket(Bucket=self.bucket)
    def put(self, key: str, content: bytes) -> None:
        options = {"Bucket": self.bucket, "Key": self._key(key), "Body": content, "ServerSideEncryption": self.encryption, "IfNoneMatch": "*"}
        if self.encryption == "aws:kms" and self.kms_key_id: options["SSEKMSKeyId"] = self.kms_key_id
        try:
            self.client.put_object(**options)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"PreconditionFailed", "412"}:
                try:
                    existing = self.get(key)
                except StorageError:
                    raise StorageError("Unable to verify existing document object") from exc
                if hashlib.sha256(existing).digest() == hashlib.sha256(content).digest():
                    return
                raise StorageError("Refusing to replace an existing document object") from exc
            raise StorageError("Unable to store document") from exc
    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise StorageError("Unable to check stored document") from exc
        except Exception as exc:
            raise StorageError("Unable to check stored document") from exc
    def get(self, key: str) -> bytes:
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"]
            try: return body.read()
            finally: body.close()
        except Exception as exc: raise StorageError("Stored document is unavailable") from exc


def make_storage(local_root: Path):
    backend = os.getenv("APP_STORAGE_BACKEND", "local").lower()
    if backend == "local": return LocalStorage(local_root)
    if backend == "s3": return S3Storage(os.getenv("APP_S3_BUCKET", ""), os.getenv("APP_S3_PREFIX", "arbiter"), os.getenv("APP_S3_REGION"), os.getenv("APP_S3_ENDPOINT_URL"), os.getenv("APP_S3_ENCRYPTION", "aws:kms"), os.getenv("APP_S3_KMS_KEY_ID"))
    raise StorageError("APP_STORAGE_BACKEND must be 'local' or 's3'")
