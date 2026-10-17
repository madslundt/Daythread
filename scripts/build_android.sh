#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ -z "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}" && ! -f src/android/local.properties ]]; then
  echo 'Set ANDROID_HOME to your Android SDK, or open src/android in Android Studio first.' >&2
  exit 1
fi

src/android/gradlew -p src/android testDebugUnitTest lintDebug assembleDebug "$@"
mkdir -p dist/android
cp src/android/app/build/outputs/apk/debug/app-debug.apk dist/android/Daythread-debug.apk
echo "Installable APK: $(pwd)/dist/android/Daythread-debug.apk"
