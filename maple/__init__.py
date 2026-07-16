"""MAPLE — Marker-based Annotation with PubMed Literature Evidence.

Public API:
    from maple import annotate, annotate_async, annotate_marker_sets
"""

from maple.api import (
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
