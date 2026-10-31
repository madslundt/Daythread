# Omi device and iPhone feasibility

Researched 2026-10-16. Target: the purchased [Omi pendant](https://www.omi.me/pages/product) and an iPhone. Source observations below are pinned to upstream commit [`2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae`](https://github.com/BasedHardware/omi/tree/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae); no physical device has been tested.

## Conclusion

A separate native iPhone companion can receive Omi audio and available button events directly over BLE and send only to the user's home-core. This makes cloud-free capture, local STT/LLM, rewind, diary and chat a feasible architecture. BLE itself does not require Omi cloud authentication: upstream's connector reads/subscribes directly to GATT characteristics. **Use configurable single-click and double-click controls; never map click-and-hold to an app action.** Stock hold powers off the device. All-day reliability and the purchased unit's exact firmware remain hardware acceptance tests. [Omi connector](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/app/lib/services/devices/connectors/omi_connection.dart#L337)

The product page advertises offline recording, iPhone support, and Bluetooth + Wi-Fi, but does not establish the shipped board revision, codec, or a supported direct Wi-Fi upload protocol. Current official CV1 hardware documentation explicitly says its Wi-Fi chip is **not enabled in current firmware**. Read device information and capabilities on pairing; use BLE for direct device access. [Purchased product](https://www.omi.me/pages/product), [CV1 hardware](https://docs.omi.me/doc/hardware/OmiConsumer)

## Verified BLE contract

| Purpose | UUID / format |
|---|---|
| Audio service | `19b10000-e8f2-537e-4f6c-d104768a1214` |
| Audio notifications | `19b10001-e8f2-537e-4f6c-d104768a1214` |
| Codec read | `19b10002-e8f2-537e-4f6c-d104768a1214` |
| Button service | `23ba7924-0000-1000-7450-346eac492e92` |
| Button read/notify | `23ba7925-0000-1000-7450-346eac492e92` |
| Haptic service | `cab1ab95-2ea5-4f4d-bb56-874b72cfc984` |
| Haptic write | `cab1ab96-2ea5-4f4d-bb56-874b72cfc984` |
| Battery | Standard `180f` / `2a19` |

Sources: [SDK protocol](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/sdks/device/PROTOCOL.md), [app UUID constants](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/app/lib/services/devices/models.dart#L8).

Current production **Omi CV1** uses codec **21**, Opus at 16 kHz mono, **320 samples / 20 ms** per frame. DevKit's codec **20** uses **160 samples / 10 ms**. Decode both to PCM signed 16-bit little-endian mono at 16 kHz. The codec characteristic is authoritative for a connected unit. The SDK decoder's `960` sample constant is a decode-buffer limit, not a frame duration. General protocol docs still omit codec 21, so prefer current SDK contract and firmware. [SDK contract](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/sdks/device/PROTOCOL.md), [CV1 firmware config](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/omi/firmware/omi/src/lib/core/config.h#L27), [DevKit config](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/omi/firmware/devkit/src/config.h#L24)

Every audio notification begins with a three-byte header: a little-endian 16-bit notification sequence counter, then a fragment index. Index zero starts an audio frame; subsequent indexes continue it. The counter wraps at 65535. Frames may span notifications. Reassemble before decoding, reset partial frames after sequence loss/disconnect, and record capture gaps. Upstream Swift code buffers fragments and flushes when the next index-zero frame arrives; thin SDKs that only strip three bytes should not be assumed to cover fragmentation. Flush the last complete frame when closing a segment. [Protocol framing](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/docs/doc/developer/Protocol.mdx#L103), [Swift reassembly](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/sdks/swift/Sources/omi-lib/helpers/Friend.swift#L69)

## Buttons and hardware differences

The protocol defines `1` single tap, `2` double tap, `3` long tap, `4` pressed and `5` released. Definitions do not prove that each event is emitted by current firmware. Payloads are integer arrays; the official app reads the first four bytes as a little-endian unsigned integer. Do not assume a one-byte payload. [Event definitions](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/omi/firmware/omi/src/lib/core/button.c#L78), [app decoding](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/app/lib/services/capture/capture_controller.dart#L1937)

**Exact current production behavior:** the active firmware state machine emits single/double taps, uses a 300 ms tap threshold and 600 ms double-tap window, and calls `turnoff_all()` after a **3,000 ms hold**. That power-off branch does **not** call the defined `notify_long_tap()` function. The active press branch also does not call `notify_press()`, so an iPhone cannot simply time a down/up pair. A release code alone is not an established duration-bearing long-hold event. The DevKit active state machine has the same power-off conflict. [Production FSM](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/omi/firmware/omi/src/lib/core/button.c#L151), [DevKit FSM](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/omi/firmware/devkit/src/button.c#L159)

Default mappings remain single click → `mark` and double click → `meeting_toggle`. Each click can instead summarize recent audio, answer the latest question, translate recent audio, start/pause capture, or do nothing. Repeat the configured meeting action to end a meeting; phone start/end controls remain available. Recent-audio windows default to 30 seconds and can be configured to 15, 30, 60, 120, or 300 seconds. Persist the click timestamp and selected action parameters for offline delivery. Hold, raw press, and release events are ignored by the app because hold belongs to device power-off; there is no verified-hold mode.

Physical clicks have fixed pendant feedback when the haptic write characteristic is available: single click writes byte `1` (100 ms), and double click writes byte `3` (500 ms), with a BLE write response. This is separate from phone notifications and has no user-selectable duration. The firmware's optional command `2` (300 ms) is unused; `0` is not a remote stop command. Missing haptic support or a failed write must not stop capture. [Official haptic implementation](https://github.com/BasedHardware/omi/blob/main/omi/firmware/omi/src/haptic.c).

**Double-click resolution:** a second short release while `btn_last_tap_time` is pending selects `BUTTON_EVENT_DOUBLE_TAP`, clears that pending timestamp and calls `notify_double_tap()` (code `2`). A recognized double does not also emit the two component single events. However, single resolution checks elapsed time above 300 ms from the first press while released, rather than waiting the full nominal 600 ms double window. A slower attempted double may therefore resolve as separate singles. Test actual timing on the purchased firmware. The native adapter must consume resolved events, produce **one semantic action per gesture**, and never create marks from a recognized double's component taps or from raw down/up events. [Resolution and notification branches](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/omi/firmware/omi/src/lib/core/button.c#L187)

The following are **our backend policies**, not Omi firmware capabilities:

- Accepted important flags have a **60-second cooldown**, regardless of click mapping. Extra flag events during that window do not create more highlights. This policy is separate from hardware gesture resolution and retry deduplication.
- A meeting shorter than **60 seconds is ignored** as a meeting. Its underlying captured audio/transcript remains available for rewind and the daily diary.
- Flags and meeting-button times are **approximate contextual hints**. They guide analysis toward surrounding speech; they do not prove an exact important-moment timestamp or exact meeting boundaries. Keep original event times and source evidence rather than presenting button precision as semantic certainty.

The official app uses single tap for voice questions and configurable double tap for mute/star/end-conversation. Its code-3 handler is explicitly **legacy** long-press support for older firmware, not evidence that current production firmware sends code 3. A custom CoreBluetooth adapter must implement the button characteristic subscription and semantic mapping itself. [Official dispatch and legacy branch](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/app/lib/services/capture/capture_controller.dart#L1978)

| Device | Confirmed distinction |
|---|---|
| Production Omi / CV1 source | Codec 21; button service above. Newer app code probes firmware-dependent ring storage capabilities. |
| DevKit 2 | Separate SD-card download protocol and documented tap events. Recording goes to SD while BLE is disconnected. |
| DevKit 1 | SD-card import is explicitly unsupported. Do not assume DevKit 2 button/storage behavior. |

Sources: [current ring protocol](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/app/lib/services/devices/ring_protocol.dart#L5), [DevKit 2 testing](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/docs/doc/developer/DevKit2Testing.mdx).

Firmware sends taps only when a BLE connection exists. Persistent replay of disconnected button presses is not established by these sources. Offline audio storage does not prove offline gesture storage. The iPhone queue preserves events received while home-core is unreachable; it cannot recover gestures it never received. [Firmware notification functions](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/omi/firmware/omi/src/lib/core/button.c#L118)

## Native iPhone boundary

The native capture path is implemented; physical-device acceptance remains outstanding:

1. CoreBluetooth discovers services, records model/firmware/codec, subscribes to audio and buttons, and maintains state restoration. Use `bluetooth-central` background mode. iOS can still terminate an app; background support is not an uninterrupted-recording guarantee. [Apple background Bluetooth guidance](https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/CoreBluetooth_concepts/CoreBluetoothBackgroundProcessingForIOSApps/PerformingTasksWhileYourAppIsInTheBackground.html)
2. A native serial writer persists short audio chunks plus capture timestamps, sequence-gap metadata and a codec version. The implementation uses an atomic JSON index and append-only original-frame files for durable IDs, delivery order and retry state. Decode/reassemble locally to **PCM WAV, mono 16 kHz**, matching the backend's ingestion boundary.
3. For each resolved gesture, persist one stable event ID and one semantic action. Do not synthesize a mark from a recognized double’s component taps; ignore hold, press, and release events. Retry the same event in device order, preserve meeting state/deadline, and upload the original approximate event time rather than retry time. The backend deduplicates deliveries, applies the 60-second flag cooldown and ignores meetings under 60 seconds. Button hints guide context selection rather than defining exact speech boundaries.
4. Upload chunks and events over the user's trusted LAN or private VPN to home-core. Delete queued audio only after durable server acknowledgement. Keep a local rewind buffer and expose unsynced minutes/gaps. Live server summaries require home-core reachability; queued capture can continue without it.
5. Probe pendant storage as a separate optional adapter. Never send destructive advance/clear commands until bytes are safely persisted. The newer ring protocol has capability flags and acknowledgement semantics; it is not interchangeable with DevKit 2 SD commands. [Ring protocol](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/app/lib/services/devices/ring_protocol.dart)

The web dashboard can control rewind/chat/diary, but a native iOS capture layer is the chosen bridge for background BLE and a durable outbox.

## Official app as an alternative

Do not claim upstream always requires cloud authentication: current source documents local iOS builds using local API plus **Firebase Auth and Firestore emulators**, including a LAN/VPN host setting. An untethered physical iPhone requires a profile/release build. However, `PROVIDER_MODE=offline` uses **fake providers**, not working local speech recognition or summarization. A source build still needs real local model adapters and deployment work. [Current app README](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/app/README.md#L11)

The main hosted product's documented data path stores transcripts, summaries and metadata in Omi's cloud; using a custom transcription key alone is not evidence that every path is local. A separate local app variant is mentioned, with availability left for support confirmation. The stock App Store build's complete cloud-free compatibility has not been verified. [Omi privacy documentation](https://help.omi.me/en/articles/13162549-omi-privacy-policy)

## Physical-device acceptance plan

Use the same timed recording and note the phone/iOS version, pendant model/firmware/codec, backend version, upload preference and storage budget for each run. Compare source duration with elapsed time, play back known speech, and check the final pending-upload count and recorded gaps. Simulator builds and unit tests do not establish physical background reliability.

- Pair the purchased unit; save discovered services, device information, firmware and codec. Confirm the actual frame duration with a timed recording and reject unsupported codecs explicitly.
- Record five minutes; play the resulting WAV and verify duration, intelligibility, monotonic timestamps, fragment loss handling and sequence rollover.
- Log raw events for short taps, double taps at varied intervals, and holds at 0.5, 1, 2 and 3+ seconds. Confirm notifications and power-off behavior. Verify both configurable click mappings and phone controls; a recognized double must produce only its configured action without component flags. Hold, press, and release events must never produce app actions. Test Start / pause recording twice to confirm the BLE button remains usable while capture is paused.
- Test flag events less than 60 seconds apart, then at/after the cooldown; test meetings below and at/above one minute. Confirm short meetings are ignored without deleting their transcript. Check that summaries treat marker/meeting times as approximate context and cite surrounding speech rather than asserting exact boundaries.
- Lock the iPhone for an hour, switch apps, answer a phone call, disconnect/reconnect BLE, restart the app and reboot the phone. Measure capture gaps, battery use and restored subscriptions.
- Disable home-core networking, capture audio/taps, then reconnect. Verify ordered replay, durable acknowledgement, deduplication, meeting auto-stop and no silent queue deletion.
- While offline, pause and resume capture several times, then reconnect while changing the pause setting once more. The newest phone choice must survive synchronization. Interrupt an upload after the server accepts it and verify the retried ID receives its own acknowledgement; a generic successful response must not release local originals.
- Test storage pressure with unsent audio: capture must stop visibly and preserve originals. After freeing space or syncing acknowledged data, resume and verify that the interrupted chunk is recovered without losing its original ID or timestamps.
- Test pendant storage import only after identifying its protocol and firmware; verify import interruption/resume without deleting unread audio.
- Run the complete route with internet disabled but LAN available. Confirm audio, transcripts, recaps, chat and diary stay on the phone/home-core path; inspect outbound connections before calling the system fully local.

## Official API compatibility and community cross-check

Reviewed again 2026-10-16. Treat hosted transcript access and local BLE capture as separate input adapters. The former reads data already held by Omi; the latter can keep new capture on the phone/home-core path.

| Route | Verified contract | Appropriate use |
|---|---|---|
| Personal Developer REST API | `https://api.omi.me/v1/dev/user/conversations`, `Authorization: Bearer omi_dev_…` | Import the owner's existing conversations |
| Integration App API | `/v2/integrations/{app_id}/conversations?uid={user_id}`, app-owned bearer key and user-enabled app | Published multi-user integrations |
| Direct device BLE | GATT UUIDs, codec read and framed notifications above | Capture without hosted Omi API access |

Sources: [Developer API authentication](https://docs.omi.me/api-reference/introduction), [Integration App prerequisites and routes](https://docs.omi.me/doc/developer/apps/Import), [official BLE contract](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/sdks/device/PROTOCOL.md).

For personal import, request `include_transcript=true`; it defaults to false. The list returns a JSON array and supports `limit` (default 25), `offset`, `start_date`, `end_date` and `cursor`. Each conversation carries an upstream ID, `created_at`, nullable `started_at`/`finished_at`, structured summary and optional transcript segments. Preserve the upstream ID for deduplication, convert segment-relative start/end into archive timestamps, and report entries with no transcript instead of inventing speech or audio. Reading this endpoint supplies no audio playback contract. [List conversations](https://docs.omi.me/api-reference/conversations/get-conversations), [single conversation schema](https://docs.omi.me/api-reference/conversations/get-conversation-endpoint)

Pagination must honor `X-Omi-Has-More` and `X-Omi-Next-Offset`. The official backend calculates continuation before filtering locked/invalid conversations, so a short or empty returned page can still have more history. Inferring completion from array length alone can omit older conversations. The connector uses these headers when present and falls back to list length for older servers. [Official read contract](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/backend/utils/external_read_contract.py#L49), [conversation pagination and filtering](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/backend/routers/developer.py#L1483)

Create the personal key in Omi Settings → Developer → Create Key, or the signed-in [web Developers page](https://app.omi.me/developers), with `conversations:read`. MCP keys (`omi_mcp_…`) cannot authenticate the REST API. The official documented rate limits are 100 requests per minute per key and 10,000 per day per user, with `X-RateLimit-*` response headers. Keep the key on the backend, handle authentication/scope/rate-limit failures explicitly, and make hosted import opt-in. [API reference](https://docs.omi.me/api-reference/introduction), [quick start](https://docs.omi.me/doc/developer/api/overview)

For a future optional export adapter, `POST /v1/dev/user/conversations/from-segments` accepts 1–500 transcript segments and supports a stable `client_session_id` so retries return the same conversation ID. It needs write permission and sends transcripts to Omi. This export path is distinct from importing existing conversations. [Create from segments](https://docs.omi.me/api-reference/conversations/create-conversation-from-segments)

Community projects corroborate both approaches and expose compatibility traps:

- [omibutfree Swift BLE service](https://github.com/kbdevs/omibutfree/blob/98b912f4e46f03bcad818646b51c8f10f35563fe/OmiLocal/Sources/Services/BLEService.swift) uses the same audio, codec, battery and button UUIDs. Its [Dart scanner](https://github.com/kbdevs/omibutfree/blob/98b912f4e46f03bcad818646b51c8f10f35563fe/lib/services/ble_service.dart) deliberately scans without the service filter, then matches device names containing `omi`. Foreground discovery should allow name-only advertisements and validate the GATT contract after connection. This is protocol evidence, not permission to copy unlicensed code.
- [omi-whisperx](https://github.com/Rahulsharma0810/omi-whisperx/tree/87f260e65031ce019e118db74c8f91d97bcb5c1f) documents the official phone app's custom-STT WebSocket path plus `/v1/dev/user/conversations/from-segments`. Its topology still writes conversations into Omi cloud. Local STT alone does not establish a fully local archive; its diagram's pendant-to-WebSocket shortcut actually passes through the phone app.
- [Boswell Phone](https://github.com/leakydata/boswell-phone) documents direct BLE capture, local phone inference and optional home-server processing. It is an Android reference, so its restart/background behavior does not establish iOS reliability.
- [Omi Offline](https://github.com/simpleedev/omi-offline/tree/5312582737aebdd063d6436e61cfba10069891d9) is archived and points to memrii. Its custom firmware removes the live audio service and uses a different storage protocol; recognizing the name `Omi` must never imply stock protocol compatibility. Do not flash alternative firmware merely to obtain local access.

The official app also configures a realtime-audio webhook: binary PCM16 little-endian mono via HTTP POST, `sample_rate` and `uid` query parameters, with a configurable buffering interval. This is now an optional implemented input adapter; see the [receiver walkthrough and source audit](official-audio-forwarding.md). The DevKit-focused guide establishes 8/16 kHz DevKit compatibility, not every consumer firmware/app-mode combination. Physical acceptance for the purchased pendant remains pending.

The 2026-10-16 source audit at `35a270d5c422828a709057f5877427c51b56ebd2` found that delivery originates in Omi’s **backend**, with `Idempotency-Key` retries and one-second POST fragments. Its SSRF guard rejects private network destinations. Therefore the earlier description of a phone-app integration must not be interpreted as a phone-direct LAN webhook. Phone VPN access does not give Omi’s hosted server a private route. Capture clocks/sequence/device identity are not included in these HTTP requests; the receiver labels receipt estimates and cannot guarantee continuity, offline replay or perfect deduplication. [Official guide](https://docs.omi.me/doc/developer/apps/AudioStreaming), [sender](https://github.com/BasedHardware/omi/blob/35a270d5c422828a709057f5877427c51b56ebd2/backend/utils/webhooks.py#L509), [network guard](https://github.com/BasedHardware/omi/blob/35a270d5c422828a709057f5877427c51b56ebd2/backend/utils/http_client.py).
