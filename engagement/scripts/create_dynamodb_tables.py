#!/usr/bin/env python3
"""Create DynamoDB tables for RaktSetu (when SAM deploy is unavailable).

Usage:
    python scripts/create_dynamodb_tables.py --region us-east-1
"""
from __future__ import annotations

import argparse
import sys
import time

TABLES = [
    ("raktsetu-conversations-prod", [("phone_number", "S")], "phone_number"),
    ("raktsetu-donors-prod", [("donorId", "S"), ("SK", "S")], "donorId", "SK"),
    ("raktsetu-patients-prod", [("patientId", "S"), ("SK", "S")], "patientId", "SK"),
    ("raktsetu-bloodRequests-prod", [("requestId", "S"), ("SK", "S")], "requestId", "SK"),
    ("raktsetu-appointments-prod", [("appointmentId", "S"), ("SK", "S")], "appointmentId", "SK"),
    ("raktsetu-waitlist-prod", [("phone", "S")], "phone"),
    ("raktsetu-processedMessages-prod", [("messageId", "S")], "messageId"),
]


def _create(client, name, attrs, hash_key, range_key=None):
    try:
        client.describe_table(TableName=name)
        print(f"  exists  {name}")
        return
    except client.exceptions.ResourceNotFoundException:
        pass

    key_schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    if range_key:
        key_schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
    params = {
        "TableName": name,
        "AttributeDefinitions": [{"AttributeName": a, "AttributeType": t} for a, t in attrs],
        "KeySchema": key_schema,
        "BillingMode": "PAY_PER_REQUEST",
    }
    client.create_table(**params)
    print(f"  creating {name} ...")
    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=name)
    if name.endswith("processedMessages-prod"):
        client.update_time_to_live(
            TableName=name,
            TimeToLiveSpecification={"AttributeName": "ttl", "Enabled": True},
        )
    print(f"  ready   {name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    import boto3
    client = boto3.client("dynamodb", region_name=args.region)
    for spec in TABLES:
        _create(client, *spec)
    print("All tables ready.")


if __name__ == "__main__":
    main()
