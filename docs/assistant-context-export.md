# Assistant context export

Daythread provides an optional, read-only export for the HomeCompute personal
assistant. It is disabled unless `OMI_ASSISTANT_CONTEXT_ENABLED=true` is set in
the Daythread service environment. The routes are `GET
/v1/integrations/assistant-context` and the bounded known-ID resolver at `POST
/v1/integrations/assistant-context/resolve`.

## Enablement

Provision these environment values through the service's private deployment
configuration; do not place the token in Daythread's runtime settings, UI
configuration, or repository:

- `OMI_ASSISTANT_CONTEXT_ENABLED=true`
- `OMI_ASSISTANT_CONTEXT_PROFILE_ID=default` or one existing profile UUID
- `OMI_ASSISTANT_CONTEXT_DOMAIN=personal` (explicit admission for this connector)
- `OMI_ASSISTANT_CONTEXT_TOKEN_FILE=/run/secrets/daythread-assistant-context-token`

On home-core, `config/compose.assistant-context.yaml` mounts
`/etc/homecompute/daythread-assistant-context-token` at
`/run/secrets/daythread-assistant-context-token`. Provision the mounted source
outside Git as a regular, single-link file owned by container UID `10001`, mode
`0600`, containing a random URL-safe token of at least 40 characters. The
collector uses a separate owner-only copy of the same independently generated
credential. Keep both copies outside Git and do not print either file. The
credential is bound to the one selected profile. Requests carrying
`X-Omi-Profile` or `profile_id` are rejected; the request cannot select a
profile. If the configured profile is unavailable, the endpoint returns 404 and
does not fall back to another profile.

The owner confirmed the `default` profile (`Mads`) contains personal/household
data only. The home-core overlay fixes that profile and the `personal` domain;
requests cannot select another profile. Daythread profiles store only an ID and
display name, so this admission relies on the explicit owner classification. A
profile containing employer recordings must not be connected to the personal
assistant. Work data requires the separate principal, domain and approval
process in HomeCompute's personal-data contract; profile binding alone does not
classify mixed recordings or grant work-data admission.

The guarded home-core helper owns credential provisioning and provider rollout:

```sh
uv run python scripts/deploy_assistant_context_homecore.py credentials-plan
uv run python scripts/deploy_assistant_context_homecore.py credentials-apply
uv run python scripts/deploy_assistant_context_homecore.py ca-plan
uv run python scripts/deploy_assistant_context_homecore.py ca-apply
uv run python scripts/deploy_assistant_context_homecore.py plan
uv run python scripts/deploy_assistant_context_homecore.py stage
uv run python scripts/deploy_assistant_context_homecore.py deploy
```

Credential apply creates one external random token only when both copies are
absent, or verifies the existing pair without printing either value. It refuses
partial or inconsistent copies. The provider rollout creates a validated
archive backup, adds only the assistant API files atop the pinned current
Daythread image, and verifies the transcription module hashes remain unchanged.
The CA helper copies only the public root certificate from the active managed
Caddy data volume. It checks the reviewed SHA-256 fingerprint and certificate
identity before installing `/etc/homecompute/daythread-assistant-context-ca.crt`
as root-owned mode `0444`; it never reads or copies a private key. HTTPS checks
use only this explicit CA file, bypass proxies, and never weaken TLS validation.
The current reviewed root is `Caddy Local Authority - 2026 ECC Root`,
fingerprint `279cbaa0596ae3ca24c44da28ac658c557e3c2a8664f81d8d3c8d034c0753a56`.
Roll back
with the exact release name printed by `plan` or `deploy`:

```sh
uv run python scripts/deploy_assistant_context_homecore.py rollback --release 20261106-assistant-context-RELEASE_HASH
```

The endpoint returns only existing saved diary summary versions and explicit
commitment decisions in `proposed` or `confirmed` review state. It does not
create summaries, expose audio or transcript quotations, or write to Daythread.
For diary summaries, it rechecks each recorded source reference against current
stored source versions and omits saved versions whose source is missing or has
changed. This protects against historical recap versions retained after a
transcript correction; it does not create a source-change or deletion feed.
Each item includes a stable object ID, version reference, source timestamp, and
bounded payload. Pages are ordered by modification time and stable tie-breakers;
the signed opaque cursor pins the profile and snapshot time. Page size is at most
20 and the source window is 30 days. The current route returns `Cache-Control:
no-store` and does not log payloads or authorization values.
It is a separate opt-in route; existing profile-selected routes keep their
current behavior.

`coverage.window_complete` is true only when the bounded source window was read
without reaching the record cap, cursor continuation, or a detected concurrent
write. `coverage.deletion_complete` remains false: a missing item on a page or
a missing page never implies deletion. Separately, the resolver accepts at most
20 previously observed object IDs per request and returns one status for each:
`valid` with its current evidence-checked saved item, `invalidated` when the
underlying saved version remains but its evidence is no longer current,
`expired-window` when the saved version is older than the 30-day export window,
or `deleted` when the source object no longer exists. Resolver responses are
bound to the same token-fixed profile, require the exact object-ID request
shape, and do not accept profile or domain selectors. If the resolver is
unavailable or partial, HomeCompute must preserve local records and retry.
Only explicit non-valid statuses for IDs already held by its revision ledger
can create tombstones.
The export is a recent snapshot, not a durable event stream; a long or changing
snapshot can be incomplete and should be retried from its first page.
Concurrent source edits between pages may also make the cursor snapshot
inconsistent; consumers should rely on `snapshot_consistent` and retry when it
is false.

HomeCompute must use a fixed operator-configured HTTPS hostname and an
externally provisioned token file. No model or request may choose the host,
profile, or credential. The HomeCompute client accepts only
`daythread.home.arpa` or `daythread.<tailnet-label>.ts.net`, disables proxy use
and redirects, and bounds each response. On home-core, project the approved
public Caddy root certificate to the source-owned connector CA setting; the
connector does not accept an arbitrary CA path. Keep the endpoint on a trusted
private network and disabled until its source configuration is deployed.

The 30-day snapshot retains only the latest diary version per day and current
eligible commitment versions modified in that window. The resolver makes
retention and deletion changes discoverable for known IDs without treating a
partial or moving snapshot as a delete feed. It does not enumerate IDs the
collector has never observed.
