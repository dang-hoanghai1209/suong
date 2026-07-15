# Offline four-view package assembly

This workflow requires three already prepared remaining views and a verified
`front_anchor_locked` manual front session. It is local-only: no BFL, R2,
credential, or provider call is required.

Validate the command contract without files or output:

```powershell
uv run python -m scripts.benchmarks.import_remaining_character_views `
  --mode validate-only
```

Prepare these real, non-animated PNGs locally:

- three-quarter portrait, 768×1024;
- side profile, 768×1024;
- full-body neutral, 768×1024.

Import them with the locked front session:

```powershell
uv run python -m scripts.benchmarks.import_remaining_character_views `
  --mode import-views `
  --repository-root . `
  --front-anchor-session-id <front-session-id> `
  --package-id <package-id> `
  --three-quarter <three-quarter.png> `
  --side-profile <side-profile.png> `
  --full-body-neutral <full-body-neutral.png>
```

The importer validates PNG signature, Pillow decoding, MIME, exact dimensions,
non-animation, truncation, size, and source/copy SHA256 equality. It preserves
bytes exactly and publishes atomically under
`out/character_reference_packages/<package-id>/`.

The package contains four atomic views, `master_sheet.png`,
`package_manifest.json`, and `package_review_template.json`. The master is a
local deterministic 1536×2048 2×2 derivative in front, three-quarter,
side-profile, full-body order. It is not provider-facing and cannot replace
the four atomic inputs.

Verify the draft without changing it:

```powershell
uv run python -m scripts.benchmarks.import_remaining_character_views `
  --mode verify-draft `
  --repository-root . `
  --package-id <package-id>
```

Mechanical validity does not approve identity, anatomy, style, pose, or
cross-view consistency. The review template remains pending; final package approval is false,
and production/provider-facing use is blocked. The next
step is interactive human approval of all four views.
