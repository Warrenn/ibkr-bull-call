"""Tests for ``research.scripts.upload_to_s3``.

The upload script is idempotent on (bucket, key, sha256). These tests
exhaustively cover the three branches: skip / refuse / upload. Real S3
is mocked via ``boto3.Session``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest

from research.scripts.upload_to_s3 import (
    UploadResult,
    _sha256_of,
    main,
    upload,
)


def _file_with(tmp_path: Path, content: bytes = b"abc") -> Path:
    f = tmp_path / "test.parquet"
    f.write_bytes(content)
    return f


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _client_not_found_error() -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        error_response={"Error": {"Code": "404", "Message": "Not Found"}},
        operation_name="HeadObject",
    )


def _mock_session_with(s3_mock: MagicMock) -> MagicMock:
    sess = MagicMock()
    sess.client.return_value = s3_mock
    return sess


def test_sha256_of_computes_correct_digest(tmp_path: Path) -> None:
    f = _file_with(tmp_path, b"hello world")
    assert _sha256_of(f) == hashlib.sha256(b"hello world").hexdigest()


def test_upload_skips_when_remote_sha256_matches(
    tmp_path: Path,
) -> None:
    """Idempotent re-runs must not re-upload. If S3 already has the
    same sha256 in object metadata, the script returns ``skipped``
    without calling ``upload_file``.
    """

    f = _file_with(tmp_path, b"abc")
    s3 = MagicMock()
    s3.head_object.return_value = {"Metadata": {"sha256": _sha(b"abc")}}

    with patch("boto3.Session", return_value=_mock_session_with(s3)):
        result = upload(file=f, bucket="b", key="k")

    assert isinstance(result, UploadResult)
    assert result.action == "skipped"
    assert result.sha256 == _sha(b"abc")
    s3.upload_file.assert_not_called()


def test_upload_refuses_when_remote_sha256_differs(
    tmp_path: Path,
) -> None:
    """If the remote object has different sha256, the upload would
    silently overwrite the manifest's pinned artifact. Refuse — the
    operator must delete the remote object first.
    """

    f = _file_with(tmp_path, b"abc")
    s3 = MagicMock()
    s3.head_object.return_value = {"Metadata": {"sha256": "different"}}

    with patch("boto3.Session", return_value=_mock_session_with(s3)):
        with pytest.raises(RuntimeError, match="refusing to overwrite"):
            upload(file=f, bucket="b", key="k")

    s3.upload_file.assert_not_called()


def test_upload_uploads_when_remote_does_not_exist(
    tmp_path: Path,
) -> None:
    f = _file_with(tmp_path, b"abc")
    s3 = MagicMock()
    s3.head_object.side_effect = _client_not_found_error()

    with patch("boto3.Session", return_value=_mock_session_with(s3)):
        result = upload(file=f, bucket="my-bucket", key="path/to/k")

    assert result.action == "uploaded"
    assert result.bucket == "my-bucket"
    assert result.key == "path/to/k"
    assert result.sha256 == _sha(b"abc")

    s3.upload_file.assert_called_once()
    kwargs = s3.upload_file.call_args.kwargs
    assert kwargs["Bucket"] == "my-bucket"
    assert kwargs["Key"] == "path/to/k"
    assert kwargs["ExtraArgs"]["Metadata"]["sha256"] == _sha(b"abc")


def test_upload_raises_on_missing_local_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        upload(file=tmp_path / "missing.parquet", bucket="b", key="k")


def test_upload_propagates_unexpected_s3_errors(
    tmp_path: Path,
) -> None:
    """Any S3 error other than not-found must propagate — silently
    proceeding to upload on a 403 (auth fail) would mask the real
    problem.
    """

    f = _file_with(tmp_path, b"abc")
    s3 = MagicMock()
    s3.head_object.side_effect = botocore.exceptions.ClientError(
        error_response={"Error": {"Code": "403", "Message": "Forbidden"}},
        operation_name="HeadObject",
    )

    with patch("boto3.Session", return_value=_mock_session_with(s3)):
        with pytest.raises(botocore.exceptions.ClientError):
            upload(file=f, bucket="b", key="k")


def test_main_smoke_skipped(tmp_path: Path) -> None:
    f = _file_with(tmp_path, b"abc")
    s3 = MagicMock()
    s3.head_object.return_value = {"Metadata": {"sha256": _sha(b"abc")}}

    with patch("boto3.Session", return_value=_mock_session_with(s3)):
        rc = main([
            "--file", str(f),
            "--bucket", "b",
            "--key", "k",
        ])

    assert rc == 0
