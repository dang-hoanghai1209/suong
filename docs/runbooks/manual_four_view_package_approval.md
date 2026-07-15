# Offline four-view package approval

Prerequisites are a locked front anchor, three imported remaining views, and a
passing `verify-draft` result. Open all four atomic PNGs at usable resolution
and inspect `master_sheet.png` before starting review.

Run the interactive approval flow:

```powershell
uv run python -m scripts.benchmarks.review_character_reference_package `
  --mode interactive-review `
  --repository-root . `
  --package-id <package-id>
```

The terminal asks for explicit yes/no answers for every per-view and
cross-view item. There is no approve-all shortcut. A mechanically valid PNG
may still fail identity, anatomy, pose, style, or background review. Rejection
or cancellation leaves images and the draft unchanged and creates no valid
approval.

Verify an approved package without changing it:

```powershell
uv run python -m scripts.benchmarks.review_character_reference_package `
  --mode verify-approval `
  --repository-root . `
  --package-id <package-id>
```

Approval creates a separate atomic `package_approval.json`; the original draft
manifest remains immutable provenance. The approval locks the four-view
reference package for local use only. Provider execution, scene generation,
and final-video generation remain unauthorized. No BFL, R2, API key, or
provider call is needed. The next task is the zero-cost three-scene proof of
concept.
