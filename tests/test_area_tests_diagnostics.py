"""Tests for AreaTests diagnostic dataclass and sensor integration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.area_selection import AreaTests

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any


class TestAreaTestsDataclass:
    """Tests for AreaTests dataclass."""

    def test_area_tests_default_values(self) -> None:
        """Test that AreaTests initializes with correct default values."""
        tests = AreaTests()

        # Identity
        assert tests.device == ""

        # Decision path
        assert tests.decision_path == "UNKNOWN"

        # Area transition
        assert tests.scannername == ("", "")
        assert tests.areas == ("", "")
        assert tests.same_area is False

        # Min-distance fields
        assert tests.pcnt_diff == 0
        assert tests.last_ad_age == (0, 0)
        assert tests.this_ad_age == (0, 0)
        assert tests.distance == (0, 0)
        assert tests.hist_min_max == (0, 0)
        assert tests.floors == (None, None)
        assert tests.floor_levels == (None, None)

        # UKF matching fields
        assert tests.ukf_match_score is None
        assert tests.ukf_current_area_score is None
        assert tests.ukf_retention_mode is False
        assert tests.ukf_stickiness_applied is False
        assert tests.ukf_threshold_used is None

        # Fingerprint profile fields
        assert tests.profile_source == "NONE"
        assert tests.profile_sample_count is None
        assert tests.profile_has_button is False

        # Scannerless room fields
        assert tests.is_scannerless_room is False
        assert tests.virtual_distance is None

        # Sanity check fields
        assert tests.passed_proximity_check is None
        assert tests.passed_topological_check is None
        assert tests.passed_rssi_sanity is None
        assert tests.nearest_scanner_distance is None
        assert tests.nearest_scanner_area is None

        # Timing fields
        assert tests.winner_advert_age is None

        # Top candidates
        assert tests.top_candidates == []

        # Result
        assert tests.reason is None

    def test_area_tests_to_dict_core_fields(self) -> None:
        """Test that to_dict() includes core decision fields."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.areas = ("Kitchen", "Living Room")
        tests.same_area = False
        tests.reason = "WIN - UKF match"

        result = tests.to_dict()

        # Core fields are always present
        assert result["decision_path"] == "UKF"
        assert result["reason"] == "WIN - UKF match"
        assert result["from_area"] == "Kitchen"
        assert result["to_area"] == "Living Room"
        assert result["same_area"] is False

    def test_area_tests_to_dict_ukf_fields(self) -> None:
        """Test that to_dict() includes UKF fields when score is set."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.ukf_match_score = 0.85
        tests.ukf_current_area_score = 0.45
        tests.ukf_retention_mode = True
        tests.ukf_threshold_used = 0.15
        tests.ukf_stickiness_applied = True

        result = tests.to_dict()

        # UKF fields are included when score is set
        assert result["ukf_score"] == 0.85
        assert result["ukf_current_score"] == 0.45
        assert result["ukf_retention_mode"] is True
        assert result["ukf_threshold"] == 0.15
        assert result["ukf_stickiness_applied"] is True

    def test_area_tests_to_dict_ukf_fields_not_present_when_none(self) -> None:
        """Test that to_dict() omits UKF fields when score is None."""
        tests = AreaTests()
        tests.decision_path = "MIN_DISTANCE"
        tests.ukf_match_score = None

        result = tests.to_dict()

        # UKF fields should not be present
        assert "ukf_score" not in result
        assert "ukf_threshold" not in result

    def test_area_tests_to_dict_profile_fields(self) -> None:
        """Test that to_dict() includes profile fields when source is not NONE."""
        tests = AreaTests()
        tests.profile_source = "BUTTON_TRAINED"
        tests.profile_sample_count = 150
        tests.profile_has_button = True

        result = tests.to_dict()

        assert result["profile_source"] == "BUTTON_TRAINED"
        assert result["profile_samples"] == 150
        assert result["profile_has_button_training"] is True

    def test_area_tests_to_dict_profile_fields_not_present_when_none(self) -> None:
        """Test that to_dict() omits profile fields when source is NONE."""
        tests = AreaTests()
        tests.profile_source = "NONE"

        result = tests.to_dict()

        assert "profile_source" not in result
        assert "profile_samples" not in result

    def test_area_tests_to_dict_scannerless_room(self) -> None:
        """Test that to_dict() includes scannerless room fields."""
        tests = AreaTests()
        tests.is_scannerless_room = True
        tests.virtual_distance = 2.5

        result = tests.to_dict()

        assert result["is_scannerless_room"] is True
        assert result["virtual_distance_m"] == 2.5

    def test_area_tests_to_dict_distance_fields(self) -> None:
        """Test that to_dict() includes distance fields when set."""
        tests = AreaTests()
        tests.distance = (4.0, 2.5)
        tests.pcnt_diff = 0.375

        result = tests.to_dict()

        assert result["distance_incumbent_m"] == 4.0
        assert result["distance_challenger_m"] == 2.5
        assert result["distance_diff_percent"] == 37.5

    def test_area_tests_to_dict_sanity_check_fields(self) -> None:
        """Test that to_dict() includes sanity check fields when checked."""
        tests = AreaTests()
        tests.passed_proximity_check = True
        tests.passed_topological_check = False
        tests.passed_rssi_sanity = True
        tests.nearest_scanner_distance = 1.5
        tests.nearest_scanner_area = "Bedroom"

        result = tests.to_dict()

        assert result["sanity_proximity_passed"] is True
        assert result["sanity_topological_passed"] is False
        assert result["sanity_rssi_passed"] is True
        assert result["nearest_scanner_m"] == 1.5
        assert result["nearest_scanner_area"] == "Bedroom"

    def test_area_tests_to_dict_sanity_fields_not_present_when_none(self) -> None:
        """Test that to_dict() omits sanity check fields when not checked."""
        tests = AreaTests()
        # All sanity checks are None by default

        result = tests.to_dict()

        assert "sanity_proximity_passed" not in result
        assert "sanity_topological_passed" not in result
        assert "sanity_rssi_passed" not in result

    def test_area_tests_to_dict_top_candidates(self) -> None:
        """Test that to_dict() includes top candidates."""
        tests = AreaTests()
        tests.top_candidates = [
            {"area": "Living Room", "score": 0.85, "type": "UKF"},
            {"area": "Kitchen", "score": 0.72, "type": "UKF"},
        ]

        result = tests.to_dict()

        assert len(result["top_candidates"]) == 2
        assert result["top_candidates"][0]["area"] == "Living Room"
        assert result["top_candidates"][0]["score"] == 0.85
        assert result["top_candidates"][1]["area"] == "Kitchen"

    def test_area_tests_to_dict_empty_top_candidates(self) -> None:
        """Test that to_dict() omits top_candidates when empty."""
        tests = AreaTests()
        tests.top_candidates = []

        result = tests.to_dict()

        assert "top_candidates" not in result

    def test_area_tests_to_dict_with_default_values(self) -> None:
        """Test that to_dict() handles default values correctly."""
        tests = AreaTests()
        result = tests.to_dict()

        # Core fields are always present
        assert result["decision_path"] == "UNKNOWN"
        assert result["reason"] is None
        assert result["from_area"] is None
        assert result["to_area"] is None
        assert result["same_area"] is False
        assert result["is_scannerless_room"] is False

    def test_sensortext_basic_format(self) -> None:
        """Test sensortext() produces readable output."""
        tests = AreaTests()
        tests.device = "Test Device"
        tests.decision_path = "MIN_DISTANCE"
        tests.areas = ("Kitchen", "Living Room")
        tests.distance = (4.0, 2.5)
        tests.pcnt_diff = 0.375
        tests.reason = "WIN - closer"

        text = tests.sensortext()

        # Should contain key information
        assert "Living Room" in text
        assert "MIN_DISTANCE" in text
        assert "WIN" in text or "closer" in text.lower()

    def test_sensortext_ukf_includes_score(self) -> None:
        """Test sensortext() includes UKF score when available."""
        tests = AreaTests()
        tests.device = "Test Device"
        tests.decision_path = "UKF"
        tests.areas = ("", "Living Room")
        tests.ukf_match_score = 0.85
        tests.reason = "WIN - UKF match"

        text = tests.sensortext()

        # Should include UKF score
        assert "0.85" in text

    def test_sensortext_ukf_retention_mode_indicator(self) -> None:
        """Test sensortext() shows retention mode indicator."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.areas = ("", "Living Room")
        tests.ukf_match_score = 0.25
        tests.ukf_retention_mode = True
        tests.reason = "WIN - retention"

        text = tests.sensortext()

        # Should show (R) for retention mode
        assert "(R)" in text

    def test_sensortext_ukf_switch_mode_indicator(self) -> None:
        """Test sensortext() shows switch mode indicator."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.areas = ("", "Living Room")
        tests.ukf_match_score = 0.75
        tests.ukf_retention_mode = False
        tests.reason = "WIN - switch"

        text = tests.sensortext()

        # Should show (S) for switch mode
        assert "(S)" in text

    def test_sensortext_with_stickiness_indicator(self) -> None:
        """Test sensortext() shows stickiness indicator."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.areas = ("", "Living Room")
        tests.ukf_match_score = 0.35
        tests.ukf_stickiness_applied = True
        tests.reason = "KEEP - stickiness"

        text = tests.sensortext()

        # Should show + for stickiness applied
        assert "+" in text

    def test_sensortext_scannerless_room_shows_virtual_distance(self) -> None:
        """Test sensortext() shows virtual distance for scannerless rooms."""
        tests = AreaTests()
        tests.decision_path = "VIRTUAL"
        tests.areas = ("", "Basement")
        tests.is_scannerless_room = True
        tests.virtual_distance = 2.5
        tests.reason = "WIN - virtual match"

        text = tests.sensortext()

        # Should show virtual distance
        assert "Virt:" in text or "2.5" in text

    def test_sensortext_with_profile_info(self) -> None:
        """Test sensortext() shows profile info when available."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.areas = ("", "Living Room")
        tests.profile_source = "BUTTON_TRAINED"
        tests.profile_has_button = True
        tests.profile_sample_count = 60
        tests.ukf_match_score = 0.85
        tests.reason = "WIN - match"

        text = tests.sensortext()

        # Should show button-trained indicator
        assert "BTN" in text or "60" in text


class TestAreaTestsDecisionPaths:
    """Tests for different decision path scenarios."""

    def test_ukf_decision_path_fields(self) -> None:
        """Test that UKF decision path populates relevant fields."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.ukf_match_score = 0.75
        tests.ukf_retention_mode = True
        tests.ukf_threshold_used = 0.15
        tests.passed_topological_check = True
        tests.passed_proximity_check = True
        tests.profile_source = "BUTTON_TRAINED"
        tests.profile_has_button = True
        tests.profile_sample_count = 100

        result = tests.to_dict()

        assert result["decision_path"] == "UKF"
        assert result["ukf_score"] == 0.75
        assert result["ukf_retention_mode"] is True
        assert result["ukf_threshold"] == 0.15
        assert result["sanity_topological_passed"] is True
        assert result["sanity_proximity_passed"] is True
        assert result["profile_source"] == "BUTTON_TRAINED"

    def test_min_distance_decision_path_fields(self) -> None:
        """Test that MIN_DISTANCE decision path populates relevant fields."""
        tests = AreaTests()
        tests.decision_path = "MIN_DISTANCE"
        tests.distance = (3.0, 1.5)
        tests.pcnt_diff = 0.50
        tests.same_area = False
        tests.floors = ("Ground", "Ground")
        tests.areas = ("Room A", "Room B")

        result = tests.to_dict()

        assert result["decision_path"] == "MIN_DISTANCE"
        assert result["distance_incumbent_m"] == 3.0
        assert result["distance_challenger_m"] == 1.5
        assert result["distance_diff_percent"] == 50.0
        assert result["same_area"] is False

    def test_virtual_decision_path_for_scannerless_rooms(self) -> None:
        """Test VIRTUAL decision path for scannerless rooms."""
        tests = AreaTests()
        tests.decision_path = "VIRTUAL"
        tests.is_scannerless_room = True
        tests.virtual_distance = 1.75
        tests.ukf_match_score = 0.50
        tests.profile_has_button = True
        tests.profile_source = "BUTTON_TRAINED"
        tests.profile_sample_count = 60

        result = tests.to_dict()

        assert result["decision_path"] == "VIRTUAL"
        assert result["is_scannerless_room"] is True
        assert result["virtual_distance_m"] == 1.75
        assert result["ukf_score"] == 0.5


class TestAreaTestsTopCandidates:
    """Tests for top_candidates field."""

    def test_top_candidates_empty_by_default(self) -> None:
        """Test that top_candidates is empty by default."""
        tests = AreaTests()
        assert tests.top_candidates == []

    def test_top_candidates_with_multiple_entries(self) -> None:
        """Test that top_candidates can hold multiple entries."""
        tests = AreaTests()
        tests.top_candidates = [
            {"area": "Living Room", "score": 0.85, "type": "UKF"},
            {"area": "Kitchen", "score": 0.72, "type": "UKF"},
            {"area": "Bedroom", "score": 0.61, "type": "UKF"},
        ]

        result = tests.to_dict()
        assert len(result["top_candidates"]) == 3
        assert result["top_candidates"][0]["area"] == "Living Room"
        assert result["top_candidates"][0]["score"] == 0.85
        assert result["top_candidates"][1]["area"] == "Kitchen"
        assert result["top_candidates"][2]["area"] == "Bedroom"


class TestAreaTestsSanityChecks:
    """Tests for sanity check fields."""

    def test_sanity_checks_none_by_default(self) -> None:
        """Test that sanity check fields are None by default (not checked)."""
        tests = AreaTests()

        assert tests.passed_proximity_check is None
        assert tests.passed_topological_check is None
        assert tests.passed_rssi_sanity is None

    def test_sanity_checks_true_when_passed(self) -> None:
        """Test sanity check fields when all checks pass."""
        tests = AreaTests()
        tests.passed_proximity_check = True
        tests.passed_topological_check = True
        tests.passed_rssi_sanity = True
        tests.nearest_scanner_distance = 0.8
        tests.nearest_scanner_area = "Office"

        result = tests.to_dict()

        assert result["sanity_proximity_passed"] is True
        assert result["sanity_topological_passed"] is True
        assert result["sanity_rssi_passed"] is True
        assert result["nearest_scanner_m"] == 0.8
        assert result["nearest_scanner_area"] == "Office"

    def test_sanity_checks_false_when_failed(self) -> None:
        """Test sanity check fields when checks fail."""
        tests = AreaTests()
        tests.passed_proximity_check = False
        tests.passed_topological_check = False
        tests.passed_rssi_sanity = False

        result = tests.to_dict()

        assert result["sanity_proximity_passed"] is False
        assert result["sanity_topological_passed"] is False
        assert result["sanity_rssi_passed"] is False


class TestAreaTestsSensortextSanityFailures:
    """Tests for sensortext() sanity check failure display."""

    def test_sensortext_shows_proximity_failure(self) -> None:
        """Test sensortext() shows PROX warning when proximity check fails."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.areas = ("", "Living Room")
        tests.passed_proximity_check = False
        tests.ukf_match_score = 0.85
        tests.reason = "FAIL - proximity"

        text = tests.sensortext()

        # Should show proximity failure warning
        assert "PROX" in text

    def test_sensortext_shows_topological_failure(self) -> None:
        """Test sensortext() shows TOPO warning when topological check fails."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.areas = ("", "Basement")
        tests.passed_topological_check = False
        tests.ukf_match_score = 0.70
        tests.reason = "FAIL - topological"

        text = tests.sensortext()

        # Should show topological failure warning
        assert "TOPO" in text

    def test_sensortext_shows_rssi_sanity_failure(self) -> None:
        """Test sensortext() shows RSSI warning when RSSI sanity check fails."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.areas = ("", "Bedroom")
        tests.passed_rssi_sanity = False
        tests.ukf_match_score = 0.65
        tests.reason = "FAIL - RSSI sanity"

        text = tests.sensortext()

        # Should show RSSI failure warning
        assert "RSSI" in text

    def test_sensortext_shows_multiple_failures(self) -> None:
        """Test sensortext() shows all failure warnings when multiple checks fail."""
        tests = AreaTests()
        tests.decision_path = "UKF"
        tests.areas = ("", "Kitchen")
        tests.passed_proximity_check = False
        tests.passed_topological_check = False
        tests.passed_rssi_sanity = False
        tests.ukf_match_score = 0.45
        tests.reason = "FAIL - multiple"

        text = tests.sensortext()

        # Should show all failure warnings
        assert "PROX" in text
        assert "TOPO" in text
        assert "RSSI" in text


class TestAreaTestsWinnerAdvertAge:
    """Tests for winner_advert_age field."""

    def test_sensortext_shows_age_when_stale(self) -> None:
        """Test sensortext() shows age warning when advert is stale (> 10s)."""
        tests = AreaTests()
        tests.decision_path = "MIN_DISTANCE"
        tests.areas = ("", "Office")
        tests.winner_advert_age = 25.0
        tests.distance = (0, 2.5)
        tests.reason = "WIN - closest"

        text = tests.sensortext()

        # Should show age warning for stale adverts
        assert "Age:" in text
        assert "25" in text

    def test_sensortext_no_age_when_fresh(self) -> None:
        """Test sensortext() does not show age when advert is fresh (< 10s)."""
        tests = AreaTests()
        tests.decision_path = "MIN_DISTANCE"
        tests.areas = ("", "Office")
        tests.winner_advert_age = 5.0  # Less than 10 seconds
        tests.distance = (0, 2.5)
        tests.reason = "WIN - closest"

        text = tests.sensortext()

        # Should NOT show age when fresh
        assert "Age:" not in text

    def test_to_dict_includes_winner_advert_age(self) -> None:
        """Test to_dict() includes winner_advert_age_s when set."""
        tests = AreaTests()
        tests.winner_advert_age = 15.7

        result = tests.to_dict()

        assert "winner_advert_age_s" in result
        assert result["winner_advert_age_s"] == 15.7

    def test_to_dict_omits_winner_advert_age_when_none(self) -> None:
        """Test to_dict() omits winner_advert_age_s when None."""
        tests = AreaTests()
        tests.winner_advert_age = None

        result = tests.to_dict()

        assert "winner_advert_age_s" not in result


class TestAreaTestsStrMethod:
    """Tests for __str__() verbose debug output."""

    def test_str_basic_output(self) -> None:
        """Test __str__() produces basic debug output."""
        tests = AreaTests()
        tests.device = "Test Device"
        tests.decision_path = "MIN_DISTANCE"
        tests.reason = "WIN - closer"
        tests.areas = ("Kitchen", "Living Room")

        output = str(tests)

        assert "Test Device" in output
        assert "MIN_DISTANCE" in output
        assert "WIN - closer" in output
        assert "Kitchen" in output
        assert "Living Room" in output

    def test_str_ukf_fields(self) -> None:
        """Test __str__() includes UKF information."""
        tests = AreaTests()
        tests.device = "UKF Device"
        tests.decision_path = "UKF"
        tests.ukf_match_score = 0.85
        tests.ukf_threshold_used = 0.15
        tests.ukf_retention_mode = True
        tests.ukf_stickiness_applied = True
        tests.areas = ("", "Office")

        output = str(tests)

        assert "UKF Score" in output
        assert "0.850" in output
        assert "threshold" in output.lower()
        assert "Retention" in output
        assert "Stickiness" in output

    def test_str_scannerless_room(self) -> None:
        """Test __str__() includes scannerless room info."""
        tests = AreaTests()
        tests.device = "Virtual Device"
        tests.decision_path = "VIRTUAL"
        tests.is_scannerless_room = True
        tests.virtual_distance = 2.5
        tests.areas = ("", "Basement")

        output = str(tests)

        assert "Scannerless" in output
        assert "Virtual Distance" in output
        assert "2.50" in output

    def test_str_profile_info(self) -> None:
        """Test __str__() includes profile information."""
        tests = AreaTests()
        tests.device = "Profile Device"
        tests.decision_path = "UKF"
        tests.profile_source = "BUTTON_TRAINED"
        tests.profile_sample_count = 100
        tests.ukf_match_score = 0.75
        tests.areas = ("", "Bedroom")

        output = str(tests)

        assert "Profile" in output
        assert "BUTTON_TRAINED" in output
        assert "100" in output

    def test_str_distance_info(self) -> None:
        """Test __str__() includes distance information."""
        tests = AreaTests()
        tests.device = "Distance Device"
        tests.decision_path = "MIN_DISTANCE"
        tests.distance = (3.5, 2.0)
        tests.pcnt_diff = 0.43
        tests.areas = ("Room A", "Room B")

        output = str(tests)

        assert "Distance" in output
        assert "3.50" in output
        assert "2.00" in output

    def test_str_sanity_checks(self) -> None:
        """Test __str__() includes sanity check results."""
        tests = AreaTests()
        tests.device = "Sanity Device"
        tests.decision_path = "UKF"
        tests.passed_proximity_check = True
        tests.passed_topological_check = False
        tests.passed_rssi_sanity = True
        tests.ukf_match_score = 0.60
        tests.areas = ("", "Hall")

        output = str(tests)

        assert "Sanity" in output
        assert "Proximity" in output
        assert "Topo" in output
        assert "RSSI" in output
        # Check marks for pass/fail
        assert "✓" in output
        assert "✗" in output

    def test_str_top_candidates(self) -> None:
        """Test __str__() includes top candidates."""
        tests = AreaTests()
        tests.device = "Candidate Device"
        tests.decision_path = "UKF"
        tests.top_candidates = [
            {"area": "Living Room", "score": 0.85, "type": "UKF"},
            {"area": "Kitchen", "score": 0.72, "type": "UKF"},
        ]
        tests.ukf_match_score = 0.85
        tests.areas = ("", "Living Room")

        output = str(tests)

        assert "Top Candidates" in output
        assert "Living Room" in output
        assert "0.85" in output

    def test_str_floor_transition(self) -> None:
        """Test __str__() shows floor transition info in to_dict()."""
        tests = AreaTests()
        tests.device = "Floor Device"
        tests.decision_path = "MIN_DISTANCE"
        tests.floors = ("Ground Floor", "First Floor")
        tests.areas = ("Room A", "Room B")

        result = tests.to_dict()

        assert result["from_floor"] == "Ground Floor"
        assert result["to_floor"] == "First Floor"


class TestAreaTestsTopCandidatesLimiting:
    """Tests for top_candidates limiting behavior."""

    def test_to_dict_limits_top_candidates_to_five(self) -> None:
        """Test that to_dict() limits top_candidates to 5 entries."""
        tests = AreaTests()
        tests.top_candidates = [
            {"area": f"Room {i}", "score": 0.9 - i * 0.05, "type": "UKF"}
            for i in range(10)  # Create 10 candidates
        ]

        result = tests.to_dict()

        # Should be limited to 5
        assert len(result["top_candidates"]) == 5
        # First should be Room 0 with highest score
        assert result["top_candidates"][0]["area"] == "Room 0"
        # Last should be Room 4
        assert result["top_candidates"][4]["area"] == "Room 4"

    def test_to_dict_handles_missing_fields_in_candidates(self) -> None:
        """Test that to_dict() handles candidates with missing fields."""
        tests = AreaTests()
        tests.top_candidates = [
            {"area": "Room A"},  # Missing score and type
            {"area": "Room B", "distance": 2.5},  # Has distance instead of score
        ]

        result = tests.to_dict()

        assert len(result["top_candidates"]) == 2
        assert result["top_candidates"][0]["score"] is None
        assert result["top_candidates"][1]["distance"] == 2.5


class TestSensorExtraStateAttributes:
    """Tests for BermudaSensorAreaSwitchReason.extra_state_attributes property."""

    def test_extra_state_attributes_returns_none_when_area_tests_is_none(
        self,
    ) -> None:
        """Test extra_state_attributes returns None when area_tests is None."""
        from custom_components.bermuda.sensor import BermudaSensorAreaSwitchReason

        # Create a mock device with area_tests = None
        mock_device = MagicMock()
        mock_device.area_tests = None
        mock_device.unique_id = "test_device_123"
        mock_device.name = "Test Device"
        mock_device.diag_area_switch = "Test diag"

        # Create a mock coordinator
        mock_coordinator = MagicMock()

        # Create sensor instance using __new__ to skip __init__
        sensor = BermudaSensorAreaSwitchReason.__new__(BermudaSensorAreaSwitchReason)
        sensor._device = mock_device
        sensor._coordinator = mock_coordinator

        # Test that extra_state_attributes returns None
        result = sensor.extra_state_attributes
        assert result is None

    def test_extra_state_attributes_returns_dict_when_area_tests_set(self) -> None:
        """Test extra_state_attributes returns to_dict() when area_tests is set."""
        from custom_components.bermuda.sensor import BermudaSensorAreaSwitchReason

        # Create AreaTests with some data
        area_tests = AreaTests()
        area_tests.decision_path = "UKF"
        area_tests.areas = ("Kitchen", "Living Room")
        area_tests.ukf_match_score = 0.85
        area_tests.reason = "WIN - UKF match"

        # Create a mock device with area_tests set
        mock_device = MagicMock()
        mock_device.area_tests = area_tests
        mock_device.unique_id = "test_device_456"
        mock_device.name = "Test Device"
        mock_device.diag_area_switch = "Test diag"

        # Create a mock coordinator
        mock_coordinator = MagicMock()

        # Create sensor instance using __new__ to skip __init__
        sensor = BermudaSensorAreaSwitchReason.__new__(BermudaSensorAreaSwitchReason)
        sensor._device = mock_device
        sensor._coordinator = mock_coordinator

        # Test that extra_state_attributes returns the to_dict() result
        result = sensor.extra_state_attributes
        assert result is not None
        assert result["decision_path"] == "UKF"
        assert result["from_area"] == "Kitchen"
        assert result["to_area"] == "Living Room"
        assert result["ukf_score"] == 0.85
        assert result["reason"] == "WIN - UKF match"

    def test_extra_state_attributes_includes_all_ukf_fields(self) -> None:
        """Test extra_state_attributes includes all UKF-related fields."""
        from custom_components.bermuda.sensor import BermudaSensorAreaSwitchReason

        # Create comprehensive AreaTests
        area_tests = AreaTests()
        area_tests.decision_path = "UKF"
        area_tests.areas = ("", "Bedroom")
        area_tests.ukf_match_score = 0.75
        area_tests.ukf_current_area_score = 0.55
        area_tests.ukf_retention_mode = True
        area_tests.ukf_stickiness_applied = True
        area_tests.ukf_threshold_used = 0.15
        area_tests.profile_source = "BUTTON_TRAINED"
        area_tests.profile_sample_count = 60
        area_tests.profile_has_button = True
        area_tests.passed_proximity_check = True
        area_tests.passed_topological_check = True
        area_tests.reason = "WIN"

        # Create sensor with mock device
        mock_device = MagicMock()
        mock_device.area_tests = area_tests

        sensor = BermudaSensorAreaSwitchReason.__new__(BermudaSensorAreaSwitchReason)
        sensor._device = mock_device
        sensor._coordinator = MagicMock()

        result = sensor.extra_state_attributes
        assert result is not None

        # Verify UKF fields
        assert result["ukf_score"] == 0.75
        assert result["ukf_current_score"] == 0.55
        assert result["ukf_retention_mode"] is True
        assert result["ukf_stickiness_applied"] is True
        assert result["ukf_threshold"] == 0.15

        # Verify profile fields
        assert result["profile_source"] == "BUTTON_TRAINED"
        assert result["profile_samples"] == 60
        assert result["profile_has_button_training"] is True

        # Verify sanity checks
        assert result["sanity_proximity_passed"] is True
        assert result["sanity_topological_passed"] is True
