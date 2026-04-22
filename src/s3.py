"""S3 file upload with presigned URL generation. Availability checked once at startup."""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("acp-bridge.s3")

_available: bool = False
_bucket: str = ""
_prefix: str = "acp-bridge/files"
_region: str = ""
_expires: int = 3600


def init(bucket: str = "", prefix: str = "acp-bridge/files", expires: int = 3600) -> bool:
    """Probe S3 access at startup. Returns True if usable."""
    global _available, _bucket, _prefix, _region, _expires
    _expires = expires
    _prefix = prefix
    _bucket = bucket or os.environ.get("ACP_S3_BUCKET", "")

    if not _bucket:
        try:
            import boto3
            session = boto3.session.Session()
            s3 = session.client("s3")
            buckets = s3.list_buckets().get("Buckets", [])
            if buckets:
                _bucket = buckets[0]["Name"]
        except Exception as e:
            log.info("s3: disabled (auto-detect failed: %s)", e)
            return False

    if not _bucket:
        log.info("s3: disabled (no bucket)")
        return False

    try:
        import boto3
        session = boto3.session.Session()
        region = session.region_name or "us-east-1"
        s3 = session.client("s3")
        # Create bucket if it doesn't exist
        try:
            s3.head_bucket(Bucket=_bucket)
        except Exception:
            try:
                if region == "us-east-1":
                    s3.create_bucket(Bucket=_bucket)
                else:
                    s3.create_bucket(Bucket=_bucket,
                                     CreateBucketConfiguration={"LocationConstraint": region})
                log.info("s3: created bucket %s in %s", _bucket, region)
            except Exception as e:
                log.warning("s3: cannot create bucket %s: %s", _bucket, e)
                return False
        _region = s3.get_bucket_location(Bucket=_bucket).get("LocationConstraint") or "us-east-1"
        s3.put_object(Bucket=_bucket, Key=f"{_prefix}/.probe", Body=b"ok")
        _available = True
        log.info("s3: enabled bucket=%s prefix=%s region=%s", _bucket, _prefix, _region)
        return True
    except Exception as e:
        log.warning("s3: probe failed bucket=%s error=%s", _bucket, e)
        return False


def is_available() -> bool:
    return _available


def upload(local_path: str, key_name: str = "") -> Optional[str]:
    """Upload file to S3, return presigned URL or None on failure."""
    if not _available:
        return None
    try:
        import boto3
        s3 = boto3.client("s3", region_name=_region)
        if not key_name:
            key_name = os.path.basename(local_path)
        key = f"{_prefix}/{key_name}"
        s3.upload_file(local_path, _bucket, key)
        url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": _bucket, "Key": key}, ExpiresIn=_expires,
        )
        log.info("s3: uploaded %s -> s3://%s/%s", local_path, _bucket, key)
        return url
    except Exception as e:
        log.warning("s3: upload failed path=%s error=%s", local_path, e)
        return None
