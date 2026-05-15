#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

from create_asc_app_via_itms import iris_session, login


DEFAULT_TESTERS = [
    "tide_lin@126.com",
    "3559299280@qq.com",
    "269123786@qq.com",
    "linyibin8@qq.com",
    "643014114@qq.com",
    "2811903135@qq.com",
    "3972104921@qq.com",
    "353118924@qq.com",
]


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def name_parts(email: str) -> tuple[str, str]:
    local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
    first = local[:30] or "Test"
    return first, "Tester"


def main() -> None:
    username = env("ASC_USERNAME")
    password = env("ASC_APP_PASSWORD")
    provider_id = env("ASC_PROVIDER_ID", "e5e4b9b8-e882-4f89-a35b-8f7fc95edfef")
    group_id = env("BETA_GROUP_ID")
    emails = [email.strip() for email in env("TESTER_EMAILS", ",".join(DEFAULT_TESTERS)).split(",") if email.strip()]

    cookie_name, token = login(username, password)
    session = iris_session(cookie_name, token, provider_id)
    beta_testers = []
    for email in emails:
        first_name, last_name = name_parts(email)
        beta_testers.append(
            {
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
                "errors": [],
            }
        )
    payload = {
        "data": {
            "type": "bulkBetaTesterAssignments",
            "attributes": {"betaTesters": beta_testers},
            "relationships": {
                "betaGroup": {"data": {"type": "betaGroups", "id": group_id}},
            },
        }
    }
    base_url = f"https://appstoreconnect.apple.com/iris/provider/{provider_id}/v1"
    response = session.post(f"{base_url}/bulkBetaTesterAssignments", json=payload, timeout=60)
    print(f"BULK_ASSIGN_STATUS={response.status_code}")
    if response.status_code >= 400:
        print(response.text[:4000], file=sys.stderr)
        response.raise_for_status()
    print(response.text[:4000])


if __name__ == "__main__":
    main()
