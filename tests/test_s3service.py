"""Tests for the S3 wrapper against a moto-mocked S3."""

import io

import boto3
import pytest
from moto import mock_aws

from janasunani.ingestion.s3service import S3Service

BUCKET = "test-docs"


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        yield S3Service(BUCKET)


def test_upload_fileobj_exists_and_list(s3):
    assert s3.upload_fileobj(io.BytesIO(b"hello"), "OR1_complaint_x.pdf") is True
    assert s3.object_exists("OR1_complaint_x.pdf") is True
    assert s3.object_exists("missing.pdf") is False
    keys = [o["Key"] for o in s3.list_objects(prefix="OR1_complaint_")]
    assert keys == ["OR1_complaint_x.pdf"]


def test_download_and_delete(s3, tmp_path):
    s3.upload_fileobj(io.BytesIO(b"data"), "k.bin")
    dest = tmp_path / "out" / "k.bin"
    assert s3.download_file("k.bin", str(dest)) is True
    assert dest.read_bytes() == b"data"
    assert s3.delete_object("k.bin") is True
    assert s3.object_exists("k.bin") is False


def test_presigned_url(s3):
    s3.upload_fileobj(io.BytesIO(b"x"), "k.bin")
    url = s3.get_presigned_url("k.bin")
    assert url and "k.bin" in url
