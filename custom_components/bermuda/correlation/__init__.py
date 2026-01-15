"""
Scanner correlation learning for improved area localization.

This module learns typical RSSI relationships between scanners for each area.
When the observed pattern doesn't match the learned pattern, it suggests
the device might not actually be in the suspected area.

Example usage in coordinator::

    from .correlation import AreaProfile, CorrelationStore, z_scores_to_confidence

    # On confirmed area selection:
    profile.update(primary_rssi, other_readings)

    # When evaluating area candidates:
    z_scores = profile.get_z_scores(primary_rssi, other_readings)
    confidence = z_scores_to_confidence(z_scores)
    if confidence < 0.5:
        # Pattern doesn't match - device might not be in this area
        effective_distance *= 1.5  # Penalty

Architecture:
    - ScannerPairCorrelation: Single Kalman-filtered delta tracker
    - AreaProfile: Collection of correlations for one area
    - confidence: Pure functions for z-score to confidence conversion
    - CorrelationStore: Home Assistant persistence

"""

from .area_profile import AreaProfile
from .confidence import weighted_z_scores_to_confidence, z_scores_to_confidence
from .scanner_pair import ScannerPairCorrelation
from .store import CorrelationStore

__all__ = [
    "AreaProfile",
    "CorrelationStore",
    "ScannerPairCorrelation",
    "weighted_z_scores_to_confidence",
    "z_scores_to_confidence",
]
