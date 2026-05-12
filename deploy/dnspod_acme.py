import json
import os
import sys
import time

from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.dnspod.v20210323 import dnspod_client, models


DOMAIN = "evowit.com"
SUBDOMAIN = "_acme-challenge.evovoice"


def client():
    secret_id = os.environ["DNSPOD_SECRET_ID"]
    secret_key = os.environ["DNSPOD_SECRET_KEY"]
    cred = credential.Credential(secret_id, secret_key)
    http_profile = HttpProfile()
    http_profile.endpoint = "dnspod.tencentcloudapi.com"
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    return dnspod_client.DnspodClient(cred, "", client_profile)


def list_records(api):
    req = models.DescribeRecordListRequest()
    req.from_json_string(json.dumps({
        "Domain": DOMAIN,
        "Subdomain": SUBDOMAIN,
        "RecordType": "TXT",
    }))
    try:
        return api.DescribeRecordList(req).RecordList or []
    except TencentCloudSDKException as err:
        if err.code == "ResourceNotFound.NoDataOfRecord":
            return []
        raise


def create_record(api, value):
    req = models.CreateRecordRequest()
    req.from_json_string(json.dumps({
        "Domain": DOMAIN,
        "SubDomain": SUBDOMAIN,
        "RecordType": "TXT",
        "RecordLine": "默认",
        "Value": value,
        "TTL": 600,
    }, ensure_ascii=False))
    return api.CreateRecord(req)


def delete_record(api, record_id):
    req = models.DeleteRecordRequest()
    req.from_json_string(json.dumps({
        "Domain": DOMAIN,
        "RecordId": record_id,
    }))
    return api.DeleteRecord(req)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: dnspod_acme.py auth|cleanup")
    mode = sys.argv[1]
    value = os.environ.get("CERTBOT_VALIDATION", "")
    if not value:
        raise SystemExit("CERTBOT_VALIDATION is missing")

    api = client()
    if mode == "auth":
        create_record(api, value)
        print("created TXT challenge")
        time.sleep(75)
    elif mode == "cleanup":
        for record in list_records(api):
            if getattr(record, "Value", None) == value:
                delete_record(api, record.RecordId)
                print(f"deleted TXT challenge {record.RecordId}")
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()
