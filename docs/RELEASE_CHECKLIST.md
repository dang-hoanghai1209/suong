# Release-candidate checklist

## Before merge

- Confirm the task branch is synchronized with its locally available tracking reference.
- Require a clean worktree with no staged changes or merge conflicts.
- Run `uv run python -m scripts.release_preflight` and retain the result.
- Require green Windows full-suite CI and green Linux smoke CI.
- Confirm fresh-clone tests pass without `music/tracks/practical_calm_01.mp3`.
- Record the full test count and confirm tests made zero production-provider calls.
- Run the production recipe dry-run and review its request envelope.
- Manually review an accepted local production video, subtitle/scene timing, and narration.
- Review the selected music license, attribution, and Content ID status.
- Confirm production outputs are ignored and no API key or credential file is tracked.
- Confirm the intended merge target.

## Merge

- Review the commit history and final diff.
- Merge into `main`, rerun CI, and record the merge commit.
- Do not delete the backup branch immediately.

## Release candidate

- Add a changelog entry and choose an appropriate pre-1.0 semantic version.
- Create a tag only after `main` is green; this checklist does not automate tagging.
- Document known limitations, the preview-model dependency, music Content ID risk, and Windows-first support.
- Do not publish packages, deploy services, or post to social platforms from this process.

## Rollback

- Preserve the previous tag and identify the last known-good commit.
- Restore or add a recipe version; never silently change a released `v1` configuration.
- Never overwrite an existing released recipe configuration or tag.
