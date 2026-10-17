# Daythread for Android

An installable Android companion for Android 8.0+ (API 26), targeting Android 16 (API 36).
It connects to the same private FastAPI backend as the iPhone app. The native shell embeds
Daythread's responsive web interface, so Today, Rewind, Diary, Chat, People, Inbox, decisions,
transcript correction, imports, and server settings use the existing implementation and API.
Native capture works independently of the web page, including on trusted LAN HTTP addresses.

## Install and connect

1. Build the debug APK as described below, then install `dist/android/Daythread-debug.apk`
   on the phone (allow installation from the app used to open the APK), or use
   `adb install -r dist/android/Daythread-debug.apk`.
2. In **Connection**, enter the backend origin, for example `https://daythread.home.arpa`
   or `http://192.168.1.20:8787`. Optional fallback origins go on separate lines, in order.
   Every fallback must serve the same archive. Tap **Save & test connection** and choose
   a capture profile. The server must already have its web interface installed.
3. Open **Archive** for the complete shared interface. Its profile picker remains in
   **Settings → Profiles**; the native **Capture profile** is shown separately and controls
   where recordings from this phone are sent.
4. In **Capture**, choose **Record with phone** or **Connect Omi pendant** and grant the
   requested permissions. Select the pendant from the discovery list. Stop from the app
   or its ongoing notification. Capture continues when switching screens or locking the phone.

The backend remains private: use your LAN or private VPN. HTTP is unencrypted; HTTPS uses
normal Android certificate validation. No TLS bypass or public ingress is installed.
This native capture transport supports the private backend; it does not implement managed
account bearer-token authentication. Server/provider API keys remain on the backend.

**Capture uploads automatically.** Completed 30-second chunks enter the durable queue;
connectivity and unmetered-network preferences control delivery. **Upload now** retries the
queue. **Listen to saved audio** plays up to 20 recent chunks still waiting for confirmation.
The archive interface's browser recorder is separate; use native Capture for background recording.

## Build

Install JDK 17+, Android SDK command-line tools or Android Studio, SDK Platform 36,
Build Tools 36.0.0, and accept the SDK licenses. With the SDK installed:

```sh
export ANDROID_HOME="$HOME/Library/Android/sdk" # macOS; use your actual SDK directory
# Set JAVA_HOME to a JDK 17+ installation if needed.
bash scripts/build_android.sh
```

Or open `src/android` in Android Studio and run the `app` configuration. For an emulator,
`http://10.0.2.2:8787` reaches a backend bound to the development computer's loopback address.
Phones need its LAN/VPN address, not `localhost` or `10.0.2.2`.

The checked-in Gradle 8.13 wrapper verifies its distribution SHA-256. The Android Gradle
plugin is pinned to 8.13.2. Concentus 1.0.2 provides a Java Opus decoder for every Android ABI;
no NDK installation is needed. Licenses are available under **Connection → Open-source licenses**.
The debug APK is for direct installation; production/store distribution needs your own signing setup.

## Capture and recovery contract

- Native `AudioRecord` captures mono PCM16 at 16 kHz. Original start times use a wall-clock
  anchor plus monotonic time; chunk durations come from actual sample counts.
- A foreground service declares only the active microphone or connected-device type, holds
  a capture wake lock, and exposes a persistent Stop action. Permissions are requested from
  the foreground. It does not restart recording silently after Android kills the process.
- One private file journal per chunk allocates its UUID, endpoint list, device ID, capture
  timestamp, and profile before audio arrives. PCM writes reach the OS immediately and sync
  to disk at least every second; final audio and manifests sync before an atomic rename.
  Process restarts recover complete samples from unfinished chunks and queue an explicit
  interruption gap up to the restart time. Device power loss can
  lose recently written data. Pending audio is excluded from Android backup/transfer.
- Uploads use stable UUIDs and identical WAV bytes on every retry. They check response profile,
  item ID, device, capture bounds, state, and event policies before removing local originals.
  A gateway/network failure tries the captured fallback list; HTTP rejection and redirects do
  not fall through. Editing Connection cannot retarget existing queued audio.
- `JobScheduler` retries after connectivity returns, with persisted jobs and exponential
  backoff. While capturing or opening the app, uploads also run immediately. Android controls
  background job timing; force-stopping the app delays work until it is reopened.
- Local storage has a 2 GB budget and stops safely without evicting unsent audio. Clearing app
  data or uninstalling removes local recordings. Server acknowledgement releases local WAV
  and compressed frames; this is a delivery queue, not a second full offline archive.
- Omi discovery accepts the documented audio service or Omi names, then verifies GATT audio
  and readable codec characteristics. Reads and notification subscriptions are serialized.
  Supported codecs match iPhone: PCM16 `0`, Opus `20` (10 ms), and Opus `21` (20 ms).
  Unsupported codecs stop capture. Raw length-prefixed Opus/PCM frames remain alongside the
  queued WAV until confirmation. Notification sequence wrap, duplicates, fragmentation,
  loss, and reconnects are handled; gaps split chunks instead of joining missing speech.
- Single click records a raw important-moment hint; double click toggles a raw meeting hint.
  On-screen hint controls work during capture. The backend applies its cooldown and meeting
  minimum to the original event times. Policy values cached at connection setup are captured
  with each event; reconnect/test after changing server hint policies. Stock long-hold remains
  a power gesture; Android does not infer a meeting from it.

The archive uses its existing translations; the native shell follows the phone's English or
Danish language. Native capture does not yet attach phone GPS observations or create system geofences.
Apple Reminders/EventKit editors remain iPhone-only; Android can save the
shared interface's portable calendar/Markdown/JSON exports through the system file picker.
The WebView has no JavaScript/native bridge, file URL access, third-party cookies, or mixed
content; external navigation opens the system browser. File import uses Android's document
picker. Native exports are bounded to 25 MB; larger archive downloads use a browser.

## Validation

```sh
cd src/android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

JVM tests exercise packet reassembly/loss/wrap, actual Opus decoding and codec-duration
validation, atomic queue recovery, active-writer isolation, capture-time receipts, corrupted
metadata, endpoint validation, retry stability, and HTTP fallback/redirect/rejection behavior.

Validated on a Pixel 7 Android 16 emulator against a temporary real backend: connection/profile
setup, shared archive loading, foreground microphone recording, recording while backgrounded
and offline, force-stop recovery, subsequent receipt-checked uploads, and normal Stop. All 15
JVM tests and Android lint passed; the debug APK signature was verified. These checks used no
user archive or model service.

Physical acceptance is still required: verify pendant audio and double-click behavior on the
purchased firmware; background/endurance and battery behavior on representative Android vendors;
permission denial, calls and microphone contention; Bluetooth disable/reconnect; reboot/process
kill; network loss, fallback and unmetered policy; storage exhaustion; and playback after recovery.
Emulator checks do not establish physical Omi or all-day capture reliability. See the existing
[device evidence](../../docs/device-research.md) for the shared wire protocol and hardware limits.

## Microphone controls and schedules

Capture includes **Microphone enabled**, a device-local weekday/time schedule, and a choice
of phone or Omi input. Arming **Recording schedule** / **Follow schedule** starts the foreground
service with the normal permissions. During scheduled hours it records automatically; outside
those hours it releases the phone microphone or closes the pendant connection, keeping the
service notification available for the next window. Android may delay a start while the device
is idle; the waiting service uses alarms instead of keeping the CPU awake between windows. Stop times are exclusive. An overnight
window belongs to its starting weekday, in the phone's current time zone. Equal times or no
selected days allow no scheduled capture.

**Record with phone**, **Connect Omi pendant**, and enabling the microphone explicitly override
the schedule, including outside its hours. Manual off / the notification's Stop action prevents
further capture. These choices persist until **Follow schedule** restores automatic control.
Previously saved audio stays queued for upload; off does not delete it. Stop the current service
before changing its scheduled input. Device shutdown, force-stop, service termination, or a
permission/capture error requires reopening the app and following the schedule again. No reboot
receiver or hidden microphone restart is installed. Schedules and overrides stay on this phone;
they do not change the backend's settings or another device's capture policy.
