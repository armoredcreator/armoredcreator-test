"""Vision V2 candidate discovery and reconciliation.

V2 is intentionally isolated from ArmoredVision V1. It expands a product
identity into independently validated alternative offers without changing the
V1 exact-resolution implementation.
"""
from .service import CandidateDiscovery, VisionCandidateError
__all__ = ["CandidateDiscovery", "VisionCandidateError"]
