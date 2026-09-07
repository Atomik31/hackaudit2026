"""Synchronisation de la Knowledge Base d'un client avant analyse : déclenche
l'indexation des documents déposés depuis son dernier passage (côté
commissaire, dont le rôle IAM porte cette permission de façon scoppée aux
Data Sources des clients)."""

from __future__ import annotations

import boto3

from src.aws_identity import TemporaryAwsCredentials


def start_sync(
    credentials: TemporaryAwsCredentials,
    region: str,
    knowledge_base_id: str,
    data_source_id: str,
) -> str:
    """Démarre un ingestion job et renvoie son identifiant."""
    client = boto3.client(
        "bedrock-agent",
        region_name=region,
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
    )
    response = client.start_ingestion_job(
        knowledgeBaseId=knowledge_base_id,
        dataSourceId=data_source_id,
    )
    return response["ingestionJob"]["ingestionJobId"]
