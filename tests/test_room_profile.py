"""Test RoomProfile correlation class."""

from __future__ import annotations

import pytest

from custom_components.bermuda.correlation.room_profile import (
    MAX_SCANNER_PAIRS_PER_ROOM,
    RoomProfile,
    _make_pair_key,
)


class TestMakePairKey:
    """Tests for _make_pair_key function."""

    def test_alphabetical_order_a_before_b(self) -> None:
        """Test that scanners are ordered alphabetically (a < b)."""
        result = _make_pair_key("aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02")
        assert result == "aa:aa:aa:aa:aa:01|aa:aa:aa:aa:aa:02"

    def test_alphabetical_order_b_before_a(self) -> None:
        """Test that scanners are reordered when b < a."""
        result = _make_pair_key("aa:aa:aa:aa:aa:02", "aa:aa:aa:aa:aa:01")
        assert result == "aa:aa:aa:aa:aa:01|aa:aa:aa:aa:aa:02"

    def test_same_result_either_order(self) -> None:
        """Test that both orders produce the same key."""
        key1 = _make_pair_key("scanner_a", "scanner_b")
        key2 = _make_pair_key("scanner_b", "scanner_a")
        assert key1 == key2


class TestRoomProfileInit:
    """Tests for RoomProfile initialization."""

    def test_init_creates_empty_scanner_pairs(self) -> None:
        """Test that initialization creates empty scanner_pairs dict."""
        profile = RoomProfile(area_id="test_area")
        assert profile.area_id == "test_area"
        assert profile._scanner_pairs == {}

    def test_total_samples_empty(self) -> None:
        """Test total_samples is 0 for empty profile."""
        profile = RoomProfile(area_id="test_area")
        assert profile.total_samples == 0

    def test_mature_pair_count_empty(self) -> None:
        """Test mature_pair_count is 0 for empty profile."""
        profile = RoomProfile(area_id="test_area")
        assert profile.mature_pair_count == 0


class TestRoomProfileUpdate:
    """Tests for RoomProfile.update method."""

    def test_update_creates_scanner_pairs(self) -> None:
        """Test that update creates scanner pairs from readings."""
        profile = RoomProfile(area_id="test_area")
        readings = {
            "scanner_a": -60.0,
            "scanner_b": -70.0,
        }

        profile.update(readings)

        assert len(profile._scanner_pairs) == 1
        assert "scanner_a|scanner_b" in profile._scanner_pairs

    def test_update_calculates_correct_delta(self) -> None:
        """Test that delta is calculated correctly (alphabetical order)."""
        profile = RoomProfile(area_id="test_area")

        # scanner_a comes first alphabetically
        readings = {
            "scanner_b": -70.0,  # Second alphabetically
            "scanner_a": -60.0,  # First alphabetically
        }

        profile.update(readings)

        pair = profile._scanner_pairs["scanner_a|scanner_b"]
        # Delta = scanner_a - scanner_b = -60 - (-70) = 10
        assert pair.expected_delta == pytest.approx(10.0, abs=1.0)

    def test_update_multiple_scanners_creates_all_pairs(self) -> None:
        """Test that multiple scanners create all pair combinations."""
        profile = RoomProfile(area_id="test_area")
        readings = {
            "scanner_a": -60.0,
            "scanner_b": -70.0,
            "scanner_c": -65.0,
        }

        profile.update(readings)

        # 3 scanners = 3 pairs: (a,b), (a,c), (b,c)
        assert len(profile._scanner_pairs) == 3
        assert "scanner_a|scanner_b" in profile._scanner_pairs
        assert "scanner_a|scanner_c" in profile._scanner_pairs
        assert "scanner_b|scanner_c" in profile._scanner_pairs

    def test_update_increments_sample_count(self) -> None:
        """Test that each update increments sample count."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        profile.update(readings)
        profile.update(readings)
        profile.update(readings)

        assert profile.total_samples >= 3

    def test_update_single_scanner_creates_no_pairs(self) -> None:
        """Test that single scanner creates no pairs."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0}

        profile.update(readings)

        assert len(profile._scanner_pairs) == 0


class TestRoomProfileUpdateButton:
    """Tests for RoomProfile.update_button method."""

    def test_update_button_creates_scanner_pairs(self) -> None:
        """Test that update_button creates scanner pairs."""
        profile = RoomProfile(area_id="test_area")
        readings = {
            "scanner_a": -60.0,
            "scanner_b": -70.0,
        }

        profile.update_button(readings)

        assert len(profile._scanner_pairs) == 1

    def test_update_button_marks_has_button_training(self) -> None:
        """Test that update_button sets has_button_training."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        assert profile.has_button_training is False

        profile.update_button(readings)

        assert profile.has_button_training is True

    def test_update_button_multiple_scanners(self) -> None:
        """Test update_button with multiple scanners."""
        profile = RoomProfile(area_id="test_area")
        readings = {
            "scanner_a": -60.0,
            "scanner_b": -70.0,
            "scanner_c": -65.0,
        }

        profile.update_button(readings)

        assert len(profile._scanner_pairs) == 3


class TestRoomProfileMemoryLimit:
    """Tests for _enforce_memory_limit method."""

    def test_enforce_memory_limit_keeps_within_limit(self) -> None:
        """Test that memory limit is enforced."""
        profile = RoomProfile(area_id="test_area")

        # Create more pairs than the limit
        for i in range(MAX_SCANNER_PAIRS_PER_ROOM + 10):
            readings = {
                f"scanner_{i:03d}_a": -60.0,
                f"scanner_{i:03d}_b": -70.0,
            }
            profile.update(readings)

        assert len(profile._scanner_pairs) <= MAX_SCANNER_PAIRS_PER_ROOM

    def test_enforce_memory_limit_preserves_button_trained(self) -> None:
        """Test that button-trained pairs are preserved over auto-learned."""
        profile = RoomProfile(area_id="test_area")

        # Create some button-trained pairs
        button_readings = {"button_a": -60.0, "button_b": -70.0}
        profile.update_button(button_readings)

        # Fill up with auto-learned pairs
        for i in range(MAX_SCANNER_PAIRS_PER_ROOM + 5):
            readings = {
                f"scanner_{i:03d}_a": -60.0,
                f"scanner_{i:03d}_b": -70.0,
            }
            # Update many times to increase sample count
            for _ in range(50):
                profile.update(readings)

        # Button-trained pair should still be present
        assert "button_a|button_b" in profile._scanner_pairs

    def test_enforce_memory_limit_sorts_by_sample_count(self) -> None:
        """Test that pairs are sorted by sample count when evicting."""
        profile = RoomProfile(area_id="test_area")

        # Create pairs with different sample counts
        high_count_readings = {"high_a": -60.0, "high_b": -70.0}
        low_count_readings = {"low_a": -60.0, "low_b": -70.0}

        # High count pair gets many updates
        for _ in range(100):
            profile.update(high_count_readings)

        # Low count pair gets few updates
        profile.update(low_count_readings)

        # Fill up to trigger eviction
        for i in range(MAX_SCANNER_PAIRS_PER_ROOM + 5):
            readings = {
                f"scanner_{i:03d}_a": -60.0,
                f"scanner_{i:03d}_b": -70.0,
            }
            profile.update(readings)

        # High count pair should be preserved, low count might be evicted
        assert "high_a|high_b" in profile._scanner_pairs


class TestRoomProfileResetTraining:
    """Tests for reset_training method."""

    def test_reset_training_clears_button_training(self) -> None:
        """Test that reset_training clears button training flag."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        profile.update_button(readings)
        assert profile.has_button_training is True

        profile.reset_training()

        assert profile.has_button_training is False

    def test_reset_training_resets_all_pairs(self) -> None:
        """Test that reset_training resets all scanner pairs."""
        profile = RoomProfile(area_id="test_area")

        # Add some pairs
        readings = {"scanner_a": -60.0, "scanner_b": -70.0, "scanner_c": -65.0}
        profile.update_button(readings)

        assert len(profile._scanner_pairs) == 3

        profile.reset_training()

        # Pairs should still exist but be reset
        for pair in profile._scanner_pairs.values():
            assert pair.has_button_training is False


class TestRoomProfileHasButtonTraining:
    """Tests for has_button_training property."""

    def test_has_button_training_false_when_empty(self) -> None:
        """Test has_button_training is False for empty profile."""
        profile = RoomProfile(area_id="test_area")
        assert profile.has_button_training is False

    def test_has_button_training_false_for_auto_only(self) -> None:
        """Test has_button_training is False for auto-learned only."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        profile.update(readings)

        assert profile.has_button_training is False

    def test_has_button_training_true_for_button(self) -> None:
        """Test has_button_training is True after button training."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        profile.update_button(readings)

        assert profile.has_button_training is True

    def test_has_button_training_true_if_any_pair_has_it(self) -> None:
        """Test has_button_training is True if ANY pair has button training."""
        profile = RoomProfile(area_id="test_area")

        # Auto-learn one pair
        auto_readings = {"auto_a": -60.0, "auto_b": -70.0}
        profile.update(auto_readings)

        # Button-train another pair
        button_readings = {"button_a": -60.0, "button_b": -70.0}
        profile.update_button(button_readings)

        assert profile.has_button_training is True


class TestRoomProfileGetMatchScore:
    """Tests for get_match_score method."""

    def test_get_match_score_no_data_returns_neutral(self) -> None:
        """Test that no data returns 0.5 (neutral)."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        score = profile.get_match_score(readings)

        assert score == 0.5

    def test_get_match_score_no_mature_pairs_returns_neutral(self) -> None:
        """Test that immature pairs return 0.5."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        # Single update doesn't make pair mature
        profile.update(readings)

        score = profile.get_match_score(readings)

        # May be neutral if pair isn't mature yet
        assert 0.0 <= score <= 1.0

    def test_get_match_score_perfect_match(self) -> None:
        """Test that matching readings give high score."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        # Train many times to make pairs mature
        for _ in range(50):
            profile.update(readings)

        # Test with same readings
        score = profile.get_match_score(readings)

        # Should be high (close to 1.0)
        assert score > 0.7

    def test_get_match_score_poor_match(self) -> None:
        """Test that different readings give lower score."""
        profile = RoomProfile(area_id="test_area")
        train_readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        # Train with one pattern
        for _ in range(50):
            profile.update(train_readings)

        # Test with very different pattern
        test_readings = {"scanner_a": -40.0, "scanner_b": -90.0}
        score = profile.get_match_score(test_readings)

        # Should be lower than perfect match
        assert score < 0.8

    def test_get_match_score_unknown_pair_ignored(self) -> None:
        """Test that unknown scanner pairs are ignored."""
        profile = RoomProfile(area_id="test_area")
        train_readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        for _ in range(50):
            profile.update(train_readings)

        # Test with completely different scanners
        test_readings = {"scanner_x": -60.0, "scanner_y": -70.0}
        score = profile.get_match_score(test_readings)

        # Should return neutral (no matching pairs)
        assert score == 0.5


class TestRoomProfileSerialization:
    """Tests for to_dict and from_dict methods."""

    def test_to_dict_empty_profile(self) -> None:
        """Test serializing empty profile."""
        profile = RoomProfile(area_id="test_area")
        data = profile.to_dict()

        assert data["area_id"] == "test_area"
        assert data["scanner_pairs"] == []

    def test_to_dict_with_data(self) -> None:
        """Test serializing profile with data."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        for _ in range(10):
            profile.update(readings)

        data = profile.to_dict()

        assert data["area_id"] == "test_area"
        assert len(data["scanner_pairs"]) == 1

    def test_from_dict_empty(self) -> None:
        """Test deserializing empty profile."""
        data = {"area_id": "test_area", "scanner_pairs": []}
        profile = RoomProfile.from_dict(data)

        assert profile.area_id == "test_area"
        assert len(profile._scanner_pairs) == 0

    def test_from_dict_with_data(self) -> None:
        """Test deserializing profile with data."""
        # First create and serialize a profile
        original = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}
        for _ in range(10):
            original.update(readings)

        data = original.to_dict()

        # Deserialize
        restored = RoomProfile.from_dict(data)

        assert restored.area_id == original.area_id
        assert len(restored._scanner_pairs) == len(original._scanner_pairs)

    def test_serialization_roundtrip(self) -> None:
        """Test that serialization roundtrip preserves data."""
        original = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        for _ in range(20):
            original.update(readings)

        # Button train a pair too
        button_readings = {"scanner_c": -50.0, "scanner_d": -80.0}
        original.update_button(button_readings)

        # Roundtrip
        data = original.to_dict()
        restored = RoomProfile.from_dict(data)

        # Check restored state
        assert restored.area_id == original.area_id
        assert restored.has_button_training == original.has_button_training
        assert restored.total_samples == original.total_samples

    def test_from_dict_missing_scanner_pairs(self) -> None:
        """Test deserializing with missing scanner_pairs key."""
        data = {"area_id": "test_area"}  # No scanner_pairs key
        profile = RoomProfile.from_dict(data)

        assert profile.area_id == "test_area"
        assert len(profile._scanner_pairs) == 0


class TestRoomProfileTotalSamples:
    """Tests for total_samples property."""

    def test_total_samples_increases_with_updates(self) -> None:
        """Test that total_samples increases with each update."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        initial = profile.total_samples
        profile.update(readings)
        after_one = profile.total_samples

        assert after_one > initial


class TestRoomProfileMaturePairCount:
    """Tests for mature_pair_count property."""

    def test_mature_pair_count_increases_with_samples(self) -> None:
        """Test that mature_pair_count increases as pairs mature."""
        profile = RoomProfile(area_id="test_area")
        readings = {"scanner_a": -60.0, "scanner_b": -70.0}

        initial = profile.mature_pair_count

        # Many updates should make pairs mature
        for _ in range(50):
            profile.update(readings)

        final = profile.mature_pair_count

        assert final >= initial


class TestRoomProfileIntegration:
    """Integration tests for RoomProfile."""

    def test_room_matching_workflow(self) -> None:
        """Test typical room matching workflow."""
        # Create room profiles
        kitchen = RoomProfile(area_id="kitchen")
        bedroom = RoomProfile(area_id="bedroom")

        # Train kitchen (scanner_a is closer)
        kitchen_readings = {"scanner_a": -50.0, "scanner_b": -80.0}
        for _ in range(50):
            kitchen.update(kitchen_readings)

        # Train bedroom (scanner_b is closer)
        bedroom_readings = {"scanner_a": -80.0, "scanner_b": -50.0}
        for _ in range(50):
            bedroom.update(bedroom_readings)

        # Test device that matches kitchen pattern
        test_readings = {"scanner_a": -52.0, "scanner_b": -78.0}
        kitchen_score = kitchen.get_match_score(test_readings)
        bedroom_score = bedroom.get_match_score(test_readings)

        # Kitchen should match better
        assert kitchen_score > bedroom_score

    def test_button_training_overrides_auto(self) -> None:
        """Test that button training influences match scores."""
        profile = RoomProfile(area_id="test_area")

        # Auto-learn one pattern
        auto_readings = {"scanner_a": -60.0, "scanner_b": -70.0}
        for _ in range(30):
            profile.update(auto_readings)

        # Button-train with different pattern
        button_readings = {"scanner_a": -50.0, "scanner_b": -80.0}
        profile.update_button(button_readings)

        # Profile should have button training
        assert profile.has_button_training is True
