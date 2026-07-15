# Practical character atomic-reference bootstrap

This workflow resolves the initial-reference problem without pretending that
text-only generation guarantees identity. It is a planning and validation
boundary only; it does not authorize or implement provider execution.

## Stage A: select one front anchor

Generate only `front_portrait` candidates from the locked canonical character
specification. At most three initial candidates and two targeted candidates may
be submitted. A human reviewer must select exactly one candidate. Its PNG bytes
and SHA256 become the immutable `bootstrap_identity_anchor`; this decision is
not final package approval.

Cloudflare's current adapter may be considered for candidate generation because
it supports text-to-image. It does not support reference conditioning and must
not be described as guaranteeing identity. Any live use requires separate
authorization and PNG/dimension postcondition validation.

## Stage B: derive three views from the locked bytes

The three-quarter portrait, side profile, and full-body neutral view each bind
to the exact selected anchor SHA256. Every request must use the same anchor
bytes. A mismatch blocks the workflow; the anchor is never regenerated,
replaced, or silently downgraded to text-only generation.

The BFL FLUX.2 adapter can be considered only after an approved anchor, private
reference transport, live BFL authorization, and per-request anchor verification
exist. The state machine contains no paid-provider execution path.

## Budget and approval boundaries

The fixed per-view maxima are 5, 2, 2, and 3, for a global maximum of 12 image
submissions. Automatic retries and fallbacks are zero. Targeted candidates
require a recorded strict-QC failure and the workflow stops at the relevant
per-view or global limit.

All four atomic assets require anatomy, style, identity, and human QC. Only then
may the deterministic local 1536x2048 master be assembled. The package remains
unavailable to provider-facing consumers until the separate immutable final
package approval is complete.

Use the zero-network validator:

```powershell
python -m scripts.benchmarks.character_reference_bootstrap `
  --config configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json `
  --mode validate-only
```

Validate-only constructs no provider client, generates no image, and performs
no network operation.
