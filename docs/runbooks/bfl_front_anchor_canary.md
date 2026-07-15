# Direct BFL front-anchor canary

The front-anchor canary is a separate Stage-A text-to-image path. It does not
reuse the Cloudflare adapter and it never conditions on a reference image.

`bfl_flux_1_1_pro_front_anchor` is fixed to `/v1/flux-pro-1.1`, 768×1024 PNG,
`prompt_upsampling=false`, and one explicit seed per candidate. The first
authorized run has exactly three candidates, one create submission per
candidate, bounded polling, at most one result download, zero retries, and zero
fallbacks. A failed candidate consumes its submission and is not replaced.

Validation is always offline and does not read `BFL_API_KEY`:

```powershell
uv run python -m scripts.benchmarks.bfl_front_anchor_canary `
  --config configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json `
  --mode validate-only
```

The future live command is executable only after the source worktree, canonical
fingerprint, prompt hash, fixed dimensions, output ownership, and three-request
budget gates pass:

```powershell
uv run python -m scripts.benchmarks.bfl_front_anchor_canary `
  --config configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json `
  --mode live-front-bfl `
  --repository-root . `
  --session-id practical_front_anchor_canary_01 `
  --authorization-token AUTHORIZE_BFL_FRONT_ANCHOR_CANARY_01
```

Before that command, provide `BFL_API_KEY` only in the current process using a
secure interactive secret-entry procedure, for example:

```powershell
$env:BFL_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'BFL_API_KEY' -AsSecureString)).Password
```

Do not put it in `.env`, a command
argument, source code, config JSON, or persisted PowerShell history. Clear it
immediately afterward, for example by closing the process or running
`Remove-Item Env:BFL_API_KEY` in the same PowerShell session.

The fixed seeds are 17001, 17002, and 17003. The run permits three create
submissions total, one create attempt and one result download per candidate,
bounded polling, zero retries, zero replacement candidates, and zero fallback.
Exit code 0 means `completed_awaiting_human_review`; exit code 2 means blocked
before provider construction; exit code 3 means `partial_failed`. Cancellation
preserves completed PNGs, records the active candidate as cancelled, marks later
candidates not attempted, and finalizes the manifest on a best-effort basis.

Successful output contains three candidate PNGs, `candidates_manifest.json`, a
local-only `contact_sheet.png`, and an unapproved `review_template.json`.
Partial or failed runs never create contact-sheet or review artifacts. No
candidate is automatically selected, and Stage B remains blocked until human
approval.

Verify generated files remain ignored and untracked with:

```powershell
git status --short --ignored out/character_reference_bootstrap/practical_front_anchor_canary_01
```

The canary never loads `.env`, uses a webhook, rotates accounts, falls back to
another provider, or persists polling/result URLs. Manifests retain only
request IDs, hashes, dimensions, and redacted accounting.
