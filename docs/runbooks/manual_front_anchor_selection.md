# Manual front-anchor selection

This workflow is entirely local. BFL, R2, credentials, and provider calls are
not required. It locks only the bootstrap front identity anchor; it does not
approve the four-view Character Reference Package or authorize Stage B.

First import three real, non-animated 768×1024 `image/png` candidates with the
manual-import command. Do not paste raw JSON into PowerShell. Inspect the
resulting `contact_sheet.png`, then run:

```powershell
uv run python -m scripts.benchmarks.review_front_anchor `
  --mode interactive-review `
  --repository-root . `
  --session-id <session-id>
```

The terminal flow requires an explicit contact-sheet confirmation, exactly one
candidate ID, an individual yes/no answer for every semantic checklist item,
an approver role, non-empty review notes, and a final candidate/hash
confirmation. There is no default or “best candidate” selection. Duplicate
bytes are warned about and require separate confirmation.

To validate an existing immutable selection without changing files:

```powershell
uv run python -m scripts.benchmarks.review_front_anchor `
  --mode verify-selection `
  --repository-root . `
  --session-id <session-id>
```

The selection record stores only relative filenames, hashes, dimensions, the
complete explicit checklist, safe timestamps, and approval metadata. It is
canonicalized and hashed, written atomically as UTF-8 without BOM, and binds
the selected candidate bytes exactly. Any changed candidate, manifest, review
template, contact sheet, or selection record fails verification.

Cancellation or a failed checklist leaves the imported candidates intact and
creates no approval. The next zero-cost step is to import and review the
three-quarter, side-profile, and full-body views. Final package approval and
provider-facing use remain blocked until all four views are separately
reviewed and approved.
