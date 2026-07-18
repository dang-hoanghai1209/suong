"""Run the real Tella seven-scene Asset-library V2 acceptance pipeline."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Make direct ``python scripts/render_...py`` invocation resolve the local
# package exactly like the repository's module-based entry points.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tella.asset_library.production_mvp import (  # noqa: E402
    BASE_SEED,
    OUTPUT_DIR,
    render_acceptance_job,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--base-seed", type=int, default=BASE_SEED)
    args = parser.parse_args()
    final_video, metadata = asyncio.run(
        render_acceptance_job(args.output_dir, base_seed=args.base_seed)
    )
    print(json.dumps({
        "final_video": str(final_video),
        "scene_count": metadata["scene_count"],
        "video_properties": metadata["final_video_properties"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
