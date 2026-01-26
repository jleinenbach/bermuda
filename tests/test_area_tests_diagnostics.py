"""Tests for AreaTests diagnostic dataclass and sensor integration."""

from __future__ import annotations

from custom_components.bermuda.area_selection import AreaTests


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
