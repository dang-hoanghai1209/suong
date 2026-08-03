# Semantic object library ingestion

This opt-in package builds a local object library for Tella's minimalist emotional compositor. Iconify is queried first; Noun Project is the authenticated fallback. Existing asset-library V2 manifests and scenic composition remain unchanged.

## Architecture and storage

`tella.object_library.sources` contains provider adapters. Both emit the same `SourceCandidate` model. `ObjectIngestionService` downloads candidates into an `ObjectStore`; `processor` preserves raw files, sanitizes SVG, rasterizes it, removes invisible PNG edge RGB, trims excess padding, and creates transparent processed PNGs and previews. `registry` atomically writes the master/source manifests and a semantic inverted index. `ObjectRegistry.search()` returns scored, structured results for composition.

The default generated root is `./object_library_data` and is ignored by Git:

```text
object_library_data/
  raw/{source}/              untouched downloads
  processed/{source}/        normalized SVG plus compositor-ready PNG
  previews/{source}/         256px PNG previews
  records/                   one durable JSON record per object
  manifests/
    object_manifest.json     master registry
    iconify.json             source manifest
    noun_project.json        source manifest
    local.json               source manifest
    semantic_index.json      searchable token index
```

Set `TELLA_OBJECT_LIBRARY_ROOT` to put generated data elsewhere. When this variable is set, the existing V2 semantic resolver can fall back to an eligible PNG from the new registry after its existing object index misses. This is the only integration point and keeps current flows opt-in.

## Credentials and provider behavior

Iconify's public API requires no credentials. Its search response supplies icon-set license metadata; review each selected set's license before distribution.

Noun Project requires OAuth credentials:

```powershell
$env:NOUN_PROJECT_KEY = "..."
$env:NOUN_PROJECT_SECRET = "..."
```

The adapter OAuth-signs API requests. It defaults to the documented `/v2/icon?query=...` search endpoint; `NOUN_PROJECT_API_URL` and `NOUN_PROJECT_SEARCH_PATH` (including `{query}` templates) are configurable so an account can target its enabled API version without code changes. Returned CDN asset URLs are fetched directly because they are temporary signed URLs. Missing credentials produce an explicit error. Attribution fields are retained in every object record and must be honored in published output.

## Commands

Install the project (`pip install -e .`) to get `tella-objects`, or replace it with `python -m tella.object_library`:

```powershell
tella-objects search phone --source iconify --limit 20
tella-objects ingest phone --source iconify --count 8
tella-objects ingest letter --source noun_project --count 4
tella-objects process
tella-objects build-registry
tella-objects lookup phone --mood waiting --context bedroom
tella-objects lookup "" --category comfort --source iconify
tella-objects contact-sheet --output out/object-qc.png
```

With `--source all`, Iconify is called first and Noun Project only fills remaining requested slots. Provider failures are isolated; the workflow only fails when no provider returns candidates. Use `--no-process` for download-only staging.

## Taxonomy and selection

The first-pass taxonomy is intentionally practical: communication, self-care, memory, comfort, room, outdoor, cafe, travel/waiting, and emotional-symbol groups. It enriches labels with scene contexts and moods including sadness, loneliness, waiting, overthinking, healing, reflection, acceptance, daily solitude, and quiet comfort.

Local lookup filters rejected/review assets by default, then ranks exact and semantic matches, requested mood/context, approval, and source preference (Iconify before Noun Project). Results include the object record, numeric score, and human-readable reasons. Deterministic IDs are derived from source plus immutable provider ID.

Minimal background presets live in `configs/object_library/minimal_background_presets.json`. They define muted colors, subtle noise, object zones, character anchors, and a two-prop clutter cap. They complement the existing `TELLA_ASSET_BACKGROUND_MODE=procedural_minimal` renderer; they do not introduce rich background dependencies.

## Quality rules and limitations

- Unsafe SVG scripts, foreign objects, event attributes, external references, malformed dimensions, oversized SVGs, empty rasters, tiny rasters, and unsupported formats fail closed.
- Opaque PNGs are marked `review` and excluded from production lookup. Raw sources are never overwritten.
- CairoSVG is used to generate Pillow-compatible transparent PNGs from vectors. Complex SVG filters may still need human QC.
- Semantic enrichment is deterministic keyword taxonomy, not embedding search. Multilingual aliases and learned visual similarity are future work.
- License metadata quality is limited by each provider response; ingestion never implies redistribution permission.
- Contact sheets currently include raster previews only.

Recommended next step: curate a small approved style family (roughly 10–20 objects for each high-value emotional group), review contact sheets and attribution, then teach composition templates to request category/mood/context rather than fixed object IDs.
