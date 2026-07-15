# Direct BFL front-anchor canary

The front-anchor canary is a separate Stage-A text-to-image path. It does not
reuse the Cloudflare adapter and it never conditions on a reference image.

`bfl_flux_1_1_pro_front_anchor` is fixed to `/v1/flux-pro-1.1`, 768×1024 PNG,
`prompt_upsampling=false`, and one explicit seed per candidate. The first
authorized run has exactly three candidates, one create submission per
candidate, bounded polling, at most one result download, zero retries, and zero
fallbacks. A failed candidate consumes its submission and is not replaced.

Validation is always offline:

```powershell
uv run python -m scripts.benchmarks.bfl_front_anchor_canary `
  --config configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json `
  --mode validate-only
```

Live execution requires a separately reviewed transport, process-scoped
`BFL_API_KEY`, and the exact authorization token
`AUTHORIZE_BFL_FRONT_ANCHOR_CANARY_01`. The canary must not load `.env`, use a
webhook, rotate accounts, fall back to another provider, or select a candidate
automatically. Polling and result URLs are never persisted; manifests retain
only request IDs and redacted accounting.
