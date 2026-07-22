"""Compatibility import for the canonical pure-kernel Dawid-Skene estimator.

New code should import from :mod:`trustforge_core.dawid_skene`.  This module
keeps existing TrustForge callers stable during the staged core extraction.
"""

from trustforge_core.dawid_skene import LABELS, N_LABELS, em_source_reliability

__all__ = ["LABELS", "N_LABELS", "em_source_reliability"]
