# Open-source references for a local Omi diary

Reviewed 2026-10-16 against the original repositories, documentation and license files. These are architectural references, not installed dependencies. The target stays **Omi → native iPhone capture/outbox → home-core STT/LLM → rewind, meetings, diary and cited chat**.

## Shortlist and maintenance evidence

All six repositories were unarchived when checked. The dates below are the newest inspected default-branch commits; they show recent activity, not a promise of support. Links pin the reviewed revisions. Model weights and bundled dependencies have their own terms.

| Project | Inspected commit date | Root license | Best use here |
|---|---|---|---|
| [Omi](https://github.com/BasedHardware/omi/tree/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae) | [2026-10-15](https://github.com/BasedHardware/omi/commit/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae) | [MIT](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/LICENSE) | Device protocol and native capture recovery |
| [WhisperLive](https://github.com/collabora/WhisperLive/tree/f26bdd5294ace7d31020ca00e04e7b14c2b7d954) | [2026-10-07](https://github.com/collabora/WhisperLive/commit/f26bdd5294ace7d31020ca00e04e7b14c2b7d954) | [MIT](https://github.com/collabora/WhisperLive/blob/f26bdd5294ace7d31020ca00e04e7b14c2b7d954/LICENSE) | Phone/server streaming and transcript finalization |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp/tree/0c7be696371069e0baa2515ed7aa08d5a5473aa6) | [2026-10-12](https://github.com/ggml-org/whisper.cpp/commit/0c7be696371069e0baa2515ed7aa08d5a5473aa6) | [MIT](https://github.com/ggml-org/whisper.cpp/blob/0c7be696371069e0baa2515ed7aa08d5a5473aa6/LICENSE) | Optional offline phone transcription |
| [Memos](https://github.com/usememos/memos/tree/42ae2447670fdb010d3ea495fb27a315e0e938c3) | [2026-10-15](https://github.com/usememos/memos/commit/42ae2447670fdb010d3ea495fb27a315e0e938c3) | [MIT](https://github.com/usememos/memos/blob/42ae2447670fdb010d3ea495fb27a315e0e938c3/LICENSE) | Diary timeline, tags and export |
| [Khoj](https://github.com/khoj-ai/khoj/tree/ae229ca894c0b80ad84664afcfdde523b5e87057) | [2026-08-02](https://github.com/khoj-ai/khoj/commit/ae229ca894c0b80ad84664afcfdde523b5e87057) | [AGPL-3.0](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/LICENSE) | Chat over a portable knowledge archive |
| [Hindsight](https://github.com/vectorize-io/hindsight/tree/9a499b1759084a86598eff8af30ed59f7354e76c) | [2026-10-15](https://github.com/vectorize-io/hindsight/commit/9a499b1759084a86598eff8af30ed59f7354e76c) | [MIT](https://github.com/vectorize-io/hindsight/blob/9a499b1759084a86598eff8af30ed59f7354e76c/LICENSE) | Temporal and evidence-backed long-term memory |

## 1. Omi: use the hardware source as the reference

The upstream project includes the wearable firmware, Flutter mobile app, native capture bridges, Swift SDK and backend. Its current app source supports local API/Auth/Firestore emulator builds, but its offline provider mode supplies fake AI providers. A cloud-free production deployment therefore still needs deliberate provider and data-path configuration. [App setup](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/app/README.md)

**Transfer:** characteristic discovery, codec negotiation, packet reassembly, on-phone audio persistence, reconnect/state restoration and capability-gated pendant storage. Study the existing native writer before designing a second capture pipeline. Keep our backend contract smaller: finalized WAV chunks plus durable semantic events. [Connection/capability code](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/app/lib/services/devices/connectors/omi_connection.dart), [SDK protocol](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/sdks/device/PROTOCOL.md)

**Boundary:** use configurable single-click and double-click actions, defaulting to important flags and meeting toggles. Never map click-and-hold to an app action: current production source powers off after three seconds without sending its defined long-tap event, but does emit resolved double-tap code `2`. A recognized double must produce only its configured action and no component flags. Firmware single resolution does not wait the entire nominal double window, so physical timing still needs testing. [Active firmware FSM](https://github.com/BasedHardware/omi/blob/2bfdcda2c3c89d260339707e7e6295a6c5d2a1ae/omi/firmware/omi/src/lib/core/button.c#L187), [detailed device research](device-research.md)

## 2. WhisperLive: separate provisional and committed text

WhisperLive runs speech recognition on a server using local backends such as faster-whisper, TensorRT or OpenVINO. Its iOS example sends phone microphone audio over WebSocket and renders incoming segments. The Python client API distinguishes partial and committed transcript callbacks, and its default connection timeout is 600 seconds. [Server/client README](https://github.com/collabora/WhisperLive/blob/f26bdd5294ace7d31020ca00e04e7b14c2b7d954/README.md), [native iOS example](https://github.com/collabora/WhisperLive/blob/f26bdd5294ace7d31020ca00e04e7b14c2b7d954/Audio-Transcription-iOS/README.md)

**Transfer:** provisional text for immediate rewind, committed segments for diary/chat, explicit end-of-stream handling, reconnect and session rollover. The iOS example is a transport/UI reference, not an Omi BLE collector or a durable offline queue. Its wire format must be adapted; the starter backend currently accepts WAV chunks. Local execution needs downloaded models and appropriate compute; optional model integrations can have additional requirements. Reuse the approach while continuing to use home-core's existing STT service.

## 3. whisper.cpp: keep an optional phone-only escape hatch

whisper.cpp provides local C/C++ Whisper inference, iOS examples, Apple acceleration, quantization and VAD. Its CLI expects 16-bit WAV input; the documented conversion targets mono 16 kHz, matching our ingestion boundary. Models are downloaded separately. [README](https://github.com/ggml-org/whisper.cpp/blob/0c7be696371069e0baa2515ed7aa08d5a5473aa6/README.md), [SwiftUI example](https://github.com/ggml-org/whisper.cpp/tree/0c7be696371069e0baa2515ed7aa08d5a5473aa6/examples/whisper.swiftui)

**Transfer:** a later optional local transcription fallback can make a ten-minute rewind useful when home-core is unreachable. Keep inference behind a provider interface so server STT remains the default. This is an inference engine, not a diary or memory product; it provides neither BLE capture nor meeting semantics. Sustained phone inference needs device-specific latency, heat and battery measurements before enabling it throughout the day.

## 4. Memos: diary entries should stay useful without AI

Memos is a self-hosted Markdown note timeline with tags, daily browsing, pinned items, private notes by default and export. Its official README describes zero telemetry and a Docker deployment with a persistent data volume. It does not provide Omi capture or the STT/LLM pipeline. [README](https://github.com/usememos/memos/blob/42ae2447670fdb010d3ea495fb27a315e0e938c3/README.md)

**Transfer:** represent each day as a readable, editable document; use meeting and important-moment tags; link every summary item to its source interval. Preserve Markdown export and user corrections independently of the search index. Start with daily Markdown files in this project rather than requiring another server. An optional Memos adapter can publish selected diary entries later if the user wants its UI.

## 5. Khoj: portable notes can become a second knowledge interface

Khoj supports chat and search over Markdown and other documents, with a self-hosted web UI and local or hosted models. Its setup documents local OpenAI-compatible endpoints such as Ollama. The default Compose example also includes optional online tools and leaves telemetry-disable configuration commented out; self-hosting alone is not an offline guarantee. [README](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/README.md), [Compose configuration](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/docker-compose.yml), [self-host guide](https://docs.khoj.dev/get-started/setup/)

**Transfer:** export diaries and meeting notes as documents that another local assistant can index. Keep transcript references and dates in those documents so future chat can cite the underlying event. Khoj is an optional consumer of the archive, not the capture backend. Its AGPL license also differs from the MIT components; understand the terms before incorporating its code into a distributed application.

## 6. Hindsight: memory needs time and supporting evidence

Hindsight separates retaining observations, recalling relevant memories and reflecting to answer questions. Its documented retrieval combines semantic, keyword, graph and temporal searches; consolidated observations retain supporting evidence. It supports self-hosting with local LLM providers, but its quick start uses an OpenAI key and some wrappers default to Hindsight Cloud. Select local endpoints explicitly. [README and examples](https://github.com/vectorize-io/hindsight/blob/9a499b1759084a86598eff8af30ed59f7354e76c/README.md)

**Transfer:** preserve an event's actual time, source transcript IDs and the distinction between a quote, an extracted fact and an inferred summary. Later, combine exact-name/keyword search with semantic search and date filters. Evaluate Hindsight as an optional memory adapter after we have real diary data; introducing PostgreSQL/vector search and another inference-driven service at the first milestone would add operating cost without validating wearable capture.

## Screenpipe and smaller Omi alternatives

Screenpipe is relevant to time-based recall and local audio indexing, but **current main is source-available under a commercial license**, including a restriction on competing products. It does not belong in a shortlist of current, freely reusable open-source projects. The license says older MIT releases retain their terms, so any historic reference must pin and verify that exact revision. [Current license](https://github.com/screenpipe/screenpipe/blob/ada8f2fab2f78ea6a9ac8d59f97c932063418a24/LICENSE.md)

`kbdevs/omibutfree` and `Rahulsharma0810/omi-whisperx` are relevant experiments, but no root license was identified in the inspected repository metadata. Do not treat visible source as permission to copy it. The latter's documented route also uses an Omi API key and memory creation, which does not establish a cloud-free diary. [omibutfree](https://github.com/kbdevs/omibutfree/tree/98b912f4e46f03bcad818646b51c8f10f35563fe), [omi-whisperx](https://github.com/Rahulsharma0810/omi-whisperx/tree/87f260e65031ce019e118db74c8f91d97bcb5c1f)

## Recommendation for this project

Use Omi as the protocol reference, WhisperLive as the transcript-lifecycle reference and Memos as the diary UX reference. Keep existing home-core inference services and the small local backend. Export Markdown with source references now, allowing Khoj or Hindsight to consume it later without migrating the capture system. Add phone inference only when offline rewind requirements justify it. First validate BLE/background capture, single/double-click resolution, and power-off behavior on the purchased unit; software-only memory demos cannot settle those questions.

Keep user policies above the hardware layer: one semantic action per resolved gesture; a 60-second cooldown between accepted important flags regardless of click mapping; meetings under one minute ignored as meetings, with the underlying capture preserved. Both flags and meeting times are approximate contextual hints for surrounding speech. They should help retrieval and summarization without becoming false claims of exact event or meeting boundaries. These are this project's requirements, not inherited behavior promised by any reference repository.
