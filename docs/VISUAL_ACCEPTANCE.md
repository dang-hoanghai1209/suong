# Visual acceptance and selected-scene regeneration

Automated image checks can detect file corruption and some obvious defects, but they cannot reliably decide whether an illustration clearly communicates the requested action, preserves a character, or contradicts narration. A production candidate therefore requires both technical QC and explicit human review. Automated observations are stored as context only and never count as human approval.

## Acceptance workflow

The versioned suite is `configs/acceptance/practical_life_steps_visual_v1.json`. Its thresholds are initial acceptance policy, not a claim about provider capability.

1. Create the suite’s real jobs manually with the named production recipe. Provider use remains a separately approved production activity; CI never creates these jobs.
2. Initialize each seven-scene review:

   ```powershell
   uv run python -m scripts.production_acceptance init --job out/acceptance/practical_life_steps_visual_v1/<case-id> --output out/acceptance/practical_life_steps_visual_v1/<case-id>/visual_acceptance_review.json
   ```

3. Review every scene and fill every human classification. `hard_fail` means the requested action is absent or contradicted. `soft_fail` means the action is present but ambiguous. Readable generated words, labels, logos, UI text, letters, or watermarks fail by default. Character identity, clothing, pose language, and overall style must remain consistent; minor drift is a warning and material drift is a failure.
4. Aggregate the reviewed jobs:

   ```powershell
   uv run python -m scripts.production_acceptance report --suite configs/acceptance/practical_life_steps_visual_v1.json --jobs-root out/acceptance/practical_life_steps_visual_v1 --output out/acceptance/report.json
   ```

`accepted` and `conditionally_accepted` return zero when policy thresholds pass. `rejected` returns 1. Missing, stale, or unreviewed scenes produce `incomplete_review` and return 2. JSON separately records command completion, threshold result, human acceptance, and `release_approved`; conditional acceptance is never release approval. A changed image hash always invalidates its old review.

## Correction templates and regeneration

For scenes marked `regenerate`, create a human-editable template without a provider call:

```powershell
uv run python -m scripts.production_acceptance corrections --review <review.json> --output <corrections.json>
```

Review and refine the structured `must_show`, `must_not_show`, object-state, action, character-lock, and composition fields. Notes cannot select providers or carry credentials. Then inspect the exact local request envelope:

```powershell
uv run python -m scripts.scene_regeneration --source-job out/<source-job> --target-job-id <derived-job> --scene-indices 3,4 --reason action_mismatch --max-ai-images 2 --prompt-corrections <corrections.json> --dry-run --json
```

Dry-run validates source hashes and indices, performs no rendering or network/provider calls, creates no job, and writes only an explicitly requested `--output` atomically. A real run creates a separate derived sibling job. Source and target locks are acquired in normalized-path order; production source files remain byte-for-byte unchanged. Never remove an active or unverifiable lock. Stale recovery remains explicit and conservative.

The real image budget is exactly one candidate for each unique selected index. A larger `--max-ai-images` value does not authorize hidden calls; a smaller value fails before provider access. The direct provider adapter permits one account and one transport attempt. There are no retries, account rotation, alternate-model, stock-media, or placeholder fallbacks.

Unselected images, narration, accepted mixed audio, alignment, boundaries, subtitles, scene timing, music metadata, recipe, and voice metadata are independent regular-file copies retaining their source hashes; links, junction aliases, and hardlink identity are rejected. New images invalidate the derived video, video QC, selected-scene visual QC, and any completed claim. With `--no-render`, the derived job remains render-required and its local render stage may be continued by the documented renderer. Image-regeneration resume itself is intentionally unsupported: a provider failure may preserve partial files for inspection, but they are not trusted for reuse; restart with a new empty target job ID. Existing nonempty targets are rejected. Otherwise, local rendering preserves timing and stream-copies the already accepted mixed audio without TTS, alignment, music preparation, remixing, or audio transcoding. Inspect `scene_regeneration.json` and `production_manifest.json` for lineage and hashes. Every regenerated scene must receive a new human review.

Known limitations: review is manual; there is no speech-to-image semantic verifier; a technically valid image may still be unsuitable; and the harness does not generate, schedule, or publish acceptance jobs.
