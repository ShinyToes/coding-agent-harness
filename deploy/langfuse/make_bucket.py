"""给本地 Langfuse 建 S3 bucket（SeaweedFS，S3 兼容）。"""

from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

client = boto3.client(
    "s3",
    endpoint_url="http://127.0.0.1:8333",
    aws_access_key_id="minio",
    aws_secret_access_key="miniosecret",
    region_name="us-east-1",
    config=Config(s3={"addressing_style": "path"}, connect_timeout=5, read_timeout=5),
)

try:
    client.create_bucket(Bucket="langfuse")
    print("created bucket langfuse")
except ClientError as exc:
    code = exc.response.get("Error", {}).get("Code", "")
    print("create_bucket:", code or exc)
print("buckets:", [bucket["Name"] for bucket in client.list_buckets().get("Buckets", [])])
