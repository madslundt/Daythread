# Local Nemotron diarization staging

Daythread includes an optional adapter for NVIDIA Nemotron 3 Diarization through
the official NVIDIA NeMo-Speech.cpp native runtime. The model is anonymous: it
does not assign names or identify the recording owner. This staging setup is
for Apple Silicon macOS CPU evaluation. It does not alter Daythread settings or
deploy the runtime.

## Pinned artifacts

- Model: [`nvidia/Nemotron-3-Diarization`](https://huggingface.co/nvidia/Nemotron-3-Diarization), revision
  `f667ed73aee57d40cc39428eb768b4fd87a0a29e`. The official repository API reports
  `gated=false`; the model card lists `openmdw-1.1`. Review the model card's
  license/terms before use.
- Model GGUF: `Nemotron-3-Diarization.q8_0.gguf`, 107,012,128 bytes,
  SHA-256 `08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1`.
- Runtime: NVIDIA/NeMo-Speech.cpp v0.3.0, source commit
  `20999cc921d2b14aa58708f61756a8b2e787ba20` (Apache-2.0).
- macOS ARM64 CPU runtime archive SHA-256:
  `2c3a7a719b778daeb14f147e011891f16e4b2ac62388d8b8d8b365cc2eebdf15`.

The pinned downloads and checksums are in
[`scripts/provision_nemotron_diarization.sh`](../scripts/provision_nemotron_diarization.sh).
Run it explicitly on an Apple Silicon Mac or Linux x86_64 host, supplying a
new destination:

```sh
sh scripts/provision_nemotron_diarization.sh /absolute/path/to/nemotron-diarization
```

It downloads only from the official NVIDIA GitHub release and NVIDIA's public
Hugging Face model repository, verifies both SHA-256 digests, saves the verified
runtime archive and a provenance record, and prints the two environment
assignments required below. It refuses an existing destination and does not run
automatically at service startup.

## Start the local service

Export the printed paths in the shell where the service runs, then start the
adapter from the repository root:

```sh
export OMI_NEMO_SPEECH_BIN=/absolute/path/to/nemotron-diarization/nemo-speech-0.3.0-macos-aarch64-cpu/bin/nemo-speech
export OMI_NEMO_DIARIZATION_MODEL=/absolute/path/to/nemotron-diarization/Nemotron-3-Diarization.q8_0.gguf
export OMI_DIARIZATION_HOST=127.0.0.1
export OMI_DIARIZATION_PORT=8790
uv run --no-project --python 3.12 scripts/nemotron_diarization_server.py
```

The service validates mono PCM16 16 kHz WAV uploads up to eight hours, serializes
CPU inference, and invokes the CLI with a local GGUF file and offline Hugging
Face flags. It never falls back to a model ID or an implicit weight download.
`GET /health` reports both pinned model/runtime provenance and whether those
local files exist. `POST /diarize` retains the existing API shape and includes
`model_revision` and runtime version metadata. Concurrent speaker intervals
remain concurrent; each model output channel is normalized to an anonymous
`SPEAKER_00`-style ID. An explicit requested speaker count is rejected because
this CLI path does not expose a safe count-forcing option.

## Linux x86_64 container image

NixOS does not provide the generic Linux dynamic loader required by NVIDIA's
release executable. The official CPU archive runs inside the pinned Debian
container below. This also provides a reproducible, no-download-at-request-time
service image. Provision the assets on the target Linux x86_64 host, then build
the image locally:

```sh
sh scripts/provision_nemotron_diarization.sh /tmp/nemotron-assets
sudo sh scripts/build_nemotron_cpu_image.sh /tmp/nemotron-assets
```

`scripts/build_nemotron_cpu_image.sh /tmp/nemotron-assets` creates a minimal,
temporary build context, verifies the archive and model digests, and runs that
build without copying weights into the repository. The Dockerfile pins the
Python base image digest and Python service package versions. It copies in the
verified executable and GGUF; neither the image build nor inference relies on
an implicit model download.

The image listens on port 8790 inside the container. Run it only after review,
with explicit resource bounds and a host binding reachable only where intended:

```sh
sudo docker run --rm --cpus=8 --memory=4g \
  --publish 127.0.0.1:8790:8790 daythread-nemotron-diarization:0.3.0
```

The server serializes inference and caps each request at eight hours. The 8 CPU
and 4 GiB limits are staging values verified on home-core's 14-thread, 46 GiB
host; adjust only after measuring on the target. A backend in another container
cannot call its own `127.0.0.1` to reach this service: configure a private,
host-reachable address and firewall policy separately. This document does not
start the container or change Daythread's saved speaker setting.

## Recording qualification

Use [`scripts/compare_diarization.py`](../scripts/compare_diarization.py) against
a reference JSON of anonymous timed turns. The script compares diarized speaker
activity after a **single global** speaker permutation; it does not relabel each
turn independently. It reports per-speaker activity overlap and boundary
agreement as a vendor-reference comparison, not human-reference DER. Inspect
representative disagreements in the original audio before making quality claims.

Model turn boundaries are thresholded predictions, not transcript timing.
Utterance projection still requires independently aligned canonical word spans;
it leaves words unknown when those spans overlap multiple model tracks or cross
an ambiguous turn boundary.
