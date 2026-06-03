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


# --- L3 mesh workspace relay helpers ---------------------------------------

def _client():
    import boto3
    return boto3.client("s3", region_name=_region)


def presigned_put(key_name: str, expires: int = 0) -> Optional[str]:
    """Presigned PUT URL so a peer can upload WITHOUT S3 credentials."""
    if not _available:
        return None
    try:
        key = f"{_prefix}/{key_name}"
        return _client().generate_presigned_url(
            "put_object", Params={"Bucket": _bucket, "Key": key},
            ExpiresIn=expires or _expires)
    except Exception as e:
        log.warning("s3: presigned_put failed key=%s error=%s", key_name, e)
        return None


def presigned_get(key_name: str, expires: int = 0) -> Optional[str]:
    """Presigned GET URL so a peer can download WITHOUT S3 credentials."""
    if not _available:
        return None
    try:
        key = f"{_prefix}/{key_name}"
        return _client().generate_presigned_url(
            "get_object", Params={"Bucket": _bucket, "Key": key},
            ExpiresIn=expires or _expires)
    except Exception as e:
        log.warning("s3: presigned_get failed key=%s error=%s", key_name, e)
        return None


def put_bytes(key_name: str, data: bytes) -> bool:
    """Upload raw bytes to S3 (used by the originating node to stage a workspace)."""
    if not _available:
        return False
    try:
        _client().put_object(Bucket=_bucket, Key=f"{_prefix}/{key_name}", Body=data)
        return True
    except Exception as e:
        log.warning("s3: put_bytes failed key=%s error=%s", key_name, e)
        return False


def delete_prefix(key_prefix: str) -> None:
    """Best-effort cleanup of all objects under a key prefix. Never raises."""
    if not _available:
        return
    try:
        s3 = _client()
        full = f"{_prefix}/{key_prefix}"
        objs = s3.list_objects_v2(Bucket=_bucket, Prefix=full).get("Contents", [])
        if objs:
            s3.delete_objects(Bucket=_bucket,
                              Delete={"Objects": [{"Key": o["Key"]} for o in objs]})
    except Exception as e:
        log.info("s3: delete_prefix best-effort failed prefix=%s error=%s", key_prefix, e)


def pack_dir(path: str) -> bytes:
    """tar.gz a directory's contents (arcname='.')."""
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(path, arcname=".")
    return buf.getvalue()


def unpack_dir(data: bytes, dest: str) -> None:
    """Extract a tar.gz produced by pack_dir into dest (created if needed)."""
    import io
    import os
    import tarfile
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest)
