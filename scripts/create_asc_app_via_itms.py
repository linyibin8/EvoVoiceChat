#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import sys
import time

import requests


LABEL_SERVICE_URL = "https://contentdelivery.itunes.apple.com/WebObjects/MZLabelService.woa/json/MZITunesSoftwareService"


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def login(username: str, password: str) -> tuple[str, str]:
    request_id = time.strftime("%Y%m%d%H%M%S") + "-studylog"
    os_name = f"Mac OS X {platform.mac_ver()[0]} ({platform.machine()})"
    payload = {
        "id": request_id,
        "jsonrpc": "2.0",
        "method": "generateAppleConnectToken",
        "params": {
            "Application": "altool",
            "ApplicationBundleId": "com.apple.itunes.altool",
            "OSIdentifier": os_name,
            "Password": password,
            "Username": username,
            "Version": "26.30.4 (173004)",
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-request-id": request_id,
        "x-tx-client-name": "altool",
        "x-tx-client-version": "26.30.4 (173004)",
        "x-tx-method": "generateAppleConnectToken",
    }
    response = requests.post(LABEL_SERVICE_URL, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    result = response.json()["result"]
    return result["DSTokenCookieName"], result["DSToken"]


def iris_session(cookie_name: str, token: str, provider_id: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "Accept-Language": "zh-Hans-CN",
            "Content-Type": "application/json",
            "Cookie": f"{cookie_name}={token}",
            "User-Agent": "altool/26.30.4-173004 (Macintosh) ContentDelivery/26.30.4-173004",
            "x-connect-team-id": provider_id,
            "x-connect-team-type": "CONTENT_PROVIDER",
        }
    )
    return session


def create_payload(app_name: str, bundle_id: str, sku: str, locale: str, version: str) -> dict:
    app_info_id = "${new-appInfo-id}"
    app_info_localization_id = "${new-appInfoLocalization-id}"
    app_store_version_id = "${new-app-store-version-id}"
    app_store_version_localization_id = "${new-version-localization-id}"
    return {
        "included": [
            {
                "id": app_info_id,
                "type": "appInfos",
                "relationships": {
                    "appInfoLocalizations": {
                        "data": [{"id": app_info_localization_id, "type": "appInfoLocalizations"}]
                    }
                },
            },
            {
                "id": app_store_version_id,
                "type": "appStoreVersions",
                "attributes": {"platform": "IOS", "versionString": version},
                "relationships": {
                    "appStoreVersionLocalizations": {
                        "data": [
                            {
                                "id": app_store_version_localization_id,
                                "type": "appStoreVersionLocalizations",
                            }
                        ]
                    }
                },
            },
            {
                "id": app_info_localization_id,
                "type": "appInfoLocalizations",
                "attributes": {"name": app_name, "locale": locale},
            },
            {
                "id": app_store_version_localization_id,
                "type": "appStoreVersionLocalizations",
                "attributes": {"locale": locale},
            },
        ],
        "data": {
            "type": "apps",
            "attributes": {
                "sku": sku,
                "primaryLocale": locale,
                "bundleId": bundle_id,
            },
            "relationships": {
                "appInfos": {"data": [{"id": app_info_id, "type": "appInfos"}]},
                "appStoreVersions": {
                    "data": [{"id": app_store_version_id, "type": "appStoreVersions"}]
                },
            },
        },
    }


def main() -> None:
    username = env("ASC_USERNAME")
    password = env("ASC_APP_PASSWORD")
    provider_id = env("ASC_PROVIDER_ID", "e5e4b9b8-e882-4f89-a35b-8f7fc95edfef")
    bundle_id = env("APP_BUNDLE_ID", "com.linyibin8.evovoicechatlocal")
    app_name = env("APP_NAME", "Evo Voice LAN")
    sku = env("APP_SKU", "evovoicechatlocal")
    locale = env("APP_PRIMARY_LOCALE", "zh-Hans")
    version = env("APP_VERSION", "1.0.0")

    cookie_name, token = login(username, password)
    session = iris_session(cookie_name, token, provider_id)
    base_url = f"https://appstoreconnect.apple.com/iris/provider/{provider_id}/v1"

    existing = session.get(
        f"{base_url}/apps",
        params={"filter[bundleId]": bundle_id, "include": "appStoreVersions", "limit": "1"},
        timeout=60,
    )
    existing.raise_for_status()
    apps = existing.json().get("data", [])
    if apps:
        app = apps[0]
        print(f"ASC_APP_ID={app['id']}")
        print(f"ASC_APP_NAME={app.get('name') or app.get('attributes', {}).get('name') or app_name}")
        return

    response = session.post(
        f"{base_url}/apps",
        json=create_payload(app_name, bundle_id, sku, locale, version),
        timeout=60,
    )
    if response.status_code >= 400:
        print(f"CREATE_APP_FAILED={response.status_code} {response.text[:4000]}", file=sys.stderr)
        response.raise_for_status()

    app = response.json()["data"]
    print(f"ASC_APP_ID={app['id']}")
    print(f"ASC_APP_NAME={app.get('name') or app_name}")


if __name__ == "__main__":
    main()
