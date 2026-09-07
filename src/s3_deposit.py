"""Dépôt de documents client : upload direct vers le bucket S3 du client,
avec les identifiants AWS temporaires scoppés à ce bucket uniquement (le
rôle IAM assumé n'autorise aucun autre bucket — voir README)."""

from __future__ import annotations

from dataclasses import dataclass

import boto3

from src.aws_identity import TemporaryAwsCredentials


@dataclass
class DepositedDocument:
    key: str
    size_bytes: int
    last_modified: str


def _s3_client(credentials: TemporaryAwsCredentials, region: str):
    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
    )


def upload_document(
    credentials: TemporaryAwsCredentials,
    region: str,
    bucket: str,
    file_name: str,
    file_bytes: bytes,
) -> None:
    _s3_client(credentials, region).put_object(Bucket=bucket, Key=file_name, Body=file_bytes)


def list_documents(credentials: TemporaryAwsCredentials, region: str, bucket: str) -> list[DepositedDocument]:
    response = _s3_client(credentials, region).list_objects_v2(Bucket=bucket)
    return [
        DepositedDocument(
            key=obj["Key"],
            size_bytes=obj["Size"],
            last_modified=obj["LastModified"].strftime("%Y-%m-%d %H:%M"),
        )
        for obj in response.get("Contents", [])
    ]
