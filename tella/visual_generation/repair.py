"""Targeted, bounded image-edit request construction."""
from __future__ import annotations

from .models import GenerationRequest, VisualQCResult


def build_repair_request(
    request: GenerationRequest,
    qc: VisualQCResult,
    *,
    attempt: int,
) -> GenerationRequest:
    instructions = qc.repair_instructions or [qc.notes]
    clean = [item.strip() for item in instructions if item.strip()]
    if not clean:
        raise ValueError("minor repair requires a targeted repair instruction")
    preservation = (
        "Preserve all other composition, style, character identity, relationships, and details "
        "unless explicitly requested below."
    )
    return request.model_copy(
        update={
            "attempt": attempt,
            "preserve_existing": True,
            "repair_instructions": [preservation, *clean],
            "instruction": request.instruction
            + "\n\nTARGETED IMAGE EDIT:\n"
            + preservation
            + "\n"
            + "\n".join(f"- {item}" for item in clean),
        }
    )
