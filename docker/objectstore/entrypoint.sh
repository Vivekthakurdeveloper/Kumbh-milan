#!/bin/sh
# Local S3-compatible store (SeaweedFS). With an identity configured, anonymous access is denied,
# so every bucket is private. Volume data is encrypted at rest with per-chunk keys held by the filer.
set -eu

: "${S3_ACCESS_KEY_ID:?S3_ACCESS_KEY_ID is required}"
: "${S3_SECRET_ACCESS_KEY:?S3_SECRET_ACCESS_KEY is required}"

mkdir -p /etc/seaweedfs /data
cat > /etc/seaweedfs/s3.json <<JSON
{
  "identities": [
    {
      "name": "kumbh-api",
      "credentials": [{"accessKey": "${S3_ACCESS_KEY_ID}", "secretKey": "${S3_SECRET_ACCESS_KEY}"}],
      "actions": ["Read", "Write", "List", "Tagging", "Admin"]
    }
  ]
}
JSON

exec weed server \
  -dir=/data \
  -ip.bind=0.0.0.0 \
  -volume.max=0 \
  -filer.encryptVolumeData \
  -s3 \
  -s3.port=8333 \
  -s3.config=/etc/seaweedfs/s3.json
