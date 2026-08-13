"""Offline evaluation contracts for TripStar.

This package is deliberately disconnected from production routes and providers.
"""

from .models import (
    EvalCase,
    EvalRunArtifact,
    BatchEvaluationReport,
    FixtureRequirement,
    HumanReviewDimension,
    HumanReviewRubric,
    MetricResult,
    PairedComparison,
    PlannerVersionMetadata,
    SanitizedProviderFixture,
)
from .capture_models import PlannerCaptureArtifact

__all__ = [
    "EvalCase",
    "EvalRunArtifact",
    "BatchEvaluationReport",
    "FixtureRequirement",
    "HumanReviewDimension",
    "HumanReviewRubric",
    "MetricResult",
    "PairedComparison",
    "PlannerVersionMetadata",
    "SanitizedProviderFixture",
    "PlannerCaptureArtifact",
]
