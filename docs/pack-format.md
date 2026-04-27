# `.memee` pack format

A `.memee` pack is a portable, signed bundle of institutional memory.
One file. Versioned. Distributable.

This is the spec the v2.0.0 export/install path is built against.

## File layout

A `.memee` file is a gzipped tarball with this structure:

```
foo.memee/                 (gzip(tar(...)))
├── manifest.toml          required — see "Manifest" below
├── memories.jsonl         required — one memory per line
├── signature.bin          optional — ed25519 signature over (manifest||memories)
└── pubkey.pem             optional — author public key (or fingerprint)
```

`memories.jsonl` is **append-friendly**. Each line is a self-contained JSON
object that maps to a row in the receiver's `memories` table. The order of
lines doesn't matter for correctness; importers should dedup by fingerprint.

## Manifest

`manifest.toml` declares pack identity, scope, and integrity hints:

```toml
name = "python-web"            # short, slugified, unique within an author
version = "0.1.0"              # semver
title = "Python web canon"
description = "..."
author = "..."
homepage = "..."
license = "MIT"                # SPDX identifier
created = "2026-04-27"         # ISO date

confidence_cap = 0.6           # every memory caps at this on import
stack = ["python", "fastapi"]  # informational; surfaced in `memee pack search`

[counts]
memories = 30
patterns = 16
anti_patterns = 11
decisions = 3
lessons = 2

# Optional. Multiple [[provenance]] tables describe lineage.
[[provenance]]
kind = "seed" | "exported" | "merged"
note = "..."
project = "..."          # for kind=exported
exported_at = "2026-..."
```

Required fields: `name`, `version`, `title`, `confidence_cap`. Everything
else is optional but expected for public packs.

## Memory line format

Each line of `memories.jsonl` is a JSON object. Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `type` | `"pattern"` `"anti_pattern"` `"decision"` `"lesson"` `"observation"` | yes | maps to `memories.type` |
| `title` | string, ≤500 chars | yes | first line of the memory |
| `content` | string | yes | the body. Multi-line OK. ≥15 chars |
| `tags` | string array | yes | at least one. lowercased on import |
| `maturity` | `"hypothesis"` `"tested"` `"validated"` `"canon"` | no | default `"validated"` for hand-authored packs |
| `confidence` | float in [0, 1] | no | default 0.5; capped at `confidence_cap` on import |
| `severity` | `"low"` `"medium"` `"high"` `"critical"` | no | only meaningful on `anti_pattern` |
| `summary` | string | no | precomputed short form |
| `evidence_chain` | array | no | upstream provenance entries |

## Signing (optional but recommended)

Authors with a key sign the pack. The signature covers
`SHA256(manifest.toml || memories.jsonl)` with ed25519. Importers verify
against the bundled `pubkey.pem` and warn loudly when the signature is
missing or invalid.

Distribution model for v2.0.0:

- A pack hosted on `memee.eu/packs/<name>.memee` carries the maintainer
  signature. `memee pack install --from-url <https-url>` verifies on
  download.
- Packs from arbitrary URLs / files install with `--unsigned` flag and
  print a yellow banner.
- `memee pack verify foo.memee` checks the signature without importing.

There is no central authority and no key revocation list. This is a
self-published format; trust is per-key, not per-registry.

## Import semantics

`memee pack install foo.memee` does:

1. Verify signature (or warn if absent / unsigned flag).
2. Read manifest. Check name + version against already-installed packs.
   If same `(name, version)` already imported, no-op (idempotent). If
   same name, different version, prompt to upgrade or import alongside.
3. Stream `memories.jsonl`. For each row:
   a. Run the existing quality gate (`run_quality_gate`) so the same
      validation rules apply to imports as to user records.
   b. On dedup hit, fold the pack memory into the existing row via
      `merge_duplicate` and tag it with `source_type=import` so the
      source multiplier (×0.6) applies.
   c. On new memory, insert with `source_type=import`, confidence
      capped at `confidence_cap`, `evidence_chain` includes a
      `{"kind": "pack", "name": "...", "version": "..."}` entry.
4. Record one `Pack` row in storage (or a JSON ledger; v2 starts with
   the JSON ledger to avoid a schema migration).

User-validated memories ALWAYS outrank pack defaults. The
`confidence_cap` and the `source_type=import` multiplier ensure that.

## Export semantics

`memee pack export --canon-only > foo.memee` does:

1. Pick memories with `maturity in ("validated", "canon")` and
   `confidence >= 0.7`. Future flags can broaden this.
2. Strip identity columns (`owner_id`, `team_id`, `organization_id`,
   `validated_project_ids`, `same_project_val_counts`,
   `model_families_seen`, `source_session`, `source_url`,
   `source_commit`). Pack memories are public; provenance lives in the
   pack manifest, not the per-memory columns.
3. Write `manifest.toml` with computed counts.
4. Sign if a private key is configured (`MEMEE_PACK_KEY` env or
   `~/.memee/keys/pack.ed25519`).
5. Tar + gzip.

## Anti-goals

- **Not** a sync protocol. `memee pack` is one-shot import/export.
- **Not** a marketplace. Distribution is whatever you want — GitHub
  releases, an S3 bucket, `memee.eu/packs`, an internal artefact
  store. The format outlives the registry.
- **Not** a search index. Packs are atoms, not a network.

## Why this shape

Packs are the answer to two problems Memee otherwise can't solve:

1. **Cold start.** A fresh `memee setup` has zero canon. The dogfooding
   theory (record-as-you-go) takes weeks. A pack collapses that to one
   command.
2. **Knowledge portability.** Today an org's canon dies with the laptop
   it's installed on. `memee pack export` makes it backupable, shareable,
   handover-able, audit-able.

The format is the first-class artefact. Tools come and go; formats outlive
products.
