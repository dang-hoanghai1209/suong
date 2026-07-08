# girl_v1 Sprite Pack

This folder contains the replaceable sprite assets for the `minimalist_emotional` theme.

The character system supports three asset styles:

- Curated full-body pose sprites in `curated_poses/` driven by `curated_pose_manifest.json`.
- Legacy full-pose sprites in `poses/`.
- Hybrid rig parts in `parts/` driven by `rig.json`.

By default `TELLA_MINIMALIST_CHARACTER_MODE=auto`: curated non-placeholder full-body PNGs are used when available; otherwise the local rig is rendered from parts. The rig remains a fallback/development path.

## Curated Full-Body Pose Sprites

For MVP visual quality, prefer curated full-body pose sprites. Place transparent PNG files in `curated_poses/` with the filenames listed in `curated_pose_manifest.json`:

- `standing_calm.png`
- `standing_lonely.png`
- `sitting_sad.png`
- `hugging_knees_sad.png`
- `looking_down_tired.png`
- `looking_up_hopeful.png`
- `walking_away.png`
- `walking_forward_soft.png`
- `reaching_forward_hopeful.png`
- `holding_paper_heart.png`
- `arms_open_relief.png`
- `sitting_by_lamp.png`

Each curated pose is a complete full-body character illustration with baked-in expression, hair, head, body, arms, legs, and clothing. Runtime does not rig or swap face layers for curated sprites.

Each curated PNG must:

- Be a transparent PNG with an alpha channel.
- Use canvas size `600x900`.
- Share anchor `x=300, y=850` as the feet/ground point.
- Keep one consistent character identity, hairstyle, outfit, palette, and line width.
- Keep the full body visible with no cropped head or feet.
- Include no text and no watermark.

Run the QA contact sheet after adding or replacing curated assets:

```powershell
uv run python scripts/render_minimalist_contact_sheet.py
```

The output is:

```text
out/dev/minimalist_contact_sheet.png
```

The contact sheet shows curated sprites, scene placement, motif compatibility, rig fallback, and placeholder/missing warnings.

## Required Pose PNGs

Place transparent PNG files in `poses/` with these exact names:

- `front_standing.png`
- `side_sitting.png`
- `side_walking.png`
- `hugging_knees.png`
- `looking_up.png`
- `looking_down.png`
- `reaching_forward.png`
- `holding_paper_heart.png`
- `arms_open.png`

Each pose must:

- Be a transparent PNG with an alpha channel.
- Use the manifest canvas size: `600x900`.
- Share the same anchor point: `x=300, y=850`.
- Treat the anchor as the girl's feet / ground-contact point.
- Keep consistent character height, head size, line width, and palette.
- Include the same character design: warm hand-drawn girl, short black bob hair, visible simple face, mustard dress, rust sleeves.
- Contain no text and no watermark.

## Motifs And Backgrounds

Motifs live in `motifs/`; backgrounds live in `backgrounds/`.
They are also replaceable PNGs. Motifs should be simple, faceless symbols that support the scene without becoming a second character.

## Placeholder Sprites

Generated placeholder sprites are for development only. They are marked by either:

- A neighboring `*.placeholder` marker file, or
- Matching the generated placeholder image hash.

While developing, missing sprites can be generated automatically. For production, set:

```powershell
$env:TELLA_ALLOW_PLACEHOLDER_SPRITES = "0"
```

With that setting, Tella fails clearly if required pose PNGs are missing.

## Replacement Workflow

1. Replace PNG files in `poses/`, `motifs/`, or `backgrounds/`.
2. Keep filenames and canvas size unchanged.
3. Delete any matching `*.placeholder` marker file for replaced pose sprites.
4. Re-run the contact sheet:

```powershell
uv run python scripts/render_minimalist_contact_sheet.py
```

The output is:

```text
out/dev/minimalist_contact_sheet.png
```

## Required Rig Part PNGs

Place transparent PNG files in `parts/` with these exact names:

- `head_face_hair.png`
- `torso.png`
- `left_arm.png`
- `right_arm.png`
- `left_leg.png`
- `right_leg.png`

Each rig part must:

- Be a transparent PNG with an alpha channel.
- Keep the same visual design and scale as the other parts.
- Match the pivots and default positions in `rig.json`.
- Keep the head, hair, and face together in `head_face_hair.png`.
- Avoid text and watermarks.

Recommended design:

- Warm simple emotional hand-drawn girl.
- Short black bob hair.
- Visible simple face.
- Mustard dress.
- Rust sleeves.
- Soft expressive posture.
- Consistent line width.
- Transparent background.

## Rig Replacement Workflow

1. Replace PNG files in `parts/`.
2. Keep filenames unchanged.
3. If the part dimensions change, update its `pivot` in `rig.json`.
4. Delete any matching `*.placeholder` marker file for replaced rig parts.
5. Re-run the contact sheet:

```powershell
uv run python scripts/render_minimalist_contact_sheet.py
```

## Runtime Flags

```powershell
$env:TELLA_MINIMALIST_CHARACTER_MODE = "auto"
```

Allowed values:

- `auto`: use non-placeholder curated full-body sprites if available; otherwise render the rig.
- `rig`: always render from `parts/` and `rig.json`.
- `sprite`: use curated full-body sprites only. This is the recommended production mode once curated PNGs exist.

Generated placeholders are development-only. Production should set:

```powershell
$env:TELLA_ALLOW_PLACEHOLDER_SPRITES = "0"
```

Recommended production setup:

```powershell
$env:TELLA_MINIMALIST_CHARACTER_MODE = "sprite"
$env:TELLA_ALLOW_PLACEHOLDER_SPRITES = "0"
Remove-Item Env:TELLA_MINIMALIST_USE_AI_SCENES -ErrorAction SilentlyContinue
```

Keep `TELLA_MINIMALIST_USE_AI_SCENES` unset unless you explicitly want Cloudflare full-scene image generation. `--media ai_image` still routes `minimalist_emotional` to local composition by default.
