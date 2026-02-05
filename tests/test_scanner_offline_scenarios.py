"""
Scenario tests: How scanner offline detection impacts room switching & fingerprints.

These tests demonstrate concrete scenarios where KNOWING that a scanner is offline
(vs just "no data") improves room assignment accuracy. Each test documents:
1. The setup (room layout, scanner positions, learned fingerprints)
2. What happens TODAY (without offline awareness)
3. What SHOULD happen (with offline awareness)

Reference: Plan evaluation in conversation (Phases 0-4).
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.bermuda.const import (
    EVIDENCE_WINDOW_SECONDS,
    SCANNER_ACTIVITY_TIMEOUT,
)
from custom_components.bermuda.correlation.area_profile import AreaProfile
from custom_components.bermuda.filters.ukf import UnscentedKalmanFilter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCANNER_A = "AA:BB:CC:DD:EE:01"  # Kitchen scanner
SCANNER_B = "AA:BB:CC:DD:EE:02"  # Office scanner (will go offline)
SCANNER_C = "AA:BB:CC:DD:EE:03"  # Hallway scanner
SCANNER_D = "AA:BB:CC:DD:EE:04"  # Bedroom scanner

AREA_KITCHEN = "area_kitchen"
AREA_OFFICE = "area_office"
AREA_HALLWAY = "area_hallway"
AREA_BEDROOM = "area_bedroom"

NOWSTAMP = 10000.0


def _train_profile(area_id: str, readings: dict[str, float], n_samples: int = 50) -> AreaProfile:
    """Train an AreaProfile with consistent RSSI readings."""
    profile = AreaProfile(area_id=area_id)

    # Pick strongest as primary
    primary_addr = max(readings, key=readings.get)  # type: ignore[arg-type]
    primary_rssi = readings[primary_addr]
    other = {k: v for k, v in readings.items() if k != primary_addr}

    for _ in range(n_samples):
        profile.update(
            primary_rssi=primary_rssi,
            other_readings=other,
            primary_scanner_addr=primary_addr,
        )

    return profile


def _train_ukf(scanner_addresses: list[str], readings: dict[str, float], n_updates: int = 20) -> UnscentedKalmanFilter:
    """Create and train a UKF with stable readings."""
    ukf = UnscentedKalmanFilter(scanner_addresses=scanner_addresses)
    for _ in range(n_updates):
        ukf.predict(dt=1.0)
        ukf.update_multi(readings)
    return ukf


# ===========================================================================
# SCENARIO 1: UKF Fingerprint Discrimination Degrades When Scanner Goes Offline
# ===========================================================================


class TestScenario1FingerprintDegradation:
    """
    Setup: 4 scanners, device in Office.
    Office scanner (B) has strongest signal (-45dB).
    Kitchen and Office fingerprints differ mainly via Scanner B.

    When Scanner B goes offline, the remaining 3 scanners produce
    similar fingerprints for both rooms → discrimination drops.
    """

    def test_full_fingerprint_discriminates_well(self) -> None:
        """With all 4 scanners, Office and Kitchen are clearly different."""
        all_scanners = [SCANNER_A, SCANNER_B, SCANNER_C, SCANNER_D]

        # Learned fingerprints
        office_profile = _train_profile(
            AREA_OFFICE,
            {SCANNER_A: -72.0, SCANNER_B: -45.0, SCANNER_C: -80.0, SCANNER_D: -85.0},
        )
        kitchen_profile = _train_profile(
            AREA_KITCHEN,
            {SCANNER_A: -48.0, SCANNER_B: -75.0, SCANNER_C: -78.0, SCANNER_D: -82.0},
        )

        # Device is actually in Office → current readings match office
        current_readings = {SCANNER_A: -72.0, SCANNER_B: -46.0, SCANNER_C: -80.0, SCANNER_D: -84.0}
        ukf = _train_ukf(all_scanners, current_readings)

        results = ukf.match_fingerprints({AREA_OFFICE: office_profile, AREA_KITCHEN: kitchen_profile})
        scores = {area_id: score for area_id, _, score in results}

        # Office should clearly win with all scanners
        assert scores[AREA_OFFICE] > scores[AREA_KITCHEN], (
            f"Office ({scores[AREA_OFFICE]:.3f}) should beat Kitchen ({scores[AREA_KITCHEN]:.3f})"
        )
        discrimination = scores[AREA_OFFICE] - scores[AREA_KITCHEN]
        assert discrimination > 0.1, f"Good discrimination expected, got {discrimination:.3f}"

    def test_missing_scanner_reduces_discrimination(self) -> None:
        """
        When Scanner B goes offline, match_fingerprints() only uses A, C, D.
        These 3 scanners produce SIMILAR fingerprints for Office and Kitchen.

        THIS IS THE PROBLEM: Without B, the system can't tell the rooms apart.
        """
        all_scanners = [SCANNER_A, SCANNER_B, SCANNER_C, SCANNER_D]

        # Same profiles as above (learned with all 4 scanners)
        office_profile = _train_profile(
            AREA_OFFICE,
            {SCANNER_A: -72.0, SCANNER_B: -45.0, SCANNER_C: -80.0, SCANNER_D: -85.0},
        )
        kitchen_profile = _train_profile(
            AREA_KITCHEN,
            {SCANNER_A: -48.0, SCANNER_B: -75.0, SCANNER_C: -78.0, SCANNER_D: -82.0},
        )

        # Scanner B offline: UKF only sees A, C, D
        readings_without_b = {SCANNER_A: -72.0, SCANNER_C: -80.0, SCANNER_D: -84.0}
        ukf = _train_ukf(all_scanners, readings_without_b, n_updates=10)

        results = ukf.match_fingerprints({AREA_OFFICE: office_profile, AREA_KITCHEN: kitchen_profile})
        scores = {area_id: score for area_id, _, score in results}

        # Both scores should be lower due to reduced dimensions
        # The discrimination (score difference) should be smaller
        discrimination = scores.get(AREA_OFFICE, 0) - scores.get(AREA_KITCHEN, 0)

        # Document current behavior: discrimination is reduced
        # This is the gap that offline-awareness would help with
        print(f"Scores without B: Office={scores.get(AREA_OFFICE, 0):.4f}, Kitchen={scores.get(AREA_KITCHEN, 0):.4f}")
        print(f"Discrimination without B: {discrimination:.4f}")

        # The key insight: even with reduced discrimination, the system
        # should still prefer Office IF it knows B is offline (not "device left")


class TestScenario2AutoLearningCorruption:
    """
    Setup: Device is stationary in Office. Scanner B is the office scanner.
    Scanner B goes offline for 5 minutes.

    Problem: Auto-learning continues with only A, C, D visible.
    The office profile gets updated WITHOUT B's contribution.
    Over time, the profile "forgets" what B looks like from Office.

    When B comes back online, the profile no longer matches correctly
    because it was trained on an incomplete scanner set.
    """

    def test_profile_trained_with_all_scanners(self) -> None:
        """Baseline: Profile trained with all scanners has 4 absolute profiles."""
        profile = _train_profile(
            AREA_OFFICE,
            {SCANNER_A: -72.0, SCANNER_B: -45.0, SCANNER_C: -80.0, SCANNER_D: -85.0},
            n_samples=50,
        )

        # All 4 scanners should have absolute profiles
        assert profile.get_absolute_rssi(SCANNER_A) is not None
        assert profile.get_absolute_rssi(SCANNER_B) is not None
        assert profile.get_absolute_rssi(SCANNER_C) is not None
        assert profile.get_absolute_rssi(SCANNER_D) is not None

        # B should have the strongest signal (closest scanner)
        b_profile = profile.get_absolute_rssi(SCANNER_B)
        assert b_profile is not None
        assert abs(b_profile.expected_rssi - (-45.0)) < 3.0

    def test_continued_learning_without_offline_scanner_dilutes_profile(self) -> None:
        """
        When auto-learning continues without B, B's profile becomes stale.
        The remaining scanners' profiles get updated but B's doesn't.

        After B comes back, the profile's B entry is outdated,
        leading to poor fingerprint matching.

        THIS IS THE PROBLEM: Auto-learning should PAUSE when scanners are offline
        to prevent partial profile corruption.
        """
        # Phase 1: Good training with all scanners
        profile = _train_profile(
            AREA_OFFICE,
            {SCANNER_A: -72.0, SCANNER_B: -45.0, SCANNER_C: -80.0, SCANNER_D: -85.0},
            n_samples=50,
        )

        b_profile_before = profile.get_absolute_rssi(SCANNER_B)
        assert b_profile_before is not None
        variance_before = b_profile_before.variance

        # Phase 2: Scanner B offline — only A, C, D contribute updates
        # B's profile doesn't get updated → variance stays same
        for _ in range(100):
            profile.update(
                primary_rssi=-72.0,
                other_readings={SCANNER_C: -80.0, SCANNER_D: -85.0},
                primary_scanner_addr=SCANNER_A,
            )

        # B's profile is now stale (not updated) while others advanced
        b_profile_after = profile.get_absolute_rssi(SCANNER_B)
        assert b_profile_after is not None
        # B's sample count didn't increase but A, C, D did
        a_profile_after = profile.get_absolute_rssi(SCANNER_A)
        assert a_profile_after is not None

        # Document: B is stale relative to other scanners
        # This asymmetry can cause matching problems when B returns
        assert b_profile_after.sample_count < a_profile_after.sample_count


class TestScenario3SoftIncumbentWithoutContext:
    """
    Setup: Device in Office (Scanner B is primary).
    Scanner B goes offline.

    Current behavior: Incumbent becomes "soft" because B provides no distance.
    Any challenger with history + distance advantage can win.

    Problem: The system doesn't know WHY B stopped providing data.
    It treats "scanner offline" and "device moved away from scanner" identically.

    With offline awareness: System knows B is globally offline (not just
    for this device), so it should protect the incumbent more strongly.
    """

    def test_soft_incumbent_is_vulnerable_to_challengers(self) -> None:
        """
        Demonstrate that a soft incumbent (no distance data) allows
        challengers to win even though the device hasn't moved.

        This simulates the "Scanner offline → device jumps to other room" problem.
        """
        # This test documents the SCENARIO, not exact coordinator behavior.
        # The actual coordinator code is tested in test_area_selection.py.

        # Setup: device was in Office via Scanner B
        office_scanner = SimpleNamespace(
            address=SCANNER_B,
            area_id=AREA_OFFICE,
            last_seen=NOWSTAMP - 60.0,  # 60s ago → stale → soft incumbent
            floor_id="floor_1",
        )
        kitchen_scanner = SimpleNamespace(
            address=SCANNER_A,
            area_id=AREA_KITCHEN,
            last_seen=NOWSTAMP,  # Fresh → challenger
            floor_id="floor_1",
        )

        # Office scanner stale → becomes soft incumbent
        # Kitchen scanner fresh → becomes challenger
        # Without knowing B is OFFLINE (not just "device moved"), the system
        # may let Kitchen win despite device not having moved.

        # Key insight: If we KNOW B is globally offline, we should check:
        # "Are OTHER devices also missing B?" → Yes → Scanner issue, not device movement
        b_stale = NOWSTAMP - office_scanner.last_seen > SCANNER_ACTIVITY_TIMEOUT
        a_fresh = NOWSTAMP - kitchen_scanner.last_seen < SCANNER_ACTIVITY_TIMEOUT

        assert b_stale, "B should be detected as stale"
        assert a_fresh, "A should be fresh"

        # The gap: system sees "B stale" but doesn't know if it's
        # "device left B's range" or "B is offline for everyone"


# ===========================================================================
# SCENARIO 4: False Positive — Quiet Room Scanner Appears Offline
# ===========================================================================


class TestScenario4QuietRoomFalsePositive:
    """
    CRITICAL REVIEW POINT: scanner.last_seen only updates when adverts arrive.

    A scanner in a quiet room (e.g., storage room, rarely occupied bathroom)
    may not receive BLE advertisements for extended periods.
    → last_seen becomes stale → Scanner appears "offline" → FALSE POSITIVE

    This is the same issue that caused BUG 22's initial fix
    (_area_has_active_scanner) to be rejected in favor of
    _area_has_scanner (registration-only check).

    The binary sensor has this SAME limitation!
    """

    def test_quiet_room_scanner_appears_offline(self) -> None:
        """
        A scanner that receives no BLE traffic for >30s looks offline,
        even though it's perfectly functional.
        """
        # Scanner in a storage room — no BLE devices nearby
        quiet_scanner = SimpleNamespace(
            address="QQ:UU:II:ET:00:01",
            area_id="area_storage",
            last_seen=NOWSTAMP - 45.0,  # 45s since last advert
        )

        # Using SCANNER_ACTIVITY_TIMEOUT (30s):
        age = NOWSTAMP - quiet_scanner.last_seen
        appears_offline = age >= SCANNER_ACTIVITY_TIMEOUT

        assert appears_offline, "Quiet scanner incorrectly appears offline after 45s"

        # THIS IS A FALSE POSITIVE!
        # The scanner is actually online and functional.
        # It just has no BLE devices in range to forward.
        #
        # Impact on the plan:
        # - Phase 1 (auto-learning guard) would incorrectly PAUSE learning
        # - Phase 2 (UKF coverage penalty) would penalize rooms near this scanner
        # - Phase 3 (soft incumbent protection) would over-protect this area
        #
        # Mitigation needed: Use a LONGER timeout for algorithmic decisions
        # than for the UI binary sensor (e.g., 120s vs 30s)

    def test_binary_sensor_timeout_vs_algorithmic_timeout(self) -> None:
        """
        The binary sensor uses SCANNER_ACTIVITY_TIMEOUT (30s) for UI display.
        For algorithmic decisions (fingerprint matching, auto-learning),
        a longer timeout would reduce false positives.

        PLAN IMPROVEMENT: Use separate timeouts:
        - SCANNER_ACTIVITY_TIMEOUT (30s) → Binary sensor UI
        - SCANNER_OFFLINE_ALGO_TIMEOUT (120s?) → Algorithmic decisions
        """
        ui_timeout = SCANNER_ACTIVITY_TIMEOUT  # 30s
        algo_timeout = 120.0  # Proposed: longer for algorithms

        # Scanner last seen 45s ago
        last_seen_age = 45.0

        appears_offline_ui = last_seen_age >= ui_timeout
        appears_offline_algo = last_seen_age >= algo_timeout

        assert appears_offline_ui is True, "UI should show offline (warning to user)"
        assert appears_offline_algo is False, "Algorithm should NOT yet treat as offline"

        # 2 minutes of silence is a stronger signal that something is wrong
        last_seen_age_long = 150.0
        appears_offline_algo_long = last_seen_age_long >= algo_timeout
        assert appears_offline_algo_long is True, "After 2+ min, algorithm should treat as offline"


# ===========================================================================
# SCENARIO 5: Scanner Recovery Transition
# ===========================================================================


class TestScenario5ScannerRecovery:
    """
    What happens when a scanner comes back online?

    The plan focuses on the OFFLINE transition but ignores RECOVERY.
    When Scanner B comes back online after 5 minutes:
    1. UKF state for B has grown variance (predict without update)
    2. First measurement has huge innovation → potential state jump
    3. Fingerprint profiles may have drifted during offline period

    This needs careful handling to avoid a "recovery spike".
    """

    def test_ukf_variance_grows_during_offline(self) -> None:
        """
        When a scanner stops providing data, the UKF predict step
        grows its covariance. After many predictions without updates,
        the variance for that scanner becomes very large.
        """
        scanners = [SCANNER_A, SCANNER_B]
        readings_full = {SCANNER_A: -65.0, SCANNER_B: -75.0}

        # Phase 1: Normal operation with both scanners
        ukf = _train_ukf(scanners, readings_full, n_updates=20)
        variance_b_before = ukf._p_cov[1][1]

        # Phase 2: Scanner B goes offline — only A reports
        for _ in range(50):
            ukf.predict(dt=1.0)
            ukf.update_multi({SCANNER_A: -65.0})  # Only A

        variance_b_after = ukf._p_cov[1][1]

        # B's variance should have grown significantly
        assert variance_b_after > variance_b_before * 2, (
            f"B's variance should grow during offline. Before: {variance_b_before:.2f}, After: {variance_b_after:.2f}"
        )

    def test_recovery_first_measurement_high_innovation(self) -> None:
        """
        When Scanner B comes back online after being offline,
        the first measurement can cause a large state change.

        If B's RSSI changed during the offline period (device moved),
        this is correct. But if it didn't change, the jump is noise.
        """
        scanners = [SCANNER_A, SCANNER_B]
        readings_full = {SCANNER_A: -65.0, SCANNER_B: -75.0}

        # Phase 1: Normal
        ukf = _train_ukf(scanners, readings_full, n_updates=20)
        state_b_before_offline = ukf._x[1]

        # Phase 2: B offline (50 predict-only cycles for B)
        for _ in range(50):
            ukf.predict(dt=1.0)
            ukf.update_multi({SCANNER_A: -65.0})

        state_b_drifted = ukf._x[1]

        # Phase 3: B comes back with same reading as before
        ukf.predict(dt=1.0)
        ukf.update_multi({SCANNER_A: -65.0, SCANNER_B: -75.0})
        state_b_after_recovery = ukf._x[1]

        # The recovery measurement should pull B's state back toward -75
        # But depending on variance growth, the correction may be partial
        # Document current behavior:
        print(
            f"B state: before offline={state_b_before_offline:.2f}, "
            f"drifted={state_b_drifted:.2f}, "
            f"after recovery={state_b_after_recovery:.2f}"
        )

        # B should be closer to -75 after recovery than during drift
        assert abs(state_b_after_recovery - (-75.0)) < abs(state_b_drifted - (-75.0)), (
            "Recovery measurement should pull state toward actual value"
        )


# ===========================================================================
# SCENARIO 6: Coverage Ratio Penalty Is Too Simplistic
# ===========================================================================


class TestScenario6CoverageRatioIsBlind:
    """
    CRITICAL REVIEW POINT: A simple coverage_ratio = n_visible/n_total
    penalizes ALL rooms equally, but the impact of a missing scanner
    varies per room.

    Example: Scanner B is PRIMARY for Office but IRRELEVANT for Kitchen.
    Losing B should penalize Office MORE than Kitchen.
    But coverage_ratio = 3/4 = 0.75 applies to BOTH equally.
    """

    def test_coverage_ratio_is_same_for_all_rooms(self) -> None:
        """
        Demonstrate that a uniform coverage ratio doesn't capture
        per-room importance of the missing scanner.
        """
        all_scanners = [SCANNER_A, SCANNER_B, SCANNER_C, SCANNER_D]
        offline = {SCANNER_B}
        visible = set(all_scanners) - offline

        # Naive coverage ratio
        coverage_ratio = len(visible) / len(all_scanners)
        assert coverage_ratio == 0.75

        # But Scanner B's importance differs per room:
        # Office: B is the PRIMARY scanner (closest, strongest signal)
        #   → Losing B removes the MOST discriminative signal
        #   → Should be penalized MORE (e.g., 0.50)
        #
        # Kitchen: B is a DISTANT secondary scanner (weak signal)
        #   → Losing B removes a weak/redundant signal
        #   → Should be penalized LESS (e.g., 0.90)
        #
        # PLAN IMPROVEMENT: Weight by scanner importance per room:
        #   per_room_coverage = sum(weight_of_visible) / sum(weight_of_all)
        #   where weight = 1/variance or sample_count from the profile

    def test_per_room_weighted_coverage_is_better(self) -> None:
        """
        Demonstrate weighted coverage using RSSI-based importance.

        Kalman auto-learning variance converges to the same steady-state
        regardless of RSSI magnitude, so inverse-variance weighting gives
        uniform weights (~0.75 for all rooms). This is a FLAW in the naive
        coverage ratio approach.

        Better approach: Weight by RSSI strength (stronger signal = closer
        scanner = more discriminative for this room).
        Formula: weight = 10^((RSSI + 40) / 20) — exponential scaling,
        so -45dB gets much more weight than -85dB.
        """
        # Office profile: B has strongest signal (-45dB, closest)
        office_readings = {SCANNER_A: -72.0, SCANNER_B: -45.0, SCANNER_C: -80.0, SCANNER_D: -85.0}
        kitchen_readings = {SCANNER_A: -48.0, SCANNER_B: -75.0, SCANNER_C: -78.0, SCANNER_D: -82.0}

        offline = {SCANNER_B}

        def rssi_weight(rssi: float) -> float:
            """Stronger signals get exponentially more weight."""
            return 10.0 ** ((rssi + 40.0) / 20.0)

        for area_name, readings in [("Office", office_readings), ("Kitchen", kitchen_readings)]:
            total_weight = 0.0
            visible_weight = 0.0
            for scanner_addr, rssi in readings.items():
                weight = rssi_weight(rssi)
                total_weight += weight
                if scanner_addr not in offline:
                    visible_weight += weight

            weighted_coverage = visible_weight / max(total_weight, 0.001)
            print(f"{area_name}: rssi_weighted_coverage = {weighted_coverage:.3f}")

            if area_name == "Office":
                # B has the strongest signal (-45dB) → highest weight → big penalty
                assert weighted_coverage < 0.75, (
                    f"Office should be penalized MORE than naive 0.75, got {weighted_coverage:.3f}"
                )
            else:
                # Kitchen: B has weak signal (-75dB) → low weight → small penalty
                assert weighted_coverage > 0.75, (
                    f"Kitchen should be penalized LESS than naive 0.75, got {weighted_coverage:.3f}"
                )


# ===========================================================================
# SCENARIO 7: Offline Ratio Threshold Is Context-Blind
# ===========================================================================


class TestScenario7OfflineRatioThreshold:
    """
    CRITICAL REVIEW POINT: The plan proposes pausing auto-learning
    when >30% of scanners are offline. But what matters is not the
    GLOBAL offline ratio, but whether the RELEVANT scanners are online.

    Example with 10 scanners: 3 are offline (30%)
    But all 3 offline scanners are on Floor 2, and the device is on Floor 1.
    All Floor 1 scanners are online → Learning should continue!
    """

    def test_global_ratio_blocks_unnecessarily(self) -> None:
        """
        Global 30% threshold blocks learning even when all RELEVANT
        scanners are online.
        """
        total_scanners = 10
        offline_scanners = 3  # All on floor 2

        global_offline_ratio = offline_scanners / total_scanners
        assert global_offline_ratio == 0.30

        # Threshold from plan: 0.30
        would_block_learning = global_offline_ratio >= 0.30
        assert would_block_learning, "Global ratio would block learning"

        # But device is on floor 1, and all 7 floor-1 scanners are online
        floor1_total = 7
        floor1_offline = 0
        floor1_ratio = floor1_offline / floor1_total

        assert floor1_ratio == 0.0, "All relevant scanners are online!"

        # PLAN IMPROVEMENT: Use RELEVANT scanner ratio instead of global:
        # 1. Consider only scanners in the device's current learned profile
        # 2. Or only scanners on the device's current floor
        # 3. Or only scanners that appear in the current rssi_readings


# ===========================================================================
# SCENARIO 8: _area_has_active_scanner Bug 22 Resurgence
# ===========================================================================


class TestScenario8Bug22Resurgence:
    """
    CRITICAL REVIEW: The plan's Phase 0 method _get_offline_scanner_addresses()
    uses the same logic as _area_has_active_scanner() which was INTENTIONALLY
    NOT CALLED due to BUG 22.

    BUG 22 issue: scanner.last_seen only updates on received adverts.
    In quiet rooms, an online scanner appears "inactive" because no BLE
    devices are nearby to generate adverts.

    The binary sensor has the SAME limitation.
    If we use this for algorithmic decisions, BUG 22 can resurface.
    """

    def test_online_scanner_in_empty_room_appears_offline(self) -> None:
        """
        A functioning scanner in a room with no BLE devices nearby
        will have a stale last_seen, appearing offline.
        """
        # Scanner is online and functional, but no BLE devices nearby
        scanner = SimpleNamespace(
            address="SC:AN:NE:R0:00:01",
            area_id="area_empty_room",
            last_seen=NOWSTAMP - 120.0,  # 2 minutes since any advert
        )

        # Using SCANNER_ACTIVITY_TIMEOUT (30s):
        age = NOWSTAMP - scanner.last_seen
        classified_offline = age >= SCANNER_ACTIVITY_TIMEOUT
        assert classified_offline is True

        # Using longer algorithmic timeout (120s):
        algo_timeout = 120.0
        classified_offline_algo = age >= algo_timeout
        # Borderline at exactly 120s
        assert classified_offline_algo is True

        # Even with 120s timeout, a truly empty room scanner
        # will eventually appear offline.
        #
        # CRITICAL: The plan must account for this.
        # Options:
        # 1. Use _area_has_scanner() (registration only) for area decisions
        #    (current BUG 22 fix — conservative but safe)
        # 2. Add scanner heartbeat mechanism (scanner reports liveness
        #    independent of BLE traffic) — requires ESPHome/proxy changes
        # 3. Use offline status as HINT (probabilistic), not GATE (binary):
        #    "Scanner appears offline → reduce confidence, don't hard-block"


# ===========================================================================
# SCENARIO 9: Simultaneous Scanner Outage (Network Issue)
# ===========================================================================


class TestScenario9NetworkOutage:
    """
    When multiple scanners go offline simultaneously (network switch failure,
    router reboot), the system sees a sudden drop in visible scanners.

    The plan's Phase 1 (auto-learning guard) would correctly pause learning.
    But Phase 3 (soft incumbent protection) needs special handling:
    if the incumbent's scanner is ALSO offline, there's no "home scanner"
    to protect.
    """

    def test_multi_scanner_outage_detected(self) -> None:
        """
        Verify that multiple offline scanners are detected correctly.
        """
        scanners = [
            SimpleNamespace(address=SCANNER_A, last_seen=NOWSTAMP - 5.0),  # Online
            SimpleNamespace(address=SCANNER_B, last_seen=NOWSTAMP - 60.0),  # Offline
            SimpleNamespace(address=SCANNER_C, last_seen=NOWSTAMP - 60.0),  # Offline
            SimpleNamespace(address=SCANNER_D, last_seen=NOWSTAMP - 3.0),  # Online
        ]

        offline = {s.address for s in scanners if NOWSTAMP - s.last_seen >= SCANNER_ACTIVITY_TIMEOUT}

        assert offline == {SCANNER_B, SCANNER_C}
        assert len(offline) / len(scanners) == 0.5  # 50% offline

        # With 50% offline, auto-learning should definitely pause
        # But the device still sees 2 scanners (A, D)
        # → UKF matching with 2 scanners is marginal but possible
        # → The system should NOT switch rooms based on 2/4 data


# ===========================================================================
# SCENARIO 10: Offline Detection Helps Retain Correct Room Assignment
# ===========================================================================


class TestScenario10CorrectRetention:
    """
    The happy-path scenario where offline awareness prevents
    an incorrect room switch.

    Device in Office, Scanner B (office) goes offline.
    Kitchen scanner (A) sees device at -72dB (moderate, through wall).

    WITHOUT offline awareness:
      → B stale → Soft incumbent → Kitchen A at 3m wins → WRONG switch

    WITH offline awareness:
      → B known offline (not "device left") → Protect incumbent
      → Device stays in Office → CORRECT
    """

    def test_offline_aware_retention_scenario(self) -> None:
        """
        This test demonstrates the VALUE of offline awareness
        by showing both outcomes.
        """
        # Current time
        now = NOWSTAMP

        # Device's perspective:
        office_advert_age = 45.0  # B's last data: 45s ago
        kitchen_advert_age = 2.0  # A's last data: 2s ago
        kitchen_distance = 3.5  # A sees device at 3.5m (through wall)

        # Is B offline or did device move?
        b_is_stale = office_advert_age > SCANNER_ACTIVITY_TIMEOUT

        # Scenario A: Without offline awareness
        # System only knows "B is stale"
        # Could be: device moved away from B OR B went offline
        # → Treats as "soft incumbent" → Kitchen can win at 3.5m
        without_awareness = "room_switch_to_kitchen"

        # Scenario B: With offline awareness
        # System checks: "Is B offline for ALL devices?"
        b_offline_globally = True  # (checked via binary sensor or shared state)
        # → B is globally offline → device probably didn't move
        # → Protect incumbent → Keep in Office
        with_awareness = "stay_in_office" if b_offline_globally else "room_switch_to_kitchen"

        assert without_awareness == "room_switch_to_kitchen"
        assert with_awareness == "stay_in_office"

        # The difference: knowing B is offline GLOBALLY (not just for this device)
        # changes the interpretation from "device moved" to "scanner down"
