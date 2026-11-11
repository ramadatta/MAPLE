"""MAPLES — Mapping Analogous Programs from Literature Evidence across Species, organs and diseases.

Public API:
    from maples import annotate, annotate_async, annotate_marker_sets
"""

from maples.api import (
    AnnotationResult,
    RunMetadata,
    annotate,
    annotate_async,
    annotate_marker_sets,
    annotate_marker_sets_async,
)

__all__ = [
    "annotate",
    "annotate_async",
    "annotate_marker_sets",
    "annotate_marker_sets_async",
    "AnnotationResult",
    "RunMetadata",
]
