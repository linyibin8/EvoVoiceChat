#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export APP_NAME="${APP_NAME:-EvoVoiceChat}"
export APP_SCHEME="${APP_SCHEME:-EvoVoiceChat}"
export APP_PROJECT="${APP_PROJECT:-EvoVoiceChat.xcodeproj}"
export APP_BUNDLE_ID="${APP_BUNDLE_ID:-com.linyibin8.evovoicechat}"
export APP_VERSION="${APP_VERSION:-0.1.0}"
export APPLE_TEAM_ID="${APPLE_TEAM_ID:-N3G45G5H74}"
export APP_BUILD_NUMBER="${APP_BUILD_NUMBER:-$(date +%Y%m%d%H%M)}"
export SIGNING_CERTIFICATE="${SIGNING_CERTIFICATE:-7DF2CDD786AE3F98BB0C14599BCFEA928A45B376}"
export SIGNING_KEYCHAIN="${SIGNING_KEYCHAIN:-$HOME/Library/Keychains/studylog-build.keychain-db}"
export PROVISIONING_PROFILE_SPECIFIER="${PROVISIONING_PROFILE_SPECIFIER:-evovoicechat_appstore_profile}"

if command -v xcodegen >/dev/null 2>&1; then
  xcodegen generate
elif [[ -x "$HOME/bin/xcodegen" ]]; then
  "$HOME/bin/xcodegen" generate
else
  echo "xcodegen is required" >&2
  exit 1
fi

if [[ -n "${PROVISIONING_PROFILE_PATH:-}" ]]; then
  PROFILE_PATH="$PROVISIONING_PROFILE_PATH"
elif [[ -n "${PROVISIONING_PROFILE_UUID:-}" ]]; then
  PROFILE_PATH="$HOME/Library/MobileDevice/Provisioning Profiles/$PROVISIONING_PROFILE_UUID.mobileprovision"
else
  PROFILE_PATH="$(find "$HOME/Library/MobileDevice/Provisioning Profiles" -name '*.mobileprovision' -print0 \
    | xargs -0 grep -l "$PROVISIONING_PROFILE_SPECIFIER" \
    | while IFS= read -r path; do printf '%s\t%s\n' "$(stat -f %m "$path")" "$path"; done \
    | sort -rn \
    | head -1 \
    | cut -f2-)"
fi

if [[ -z "${PROFILE_PATH:-}" || ! -f "$PROFILE_PATH" ]]; then
  echo "Missing provisioning profile: $PROVISIONING_PROFILE_SPECIFIER" >&2
  exit 1
fi

if [[ -f "$SIGNING_KEYCHAIN" && -n "${SIGNING_KEYCHAIN_PASSWORD:-}" ]]; then
  security unlock-keychain -p "$SIGNING_KEYCHAIN_PASSWORD" "$SIGNING_KEYCHAIN"
  security list-keychains -d user -s "$SIGNING_KEYCHAIN" "$HOME/Library/Keychains/login.keychain-db"
  security default-keychain -s "$SIGNING_KEYCHAIN"
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$SIGNING_KEYCHAIN_PASSWORD" "$SIGNING_KEYCHAIN" >/dev/null
fi

rm -rf "build/$APP_NAME.xcarchive" build/package build/export
mkdir -p build

xcodebuild \
  -project "$APP_PROJECT" \
  -scheme "$APP_SCHEME" \
  -configuration Release \
  -destination generic/platform=iOS \
  -archivePath "build/$APP_NAME.xcarchive" \
  APPLE_TEAM_ID="$APPLE_TEAM_ID" \
  APP_BUNDLE_ID="$APP_BUNDLE_ID" \
  APP_VERSION="$APP_VERSION" \
  APP_BUILD_NUMBER="$APP_BUILD_NUMBER" \
  MARKETING_VERSION="$APP_VERSION" \
  CURRENT_PROJECT_VERSION="$APP_BUILD_NUMBER" \
  CODE_SIGNING_ALLOWED=NO \
  clean archive

APP_PATH="$(find "build/$APP_NAME.xcarchive/Products/Applications" -maxdepth 1 -name '*.app' -print -quit)"
if [[ -z "$APP_PATH" || ! -d "$APP_PATH" ]]; then
  echo "Archive did not produce an app bundle" >&2
  exit 1
fi

cp "$PROFILE_PATH" "$APP_PATH/embedded.mobileprovision"
security cms -D -i "$PROFILE_PATH" > build/profile.plist
/usr/libexec/PlistBuddy -x -c 'Print :Entitlements' build/profile.plist > build/entitlements.plist

if [[ -d "$APP_PATH/Frameworks" ]]; then
  while IFS= read -r -d '' item; do
    /usr/bin/codesign --force --keychain "$SIGNING_KEYCHAIN" --sign "$SIGNING_CERTIFICATE" "$item"
  done < <(find "$APP_PATH/Frameworks" \( -name '*.framework' -o -name '*.dylib' \) -print0)
fi

/usr/bin/codesign \
  --force \
  --keychain "$SIGNING_KEYCHAIN" \
  --sign "$SIGNING_CERTIFICATE" \
  --entitlements build/entitlements.plist \
  --generate-entitlement-der \
  "$APP_PATH"

/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_PATH"

mkdir -p build/package/Payload build/export
ditto "$APP_PATH" "build/package/Payload/$(basename "$APP_PATH")"
if [[ -d "build/$APP_NAME.xcarchive/SwiftSupport" ]]; then
  ditto "build/$APP_NAME.xcarchive/SwiftSupport" "build/package/SwiftSupport"
fi

(cd build/package && /usr/bin/zip -qry "../export/$APP_NAME.ipa" Payload SwiftSupport 2>/dev/null || /usr/bin/zip -qry "../export/$APP_NAME.ipa" Payload)
echo "IPA_PATH=$(pwd)/build/export/$APP_NAME.ipa"
