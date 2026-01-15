"""
Tests for AreaProfile.

These tests verify that area-level correlation management works correctly,
including updates, z-score calculation, and memory limits.
"""

from __future__ import annotations

import pytest

from custom_components.bermuda.correlation.area_profile import (
    MAX_CORRELATIONS_PER_AREA,
    AreaProfile,
)
from custom_components.bermuda.correlation.scanner_pair import MIN_SAMPLES_FOR_MATURITY


class TestAreaProfileLearning:
    """Tests for multi-scanner correlation learning."""

    def test_learns_multiple_scanner_correlations(self) -> None:
        """Profile learns correlations to multiple scanners simultaneously."""
        profile = AreaProfile(area_id="area.living_room")

        # Simulate device in living room with 3 other scanners visible
        for _ in range(50):
            profile.update(
                primary_rssi=-45.0,
                other_readings={
                    "scanner_kitchen": -55.0,  # 10 dB weaker
                    "scanner_bedroom": -65.0,  # 20 dB weaker
                    "scanner_garage": -80.0,  # 35 dB weaker
                },
            )

        assert profile.correlation_count == 3, (
            f"Profile has {profile.correlation_count} correlations but expected 3. "
            f"Missing correlations mean incomplete area fingerprint, reducing "
            f"anomaly detection accuracy."
        )

        # Verify learned deltas
        kitchen_corr = profile._correlations["scanner_kitchen"]
        assert abs(kitchen_corr.expected_delta - 10.0) < 1.0, (
            f"Kitchen delta={kitchen_corr.expected_delta:.1f} dB but expected ~10 dB. "
            f"Incorrect learned delta would cause false anomaly detection."
        )

    def test_cross_floor_scanner_tracked(self) -> None:
        """Scanners on other floors are tracked just like same-floor scanners."""
        profile = AreaProfile(area_id="area.office_ground_floor")

        # Learn: bedroom scanner (upstairs) typically -15 dB difference
        for _ in range(50):
            profile.update(
                primary_rssi=-50.0,
                other_readings={
                    "scanner_hallway_ground": -58.0,  # Same floor
                    "scanner_bedroom_first": -65.0,  # Different floor
                },
            )

        assert "scanner_bedroom_first" in profile._correlations, (
            "Cross-floor scanner not tracked. Multi-floor correlations are valuable "
            "for confirming area assignments and should not be filtered out."
        )

        cross_floor_delta = profile._correlations["scanner_bedroom_first"].expected_delta
        assert abs(cross_floor_delta - 15.0) < 1.0, (
            f"Cross-floor delta={cross_floor_delta:.1f} dB but expected ~15 dB. "
            f"Incorrect cross-floor correlation reduces multi-floor detection accuracy."
        )


class TestAreaProfileZScores:
    """Tests for z-score calculation from profiles."""

    def test_z_scores_only_for_mature_correlations(self) -> None:
        """Z-scores are only returned for correlations with enough samples."""
        profile = AreaProfile(area_id="area.office")

        # Add just a few samples (not enough for maturity)
        for _ in range(5):
            profile.update(
                primary_rssi=-50.0,
                other_readings={"scanner_hallway": -60.0},
            )

        z_scores = profile.get_z_scores(
            primary_rssi=-50.0,
            other_readings={"scanner_hallway": -60.0},
        )

        sample_count = profile._correlations["scanner_hallway"].sample_count
        assert len(z_scores) == 0, (
            f"Returned {len(z_scores)} z-scores for immature correlation "
            f"(samples={sample_count}, need={MIN_SAMPLES_FOR_MATURITY}). "
            f"Using immature correlations would base confidence on unreliable data."
        )

    def test_detects_anomalous_pattern(self) -> None:
        """High z-scores indicate observation doesn't match learned pattern."""
        profile = AreaProfile(area_id="area.office")

        # Learn: hallway is typically 10 dB weaker when in office
        for _ in range(50):
            profile.update(
                primary_rssi=-50.0,
                other_readings={"scanner_hallway": -60.0},
            )

        # Test: hallway suddenly 30 dB weaker (anomaly!)
        z_scores = profile.get_z_scores(
            primary_rssi=-50.0,
            other_readings={"scanner_hallway": -80.0},  # 30 dB diff instead of 10!
        )

        assert len(z_scores) == 1, (
            f"Expected 1 z-score, got {len(z_scores)}. Missing z-scores mean anomalies go undetected."
        )

        _, z = z_scores[0]
        assert z > 2.0, (
            f"Z-score={z:.2f} for 20 dB deviation from learned pattern. "
            f"Low z-score would fail to flag this anomaly, leaving the device "
            f"potentially assigned to the wrong area."
        )

    def test_weighted_z_scores_include_sample_count(self) -> None:
        """Weighted z-scores return sample counts for confidence weighting."""
        profile = AreaProfile(area_id="area.office")

        for _ in range(50):
            profile.update(
                primary_rssi=-50.0,
                other_readings={"scanner_a": -60.0},
            )

        weighted = profile.get_weighted_z_scores(
            primary_rssi=-50.0,
            other_readings={"scanner_a": -60.0},
        )

        assert len(weighted) == 1
        scanner, z, samples = weighted[0]

        assert samples == 50, (
            f"Sample count={samples}, expected 50. Incorrect sample count would skew weighted confidence calculations."
        )
        assert isinstance(samples, int), f"Sample count is {type(samples).__name__}, expected int."


class TestAreaProfileMemoryLimit:
    """Tests for memory limit enforcement."""

    def test_memory_limit_enforced(self) -> None:
        """Profile doesn't grow unbounded - keeps only most-sampled correlations."""
        profile = AreaProfile(area_id="area.office")

        # Add more scanners than the limit
        num_scanners = MAX_CORRELATIONS_PER_AREA + 10

        for i in range(num_scanners):
            # Give earlier scanners more samples (they should be kept)
            samples = 50 if i < MAX_CORRELATIONS_PER_AREA else 5
            for _ in range(samples):
                profile.update(
                    primary_rssi=-50.0,
                    other_readings={f"scanner_{i:02d}": -60.0 - i},
                )

        assert profile.correlation_count <= MAX_CORRELATIONS_PER_AREA, (
            f"Profile has {profile.correlation_count} correlations but limit is "
            f"{MAX_CORRELATIONS_PER_AREA}. Unbounded growth would cause memory "
            f"issues over time as devices see many scanners."
        )

        # The ones with more samples should be kept
        for addr, corr in profile._correlations.items():
            assert corr.sample_count >= 50, (
                f"Scanner {addr} has only {corr.sample_count} samples but "
                f"higher-sample correlations exist. Memory eviction should keep "
                f"most-sampled (most reliable) correlations."
            )


class TestAreaProfilePersistence:
    """Tests for serialization."""

    def test_serialization_preserves_all_correlations(self) -> None:
        """All learned correlations survive serialization roundtrip."""
        original = AreaProfile(area_id="area.kitchen")

        for _ in range(50):
            original.update(
                primary_rssi=-48.0,
                other_readings={
                    "scanner_a": -55.0,
                    "scanner_b": -62.0,
                    "scanner_c": -70.0,
                },
            )

        # Roundtrip
        data = original.to_dict()
        restored = AreaProfile.from_dict(data)

        assert restored.area_id == original.area_id, (
            f"Area ID changed: '{original.area_id}' -> '{restored.area_id}'. "
            f"Correlations would be associated with wrong area after restart."
        )
        assert restored.correlation_count == original.correlation_count, (
            f"Correlation count changed: {original.correlation_count} -> "
            f"{restored.correlation_count}. Some correlations were lost in "
            f"serialization, reducing area fingerprint accuracy."
        )
        assert restored.mature_correlation_count == original.mature_correlation_count, (
            f"Mature count changed: {original.mature_correlation_count} -> "
            f"{restored.mature_correlation_count}. Maturity state was not preserved."
        )

    def test_empty_profile_serialization(self) -> None:
        """Empty profile serializes and deserializes correctly."""
        original = AreaProfile(area_id="area.new_room")

        data = original.to_dict()
        restored = AreaProfile.from_dict(data)

        assert restored.area_id == original.area_id
        assert restored.correlation_count == 0, (
            f"Restored empty profile has {restored.correlation_count} correlations. "
            f"Empty profiles should remain empty after roundtrip."
        )
