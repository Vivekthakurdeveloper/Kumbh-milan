import logging
from functools import lru_cache
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.common.errors import ServiceUnavailable
from app.config import Settings, get_settings

logger = logging.getLogger("kumbh_setu.storage")


class ObjectStorage:
    """Private S3-compatible bucket. Objects are only reachable through short-lived presigned URLs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.s3_bucket
        common = dict(
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )
        self.client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None, **common)
        # Prefer the browser-facing public endpoint so presigned URLs never embed docker-internal hosts.
        public_endpoint = settings.s3_public_endpoint_url or settings.s3_endpoint_url or None
        self.presign_client = boto3.client("s3", endpoint_url=public_endpoint, **common)
        self.public_endpoint_url = public_endpoint

    def put(self, key: str, data: bytes, *, content_type: str, checksum_sha256: str) -> None:
        extra = {"ContentType": content_type, "Metadata": {"sha256": checksum_sha256}}
        if self.settings.s3_server_side_encryption:
            extra["ServerSideEncryption"] = self.settings.s3_server_side_encryption
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        except (BotoCoreError, ClientError) as exc:
            logger.error("Object upload failed", extra={"error": type(exc).__name__})
            raise ServiceUnavailable("Media storage is unavailable; try again", code="storage_unavailable") from exc

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except (BotoCoreError, ClientError) as exc:
            logger.error("Object read failed", extra={"error": type(exc).__name__})
            raise ServiceUnavailable("Media storage is unavailable; try again", code="storage_unavailable") from exc

    def copy(self, source_key: str, dest_key: str) -> None:
        extra = {"MetadataDirective": "COPY"}
        if self.settings.s3_server_side_encryption:
            extra["ServerSideEncryption"] = self.settings.s3_server_side_encryption
        try:
            self.client.copy_object(
                Bucket=self.bucket, Key=dest_key, CopySource={"Bucket": self.bucket, "Key": source_key}, **extra
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("Object copy failed", extra={"error": type(exc).__name__})
            raise ServiceUnavailable("Media storage is unavailable; try again", code="storage_unavailable") from exc

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError):
            logger.error("Compensating object delete failed; orphaned object requires cleanup", extra={"key": key})

    def presign_get(self, key: str, *, content_type: str, filename: str, ttl_seconds: int) -> str:
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        try:
            return self.presign_client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ResponseContentType": content_type,
                    "ResponseContentDisposition": disposition,
                },
                ExpiresIn=ttl_seconds,
            )
        except (BotoCoreError, ClientError) as exc:
            raise ServiceUnavailable("Media storage is unavailable", code="storage_unavailable") from exc

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def check(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
                raise
        kwargs = {"Bucket": self.bucket}
        if self.settings.s3_region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.settings.s3_region}
        self.client.create_bucket(**kwargs)
        try:
            self.client.put_public_access_block(
                Bucket=self.bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
        except ClientError:
            logger.info("Store does not support public-access blocks; bucket stays private by default")


@lru_cache
def get_storage() -> ObjectStorage:
    return ObjectStorage(get_settings())
