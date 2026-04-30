"""Upload a research dataset artifact to S3 for long-term retention.

The parquet artifacts are committed to git, so why upload at all?

1. Re-pulling a Databento dataset costs real money. If the local
   clone is wiped or another collaborator needs the data, S3 is the
   fallback that does NOT re-charge the account.
2. Larger pulls (full SPXW chain) may exceed git's ~100 MB-per-file
   practical limit. S3 backs that without LFS infra.

Idempotency contract:

- If S3 already has an object at the target key with matching sha256
  metadata: skip the upload (no charge for re-runs, no surprise
  overwrites).
- If S3 has an object at the key with DIFFERENT sha256: fail loudly
  — the operator must explicitly delete the remote object first.
  This protects the manifest's pinned checksum from silent drift.
- Otherwise: upload with ``Metadata: sha256=<digest>``.

The bucket must already exist (deployed via
``infra/cloudformation/data.yaml``). The pattern matches
``infra/scripts/release.sh``: ``bull-call-{env}-artifacts-{account-id}``.

Usage::

    aws sso login --profile busyweb    # or otherwise authenticate
    uv run python -m research.scripts.upload_to_s3 \\
        --file research/data/dataset-v1/es_intraday.parquet \\
        --bucket bull-call-dev-artifacts-<account-id> \\
        --key research/dataset-v1/es_intraday.parquet
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


_NOT_FOUND_CODES = ("404", "NoSuchKey", "NotFound")


@dataclass(frozen=True)
class UploadResult:
    bucket: str
    key: str
    sha256: str
    size_bytes: int
    action: Literal["uploaded", "skipped"]


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload(
    *,
    file: Path,
    bucket: str,
    key: str,
    profile: str | None = None,
    region: str = "us-east-1",
) -> UploadResult:
    """Upload ``file`` to ``s3://{bucket}/{key}`` with sha256 metadata.

    Raises:
    - ``FileNotFoundError`` if ``file`` does not exist locally.
    - ``RuntimeError`` if a remote object exists with a different sha256.
    - ``botocore.exceptions.ClientError`` for any S3 error other than
      not-found (auth failures, network, bucket missing, etc.).
    """

    if not file.exists():
        raise FileNotFoundError(f"local file does not exist: {file}")

    import boto3
    import botocore.exceptions

    session = boto3.Session(profile_name=profile, region_name=region)
    s3 = session.client("s3")

    local_sha = _sha256_of(file)
    size = file.stat().st_size

    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        existing_sha = head.get("Metadata", {}).get("sha256")
        if existing_sha == local_sha:
            return UploadResult(
                bucket=bucket, key=key, sha256=local_sha,
                size_bytes=size, action="skipped",
            )
        raise RuntimeError(
            f"s3://{bucket}/{key} exists with sha256 {existing_sha!r}, "
            f"local file is {local_sha!r}; refusing to overwrite. "
            f"Delete the remote object first if a re-upload is intended.",
        )
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] not in _NOT_FOUND_CODES:
            raise

    s3.upload_file(
        Filename=str(file),
        Bucket=bucket,
        Key=key,
        ExtraArgs={"Metadata": {"sha256": local_sha}},
    )
    return UploadResult(
        bucket=bucket, key=key, sha256=local_sha,
        size_bytes=size, action="uploaded",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="upload_to_s3")
    p.add_argument("--file", type=Path, required=True,
                   help="Local file to upload")
    p.add_argument("--bucket", required=True,
                   help="Destination S3 bucket")
    p.add_argument("--key", required=True,
                   help="Destination S3 object key")
    p.add_argument("--profile", default=None,
                   help="AWS profile (default: AWS_PROFILE env or boto3 default)")
    p.add_argument("--region", default="us-east-1",
                   help="AWS region (default: us-east-1)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = upload(
        file=args.file,
        bucket=args.bucket,
        key=args.key,
        profile=args.profile,
        region=args.region,
    )
    print(f"{result.action}: s3://{result.bucket}/{result.key}")
    print(f"sha256: {result.sha256}")
    print(f"size:   {result.size_bytes:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
