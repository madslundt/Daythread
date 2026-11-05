# Local Nemotron diarization

Daythread includes an optional adapter for NVIDIA Nemotron 3 Diarization through
the official NVIDIA NeMo-Speech.cpp native runtime. The model is anonymous: it
does not assign names or identify the recording owner. The instructions below
cover local staging and a private Linux Compose service; they do not start or
deploy the runtime by themselves.

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

## Remembered voices

To recognize people after assigning their voices in Daythread, install the optional
local TitaNet-Small voice encoder once alongside Nemotron:

```sh
sh scripts/provision_speaker_embeddings.sh /absolute/new/path/to/voice-embeddings
```

Export the two printed assignments, `OMI_SPEAKER_EMBEDDING_MODEL` and
`OMI_SPEAKER_EMBEDDING_SHA256`, in the Nemotron service's environment and restart that
service. The helper downloads the pinned 40 MB ONNX model and verifies its SHA-256.
The isolated runtime pins sherpa-onnx/core 2.0.1 and NumPy 2.5.4. Inference is local;
there are no model downloads or hosted fallbacks during a request. `/health` exposes
`voice_embeddings.configured` and `voice_embeddings.loaded`.

The adapter extracts at most 60 seconds of clear speech per voice, excluding detected
overlap and short boundary windows. At least ten seconds of usable speech is needed.
Unsupported or unavailable embeddings preserve ordinary anonymous speaker detection.
The model/runtime/extraction fingerprint prevents incompatible voiceprints being mixed.

For a private Linux image with the encoder included, provide the provisioned voice
asset directory as the build helper's optional third argument:

```sh
sudo sh scripts/build_nemotron_cpu_image.sh /tmp/nemotron-assets daythread-nemotron-diarization:0.3.0-voices /absolute/path/to/voice-embeddings
```

The one- and two-argument build forms continue to produce anonymous-only images.

## Persistent private Linux x86_64 service

NixOS does not provide the generic Linux dynamic loader required by NVIDIA's
release executable. The official CPU archive runs inside the pinned Debian
container. Provision the assets on the target Linux x86_64 host, then build the
image locally with an explicit, versioned tag:

```sh
sh scripts/provision_nemotron_diarization.sh /tmp/nemotron-assets
sudo sh scripts/build_nemotron_cpu_image.sh /tmp/nemotron-assets daythread-nemotron-diarization:0.3.0-verified
sudo docker image inspect daythread-nemotron-diarization:0.3.0-verified --format '{{.Id}}'
```

`scripts/build_nemotron_cpu_image.sh /tmp/nemotron-assets` creates a minimal,
temporary build context, verifies the archive and model digests, and runs that
build without copying weights into the repository. The Dockerfile pins the
Python base image digest and Python service package versions. It copies in the
verified executable and GGUF; neither the image build nor inference relies on
an implicit model download.

The private Compose overlay is [`config/compose.nemotron.yaml`](../config/compose.nemotron.yaml).
It attaches only to the existing `omi-local_default` network, exposes port 8790
to peer containers without publishing a host port, and sets CPU, memory, PID,
and writable `/tmp` limits. It keeps the read-only root filesystem, dropped
capabilities, non-root image user, and no-new-privileges setting. `pull_policy:
never` prevents an implicit registry pull. The image variable is required; use
the versioned local tag produced above, verify its local image ID, and do not
use `latest`.

Before applying it, confirm the existing backend and network are present:

```sh
docker compose --env-file .env -f config/compose.yaml ps backend
docker network inspect omi-local_default
```

```sh
NEMOTRON_DIARIZATION_IMAGE=daythread-nemotron-diarization:0.3.0-verified \
  docker compose --env-file .env -f config/compose.yaml \
  -f config/compose.nemotron.yaml config --quiet
```

This renders and validates the merged files only; it does not create or start
containers.

Before applying, confirm that the existing backend project is running and owns
the expected private network. Set these values in the existing root `.env` only
when enabling this reviewed deployment:

```dotenv
OMI_DIARIZATION_ENABLED=true
OMI_DIARIZATION_BASE_URL=http://daythread-diarization:8790
OMI_DIARIZATION_TIMEOUT_SECONDS=1800
```

The 1800-second request timeout gives margin over the measured 875-second Linux
CPU run for the 18.7-minute qualification recording; it does not change the
application default. Then start only the new service. Specifying the service
name does not recreate the backend or change its ports, certificate routing,
archive volume, or other Compose services:

```sh
NEMOTRON_DIARIZATION_IMAGE=daythread-nemotron-diarization:0.3.0-verified \
  docker compose --env-file .env -f config/compose.yaml \
  -f config/compose.nemotron.yaml up -d daythread-diarization
NEMOTRON_DIARIZATION_IMAGE=daythread-nemotron-diarization:0.3.0-verified \
  docker compose --env-file .env -f config/compose.yaml \
  -f config/compose.nemotron.yaml ps daythread-diarization
```

The service serializes inference, bounds uploads to eight hours, and runs with
4 CPUs and 4 GiB memory. Its `/tmp` tmpfs is capped at 2300 MiB to hold the
maximum accepted multipart upload and inference copy. The service health check
verifies local model/runtime provenance. A backend can address the service by
Compose DNS name only because both are attached to `omi-local_default`.

For rollback, stop and remove just the optional service, then restore the
previous `OMI_DIARIZATION_*` values in `.env` and recreate only the backend if
those values changed. Do not use `docker compose down` or `down -v` for this
rollback:

```sh
NEMOTRON_DIARIZATION_IMAGE=daythread-nemotron-diarization:0.3.0-verified \
  docker compose --env-file .env -f config/compose.yaml \
  -f config/compose.nemotron.yaml rm --stop --force daythread-diarization
```

If the backend configuration changed, apply the restored values by recreating
only that service:

```sh
docker compose --env-file .env -f config/compose.yaml up -d --force-recreate backend
```

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
