# Zero-cost manual character bootstrap

Manual front-candidate import is the recommended MVP path. It works entirely
offline and requires no BFL, R2, Cloudflare, Gemini, or other provider
credential. BFL reference generation and private R2 transport remain available
as optional premium integrations through their explicit authorization-gated
modes; they are not default dependencies.

Prepare exactly three separate PNG files. Each must be a non-animated
768×1024 image no larger than 20 MB. The importer preserves the exact bytes: it
does not resize, crop, pad, enhance, or transcode them.

Validate the local configuration without candidate files:

```powershell
uv run python -m scripts.benchmarks.import_front_candidates `
  --mode validate-only `
  --config configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json `
  --repository-root . `
  --session-id manual_front_validate_01
```

Import three reviewed local files:

```powershell
uv run python -m scripts.benchmarks.import_front_candidates `
  --mode import `
  --config configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json `
  --repository-root . `
  --session-id practical_front_manual_01 `
  --candidate-01 <path-to-first-png> `
  --candidate-02 <path-to-second-png> `
  --candidate-03 <path-to-third-png>
```

Successful output appears under
`out/character_reference_bootstrap/practical_front_manual_01/`:

- `candidate_01.png`
- `candidate_02.png`
- `candidate_03.png`
- `candidates_manifest.json`
- `contact_sheet.png`
- `review_template.json`

The importer verifies only mechanical facts: PNG signature and decoding,
single-frame format, exact dimensions, byte limit, exact source/copy SHA256,
and duplicate-byte relationships. It cannot truthfully determine identity,
age, pose, hairstyle, outfit, anatomy, text, watermark, or suitability as a
recurring character. Those checks remain `pending_human_review`.

Open the real `contact_sheet.png` and each original candidate before editing
the review record. Do not mark `review_template.json` approved merely because
the files imported successfully. Duplicate candidates retain their IDs and are
shown as warnings; duplication never selects a candidate automatically.

The later approved anchor-selection step must record exactly one candidate ID,
the matching candidate SHA256, `human_approved=true`, approver role, approval
timestamp, review notes, and the immutable selection-record SHA256. Until that
record validates, `selected_candidate_id` remains null and Stage B remains
blocked.

Confirm runtime output remains ignored and untracked:

```powershell
git status --short --ignored out/character_reference_bootstrap/practical_front_manual_01
```

No provider call, network access, API key, `.env` file, seed, retry, or fallback
is involved in this workflow.
