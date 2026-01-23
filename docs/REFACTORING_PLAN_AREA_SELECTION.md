# Refactoring Plan: area_selection.py

## Executive Summary

This document outlines a comprehensive refactoring strategy for `custom_components/bermuda/area_selection.py` (2145 lines). The goal is to maximize maintainability and performance while ensuring 100% functional parity.

**Key Metrics:**
- Current LOC: 2145
- Target LOC: ~1800 (through deduplication, not feature removal)
- Current Cyclomatic Complexity: 50+ (estimated)
- Target Cyclomatic Complexity: <15 per method
- Test Coverage Goal: 90%+

---

## Phase 0: Preparation (Risk: NONE)

### 0.1 Baseline Test Suite Creation

**Before ANY refactoring**, create comprehensive tests that capture current behavior.

```bash
# Create test file structure
tests/
├── test_area_selection_unit.py      # Unit tests for helper functions
├── test_area_selection_ukf.py       # UKF path integration tests
├── test_area_selection_mindist.py   # Min-distance path integration tests
├── test_area_selection_edge_cases.py # Edge case coverage
└── fixtures/
    └── area_selection_scenarios.py  # Shared test scenarios
```

**Test Scenarios to Cover:**
1. Basic same-floor room switch
2. Cross-floor room switch with streak protection
3. Scannerless room detection via UKF
4. Virtual distance competition
5. Soft incumbent protection
6. RSSI fallback when no distance contenders
7. Stickiness bonus for current area
8. Bootstrap (no current area)
9. Stale incumbent handling
10. Co-visibility confidence checks

**Deliverable:** 50+ parameterized tests with >85% branch coverage

### 0.2 Performance Baseline

Create benchmarks for critical paths:

```python
# tests/benchmarks/bench_area_selection.py
import pytest

@pytest.mark.benchmark
def test_ukf_selection_10_scanners(benchmark, coordinator_fixture):
    """Benchmark UKF selection with 10 scanners."""
    device = create_device_with_adverts(10)
    result = benchmark(coordinator.area_selection._refresh_area_by_ukf, device)
    assert result is not None

@pytest.mark.benchmark
def test_min_distance_20_adverts(benchmark, coordinator_fixture):
    """Benchmark min-distance with 20 adverts."""
    device = create_device_with_adverts(20)
    benchmark(coordinator.area_selection._refresh_area_by_min_distance, device)
```

---

## Phase 1: Constants Extraction (Risk: LOW)

### 1.1 New Constants for const.py

Add missing magic numbers as named constants:

```python
# In const.py - Area Selection Section

# UKF Sanity Check Constants
UKF_RSSI_SIGMA_MULTIPLIER: Final = 3.0  # Standard deviations for RSSI variance check
UKF_MIN_RSSI_VARIANCE: Final = 4.0  # Minimum variance floor for RSSI checks
UKF_PROXIMITY_THRESHOLD_METERS: Final = 2.0  # Very close = almost certainly in room
UKF_HIGH_CONFIDENCE_OVERRIDE: Final = 0.85  # Score needed to override proximity

# Min-Distance Hysteresis Constants
MINDIST_SIGNIFICANT_IMPROVEMENT: Final = 0.30  # 30% improvement for fast-track
MINDIST_PENDING_IMPROVEMENT: Final = 0.20  # 20% improvement to reset pending

# Floor Distance Scaling
FLOOR_SKIP_MARGIN_INCREMENT: Final = 0.35  # Additional margin per skipped floor
FLOOR_MARGIN_CAP: Final = 0.80  # Maximum cross-floor margin
FLOOR_ESCAPE_CAP: Final = 0.95  # Maximum escape threshold

# Streak Logic
STREAK_LOW_CONFIDENCE_THRESHOLD: Final = 0.5  # Below this, don't count toward streak
```

### 1.2 Import Updates in area_selection.py

```python
from .const import (
    # ... existing imports ...
    UKF_RSSI_SIGMA_MULTIPLIER,
    UKF_MIN_RSSI_VARIANCE,
    UKF_PROXIMITY_THRESHOLD_METERS,
    UKF_HIGH_CONFIDENCE_OVERRIDE,
    MINDIST_SIGNIFICANT_IMPROVEMENT,
    MINDIST_PENDING_IMPROVEMENT,
    FLOOR_SKIP_MARGIN_INCREMENT,
    FLOOR_MARGIN_CAP,
    FLOOR_ESCAPE_CAP,
    STREAK_LOW_CONFIDENCE_THRESHOLD,
)
```

**Files Changed:** `const.py`, `area_selection.py`
**Lines Changed:** ~30
**Risk:** LOW - Only moving literals to named constants

---

## Phase 2: Public Property for Private Attribute (Risk: LOW)

### 2.1 Add Property to BermudaDevice

```python
# In bermuda_device.py

class BermudaDevice:
    def __init__(self, ...):
        # ... existing code ...
        self._ukf_scannerless_area: bool = False

    @property
    def ukf_scannerless_area(self) -> bool:
        """Whether this device is currently in a scannerless area (detected via UKF)."""
        return self._ukf_scannerless_area

    @ukf_scannerless_area.setter
    def ukf_scannerless_area(self, value: bool) -> None:
        """Set the scannerless area flag."""
        self._ukf_scannerless_area = value
```

### 2.2 Update area_selection.py References

Replace all `# noqa: SLF001` lines:

```python
# Before (3 occurrences):
device._ukf_scannerless_area = scanner_less_room  # noqa: SLF001

# After:
device.ukf_scannerless_area = scanner_less_room
```

**Files Changed:** `bermuda_device.py`, `area_selection.py`
**Lines Changed:** ~10
**Risk:** LOW - Simple property wrapper

---

## Phase 3: Extract Nested Functions (Risk: MEDIUM)

### 3.1 Create AdvertAnalyzer Helper Class

Extract the 9 nested functions from `_refresh_area_by_min_distance` into a dedicated helper class:

```python
# New file: custom_components/bermuda/area_selection_helpers.py

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bermuda_advert import BermudaAdvert
    from .bermuda_device import BermudaDevice

class AdvertAnalyzer:
    """
    Helper class for analyzing BLE advertisements in area selection.

    Extracted from _refresh_area_by_min_distance for testability.
    """

    def __init__(
        self,
        nowstamp: float,
        max_radius: float,
        evidence_window: float,
    ) -> None:
        """Initialize analyzer with current context."""
        self._nowstamp = nowstamp
        self._max_radius = max_radius
        self._evidence_window = evidence_window

    def effective_distance(self, advert: BermudaAdvert | None) -> float | None:
        """
        Get the effective distance for an advert.

        Uses area_distance if available (scannerless rooms),
        otherwise uses rssi_distance.
        """
        if advert is None:
            return None
        if advert.area_distance is not None:
            return advert.area_distance
        return advert.rssi_distance

    def is_within_evidence_window(self, advert: BermudaAdvert | None) -> bool:
        """Check if advert is within the evidence time window."""
        if advert is None or advert.stamp is None:
            return False
        return self._nowstamp - advert.stamp < self._evidence_window

    def has_valid_area(self, advert: BermudaAdvert | None) -> bool:
        """Check if advert has a valid area assignment."""
        if advert is None:
            return False
        if advert.area_id is not None:
            return True
        if advert.scanner_device is not None:
            return getattr(advert.scanner_device, "area_id", None) is not None
        return False

    def is_area_candidate(self, advert: BermudaAdvert | None) -> bool:
        """Check if advert can be considered for area selection."""
        return self.has_valid_area(advert) and self.is_within_evidence_window(advert)

    def is_distance_contender(self, advert: BermudaAdvert | None) -> bool:
        """
        Check if advert qualifies as a distance contender.

        Requirements:
        - Valid area
        - Within evidence window
        - Has valid distance within max_radius
        """
        if not self.is_area_candidate(advert):
            return False
        distance = self.effective_distance(advert)
        if distance is None:
            return False
        return distance <= self._max_radius

    def get_floor_id(self, advert: BermudaAdvert | None) -> str | None:
        """Get floor_id from advert's scanner device."""
        if advert is None or advert.scanner_device is None:
            return None
        return getattr(advert.scanner_device, "floor_id", None)

    def is_cross_floor(
        self,
        current: BermudaAdvert | None,
        candidate: BermudaAdvert | None
    ) -> bool:
        """Check if switching from current to candidate would be cross-floor."""
        cur_floor = self.get_floor_id(current)
        cand_floor = self.get_floor_id(candidate)
        return (
            cur_floor is not None
            and cand_floor is not None
            and cur_floor != cand_floor
        )

    def get_visible_scanner_addresses(
        self,
        device: BermudaDevice
    ) -> set[str]:
        """Get addresses of all scanners currently seeing this device."""
        visible: set[str] = set()
        for adv in device.adverts.values():
            if self.is_distance_contender(adv) and adv.scanner_device is not None:
                visible.add(adv.scanner_device.address)
        return visible
```

### 3.2 Update _refresh_area_by_min_distance

```python
def _refresh_area_by_min_distance(self, device: BermudaDevice) -> None:
    """Very basic Area setting by finding closest proxy to a given device."""
    nowstamp = monotonic_time_coarse()
    _max_radius = self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)

    # Create analyzer for this update cycle
    analyzer = AdvertAnalyzer(
        nowstamp=nowstamp,
        max_radius=_max_radius,
        evidence_window=EVIDENCE_WINDOW_SECONDS,
    )

    # Now use analyzer methods instead of nested functions
    # e.g., analyzer.effective_distance(advert) instead of _effective_distance(advert)
    # ...
```

**Files Changed:** New `area_selection_helpers.py`, `area_selection.py`
**Lines Changed:** ~200 (new file), ~50 (modifications)
**Risk:** MEDIUM - Logic extraction requires careful testing

---

## Phase 4: Extract Shared Logic (Risk: MEDIUM)

### 4.1 Extract Correlation Update Logic

Create a shared method for correlation updates used in both UKF and min-distance paths:

```python
# In area_selection.py

def _update_device_correlations(
    self,
    device: BermudaDevice,
    area_id: str,
    primary_rssi: float,
    primary_scanner_addr: str | None,
    other_readings: dict[str, float],
) -> None:
    """
    Update device correlations for area learning.

    Used by both UKF and min-distance selection paths to maintain
    consistent correlation data.

    Args:
        device: The device being tracked
        area_id: The area the device is currently in
        primary_rssi: RSSI from the primary (strongest) scanner
        primary_scanner_addr: Address of the primary scanner
        other_readings: RSSI readings from other visible scanners
    """
    if not other_readings:
        return

    # Ensure device has correlation entry
    if device.address not in self.correlations:
        self.correlations[device.address] = {}

    # Ensure area has profile
    if area_id not in self.correlations[device.address]:
        self.correlations[device.address][area_id] = AreaProfile(
            area_id=area_id,
        )

    # Update device-specific profile
    self.correlations[device.address][area_id].update(
        primary_rssi=primary_rssi,
        other_readings=other_readings,
        primary_scanner_addr=primary_scanner_addr,
    )

    # Update room-wide profile
    all_readings = dict(other_readings)
    if primary_scanner_addr is not None:
        all_readings[primary_scanner_addr] = primary_rssi

    if area_id not in self.room_profiles:
        self.room_profiles[area_id] = RoomProfile(area_id=area_id)
    self.room_profiles[area_id].update(all_readings)
```

### 4.2 Extract Streak Counter Logic

```python
# In area_selection.py

def _update_streak_counter(
    self,
    device: BermudaDevice,
    target_area_id: str,
    target_floor_id: str | None,
    current_stamps: dict[str, float],
    has_new_data: bool,
    low_confidence: bool = False,
) -> int:
    """
    Update the pending streak counter for area switching.

    Returns the current streak count after update.

    Args:
        device: The device being tracked
        target_area_id: The area the device wants to switch to
        target_floor_id: The floor of the target area
        current_stamps: Current advertisement timestamps
        has_new_data: Whether new advertisement data has arrived
        low_confidence: Whether the match has low confidence

    Returns:
        Current streak count
    """
    same_target = (
        device.pending_area_id == target_area_id
        and device.pending_floor_id == target_floor_id
    )

    if same_target:
        # Same target as before
        if has_new_data and not (low_confidence and device.pending_streak > 0):
            device.pending_streak += 1
            device.pending_last_stamps = dict(current_stamps)
    elif device.pending_area_id is not None and device.pending_area_id != target_area_id:
        # Different target - check if significant improvement
        device.pending_area_id = target_area_id
        device.pending_floor_id = target_floor_id
        device.pending_streak = 1
        device.pending_last_stamps = dict(current_stamps)
    else:
        # First pending or floor change within same area
        device.pending_area_id = target_area_id
        device.pending_floor_id = target_floor_id
        device.pending_streak = 1
        device.pending_last_stamps = dict(current_stamps)

    return device.pending_streak
```

### 4.3 Update Both Paths

Replace duplicated code in both `_refresh_area_by_ukf` and `_refresh_area_by_min_distance` with calls to these shared methods.

**Files Changed:** `area_selection.py`
**Lines Changed:** ~100 removed (duplication), ~80 added (shared methods)
**Net Change:** -20 lines
**Risk:** MEDIUM - Requires parallel testing of both paths

---

## Phase 5: Split Monolith Methods (Risk: HIGH)

### 5.1 Split _refresh_area_by_ukf

Extract logical sections into separate methods:

```python
class AreaSelectionHandler:

    def _refresh_area_by_ukf(self, device: BermudaDevice) -> bool:
        """
        Attempt area selection via UKF fingerprint matching.

        Returns True if UKF made a selection, False to fall back to min-distance.
        """
        nowstamp = monotonic_time_coarse()

        # Step 1: Gather RSSI readings
        rssi_readings = self._collect_rssi_readings(device, nowstamp)
        if len(rssi_readings) < UKF_MIN_SCANNERS:
            return False

        # Step 2: Get fingerprint matches
        matches = self._get_ukf_fingerprint_matches(device, rssi_readings, nowstamp)
        if not matches:
            return False

        # Step 3: Apply stickiness and get best match
        best_area_id, effective_score = self._apply_ukf_stickiness(
            device, matches, nowstamp
        )

        # Step 4: Validate with sanity checks
        if not self._validate_ukf_selection(
            device, best_area_id, effective_score, rssi_readings, nowstamp
        ):
            return False

        # Step 5: Apply streak logic and selection
        return self._apply_ukf_with_streak(
            device, best_area_id, effective_score, nowstamp
        )

    def _collect_rssi_readings(
        self,
        device: BermudaDevice,
        nowstamp: float
    ) -> dict[str, float]:
        """Collect fresh RSSI readings from all visible scanners."""
        readings: dict[str, float] = {}
        for advert in device.adverts.values():
            if (
                advert.rssi is not None
                and advert.stamp is not None
                and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                and advert.scanner_address is not None
            ):
                readings[advert.scanner_address] = advert.rssi
        return readings

    def _get_ukf_fingerprint_matches(
        self,
        device: BermudaDevice,
        rssi_readings: dict[str, float],
        nowstamp: float,
    ) -> list[tuple[str, float, float]]:
        """
        Get fingerprint matches from UKF.

        Returns list of (area_id, d_squared, score) tuples, sorted by score descending.
        """
        # ... extracted from current _refresh_area_by_ukf lines 725-804 ...

    def _apply_ukf_stickiness(
        self,
        device: BermudaDevice,
        matches: list[tuple[str, float, float]],
        nowstamp: float,
    ) -> tuple[str, float]:
        """
        Apply stickiness bonus to current area and return best match.

        Returns (best_area_id, effective_score).
        """
        # ... extracted from current lines 806-873 ...

    def _validate_ukf_selection(
        self,
        device: BermudaDevice,
        best_area_id: str,
        effective_score: float,
        rssi_readings: dict[str, float],
        nowstamp: float,
    ) -> bool:
        """
        Validate UKF selection with sanity checks.

        Returns False if sanity checks fail (should fall back to min-distance).
        """
        # Includes: RSSI sanity, distance sanity (BUG 14), topological check (BUG 21)
        # ... extracted from current lines 961-1073 ...

    def _apply_ukf_with_streak(
        self,
        device: BermudaDevice,
        best_area_id: str,
        effective_score: float,
        nowstamp: float,
    ) -> bool:
        """
        Apply streak logic and finalize UKF selection.

        Returns True if selection was applied, False otherwise.
        """
        # ... extracted from current lines 1075-1188 ...
```

### 5.2 Split _refresh_area_by_min_distance

```python
def _refresh_area_by_min_distance(self, device: BermudaDevice) -> None:
    """Area selection by finding closest proxy to a given device."""
    nowstamp = monotonic_time_coarse()
    analyzer = AdvertAnalyzer(nowstamp, self._max_radius, EVIDENCE_WINDOW_SECONDS)

    # Step 1: Find distance contenders
    contenders = self._find_distance_contenders(device, analyzer)

    # Step 2: Run contender competition
    winner, soft_incumbent, tests = self._run_contender_competition(
        device, contenders, analyzer, nowstamp
    )

    # Step 3: Check virtual distance for scannerless rooms
    virtual_winner = self._check_virtual_distance_winner(
        device, winner, analyzer, nowstamp
    )
    if virtual_winner:
        self._apply_virtual_winner(device, virtual_winner, tests, nowstamp)
        return

    # Step 4: Handle no-contender fallback
    if winner is None and soft_incumbent is None:
        winner = self._rssi_fallback_selection(device, analyzer, tests, nowstamp)

    # Step 5: Apply final selection with streak logic
    self._apply_min_distance_selection(
        device, winner or soft_incumbent, analyzer, tests, nowstamp
    )

def _find_distance_contenders(
    self,
    device: BermudaDevice,
    analyzer: AdvertAnalyzer,
) -> list[BermudaAdvert]:
    """Find all adverts that qualify as distance contenders."""
    # ... extracted logic ...

def _run_contender_competition(
    self,
    device: BermudaDevice,
    contenders: list[BermudaAdvert],
    analyzer: AdvertAnalyzer,
    nowstamp: float,
) -> tuple[BermudaAdvert | None, BermudaAdvert | None, AreaTests]:
    """
    Run competition between distance contenders.

    Returns (winner, soft_incumbent, diagnostic_tests).
    """
    # ... main competition loop ...

def _check_virtual_distance_winner(
    self,
    device: BermudaDevice,
    physical_winner: BermudaAdvert | None,
    analyzer: AdvertAnalyzer,
    nowstamp: float,
) -> tuple[str, float] | None:
    """
    Check if a scannerless room wins via virtual distance.

    Returns (area_id, distance) if virtual wins, None otherwise.
    """
    # ... virtual distance logic ...

def _rssi_fallback_selection(
    self,
    device: BermudaDevice,
    analyzer: AdvertAnalyzer,
    tests: AreaTests,
    nowstamp: float,
) -> BermudaAdvert | None:
    """Select winner via RSSI when no distance contenders exist."""
    # ... RSSI fallback logic ...

def _apply_min_distance_selection(
    self,
    device: BermudaDevice,
    winner: BermudaAdvert | None,
    analyzer: AdvertAnalyzer,
    tests: AreaTests,
    nowstamp: float,
) -> None:
    """Apply final min-distance selection with streak logic."""
    # ... streak logic and final application ...
```

**Files Changed:** `area_selection.py`
**Lines Changed:** ~500 reorganized
**Risk:** HIGH - Major structural change, requires extensive testing

---

## Phase 6: Performance Optimizations (Risk: LOW-MEDIUM)

### 6.1 Cache Collected Stamps

Avoid repeated iteration over adverts:

```python
class AreaSelectionHandler:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._stamp_cache: dict[str, dict[str, float]] = {}
        self._stamp_cache_time: float = 0.0

    def _get_cached_stamps(
        self,
        device: BermudaDevice,
        nowstamp: float
    ) -> dict[str, float]:
        """Get cached or fresh advertisement stamps."""
        cache_key = device.address

        # Invalidate cache if time advanced
        if nowstamp > self._stamp_cache_time + 0.1:  # 100ms cache validity
            self._stamp_cache.clear()
            self._stamp_cache_time = nowstamp

        if cache_key not in self._stamp_cache:
            self._stamp_cache[cache_key] = self._collect_current_stamps(device, nowstamp)

        return self._stamp_cache[cache_key]
```

### 6.2 Early Exit Optimizations

Add early exits to expensive operations:

```python
def _get_ukf_fingerprint_matches(self, device, rssi_readings, nowstamp):
    # Early exit: No UKF state for device
    if device.address not in self.device_ukfs:
        # Only create if device has correlations
        if device.address not in self.correlations:
            return []
        self.device_ukfs[device.address] = UnscentedKalmanFilter()

    # Early exit: No mature profiles to match against
    mature_profiles = [
        (area_id, profile)
        for area_id, profile in self.correlations.get(device.address, {}).items()
        if profile.is_mature
    ]
    if not mature_profiles:
        return []

    # ... continue with matching ...
```

### 6.3 Reduce Logger Overhead

Use lazy string formatting consistently:

```python
# Before (still has overhead even with isEnabledFor):
if _LOGGER.isEnabledFor(logging.DEBUG):
    _LOGGER.debug(
        "UKF match for %s: area=%s score=%.2f d²=%.2f",
        device.name,
        area_id,
        score,
        d_squared,
    )

# After (more efficient for high-frequency calls):
_LOGGER.debug(
    "UKF match for %s: area=%s score=%.2f d²=%.2f",
    device.name,
    area_id,
    score,
    d_squared,
)
# Let the logger handle the check internally
```

**Note:** For VERY high frequency logging, consider a module-level debug flag:
```python
_DEBUG = _LOGGER.isEnabledFor(logging.DEBUG)

# Then use:
if _DEBUG:
    _LOGGER.debug(...)
```

---

## Phase 7: Type Safety Improvements (Risk: LOW)

### 7.1 Add TypedDict for Complex Returns

```python
from typing import TypedDict

class UKFMatchResult(TypedDict):
    """Result from UKF fingerprint matching."""
    area_id: str
    d_squared: float
    score: float
    is_scannerless: bool

class CompetitionResult(TypedDict):
    """Result from contender competition."""
    winner: BermudaAdvert | None
    soft_incumbent: BermudaAdvert | None
    tests: AreaTests
```

### 7.2 Add Protocol for Analyzer

```python
from typing import Protocol

class AdvertAnalyzerProtocol(Protocol):
    """Protocol for advert analysis operations."""

    def effective_distance(self, advert: BermudaAdvert | None) -> float | None: ...
    def is_within_evidence_window(self, advert: BermudaAdvert | None) -> bool: ...
    def has_valid_area(self, advert: BermudaAdvert | None) -> bool: ...
    def is_distance_contender(self, advert: BermudaAdvert | None) -> bool: ...
```

---

## Implementation Schedule

### Week 1: Foundation
- [ ] Phase 0: Create baseline test suite (3 days)
- [ ] Phase 0: Set up performance benchmarks (1 day)
- [ ] Phase 1: Extract constants (1 day)

### Week 2: Low-Risk Changes
- [ ] Phase 2: Add BermudaDevice property (0.5 day)
- [ ] Phase 3: Create AdvertAnalyzer helper class (2 days)
- [ ] Phase 3: Integrate AdvertAnalyzer (1 day)
- [ ] Run full test suite, fix any regressions (1.5 days)

### Week 3: Medium-Risk Changes
- [ ] Phase 4: Extract shared correlation logic (1 day)
- [ ] Phase 4: Extract shared streak logic (1 day)
- [ ] Phase 4: Update both paths to use shared logic (1 day)
- [ ] Run full test suite, fix any regressions (2 days)

### Week 4: High-Risk Changes
- [ ] Phase 5: Split _refresh_area_by_ukf (2 days)
- [ ] Phase 5: Split _refresh_area_by_min_distance (2 days)
- [ ] Extensive integration testing (1 day)

### Week 5: Optimization & Polish
- [ ] Phase 6: Performance optimizations (2 days)
- [ ] Phase 7: Type safety improvements (1 day)
- [ ] Final testing and documentation (2 days)

---

## Risk Mitigation

### Testing Strategy

1. **Before each phase:** Run full test suite, record baseline
2. **After each change:** Run affected tests immediately
3. **End of each phase:** Full regression test + performance benchmark
4. **Feature flags:** Consider adding feature flag for major changes

### Rollback Plan

Each phase produces a single, revertible commit:

```bash
# If Phase 3 causes issues:
git revert HEAD~1  # Revert Phase 3 commit
# Continue with alternative approach
```

### Monitoring

After deployment, monitor:
- Area selection latency (should stay <50ms per device)
- Room flickering rate (should not increase)
- Memory usage of area_selection module
- Error rate in area selection logs

---

## Success Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Max method LOC | 955 | <200 | `wc -l` per method |
| Cyclomatic complexity | 50+ | <15 | `radon cc` |
| Test coverage | ~60% | >90% | `pytest --cov` |
| Duplicate code | ~150 lines | <20 lines | Manual review |
| Magic numbers | 10+ | 0 | `grep -E '[0-9]+\.[0-9]+'` |
| Private attr access | 3 noqa | 0 | `grep SLF001` |
| Average selection time | baseline | ≤baseline | Benchmark |

---

## Appendix A: File Structure After Refactoring

```
custom_components/bermuda/
├── area_selection.py           # Main handler (reduced to ~1000 lines)
├── area_selection_helpers.py   # AdvertAnalyzer and utilities (~300 lines)
├── const.py                    # All constants (extended with ~20 new)
├── bermuda_device.py           # Device class (added property)
└── ...

tests/
├── test_area_selection_unit.py
├── test_area_selection_ukf.py
├── test_area_selection_mindist.py
├── test_area_selection_edge_cases.py
├── test_area_selection_helpers.py   # Tests for AdvertAnalyzer
└── fixtures/
    └── area_selection_scenarios.py
```

## Appendix B: Method Responsibility Matrix (After Refactoring)

| Method | Responsibility | Max LOC |
|--------|---------------|---------|
| `refresh_areas_by_min_distance` | Entry point, orchestration | 50 |
| `_refresh_area_by_ukf` | UKF orchestration | 80 |
| `_collect_rssi_readings` | Gather fresh RSSI | 30 |
| `_get_ukf_fingerprint_matches` | UKF matching | 80 |
| `_apply_ukf_stickiness` | Stickiness bonus | 50 |
| `_validate_ukf_selection` | Sanity checks | 100 |
| `_apply_ukf_with_streak` | Streak and apply | 80 |
| `_refresh_area_by_min_distance` | Min-dist orchestration | 60 |
| `_find_distance_contenders` | Filter adverts | 40 |
| `_run_contender_competition` | Main competition | 150 |
| `_check_virtual_distance_winner` | Virtual distance | 60 |
| `_rssi_fallback_selection` | RSSI fallback | 80 |
| `_apply_min_distance_selection` | Streak and apply | 100 |
| `_update_device_correlations` | Shared correlation update | 40 |
| `_update_streak_counter` | Shared streak logic | 40 |

---

*Document Version: 1.0*
*Created: 2026-01-23*
*Author: Claude Code Assistant*
