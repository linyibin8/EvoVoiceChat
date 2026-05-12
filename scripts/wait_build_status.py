#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time

from ensure_asc_bundle_and_profile import BASE_URL, make_session


def main() -> None:
    app_id = os.environ["ASC_APP_ID"]
    build_number = os.environ["APP_BUILD_NUMBER"]
    timeout_seconds = int(os.environ.get("BUILD_WAIT_SECONDS", "1200"))
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        session = make_session()
        response = session.get(
            f"{BASE_URL}/builds",
            params={
                "filter[app]": app_id,
                "filter[version]": build_number,
                "fields[builds]": "version,processingState,usesNonExemptEncryption",
                "limit": "10",
            },
            timeout=30,
        )
        if response.status_code >= 400:
            print(f"GET builds failed: {response.status_code} {response.text[:1000]}", file=sys.stderr)
            response.raise_for_status()
        builds = response.json().get("data", [])
        if builds:
            build = builds[0]
            state = build["attributes"].get("processingState", "UNKNOWN")
            print(f"BUILD_ID={build['id']} PROCESSING_STATE={state}")
            if state in {"VALID", "FAILED", "INVALID"}:
                if state != "VALID":
                    raise SystemExit(f"Build ended as {state}")
                return
        else:
            print(f"BUILD_MISSING={build_number}")
        time.sleep(30)
    raise SystemExit(f"Timed out waiting for build {build_number}")


if __name__ == "__main__":
    main()
