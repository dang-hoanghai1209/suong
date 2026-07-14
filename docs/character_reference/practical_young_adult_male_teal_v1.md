# Practical young-adult male reference package

This brief prepares an immutable character reference package. It does not
authorize image generation or select a provider.

## Package boundary

The package ID is `practical_young_adult_male_teal_v1_package_v1`, anchored to
the canonical specification and fingerprint in
`configs/character_references/practical_young_adult_male_teal_v1.json`.

The package contains one locally assembled 1536x2048 portrait archival master
sheet and four separate 768x1024 PNG atomic views. The master is an exact 3:4,
unlabeled 2x2 derivative for human review, archival integrity, and contact-sheet
inspection. It is never generated independently by a provider. Provider
requests must use all four atomic views, in this order:

1. front portrait;
2. three-quarter portrait;
3. side profile;
4. full-body neutral pose.

No placeholder image is permitted. Missing images, unapproved images, or
images whose bytes, MIME, dimensions, or hashes differ from the approved
manifest fail closed.

## Generation and approval workflow

1. Review the provider-independent prompt and negative constraints in the
   generation specification.
2. Separately authorize a bounded provider operation.
3. Generate only the four atomic PNG assets.
4. Validate their order, MIME, 768x1024 dimensions, and hashes, then assemble
   the master locally without cropping, stretching, resampling, labels, borders,
   watermarks, or inherited path metadata.
5. Calculate the derived 1536x2048 master SHA256 and construct the typed
   immutable manifest with all four ordered source hashes.
6. Run anatomy, style, and cross-view identity QC.
7. Complete every human-approval checklist item and record all five asset
   hashes, approval timestamp, and approver role.
8. Serialize the approval record, calculate its immutable SHA256, and store
   that hash in the package manifest.
9. Revalidate every file and deterministically reassemble the master before
   any provider-facing request.

The unapproved template is intentionally invalid as a final approval record.
No asset may enter a production reference-conditioned request until the final
record validates against the package contract.

## Adapter capability assessment

- `BFLFlux2ReferenceProvider` is the only existing adapter that can potentially
  accept the four PNG atomic references. Its capability contract allows up to
  eight references and verifies reference inputs per request. The remaining
  gap is a package-to-request bridge that converts one approved package into
  four `BFLReferenceInput` values while retaining package-level approval and
  deterministic ordering.
- `CloudflareImageProvider` is text-only and declares no reference images or
  identity anchor. It cannot execute this package.
- The generic single-reference `submit_reference_conditioned` helper validates
  one `ReferenceConditionedImageRequest`; it does not yet submit a four-atomic
  package and must not silently fall back to the master sheet.

Provider selection, credentials, temporary transport, generation budgets, and
live execution require a separate authorization task.
