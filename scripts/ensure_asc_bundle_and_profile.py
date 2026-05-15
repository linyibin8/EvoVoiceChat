#!/usr/bin/env python3
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import jwt
import requests


BASE_URL = "https://api.appstoreconnect.apple.com/v1"


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def make_session() -> requests.Session:
    key_id = env("ASC_KEY_ID", "47SU743ZHZ")
    issuer_id = env("ASC_ISSUER_ID", "e5e4b9b8-e882-4f89-a35b-8f7fc95edfef")
    key_path = Path(env("ASC_KEY_PATH", "/Users/macstar/Desktop/p12/AuthKey_47SU743ZHZ.p8"))
    private_key = key_path.read_text(encoding="utf-8")
    now = int(time.time())
    token = jwt.encode(
        {"iss": issuer_id, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        private_key,
        algorithm="ES256",
        headers={"alg": "ES256", "kid": key_id, "typ": "JWT"},
    )
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return session


def api_get(session: requests.Session, path: str) -> dict:
    response = session.get(f"{BASE_URL}{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def api_post(session: requests.Session, path: str, payload: dict) -> dict:
    response = session.post(f"{BASE_URL}{path}", json=payload, timeout=30)
    if response.status_code >= 400:
        print(f"POST {path} failed: {response.status_code} {response.text[:1200]}", file=sys.stderr)
    response.raise_for_status()
    return response.json()


def api_delete(session: requests.Session, path: str) -> None:
    response = session.delete(f"{BASE_URL}{path}", timeout=30)
    if response.status_code not in (200, 204):
        print(f"DELETE {path} failed: {response.status_code} {response.text[:1200]}", file=sys.stderr)
    response.raise_for_status()


def ensure_bundle_id(session: requests.Session, bundle_id: str, name: str) -> str:
    existing = api_get(session, f"/bundleIds?filter[identifier]={bundle_id}")["data"]
    if existing:
        print(f"Using bundle id {bundle_id}: {existing[0]['id']}")
        return existing[0]["id"]

    created = api_post(
        session,
        "/bundleIds",
        {
            "data": {
                "type": "bundleIds",
                "attributes": {"identifier": bundle_id, "name": name, "platform": "IOS"},
            }
        },
    )["data"]
    print(f"Created bundle id {bundle_id}: {created['id']}")
    return created["id"]


def choose_certificate(session: requests.Session) -> str:
    target_serial = os.getenv("ASC_CERTIFICATE_SERIAL", "2B060ACFB63E6F8E486A15D092F34941").upper()
    certificates = api_get(session, "/certificates?limit=200")["data"]
    distribution = [
        certificate
        for certificate in certificates
        if "DISTRIBUTION" in certificate["attributes"].get("certificateType", "")
    ]
    for certificate in distribution:
        serial = certificate["attributes"].get("serialNumber", "").upper()
        if serial == target_serial:
            print(f"Using distribution certificate {certificate['id']} serial {serial}")
            return certificate["id"]

    if distribution:
        certificate = distribution[0]
        print(f"Using first distribution certificate {certificate['id']}")
        return certificate["id"]
    raise SystemExit("No distribution certificate found")


def create_profile(session: requests.Session, bundle_resource_id: str, certificate_id: str, profile_name: str) -> dict:
    existing = api_get(session, f"/profiles?filter[name]={profile_name}")["data"]
    for profile in existing:
        api_delete(session, f"/profiles/{profile['id']}")
        print(f"Deleted stale profile {profile['id']}")

    return api_post(
        session,
        "/profiles",
        {
            "data": {
                "type": "profiles",
                "attributes": {"name": profile_name, "profileType": "IOS_APP_STORE"},
                "relationships": {
                    "bundleId": {"data": {"type": "bundleIds", "id": bundle_resource_id}},
                    "certificates": {"data": [{"type": "certificates", "id": certificate_id}]},
                },
            }
        },
    )["data"]


def install_profile(profile: dict) -> Path:
    profile_uuid = profile["attributes"]["uuid"]
    content = base64.b64decode(profile["attributes"]["profileContent"])
    output_dir = Path.home() / "Library" / "MobileDevice" / "Provisioning Profiles"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{profile_uuid}.mobileprovision"
    output.write_bytes(content)
    return output


def main() -> None:
    bundle_id = env("APP_BUNDLE_ID", "com.linyibin8.evovoicechatlocal")
    bundle_name = env("APP_BUNDLE_NAME", "Evo Voice LAN")
    profile_name = env("PROFILE_NAME", "evovoicechatlocal_appstore_profile")
    session = make_session()
    bundle_resource_id = ensure_bundle_id(session, bundle_id, bundle_name)
    certificate_id = choose_certificate(session)
    profile = create_profile(session, bundle_resource_id, certificate_id, profile_name)
    path = install_profile(profile)
    print(f"PROFILE_NAME={profile['attributes']['name']}")
    print(f"PROFILE_UUID={profile['attributes']['uuid']}")
    print(f"PROFILE_PATH={path}")


if __name__ == "__main__":
    main()
