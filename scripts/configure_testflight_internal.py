#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time

import requests

from ensure_asc_bundle_and_profile import BASE_URL, make_session


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


def request(session: requests.Session, method: str, path: str, **kwargs) -> requests.Response:
    response = session.request(method, f"{BASE_URL}{path}", timeout=30, **kwargs)
    if response.status_code >= 400:
        print(f"{method} {path} failed: {response.status_code} {response.text[:2000]}", file=sys.stderr)
    response.raise_for_status()
    return response


def first_page(session: requests.Session, path: str, params: dict[str, str]) -> list[dict]:
    return request(session, "GET", path, params=params).json().get("data", [])


def find_build(session: requests.Session, app_id: str, build_number: str) -> dict | None:
    builds = first_page(
        session,
        "/builds",
        {
            "filter[app]": app_id,
            "filter[version]": build_number,
            "fields[builds]": "version,processingState,expired,usesNonExemptEncryption",
            "limit": "10",
        },
    )
    return builds[0] if builds else None


def wait_for_build(session: requests.Session, app_id: str, build_number: str, timeout_seconds: int) -> dict:
    deadline = time.time() + timeout_seconds
    last_state = "missing"
    while time.time() < deadline:
        build = find_build(session, app_id, build_number)
        if build:
            last_state = build["attributes"].get("processingState", "UNKNOWN")
            print(f"BUILD_ID={build['id']} PROCESSING_STATE={last_state}")
            if last_state in {"VALID", "FAILED", "INVALID"}:
                if last_state != "VALID":
                    raise SystemExit(f"Build processing ended as {last_state}")
                return build
        time.sleep(30)
    raise SystemExit(f"Timed out waiting for build {build_number}; last state: {last_state}")


def ensure_export_compliance(session: requests.Session, build: dict) -> dict:
    build_id = build["id"]
    current_value = build.get("attributes", {}).get("usesNonExemptEncryption")
    if current_value is False:
        print("EXPORT_COMPLIANCE=false")
        return build

    payload = {
        "data": {
            "type": "builds",
            "id": build_id,
            "attributes": {"usesNonExemptEncryption": False},
        }
    }
    updated = request(session, "PATCH", f"/builds/{build_id}", json=payload).json()["data"]
    print("EXPORT_COMPLIANCE_SET=false")
    return updated


def ensure_group(session: requests.Session, app_id: str, name: str) -> dict:
    groups = first_page(
        session,
        "/betaGroups",
        {
            "filter[app]": app_id,
            "fields[betaGroups]": "name,isInternalGroup,hasAccessToAllBuilds",
            "limit": "100",
        },
    )
    for group in groups:
        if group["attributes"].get("name") == name:
            print(f"BETA_GROUP_ID={group['id']} BETA_GROUP_NAME={name}")
            return group

    payload = {
        "data": {
            "type": "betaGroups",
            "attributes": {
                "name": name,
                "isInternalGroup": True,
                "hasAccessToAllBuilds": True,
            },
            "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
        }
    }
    group = request(session, "POST", "/betaGroups", json=payload).json()["data"]
    print(f"BETA_GROUP_ID={group['id']} BETA_GROUP_NAME={name}")
    return group


def ensure_build_notes(session: requests.Session, build_id: str, whats_new: str) -> None:
    localizations = first_page(
        session,
        "/betaBuildLocalizations",
        {"filter[build]": build_id, "fields[betaBuildLocalizations]": "locale,whatsNew", "limit": "20"},
    )
    for localization in localizations:
        if localization["attributes"].get("locale") == "zh-Hans":
            payload = {
                "data": {
                    "type": "betaBuildLocalizations",
                    "id": localization["id"],
                    "attributes": {"whatsNew": whats_new},
                }
            }
            request(session, "PATCH", f"/betaBuildLocalizations/{localization['id']}", json=payload)
            print(f"BETA_BUILD_LOCALIZATION_ID={localization['id']}")
            return

    payload = {
        "data": {
            "type": "betaBuildLocalizations",
            "attributes": {"locale": "zh-Hans", "whatsNew": whats_new},
            "relationships": {"build": {"data": {"type": "builds", "id": build_id}}},
        }
    }
    localization = request(session, "POST", "/betaBuildLocalizations", json=payload).json()["data"]
    print(f"BETA_BUILD_LOCALIZATION_ID={localization['id']}")


def add_build_to_group(session: requests.Session, build_id: str, group_id: str) -> None:
    payload = {"data": [{"type": "betaGroups", "id": group_id}]}
    try:
        request(session, "POST", f"/builds/{build_id}/relationships/betaGroups", json=payload)
        print(f"BUILD_ADDED_TO_GROUP={group_id}")
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code in {409, 422}:
            print(f"BUILD_GROUP_LINK_SKIPPED={group_id}")
            return
        raise


def add_existing_testers(session: requests.Session, group_id: str, emails: list[str]) -> None:
    added: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    for email in emails:
        testers = first_page(
            session,
            "/betaTesters",
            {
                "filter[email]": email,
                "fields[betaTesters]": "email,firstName,lastName",
                "limit": "1",
            },
        )
        if not testers:
            missing.append(email)
            continue
        tester_id = testers[0]["id"]
        payload = {"data": [{"type": "betaTesters", "id": tester_id}]}
        try:
            request(session, "POST", f"/betaGroups/{group_id}/relationships/betaTesters", json=payload)
            added.append(email)
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 409:
                failed.append(email)
                continue
            raise
    current = first_page(
        session,
        f"/betaGroups/{group_id}/betaTesters",
        {"fields[betaTesters]": "email,firstName,lastName", "limit": "200"},
    )
    current_emails = sorted(tester["attributes"].get("email", "") for tester in current)
    actually_in_group = [email for email in emails if email in current_emails]
    print("TESTERS_ADDED_REQUEST_ACCEPTED=" + ",".join(added))
    print("TESTERS_IN_GROUP=" + ",".join(actually_in_group))
    print("TESTERS_FAILED_ASSIGN=" + ",".join(failed))
    print("TESTERS_NOT_FOUND_AS_INTERNAL=" + ",".join(missing))


def main() -> None:
    app_id = env("ASC_APP_ID")
    build_number = env("APP_BUILD_NUMBER")
    group_name = env("TESTFLIGHT_GROUP_NAME", "EvoVoiceChat Internal")
    timeout_seconds = int(env("BUILD_WAIT_SECONDS", "1800"))
    whats_new = env(
        "WHAT_TO_TEST",
        "Evo Voice 远端域名版：iOS 固定连接 https://evovoice.evowit.com，后端 remote-server profile 通过 Tailscale 内网访问 LLM、TTS、STT。包含流式断线兜底、固定参考音色 TTS、预合成下一段和更严格的来源过滤。",
    )
    emails = [email.strip() for email in env("TESTER_EMAILS", ",".join(DEFAULT_TESTERS)).split(",") if email.strip()]

    session = make_session()
    build = wait_for_build(session, app_id, build_number, timeout_seconds)
    build = ensure_export_compliance(session, build)
    group = ensure_group(session, app_id, group_name)
    ensure_build_notes(session, build["id"], whats_new)
    add_build_to_group(session, build["id"], group["id"])
    add_existing_testers(session, group["id"], emails)


if __name__ == "__main__":
    main()
