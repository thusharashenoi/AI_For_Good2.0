"""Create the DynamoDB tables we need for local/dev testing (idempotent).

We intentionally use the STAGE-suffixed names (default 'dev') so we never clash
with the teammate's SAM-managed '-prod' tables. The Bridges table is the one we
own for real; donors/patients '-dev' tables let us exercise the full pipeline
against live DynamoDB before onboarding is wired up.

Usage:  .venv/bin/python -m scripts.create_tables
"""
from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from raktsetu import config

ddb = boto3.client("dynamodb", region_name=config.AWS_REGION)


def _exists(name: str) -> bool:
    try:
        ddb.describe_table(TableName=name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def _create(name: str, attrs: list[dict], keys: list[dict], gsis: list[dict] | None = None):
    if _exists(name):
        print(f"  exists: {name}")
        return
    kwargs = dict(
        TableName=name,
        AttributeDefinitions=attrs,
        KeySchema=keys,
        BillingMode="PAY_PER_REQUEST",
    )
    if gsis:
        kwargs["GlobalSecondaryIndexes"] = gsis
    ddb.create_table(**kwargs)
    print(f"  creating: {name} ...")
    ddb.get_waiter("table_exists").wait(TableName=name)
    print(f"  ready: {name}")


def main():
    print(f"Region: {config.AWS_REGION} | Stage: {config.STAGE}")

    _create(
        config.TABLE_DONORS,
        attrs=[{"AttributeName": "donorId", "AttributeType": "S"},
               {"AttributeName": "SK", "AttributeType": "S"}],
        keys=[{"AttributeName": "donorId", "KeyType": "HASH"},
              {"AttributeName": "SK", "KeyType": "RANGE"}],
    )

    _create(
        config.TABLE_PATIENTS,
        attrs=[{"AttributeName": "patientId", "AttributeType": "S"},
               {"AttributeName": "SK", "AttributeType": "S"}],
        keys=[{"AttributeName": "patientId", "KeyType": "HASH"},
              {"AttributeName": "SK", "KeyType": "RANGE"}],
    )

    _create(
        config.TABLE_BRIDGES,
        attrs=[{"AttributeName": "bridgeId", "AttributeType": "S"},
               {"AttributeName": "SK", "AttributeType": "S"},
               {"AttributeName": "patientId", "AttributeType": "S"}],
        keys=[{"AttributeName": "bridgeId", "KeyType": "HASH"},
              {"AttributeName": "SK", "KeyType": "RANGE"}],
        gsis=[{
            "IndexName": "patient-index",
            "KeySchema": [{"AttributeName": "patientId", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )

    # Automation / WhatsApp portal tables (same STAGE suffix as intelligence layer).
    stage = config.STAGE
    _create(
        f"raktsetu-conversations-{stage}",
        attrs=[{"AttributeName": "phone_number", "AttributeType": "S"}],
        keys=[{"AttributeName": "phone_number", "KeyType": "HASH"}],
    )
    _create(
        config.TABLE_REQUESTS,
        attrs=[{"AttributeName": "requestId", "AttributeType": "S"},
               {"AttributeName": "SK", "AttributeType": "S"}],
        keys=[{"AttributeName": "requestId", "KeyType": "HASH"},
              {"AttributeName": "SK", "KeyType": "RANGE"}],
    )
    _create(
        config.TABLE_APPOINTMENTS,
        attrs=[{"AttributeName": "appointmentId", "AttributeType": "S"},
               {"AttributeName": "SK", "AttributeType": "S"}],
        keys=[{"AttributeName": "appointmentId", "KeyType": "HASH"},
              {"AttributeName": "SK", "KeyType": "RANGE"}],
    )
    _create(
        f"raktsetu-waitlist-{stage}",
        attrs=[{"AttributeName": "phone", "AttributeType": "S"}],
        keys=[{"AttributeName": "phone", "KeyType": "HASH"}],
    )
    _create(
        f"raktsetu-processedMessages-{stage}",
        attrs=[{"AttributeName": "messageId", "AttributeType": "S"}],
        keys=[{"AttributeName": "messageId", "KeyType": "HASH"}],
    )

    print("Done.")


if __name__ == "__main__":
    main()
