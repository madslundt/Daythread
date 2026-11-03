# Local word alignment

Alignment is an optional, local forced-alignment pass after speech recognition. It takes the exact recognized text and the same original audio chunk that produced it. It does not run recognition, rewrite text, ask a hosted service, or use Whisper timestamp decoding. Recognition remains successful if alignment is disabled, unavailable, incomplete, or times out.

## Runtime and weights

The sidecar is separate from the Python 3.15 backend and defaults to CPU. Run it on a home-core CPU or a private staging Mac; do not load it on Spark's constrained GPU by default. It accepts one 16 kHz mono PCM16 WAV of at most 30 seconds and 20,000 transcript characters. It serializes model execution, loads lazily, has no request logging, and exposes `/health`. Requests use in-memory WAV data; multipart temporary files are closed by Starlette. A direct script launch binds to loopback; the container binds only within the private Docker network and publishes no host port. The service has no authentication and must stay on a trusted host/network.

The Danish safetensors snapshot is about 602 MB. On this Apple Silicon staging Mac, first model load plus one 30-second CPU request took about 6 seconds and peaked at 3.2 GB RSS; warmed requests took about 2.2 seconds with two CPU threads. Expect host-specific differences and leave several GB of RAM available for Python, Torch, and model buffers.

The runtime pins WhisperX 3.3.1, Torch/Torchaudio 2.8.0, Transformers 4.48.3, Tokenizers 0.21.1 and Hugging Face Hub 0.36.0, and accepts only pre-provisioned local model directories:

- Danish: [`CoRal-project/roest-v3-wav2vec2-315m`](https://huggingface.co/CoRal-project/roest-v3-wav2vec2-315m), snapshot `beb3e790246d6b9dec1df596b0b21d5c42f4d99c`. The publisher describes this as an ASR model trained on Danish conversation and read-aloud data and provides a custom OpenRAIL-M-based license with stated restrictions on speech synthesis and biometric identification. Review the [model card and pinned license](https://huggingface.co/CoRal-project/roest-v3-wav2vec2-315m/blob/beb3e790246d6b9dec1df596b0b21d5c42f4d99c/LICENSE) for your use. This sidecar only performs local alignment; it does not synthesize speech or perform speaker identity recognition. Its reported 13.7% conversational CER is ASR evaluation, not alignment accuracy.
- English: [`facebook/wav2vec2-base-960h`](https://huggingface.co/facebook/wav2vec2-base-960h), snapshot `22aad52d435eb6dbaf354bdad9b0da84ce7d6156` ([Apache-2.0](https://huggingface.co/facebook/wav2vec2-base-960h/blob/22aad52d435eb6dbaf354bdad9b0da84ce7d6156/LICENSE)).
- WhisperX 3.3.1 is [BSD-2-Clause](https://github.com/m-bain/whisperX/blob/v3.3.1/LICENSE). The standalone script pins its direct runtime dependencies. The container uses a resolved Linux x86_64 lockfile in `scripts/alignment-requirements.lock`.

Provision snapshots explicitly on a networked machine with `huggingface_hub` installed:

```sh
python3 -c 'from huggingface_hub import snapshot_download; snapshot_download(repo_id="CoRal-project/roest-v3-wav2vec2-315m", revision="beb3e790246d6b9dec1df596b0b21d5c42f4d99c", local_dir="/models/daythread/da", allow_patterns=["*.json", "*.safetensors"])'
python3 -c 'from huggingface_hub import snapshot_download; snapshot_download(repo_id="facebook/wav2vec2-base-960h", revision="22aad52d435eb6dbaf354bdad9b0da84ce7d6156", local_dir="/models/daythread/en", allow_patterns=["*.json", "*.safetensors"])'
```

Copy each completed directory to the private host, then set `OMI_ALIGNMENT_DA_MODEL_DIR` and/or `OMI_ALIGNMENT_EN_MODEL_DIR` to those directories. The service refuses to fetch weights on a request and sets Hugging Face offline mode before loading. Do not point these settings at writable shared caches. No credentials or model access token are needed for the listed public repos.

Install Python 3.12, then start the service from the repository root:

```sh
uv run --no-project --python 3.12 scripts/alignment_server.py
```

### Home-core CPU container

The optional `config/compose.alignment.yaml` overlay adds one CPU-only, Danish-only sidecar to
the existing private `omi-local_default` Docker network. It publishes no host
port; the backend can reach it internally at `http://alignment:8791`. The overlay
uses a read-only model bind mount, disables Hugging Face network access, drops
all Linux capabilities, and caps the process at two CPUs and 5 GiB of RAM. The
image is based on `python:3.12.13-slim-trixie` manifest digest
`sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36`;
Torch and Torchaudio are pinned to `2.8.0+cpu` from the official CPU wheel
index. All 100 resolved Python packages are pinned in
`scripts/alignment-requirements.lock`; direct versions match the PEP 723 script
dependencies, with `+cpu` added to the Torch wheel versions. The Danish weight
artifact in the provisioned snapshot has SHA-256
`0f3a84b76f9fdffdf57776a5166b71ef828997b62cca764a3f4f5783b22316ef`.
Build the image with Docker from the repository root:

```sh
docker compose -f config/compose.yaml -f config/compose.alignment.yaml \
  build alignment
```

After copying the pinned Danish snapshot to
`/opt/daythread-alignment/models/roest-da-beb3e790246d6b9dec1df596b0b21d5c42f4d99c`,
the optional sidecar can be started explicitly with:

```sh
docker compose -p omi-local -f config/compose.yaml \
  -f config/compose.alignment.yaml --profile alignment up -d alignment
```

Do not enable the backend alignment setting until timing quality has passed
independent checks. Starting the sidecar alone does not change backend settings.
The compose healthcheck verifies that the HTTP service sees a configured,
revision-pinned model path; `/health` reports `ready=false` until the first model
load. A local alignment request is needed to verify model readiness.
English weights are not mounted by this profile; provision and mount the pinned
English snapshot separately before selecting English alignment.

The pinned image was built and exercised on home-core. After loading the Danish
model, Docker reported about 1.53 GiB in use under the 5 GiB memory cap. A one
second synthetic tone request worked with the container on `--network none`;
this validates offline loading and the HTTP contract only, not timing accuracy.

For a containerized backend, set `OMI_ALIGNMENT_BASE_URL` to the sidecar's private Docker address (`http://alignment:8791`). Enable `OMI_ALIGNMENT_ENABLED` only after timing quality is qualified on representative speech. `OMI_ALIGNMENT_LANGUAGE` selects one acoustic model (`da` or `en`); mixed-language chunks can be incomplete or mistimed and should be reviewed. The alignment status and model revision are stored with transcription processing details.

## Contract

`POST /align` accepts multipart `file` (PCM16 WAV), `text` (exact recognized text), and `language` (`da` or `en`). It returns `status`, `language`, `model`, and `segments`. Each segment carries request-local `start`, `end`, exact transcript substring `text`, and character offsets `start_offset`/`end_offset` into the submitted text. `aligned` means every whitespace-delimited transcript fragment received a valid model-path time; it is not a calibrated accuracy score. Forced alignment may assign a path to text that was not spoken. `partial` returns only independently timed fragments; it does not fill gaps. `unavailable` means the aligner could not run. Invalid or overlapping times are rejected by the backend. Real-audio checks found that full text coverage can coexist with word-time drift, especially when the input transcript omits or misrecognizes speech. Timing accuracy remains unqualified; keep this feature disabled until independent phrase or word timing checks pass. The backend adds the source-local offset of the filtered audio span and rebases character anchors into the final canonical transcript after chunk texts are joined with newlines. Unaligned transcript characters stay present in the canonical transcript and have no timing.

Filtered silent gaps are not concatenated: every alignment request uses one original audio chunk with its original source offset. The stage does not change recognition retry identities or the recognition model. Alignment is disabled by default.
