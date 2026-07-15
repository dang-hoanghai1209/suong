# Front-anchor candidate review boundary

The front harness produces no image by itself. A separately authorized executor
may place exactly three independently returned `front_portrait` PNGs under the
ignored session directory:

```text
out/character_reference_bootstrap/<session-id>/
  candidate_01.png
  candidate_02.png
  candidate_03.png
  candidates_manifest.json
  contact_sheet.png
  review_template.json
```

Each candidate is decoded and checked locally for PNG format, exactly 768x1024
dimensions, bounded bytes, and the required front-view/anatomy/style signals.
Malformed, wrong-sized, duplicate, or hard-QC-failing outputs remain recorded
but are ineligible for selection. Candidate pixels are never modified.

The contact sheet is assembled locally and is review-only; it is not an atomic
reference asset. The review template starts with no selection. A reviewer must
complete every checklist item and select exactly one eligible candidate. The
selection record stores the candidate ID and SHA256, approver role, timestamp,
notes, and immutable selection-record SHA256. No automatic “best candidate”
selection exists.

Only the immutable selected front anchor can unlock the previously defined Stage
B plans. This review boundary does not approve the final reference package.

Validate the boundary without reading credentials or contacting a provider:

```powershell
python -m scripts.benchmarks.front_anchor_review `
  --mode validate-only
```
