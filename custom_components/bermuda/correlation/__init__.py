"""
Scanner correlation learning for improved area localization.

This module learns typical RSSI relationships between scanners for each area.
When the observed pattern doesn't match the learned pattern, it suggests
the device might not actually be in the suspected area.

Two types of learning:
    - Automatic learning: update() called continuously, capped influence
    - Button training: update_button() from user action, sets the "anchor"

Clamped Bayesian Fusion ensures user training retains at least 70% authority
while auto-learning can "polish" the anchor with max 30% influence.

Example usage in coordinator::

    from .correlation import AreaProfile, CorrelationStore, RoomProfile

    # Automatic learning (on confirmed area selection):
    area_profile.update(primary_rssi, other_readings, primary_scanner_addr)
    room_profile.update(all_rssi_readings)

    # Button training (user explicitly trains a room):
    area_profile.update_button(primary_rssi, other_readings, primary_scanner_addr)
    room_profile.update_button(all_rssi_readings)

    # When evaluating area candidates:
    z_scores = area_profile.get_weighted_z_scores(primary_rssi, other_readings)
    confidence = weighted_z_scores_to_confidence(z_scores)

    # Check if profile has user-trained data:
    if area_profile.has_button_training:
        # User explicitly trained this room - trust it

    # Reset ALL training (button AND auto) for a room:
    area_profile.reset_training()
    room_profile.reset_training()

Architecture:
    - ScannerPairCorrelation: Kalman-filtered delta tracker (primary-to-other)
    - ScannerAbsoluteRssi: Kalman-filtered absolute RSSI tracker (per-scanner)
    - AreaProfile: Device-specific correlations for one area
    - RoomProfile: Device-independent scanner-pair deltas for one room
    - confidence: Pure functions for z-score to confidence conversion
    - CorrelationStore: Home Assistant persistence

"""

from .area_profile import AreaProfile
from .confidence import weighted_z_scores_to_confidence, z_scores_to_confidence
from .room_profile import RoomProfile
from .scanner_absolute import ScannerAbsoluteRssi
from .scanner_pair import ScannerPairCorrelation
from .store import CorrelationStore

__all__ = [
    "AreaProfile",
    "CorrelationStore",
    "RoomProfile",
    "ScannerAbsoluteRssi",
    "ScannerPairCorrelation",
    "weighted_z_scores_to_confidence",
    "z_scores_to_confidence",
]
