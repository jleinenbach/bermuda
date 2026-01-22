# Bermuda Development Guide

## Environment Requirements

- **Python 3.13** is required (not 3.11 or 3.12)
- **Home Assistant 2025.10+** or later (2026.x recommended)
- Development happens in a Dev Container - respect `.vscode/settings.json`

## Quick Setup

```bash
# Create virtual environment with Python 3.13
python3.13 -m venv venv
source venv/bin/activate

# Install ALL dependencies (order matters for some packages like PyRIC)
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements_dev.txt
pip install -r requirements_test.txt
```

## Local Validation (Run Before EVERY Commit)

```bash
# 1. Linting and formatting (MUST pass)
python -m ruff check --fix
python -m ruff format

# 2. Type checking - strict mode (MUST pass)
python -m mypy --strict --install-types --non-interactive

# 3. Tests (MUST pass)
python -m pytest --cov -q
```

## Critical: Type-Checking & Dependency Discipline

- Do **NOT** suppress `import-not-found` or `import-untyped` errors
- Do **NOT** weaken `mypy.ini` or add blanket `# type: ignore` markers
- When mypy reports missing stubs (e.g., `Library stubs not installed for "aiofiles"`):
  → Add the matching `types-*` package to `requirements_dev.txt`
- When mypy reports `import-not-found` for a library:
  → Ensure package is in `requirements_test.txt` or `requirements.txt`
  → Assume environment is incomplete before assuming code is wrong

## Architecture Overview

### Core Components

| Component | File | Purpose |
|-----------|------|---------|
| **Coordinator** | `coordinator.py` | Drives Bluetooth processing, subscribes to HA Bluetooth manager, tracks scanners, prunes stale devices |
| **BermudaDevice** | `bermuda_device.py` | Represents each Bluetooth address, normalizes MACs, classifies address types, caches area/floor metadata |
| **Metadevices** | - | Group rotating identities (IRK, iBeacon) so changing MACs map to stable logical devices |
| **Entities** | `sensor.py`, `device_tracker.py`, etc. | Read state from coordinator |

### Area Selection System

The area selection logic in `coordinator.py` (`_refresh_area_by_min_distance`) determines which room a device is in:

1. **Distance contender check**: Adverts must have valid distance within max_radius
2. **Stability margin**: Challenger must be significantly closer (8% or 0.2m) to compete
3. **Streak requirement**: Multiple consecutive wins needed (4 same-floor, 6 cross-floor)
4. **Cross-floor protection**: Stricter requirements for floor changes
5. **Absolute profile rescue**: When primary scanner offline, secondary patterns can protect area

### Scanner Correlation Learning (`correlation/`)

Learns typical RSSI patterns for each area to improve localization:

| Class | Purpose |
|-------|---------|
| `ScannerPairCorrelation` | Tracks RSSI delta between primary and secondary scanners |
| `ScannerAbsoluteRssi` | Tracks absolute RSSI from each scanner (for offline fallback) |
| `AreaProfile` | Collection of correlations for one area |
| `CorrelationStore` | Persistence to Home Assistant storage |

**Key insight**: When primary scanner goes offline, absolute profiles let us verify if secondary scanner readings still match the learned room pattern.

### Clamped Bayesian Fusion (Controlled Evolution)

The correlation classes (`ScannerPairCorrelation`, `ScannerAbsoluteRssi`) use a dual-filter architecture with **clamped fusion**:

```
                    ┌─────────────────────────────────────┐
Automatic Learning ─┼─→ _kalman_auto ──┐                  │
                    │  (Continuous)    │ Inverse-Variance │
                    │                  │ Weighting        │
                    │                  ├─→ Clamped Fusion │
Button Training ────┼─→ _kalman_button─┘                  │
                    │  (The Anchor)    │ Auto ≤ 30%       │
                    └─────────────────────────────────────┘
```

**Why Clamped Fusion (Not Pure Override)?**
- **Problem with pure override**: Auto-learning is completely ignored, no adaptation to small environmental changes
- **Problem with pure fusion**: Auto-learning eventually overwhelms user corrections
- **Solution**: Button sets the "anchor", auto can "polish" it with max 30% influence
- User retains at least 70% authority while system adapts intelligently

**Clamped Fusion Logic:**
```python
@property
def expected_rssi(self) -> float:
    # Case 1: Only auto data available
    if not self._kalman_button.is_initialized:
        return self._kalman_auto.estimate

    # Case 2: Clamped Fusion - auto influence limited to 30%
    w_btn = 1.0 / self._kalman_button.variance
    w_auto = 1.0 / self._kalman_auto.variance

    # Clamp auto influence to max 30%
    MAX_AUTO_RATIO = 0.30
    if w_auto / (w_btn + w_auto) > MAX_AUTO_RATIO:
        w_auto = w_btn * (MAX_AUTO_RATIO / (1.0 - MAX_AUTO_RATIO))

    total = w_btn + w_auto
    return (est_btn * w_btn + est_auto * w_auto) / total
```

**Button Training via Kalman `update()`:**
```python
def update_button(self, rssi: float) -> float:
    # Add sample to button filter (same as auto-learning)
    # Each of the 10 training samples contributes to the average
    self._kalman_button.update(rssi)
    return self.expected_rssi  # Returns fused value
```

**Why `update()` instead of `reset_to_value()`?**

Previously, `reset_to_value()` was used which OVERWROTE previous samples:
- 10 training samples collected
- But only the LAST sample counted (others overwritten!)
- That one sample claimed 500 samples worth of confidence
- This could make training WORSE than auto-learning if that one sample was noisy

Now, `update()` is used which ACCUMULATES samples:
- All 10 training samples contribute to the average
- Variance naturally decreases as more samples are added
- Button filter builds confidence from ACTUAL data, not artificial inflation

**Example: Button Training Accumulation**
```
Training sample 1: RSSI = -82dB
  → Button estimate: -82dB, variance: ~8.0

Training sample 5: RSSI = -80dB
  → Button estimate: -81dB (averaged), variance: ~3.5

Training sample 10: RSSI = -79dB
  → Button estimate: -80.5dB (averaged), variance: ~2.5
  → 10 real samples, realistic confidence
```

**Key Constants:**
| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_AUTO_RATIO` | 0.30 | Auto influence capped at 30% |
| `MIN_VARIANCE` | 0.001 | Prevents division by zero |
| `TRAINING_SAMPLE_COUNT` | 20 | Samples per training session (meets maturity threshold) |
| `TRAINING_SAMPLE_DELAY` | 0.5s | Delay between samples for diverse RSSI readings |

### Calibration vs Fingerprints (Independence)

**Important**: Scanner/device calibration settings do NOT affect fingerprint data.

| Calibration | Affects | Used By | Fingerprint Impact |
|-------------|---------|---------|-------------------|
| `ref_power` | Distance calculation | `rssi_to_metres()` | ❌ None |
| `attenuation` | Signal decay model | `rssi_to_metres()` | ❌ None |
| `rssi_offset` | Per-scanner correction | `_update_raw_distance()` | ❌ None |

**Why this works:**

Both training and matching use RAW RSSI (`advert.rssi`), not calibrated values:
```python
# Training (coordinator.py)
rssi_readings[advert.scanner_address] = advert.rssi  # Raw RSSI

# UKF Matching (coordinator.py)
rssi_readings[advert.scanner_address] = advert.rssi  # Same raw RSSI
```

**Hardware biases cancel out:**
```
Scanner A: Hardware reports -5dB too low
Scanner B: Hardware reports +3dB too high

Training captures: A=-80dB, B=-72dB (biased)
Matching sees:     A=-80dB, B=-72dB (same biases)
→ Pattern match works because biases are consistent!
```

**Implication**: When user changes calibration settings, learned fingerprint data remains valid. No need to re-train or invalidate stored correlations.

### Indirect Feedback Loop (Button → Room Selection → Auto)

**Important**: While the two Kalman filters (`_kalman_auto` and `_kalman_button`) don't directly share data, there IS an indirect feedback mechanism through room selection.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    INDIRECT FEEDBACK LOOP                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  STEP 1: Room Selection (UKF Matching)                                   │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  ukf.match_fingerprints() reads:                                   │ │
│  │  abs_profile.expected_rssi  ← This IS the Clamped Fusion!          │ │
│  │                                                                     │ │
│  │  Button: -85dB (70%) ─┬─→ Fusion: -84.5dB ─→ fp_mean for matching  │ │
│  │  Auto:   -80dB (30%) ─┘                                            │ │
│  │                                                                     │ │
│  │  Current signal: -83dB → Difference: 1.5dB → Good match!           │ │
│  │  Result: "Room A wins"                                              │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                               │                                          │
│                               ▼                                          │
│  STEP 2: Auto-Learning                                                   │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  coordinator calls:                                                 │ │
│  │  profile.update(rssi=-83)  ← Learns: "In Room A I see -83dB"       │ │
│  │         │                                                          │ │
│  │         ▼                                                          │ │
│  │  _kalman_auto.update(-83)                                          │ │
│  │  Auto estimate moves: -80dB → -81dB (toward -83)                   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                               │                                          │
│                               ▼                                          │
│  STEP 3: Next Cycle                                                      │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  expected_rssi recalculated:                                        │ │
│  │  Button: -85dB (70%) ─┬─→ Fusion: -84.2dB (slightly adjusted!)     │ │
│  │  Auto:   -81dB (30%) ─┘                                            │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**The Mechanism:**

| Step | Action | Filter Affected |
|------|--------|-----------------|
| 1 | UKF reads `expected_rssi` (fused value) | Neither (read-only) |
| 2 | Room A wins based on fused fingerprint | Neither |
| 3 | `profile.update()` called for Room A | `_kalman_auto` only |
| 4 | Auto learns "-83dB = Room A" | `_kalman_auto` only |

**Key Insight**: The button training indirectly influences WHAT the auto-filter learns by affecting WHICH room is selected. This creates contextual consistency:

| Without Button Training | With Button Training |
|------------------------|---------------------|
| Auto decides alone: Room B wins | Button influences: Room A wins |
| Auto learns: "-83dB = Room B" | Auto learns: "-83dB = Room A" |
| Auto reinforces wrong decision | Auto reinforces correct decision |

**Why This Is Beneficial:**

1. **Consistent Learning**: Auto-filter learns data that matches the button-trained context
2. **Refinement, Not Contradiction**: Auto "polishes" within the button's framework
3. **Convergence**: Over time, button and auto estimates approach each other (within 30% limit)

**Convergence Example Over Time:**
```
Day 1:   Button=-85dB, Auto=-80dB → Fusion=-83.5dB
Day 7:   Button=-85dB, Auto=-82dB → Fusion=-84.1dB (Auto learned closer values)
Day 30:  Button=-85dB, Auto=-84dB → Fusion=-84.7dB (Converging)
Day 60:  Button=-85dB, Auto=-84.5dB → Fusion=-84.85dB (Stabilized)
```

**Code References:**
- `ukf.py:550`: `fp_mean.append(abs_profile.expected_rssi)` - Uses fused value
- `coordinator.py:2252`: `profile.update(...)` - Auto-learning after room selection
- `scanner_absolute.py:134-179`: `expected_rssi` property - Clamped fusion logic

## Testing Standards

### Running Tests

```bash
# Full test suite
python -m pytest tests/ --cov -q

# Single test file
python -m pytest tests/test_coordinator_hardening.py -v

# Single test
python -m pytest tests/test_area_selection.py::test_specific_function -v
```

### Test Fixture Requirements

When creating coordinator mocks, these attributes are required:

```python
coordinator = BermudaDataUpdateCoordinator.__new__(BermudaDataUpdateCoordinator)
coordinator.options = {CONF_MAX_RADIUS: 10.0}
coordinator.correlations = {}  # Scanner correlation data
coordinator._correlations_loaded = True  # Prevent async loading
coordinator._last_correlation_save = 0.0  # Last save timestamp
coordinator.correlation_store = MagicMock(async_save=AsyncMock())  # Mock store
coordinator.AreaTests = BermudaDataUpdateCoordinator.AreaTests
```

For FakeAdvert classes in tests:
```python
self.scanner_address = scanner_device.address if scanner_device else None
```

For FakeDevice classes:
```python
self.address = f"AA:BB:CC:..."  # Must have an address attribute
```

## Code Style & Clean Coding

### Logging (ruff G004)
```python
# GOOD - lazy formatting
_LOGGER.debug("Processing device %s at distance %.2f", device.name, distance)

# BAD - eager formatting (fails ruff G004)
_LOGGER.debug(f"Processing device {device.name} at distance {distance:.2f}")
```

### Exceptions
```python
# GOOD - precise type, chained
raise ValueError("Invalid distance") from original_error

# BAD - broad except, no chaining
except Exception:
    pass
```

### Async Discipline
- Keep code non-blocking
- Use `asyncio.to_thread` for blocking operations
- Handle `CancelledError` properly
- Use `asyncio.TaskGroup` when appropriate

### Security
- No `eval`/`exec`
- Avoid `shell=True`
- Prefer `yaml.safe_load`
- Redact secrets/PII in logs

## Home Assistant Integration Notes

- Keep `manifest.json` aligned with HA guidance
- Store config entry state on `entry.runtime_data` with typed structures
- Inject shared session via `async_get_clientsession(hass)`
- Store tokens/state via `helpers.storage.Store` with throttled writes
- Provide repairs/diagnostics with redaction

### ButtonEntity Implementation

**Source:** [HA Developer Docs - Button Entity](https://developers.home-assistant.io/docs/core/entity/button/), [HA Core button/__init__.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/button/__init__.py)

Buttons are stateless entities that trigger actions. Key implementation:

```python
from homeassistant.components.button import ButtonEntity

class MyButton(ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG  # For config buttons

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._do_something()
```

**Dynamic Availability (Disabling Buttons):**

Use the `available` property to dynamically enable/disable buttons. Example from [Shelly integration](https://github.com/home-assistant/core/blob/dev/homeassistant/components/shelly/button.py):

```python
@property
def available(self) -> bool:
    """Return True if button should be enabled."""
    available = super().available

    # Custom condition - button only available when room is selected
    if self._room_selection is None:
        return False

    return available
```

**Key Points:**
- `available = False` → Button grayed out in UI, press action blocked
- Call `self.async_write_ha_state()` after changing availability conditions
- Inherits from `RestoreEntity` - can restore last pressed timestamp
- Device classes: `IDENTIFY`, `RESTART`, `UPDATE` (prefer update entity for updates)

### SelectEntity Implementation

**Source:** [HA Developer Docs - Select Entity](https://developers.home-assistant.io/docs/core/entity/select/)

```python
from homeassistant.components.select import SelectEntity

class MySelect(SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: list[str] = ["Option A", "Option B"]

    @property
    def current_option(self) -> str | None:
        """Return current selected option."""
        return self._current_value

    async def async_select_option(self, option: str) -> None:
        """Handle option selection."""
        self._current_value = option
        self.async_write_ha_state()
```

**Dynamic Options:**
```python
@property
def options(self) -> list[str]:
    """Return dynamic list of options."""
    return [area.name for area in self.hass.areas]
```

## Key Constants (`const.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `SAME_FLOOR_STREAK` | 4 | Consecutive wins for same-floor switch |
| `CROSS_FLOOR_STREAK` | 6 | Consecutive wins for cross-floor switch |
| `INCUMBENT_MARGIN_PERCENT` | 0.08 | 8% closer required to challenge |
| `INCUMBENT_MARGIN_METERS` | 0.20 | OR 0.2m closer required |
| `CROSS_FLOOR_MIN_HISTORY` | 8 | Min history for cross-floor historical checks |
| `DWELL_TIME_MOVING_SECONDS` | 120 | 0-2 min: recently moved state |
| `DWELL_TIME_SETTLING_SECONDS` | 600 | 2-10 min: settling in state |
| `MARGIN_MOVING_PERCENT` | 0.05 | 5% margin when moving |
| `MARGIN_STATIONARY_PERCENT` | 0.15 | 15% margin when stationary |

## Signal Processing Architecture (`filters/`)

Modular filter system for BLE RSSI signal processing:

| Filter | File | Status | Purpose |
|--------|------|--------|---------|
| `SignalFilter` | `base.py` | ✅ | Abstract base class for all filters |
| `KalmanFilter` | `kalman.py` | ✅ | 1D linear Kalman for RSSI smoothing |
| `AdaptiveRobustFilter` | `adaptive.py` | ✅ | EMA + CUSUM changepoint detection |
| `UnscentedKalmanFilter` | `ukf.py` | ✅ | Multi-scanner fusion with fingerprints (experimental) |

### Filter Interface

```python
class SignalFilter(ABC):
    def update(self, measurement: float, timestamp: float | None = None) -> float: ...
    def get_estimate(self) -> float: ...
    def get_variance(self) -> float: ...
    def reset(self) -> None: ...
```

### Kalman Filter Usage

```python
from custom_components.bermuda.filters import KalmanFilter

filter = KalmanFilter()
filtered_rssi = filter.update(raw_rssi)

# Adaptive variant (adjusts noise based on signal strength)
filtered_rssi = filter.update_adaptive(raw_rssi, ref_power=-55)
```

## Recent Changes (Session Notes)

### Room Flickering Fix
- **Problem**: Tracker constantly switched rooms despite being stationary
- **Solution**: Added stability margin requiring challengers to be significantly closer
- **Files**: `const.py`, `coordinator.py`

### Scanner Outage Resilience
- **Problem**: When primary scanner went offline, room switched incorrectly
- **Solution**: Absolute RSSI profile learning - secondary scanner patterns protect area
- **Files**: `correlation/scanner_absolute.py`, `correlation/area_profile.py`, `coordinator.py`

### Dwell Time Based Stability
- **Problem**: Static stability margin doesn't account for how long device has been stationary
- **Solution**: Dynamic margins based on movement state (MOVING → SETTLING → STATIONARY)
- **Files**: `const.py`, `bermuda_device.py`, `coordinator.py`
- **Key methods**: `get_movement_state()`, `get_dwell_time()`, `area_changed_at`

### BLE Tracking Stability Fix (PR #94)
- **Problem**: Room flickering and button training ineffective
- **Root causes**:
  1. `VELOCITY_TELEPORT_THRESHOLD` too low (5) - BLE noise caused 100+ m/s calculated velocities, triggering false teleport resets that cleared distance history needed for cross-floor protection
  2. `ScannerAbsoluteRssi.update_button()` was missing variance inflation - only `ScannerPairCorrelation` had it, so button training couldn't override absolute RSSI profiles
- **Fixes**:
  1. Increased `VELOCITY_TELEPORT_THRESHOLD` from 5 to 10 - gives Kalman filter time to stabilize
  2. Added variance inflation to `ScannerAbsoluteRssi.update_button()` - now both correlation classes support button training override
- **Files**: `const.py`, `correlation/scanner_absolute.py`, `coordinator.py`

### Soft Incumbent Stabilization & BLE Noise Filter
- **Problem**: Two remaining causes of room flickering:
  1. "Soft Incumbent Trap": When current scanner temporarily stops sending data, any challenger wins immediately
  2. BLE noise spikes (100+ m/s calculated velocity) trigger false teleport recovery, resetting history
- **Root causes**:
  1. Same-floor soft incumbent replacement had NO protection (only cross-floor had history checks)
  2. Velocity check didn't distinguish "plausible fast" (3-10 m/s) from "impossible spike" (>10 m/s)
- **Fixes**:
  1. **Soft Incumbent Stabilization** (`coordinator.py`): Same-floor challengers need either significant distance advantage (>0.5m) OR sustained history (4+ readings) to replace a soft incumbent that was within range
  2. **BLE Noise Filter** (`bermuda_advert.py`): Velocities >10 m/s are ignored as measurement errors (don't count toward teleport recovery)
- **Refactoring**: Extracted device loop into `_determine_area_for_device()` method for better maintainability
- **Files**: `coordinator.py`, `bermuda_advert.py`

### Test Fixture Updates
- Added `correlations`, `_correlations_loaded`, `_last_correlation_save`, `correlation_store` to coordinator mocks
- Added `scanner_address` to FakeAdvert, `address` to FakeDevice
- Added `get_movement_state()` and `area_changed_at` to FakeDevice
- Added `area_locked_id`, `area_locked_name`, `area_locked_scanner_addr` to FakeDevice

### Reset Training Feature (Ghost Scanner Fix)
- **Problem**: With hierarchical priority (button > auto), incorrect training persists forever
  - "Ghost Scanner" problem: Device trained in wrong/invisible room stays stuck
  - Simple re-training doesn't help if the problematic scanner isn't visible anymore
  - No way to undo incorrect manual calibration
- **Solution**: Device-level "Reset Training" button that clears ALL user training
  - Clears button filter (Frozen Layer) in all AreaProfiles for the device
  - Preserves auto-learned data (Shadow Learning) as immediate fallback
  - Persists changes immediately via `correlation_store.async_save()`
- **Files changed**:
  - `correlation/scanner_absolute.py`: Added `reset_training()` method
  - `correlation/scanner_pair.py`: Added `reset_training()` method
  - `correlation/area_profile.py`: Added `reset_training()` method
  - `coordinator.py`: Added `async_reset_device_training()` method
  - `button.py`: Added `BermudaResetTrainingButton` class
  - `translations/*.json`: Added translations for 8 languages
- **UI**: New button per device with `mdi:eraser` icon, EntityCategory.CONFIG

### Memory Leak Fix & Dirty Object State Fix (PR Review Round 2)
- **BUG 7: Memory Leak in UKF Storage**
  - **Problem**: When devices were pruned from `self.devices`, their UKF states in `self.device_ukfs` were not cleaned up, causing unbounded memory growth
  - **Solution**: Added `self.device_ukfs.pop(device_address, None)` in `prune_devices()`
  - **File**: `coordinator.py`
- **BUG 8: Tainted Advert State**
  - **Problem**: In `_apply_ukf_selection()`, `area_id` and `area_name` were temporarily modified but not restored, leaving the advert object in a "dirty" state
  - **Solution**: Save all modified attributes before mutation, use try/finally to guarantee restoration
  - **File**: `coordinator.py`

### Area Lock Active Override Fix (BUG 9)
- **Problem**: Button training didn't immediately change the displayed area
  - User selects room "Keller" → Device still shows "Schlafzimmer" (2 floors away!)
  - `area_locked_id` only PREVENTED automatic detection, but didn't ACTIVELY SET the area
  - During training: Device stayed in the old room (lock was a guard, not an override)
  - After training: Normal competition resumed - trained room had to "win" against incumbents
- **Root cause**: Design gap in the area lock mechanism - it blocked changes but didn't apply them
- **Solution**: When `area_locked_id` is set, ACTIVELY call `device.update_area_and_floor(area_locked_id)` to force the area immediately
- **Code change** (`coordinator.py:2231-2242`):
  ```python
  # BEFORE: Just returned without setting the area
  else:
      # Locked scanner still has an advert - keep lock active.
      return

  # AFTER: Actively set the area before returning
  else:
      # Locked scanner still has an advert - keep lock active.
      # FIX: ACTIVE OVERRIDE - Set the device area to the locked area immediately.
      device.update_area_and_floor(device.area_locked_id)
      return
  ```
- **Effect**: When user selects a room for training, the device immediately shows that room in the UI, not the old/wrong room
- **File**: `coordinator.py`

### Post-Training Area Fix (BUG 10)
- **Problem**: After successful training, device still showed wrong room
  - Training completed successfully (10 samples)
  - Area lock was cleared in `finally` block
  - Coordinator refresh triggered normal area detection
  - UKF score for trained room was < 0.3 (switching threshold) → fell back to min-distance
  - Min-distance picked wrong room (e.g., Schlafzimmer 2 floors away instead of Technikraum)
- **Root cause**: After training, the device's area was determined by normal detection, not by the training result
  - UKF switching threshold (0.3) is high
  - Fresh button training creates good profiles, but RSSI values can change between training and refresh
  - If UKF score < 0.3, falls back to min-distance which may pick wrong room
- **Solution**: After successful training, DIRECTLY set `device.update_area_and_floor(target_area_id)` before clearing the lock
- **Code change** (`button.py:193-198`):
  ```python
  if successful_samples > 0:
      _LOGGER.info("Fingerprint training complete...")
      # FIX: BUG 10 - Set device area to trained room
      self._device.update_area_and_floor(target_area_id)
  ```
- **Effect**: After training, device starts in the trained room. UKF retention threshold (0.15) is much lower than switching threshold (0.3), helping keep the device in the trained room.
- **File**: `button.py`

### Button Training Sample Accumulation Fix (BUG 11)
- **Problem**: Button training produced WORSE results than auto-learning
  - User observed: Auto-learning worked well → clicked "Learn" → got worse results
  - After "Reset Training" → back to good (auto-only) results
- **Root cause**: `update_button()` used `reset_to_value()` which OVERWROTE previous samples
  - 10 training samples were collected
  - But each call to `reset_to_value()` replaced the previous value
  - Only the LAST sample counted, but it claimed 500 samples confidence!
  - This single (potentially noisy) sample dominated over well-averaged auto data
- **Solution**: Replace `reset_to_value()` with `update()` in both correlation classes
  - `ScannerAbsoluteRssi.update_button()`: Now uses `_kalman_button.update(rssi)`
  - `ScannerPairCorrelation.update_button()`: Now uses `_kalman_button.update(delta)`
  - All 10 training samples now contribute to the average
  - Variance decreases naturally based on actual data quality
- **Files**: `correlation/scanner_absolute.py`, `correlation/scanner_pair.py`

### Scannerless Room Detection Fix (BUG 12)
- **Problem**: Training for scannerless rooms (rooms without their own scanner) didn't work
  - User trains device for "Lagerraum" (basement, no scanner)
  - Device still shows "Schlafzimmer" (2 floors up, has scanner)
  - Training appeared to complete successfully but had no effect
- **Root cause**: Button training creates "immature" profiles that UKF skips
  - `TRAINING_SAMPLE_COUNT = 10` (button training collects 10 samples)
  - `MIN_SAMPLES_FOR_MATURITY = 20` (profile needs 20+ samples)
  - After button training: `sample_count = 10 < 20` → `is_mature = False`
  - `match_fingerprints()` only includes profiles where `is_mature == True`
  - Scannerless room profile is NEVER considered → UKF finds no match → falls back to min-distance
  - Min-distance can't detect scannerless rooms → picks nearest scanner's room
- **Why only scannerless rooms are affected**:
  - Rooms WITH scanners get continuous auto-learning (quickly reaches 20+ samples)
  - Scannerless rooms have NO scanner → NO auto-learning → ONLY button training
  - 10 button samples < 20 maturity threshold → profile never mature
- **Solution (two-part)**:
  1. **Semantic fix**: Added `has_button_training` property - user intent is ALWAYS trusted
     - Modified `is_mature` to return `True` if `has_button_training` OR `sample_count >= threshold`
     - User-trained profiles are now always considered "mature enough" for UKF matching
  2. **Practical fix**: Increased `TRAINING_SAMPLE_COUNT` from 10 to 20
     - Now naturally meets `MIN_SAMPLES_FOR_MATURITY` threshold
     - Added `TRAINING_SAMPLE_DELAY = 0.5s` between samples for diverse RSSI readings
     - Total training time: ~10 seconds (20 samples × 0.5s)
- **Visual feedback**: Icon changes from `mdi:brain` to `mdi:timer-sand` during training
- **Files**: `correlation/scanner_absolute.py`, `correlation/scanner_pair.py`, `button.py`

### Scannerless Room Misleading Distance Fix (BUG 13)
- **Problem**: When UKF places device in a scannerless room, the "Distance" sensor showed misleading values
  - Device in "Lagerraum" (scannerless room in basement)
  - Distance sensor shows "1.6m" - but from which scanner?
  - User assumes this is distance to the room, but it's actually distance to the nearest scanner (which is in a DIFFERENT room!)
- **Root cause**: For scannerless rooms, `area_distance` was being set to the distance from the scanner used for fingerprint matching, not the scanner in the target area (because there IS no scanner in the target area)
- **Solution**: When UKF selects a scannerless room, clear `area_distance` and `area_distance_stamp` to `None`
  - This signals "distance not applicable" for scannerless rooms
  - Prevents user confusion about what the distance actually means
- **Code change** (`coordinator.py`):
  ```python
  if scanner_less_room:
      # ... existing fingerprint application code ...
      # FIX: BUG 13 - Clear misleading distance for scannerless rooms
      device.area_distance = None
      device.area_distance_stamp = None
  ```
- **File**: `coordinator.py`

### UKF Distance Sanity Check (BUG 14)
- **Problem**: UKF fingerprint matching picks wrong room despite device being very close to a scanner
  - Device in "Technikraum" (1.6m from scanner in that room)
  - UKF fingerprints match "Bibliothek" (2 floors up!) with higher confidence
  - Result: Device shows as being in Bibliothek despite being 1.6m from Technikraum scanner
- **Root cause**: UKF matching is based purely on RSSI fingerprint patterns, not physical distance. Bad or outdated training data can cause fingerprint matches that contradict physical proximity.
- **Solution**: Add distance-based sanity check to override UKF when device is very close to a scanner
  - If device is < 2m from a scanner AND UKF picked a different area:
    - **Cross-floor**: Always reject UKF, fall back to min-distance (physical proximity wins)
    - **Same floor, different room**: Require UKF confidence ≥ 0.85 to override proximity
- **Key insight**: Physical distance < 2m is almost certain proof of room location. Fingerprint matching errors should not override this certainty.
- **Code change** (`coordinator.py:_refresh_area_by_ukf()`):
  ```python
  proximity_threshold = 2.0  # meters
  # Find nearest scanner
  for advert in device.adverts.values():
      if advert.rssi_distance < nearest_scanner_distance:
          nearest_scanner_distance = advert.rssi_distance
          nearest_scanner_area_id = scanner_area

  if nearest_scanner_distance < proximity_threshold and nearest_scanner_area_id != best_area_id:
      if is_cross_floor_ukf:
          return False  # Fall back to min-distance
      if effective_match_score < 0.85:
          return False  # Same floor but low confidence → fall back
  ```
- **Constants**:
  | Constant | Value | Purpose |
  |----------|-------|---------|
  | `proximity_threshold` | 2.0m | Distance below which physical proximity overrides fingerprints |
  | High confidence threshold | 0.85 | UKF score needed to override same-floor proximity |
- **File**: `coordinator.py`

### Virtual Min-Distance for Scannerless Rooms (BUG 15)
- **Problem**: Scannerless rooms (rooms without their own scanner) can't compete in min-distance fallback
  - Device trained for "Lagerraum" (basement, no scanner)
  - UKF score is 0.25 (below 0.3 switching threshold) → falls back to min-distance
  - Min-distance algorithm only sees physical scanners
  - "Yunas Zimmer" (upper floor, 5.2m away) wins because it HAS a scanner
  - Result: Device shows wrong room despite good fingerprint training
- **Root cause**: When UKF score is below switching threshold, min-distance takes over. But min-distance can ONLY see rooms with physical scanners. Scannerless rooms are invisible to it.
- **Solution**: "Virtual Distance" - convert UKF fingerprint scores to virtual distances
  - Only for button-trained profiles (explicit user intent)
  - Only for scannerless rooms (rooms with scanners use real distance)
  - Formula: `virtual_distance = max_radius × SCALE × (1 - score)²`
  - Quadratic formula rewards good matches more aggressively
- **Key insight**: A scannerless room with a good fingerprint match should be able to "compete" with a distant physical scanner.
- **Architecture**:
  ```
  ┌─────────────────────────────────────────────────────────────────────┐
  │           _refresh_area_by_min_distance() with Virtual Distance     │
  ├─────────────────────────────────────────────────────────────────────┤
  │                                                                      │
  │  Physical Scanners ──→ Real measured distances                      │
  │       │                      │                                       │
  │       │                      ▼                                       │
  │       │              ┌──────────────────┐                           │
  │       │              │ Distance Contest │                           │
  │       │              │ (all distances)  │                           │
  │       │              └────────┬─────────┘                           │
  │       │                       │                                      │
  │  Scannerless Rooms ──→ Virtual distances ─┘                         │
  │       │                                                              │
  │       ▼                                                              │
  │  ┌─────────────────────────────────────────────────────────────┐   │
  │  │ _get_virtual_distances_for_scannerless_rooms()              │   │
  │  │                                                              │   │
  │  │ For each button-trained, scannerless area:                  │   │
  │  │   1. Get UKF fingerprint match score                        │   │
  │  │   2. Convert to virtual distance:                           │   │
  │  │      distance = max_radius × 0.7 × (1 - score)²             │   │
  │  │   3. Add to distance contest                                │   │
  │  └─────────────────────────────────────────────────────────────┘   │
  │                                                                      │
  └─────────────────────────────────────────────────────────────────────┘
  ```
- **Formula Details**:
  ```python
  virtual_distance = max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - score) ** 2)
  # VIRTUAL_DISTANCE_SCALE = 0.7 (30% shorter than pure quadratic)
  ```
  | UKF Score | Virtual Distance (10m radius) | Interpretation |
  |-----------|------------------------------|----------------|
  | 1.0 | 0.0m | Perfect match → wins any contest |
  | 0.5 | 1.75m | Good match → beats 5m+ scanners |
  | 0.3 | 3.43m | Threshold match → competitive |
  | 0.1 | 5.67m | Poor match → likely loses |
  | 0.0 | 7.0m | No match → only beats very distant |
- **Why Quadratic (not Linear)?**
  - Linear: `7m * (1-0.5) = 3.5m` for score 0.5
  - Quadratic: `7m * (0.5)² = 1.75m` for score 0.5
  - Quadratic rewards good matches MORE aggressively
  - A "good match" (0.5+) should strongly compete, not just barely
- **Filter Conditions** (all must be true for virtual distance):
  1. `has_button_training == True` (user explicitly trained this room)
  2. `_area_has_scanner(area_id) == False` (room has no physical scanner)
  3. `score >= VIRTUAL_DISTANCE_MIN_SCORE` (0.05, prevents phantom matches)
  4. `len(rssi_readings) >= UKF_MIN_SCANNERS` (2, meaningful calculation)
- **Constants**:
  | Constant | Value | Purpose |
  |----------|-------|---------|
  | `VIRTUAL_DISTANCE_SCALE` | 0.7 | Makes virtual distances 30% shorter than pure quadratic |
  | `VIRTUAL_DISTANCE_MIN_SCORE` | 0.05 | Minimum score to generate virtual distance |
- **Files changed**: `coordinator.py`, `const.py`, `correlation/area_profile.py`
- **Test file**: `tests/test_virtual_distance_scannerless.py` (21 tests)

### UKF Dynamic Creation for Single Scanner (BUG 16)
- **Problem**: Virtual distance didn't work when only 1 scanner was visible
  - Device trained for "Lagerraum" (basement, no scanner)
  - Only 1 scanner sees the device (typical for isolated areas like basements)
  - `_refresh_area_by_ukf()` returns early at line 1902 (needs 2 scanners)
  - UKF is NEVER created (creation happens after the early return at line 1951)
  - `_get_virtual_distances_for_scannerless_rooms()` finds no UKF → returns empty
  - Result: "Bibliothek" (2 floors up with scanner) wins instead of "Lagerraum"
- **Root cause**: UKF was only created in `_refresh_area_by_ukf()`, but that function has early exit conditions that prevent UKF creation
- **Solution**: Create and update UKF dynamically in `_get_virtual_distances_for_scannerless_rooms()` when needed
- **Code change** (`coordinator.py:1822-1833`):
  ```python
  # Before: Return empty if no UKF exists
  if device.address not in self.device_ukfs:
      return virtual_distances  # Empty!

  # After: Create UKF if missing, update it with current readings
  if device.address not in self.device_ukfs:
      self.device_ukfs[device.address] = UnscentedKalmanFilter()

  ukf = self.device_ukfs[device.address]
  ukf.predict(dt=UPDATE_INTERVAL)
  ukf.update_multi(rssi_readings)
  ```
- **Key insight**: The UKF must be available for virtual distance calculation regardless of whether `_refresh_area_by_ukf()` succeeded
- **File**: `coordinator.py`

### Button Training Address Normalization Fix (BUG 17)
- **Problem**: Button training appeared to succeed but `has_button_training=False` on lookup
  - User trains device for "Lagerraum" → logs show 20/20 samples success
  - Later profile check shows `has_button_training=False`
  - Training data "lost" despite successful save
- **Root cause**: Address key mismatch between training and lookup
  - `async_train_fingerprint()` used raw `device_address` parameter as correlations key
  - Auto-learning and lookup used `device.address` (normalized to lowercase)
  - If entity passed uppercase address, training stored under different key than lookup
  - Example:
    - Training stores: `correlations["AA:BB:CC:DD:EE:FF"]["lagerraum"]`
    - Lookup reads: `correlations.get("aa:bb:cc:dd:ee:ff", {})` → empty!
- **Solution**: Use `device.address` (normalized) instead of raw `device_address` parameter
- **Code change** (`coordinator.py:775-793`):
  ```python
  # BEFORE (BUG): Uses raw parameter - may be uppercase
  if device_address not in self.correlations:
      self.correlations[device_address] = {}

  # AFTER (FIX): Uses normalized address from BermudaDevice
  normalized_address = device.address
  if normalized_address not in self.correlations:
      self.correlations[normalized_address] = {}
  ```
- **Key insight**: All correlations dictionary access must use the same key format. `BermudaDevice.address` is the canonical source because it's normalized via `normalize_address()`.
- **Files**: `coordinator.py`
- **Test file**: `tests/test_button_training_persistence.py` (TestAddressNormalization class)

### Virtual Distance for UKF-Selected Scannerless Rooms (BUG 18)
- **Problem**: When UKF selected a scannerless room, "Distance" showed "Unbekannt" (Unknown)
  - User trains device for "Lagerraum" (basement, no scanner)
  - UKF correctly identifies the room, device shows "Area: Lagerraum"
  - But "Distance: Unbekannt" confuses users
- **Root cause**: BUG 13 fix set `area_distance = None` for scannerless rooms
  - This was to avoid showing misleading distance to scanner in a DIFFERENT room
  - But `None` renders as "Unbekannt" in the UI, which is confusing
  - Other devices using min-distance fallback showed virtual distances, but UKF path didn't
- **Solution**: Calculate virtual distance using UKF match score
  - Same formula as `_get_virtual_distances_for_scannerless_rooms()`:
    `virtual_distance = max_radius × VIRTUAL_DISTANCE_SCALE × (1 - score)²`
  - Now UKF-selected scannerless rooms show meaningful distance values
- **Code change** (`coordinator.py`):
  - Added `match_score` parameter to `_apply_ukf_selection()`
  - Updated 3 call sites to pass `effective_match_score`
  - Replaced `area_distance = None` with virtual distance calculation
  ```python
  # FIX: BUG 18 - Calculate virtual distance for scannerless rooms
  max_radius = self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)
  virtual_distance = max_radius * VIRTUAL_DISTANCE_SCALE * ((1.0 - match_score) ** 2)
  device.area_distance = virtual_distance
  device.area_distance_stamp = nowstamp
  ```
- **Example**: With max_radius=10m, VIRTUAL_DISTANCE_SCALE=0.7:
  | UKF Score | Virtual Distance | Interpretation |
  |-----------|-----------------|----------------|
  | 0.9 | 0.07m | Excellent match → very close |
  | 0.7 | 0.63m | Good match → nearby |
  | 0.5 | 1.75m | Moderate match → medium distance |
  | 0.3 | 3.43m | Threshold match → further away |
- **Files**: `coordinator.py`

### Training Over-Confidence Fix (BUG 19)
- **Problem**: Button training re-read the same cached RSSI values, causing over-confidence
  - Training collected 20 samples at 0.5s intervals = 10 seconds total
  - BLE trackers typically advertise every 1-10 seconds
  - Result: Most samples were the SAME cached value repeated!
  - Kalman filter counted each as a "new" measurement → artificial confidence boost
  - Example: 20 training calls, but only 2-3 unique RSSI values
- **Root cause**: Training loop polled faster than BLE advertisement rate
  - `advert.stamp` check only verified "not too old", not "changed since last sample"
  - Same RSSI value read 5-10 times before new advertisement arrived
- **Solution**: Wait for NEW advertisements between samples
  - Track `last_stamps` (scanner_addr → timestamp) between calls
  - Only count a sample as "successful" if at least one scanner has a newer stamp
  - Use timeout (120s max) instead of fixed iteration count
  - Poll quickly (0.3s) but only train when new data arrives
- **Code changes**:
  - `coordinator.py`: `async_train_fingerprint()` now accepts `last_stamps` parameter and returns `(success, current_stamps)` tuple
  - `button.py`: Training loop tracks timestamps, waits for real new data
- **New constants** (`button.py`):
  | Constant | Value | Purpose |
  |----------|-------|---------|
  | `TRAINING_SAMPLE_COUNT` | 20 | Target number of UNIQUE samples |
  | `TRAINING_MAX_TIME_SECONDS` | 120.0 | Maximum training duration |
  | `TRAINING_POLL_INTERVAL` | 0.3s | How often to check for new data |
- **User impact**: Training may take longer (depends on device's advertisement interval), but produces REAL diverse samples instead of duplicates
- **Files**: `coordinator.py`, `button.py`

## Manual Fingerprint Training System

### Problem Statement

Auto-detection constantly overwrites manual room corrections. Users need a way to:
1. Explicitly train the system for a specific room
2. Have their training persist against continuous automatic learning
3. Break out of "stuck" states (velocity trap, wrong room lock-in)

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                   Complete Training Flow                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────────┐  │
│  │ FloorSelect │───►│ RoomSelect  │───►│ BermudaDevice               │  │
│  │ (select.py  │    │ (select.py  │    │ • training_target_floor_id  │  │
│  │  :209-322)  │    │  :53-207)   │    │ • training_target_area_id   │  │
│  └─────────────┘    └─────────────┘    │ • area_locked_id/name/addr  │  │
│                                        └──────────────┬──────────────┘  │
│                                                       │                  │
│  ┌────────────────────────────────────────────────────┘                  │
│  │                                                                       │
│  ▼                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐│
│  │ TrainingButton (button.py:47-219)                                   ││
│  │ • available: training_target_floor_id AND training_target_area_id  ││
│  │ • async_press(): Wait for 20 UNIQUE samples (max 120s timeout)     ││
│  └──────────────────────────────────┬──────────────────────────────────┘│
│                                     │                                    │
│                                     ▼                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐│
│  │ coordinator.async_train_fingerprint() (coordinator.py:708-811)      ││
│  │                                                                      ││
│  │ 1. device.reset_velocity_history()  ← Breaks velocity trap          ││
│  │ 2. Collect fresh RSSI from all scanners (< EVIDENCE_WINDOW)         ││
│  │ 3. Identify primary scanner (strongest RSSI)                        ││
│  │ 4. AreaProfile.update_button() ← Device-specific fingerprint        ││
│  │ 5. RoomProfile.update_button() ← Device-independent fingerprint     ││
│  │ 6. correlation_store.async_save() ← Immediate persistence           ││
│  └──────────────────────────────────┬──────────────────────────────────┘│
│                                     │                                    │
│                                     ▼                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐│
│  │ Hierarchical Priority (in AreaProfile)                              ││
│  │                                                                      ││
│  │ ┌─────────────────┐    ┌─────────────────┐                          ││
│  │ │ ScannerPair     │    │ ScannerAbsolute │                          ││
│  │ │ Correlation     │    │ Rssi            │                          ││
│  │ │ (delta tracking)│    │ (abs tracking)  │                          ││
│  │ └────────┬────────┘    └────────┬────────┘                          ││
│  │          │                      │                                    ││
│  │          ▼                      ▼                                    ││
│  │   _kalman_auto          _kalman_button                              ││
│  │   (Shadow Mode)         (Frozen Layer)                              ││
│  │        │                      │                                      ││
│  │        │              button.is_initialized?                        ││
│  │        │                   Yes → return button.estimate             ││
│  │        └──────────────────► No → return auto.estimate               ││
│  └─────────────────────────────────────────────────────────────────────┘│
│                                                                          │
│  finally: Clear training_target_* + area_locked_* → Dropdowns reset     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Files

| File | Class/Method | Purpose |
|------|--------------|---------|
| `button.py:47-219` | `BermudaTrainingButton` | UI button, triggers training |
| `select.py:53-207` | `BermudaTrainingRoomSelect` | Room dropdown, sets area lock |
| `select.py:209-322` | `BermudaTrainingFloorSelect` | Floor dropdown, filters rooms |
| `coordinator.py:708-811` | `async_train_fingerprint()` | Core training logic |
| `bermuda_device.py:746-776` | `reset_velocity_history()` | Breaks velocity trap |
| `correlation/area_profile.py` | `AreaProfile.update_button()` | Device-specific fingerprint |
| `correlation/scanner_pair.py` | `ScannerPairCorrelation` | Delta RSSI tracking |
| `correlation/scanner_absolute.py` | `ScannerAbsoluteRssi` | Absolute RSSI tracking |

### Training Button Behavior

**Availability Conditions** (`button.py:108-137`):
```python
@property
def available(self) -> bool:
    if not super().available:
        return False

    # Disable during training (prevent double-click)
    if self._is_training:
        return False

    # Button enabled ONLY when BOTH floor AND room selected
    floor_ok = self._device.training_target_floor_id is not None
    area_ok = self._device.training_target_area_id is not None
    return floor_ok and area_ok
```

**Press Handler** (`button.py:165-255`) - Waits for REAL new advertisements:
```python
async def async_press(self) -> None:
    # Guard against double-click
    if self._is_training:
        return

    self._is_training = True
    self._attr_icon = self.ICON_TRAINING  # mdi:timer-sand
    self.async_write_ha_state()

    try:
        # BUG 19 FIX: Wait for REAL new advertisements
        # Tracks timestamps to ensure each sample has genuinely new data
        successful_samples = 0
        last_stamps: dict[str, float] = {}
        start_time = asyncio.get_event_loop().time()

        while successful_samples < TRAINING_SAMPLE_COUNT:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= TRAINING_MAX_TIME_SECONDS:  # 120s timeout
                break

            # Only succeeds when at least one scanner has NEW data
            success, current_stamps = await self.coordinator.async_train_fingerprint(
                device_address=self.address,
                target_area_id=target_area_id,
                last_stamps=last_stamps,
            )

            if success:
                successful_samples += 1
                last_stamps = current_stamps
            elif current_stamps:
                last_stamps = current_stamps

            await asyncio.sleep(TRAINING_POLL_INTERVAL)  # 0.3s poll

    finally:
        # ALWAYS cleanup, even on exception
        self._is_training = False
        self._attr_icon = self.ICON_IDLE  # mdi:brain
        self._device.training_target_floor_id = None
        # ... clear other fields ...
        await self.coordinator.async_request_refresh()
```

**Training Data Flow:**
```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Training Sample Collection                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  BLE Tracker ─────────────► Home Assistant ─────────────► Bermuda       │
│  (advertises every 1-10s)   (receives adverts)           (caches RSSI)  │
│                                                                          │
│  Training Loop (polls every 0.3s):                                       │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ Poll 1: stamp=100.0, rssi=-75dB → NEW! Sample 1 ✓                  │ │
│  │ Poll 2: stamp=100.0, rssi=-75dB → Same stamp, skip                 │ │
│  │ Poll 3: stamp=100.0, rssi=-75dB → Same stamp, skip                 │ │
│  │ ...                                                                 │ │
│  │ Poll 12: stamp=103.5, rssi=-73dB → NEW! Sample 2 ✓                 │ │
│  │ Poll 13: stamp=103.5, rssi=-73dB → Same stamp, skip                │ │
│  │ ...                                                                 │ │
│  │ Poll 25: stamp=108.2, rssi=-76dB → NEW! Sample 3 ✓                 │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  Result: 20 UNIQUE samples with real diverse RSSI values                │
│          (not 20 copies of the same cached value!)                      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Fingerprint Training Process

**Function Signature** (`coordinator.py:710-739`):
```python
async def async_train_fingerprint(
    self,
    device_address: str,
    target_area_id: str,
    last_stamps: dict[str, float] | None = None,  # BUG 19: Track previous timestamps
) -> tuple[bool, dict[str, float]]:
    """
    Returns (success, current_stamps) - only succeeds with NEW data.
    """
```

**Step 1: Velocity Reset** (`coordinator.py:750-760`)

Breaks the "Velocity Trap" where calculated velocity > MAX_VELOCITY causes all readings to be rejected:
```python
device.reset_velocity_history()
# Clears: hist_velocity, hist_distance, hist_stamp on ALL adverts
# Resets: Kalman filters, velocity_blocked_count
```

**Step 2: RSSI + Timestamp Collection** (`coordinator.py:762-795`)
```python
rssi_readings: dict[str, float] = {}
current_stamps: dict[str, float] = {}  # BUG 19: Track timestamps

for advert in device.adverts.values():
    if (advert.rssi is not None
        and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS):
        rssi_readings[advert.scanner_address] = advert.rssi
        current_stamps[advert.scanner_address] = advert.stamp  # NEW
        # Track strongest signal as "primary"
        if advert.rssi > primary_rssi:
            primary_rssi = advert.rssi
            primary_scanner_addr = advert.scanner_address
```

**Step 2b: Check for NEW Data** (`coordinator.py:797-810`)
```python
# BUG 19 FIX: Only train if we have NEW advertisement data
if last_stamps:
    has_new_data = any(
        current_stamps.get(k, 0) > last_stamps.get(k, 0)
        for k in current_stamps
    )
    if not has_new_data:
        return (False, current_stamps)  # No new data, caller should retry
```

**Step 3: Profile Updates** (`coordinator.py:820-835`)
```python
# Device-specific profile (AreaProfile)
# BUG 17 FIX: Use normalized address
normalized_address = device.address
self.correlations[normalized_address][target_area_id].update_button(
    primary_rssi=primary_rssi,
    other_readings=other_readings,
    primary_scanner_addr=primary_scanner_addr,
)

# Device-independent profile (RoomProfile)
self.room_profiles[target_area_id].update_button(rssi_readings)

return (True, current_stamps)  # Success with new data
```

### Anchor Creation Mechanism (Clamped Fusion)

**Problem**: The old approach used inverse-variance weighted fusion, but auto-learning eventually overwhelmed manual corrections regardless of variance inflation.

**Solution**: Clamped Bayesian Fusion with explicit auto-influence limit (`scanner_pair.py:109-134`, `scanner_absolute.py:108-134`):
```python
def update_button(self, rssi: float) -> float:
    # Create anchor state - high confidence but PHYSICALLY REALISTIC
    # IMPORTANT: variance=2.0 (σ≈1.4dB) allows normal BLE fluctuations
    # Do NOT use variance < 1.0! (See Hyper-Precision Paradox)
    self._kalman_button.reset_to_value(
        value=rssi,
        variance=2.0,       # High confidence, realistic for BLE
        sample_count=500,   # Massive inertia
    )
    return self.expected_rssi  # Returns clamped fusion result
```

**How `reset_to_value()` Works** (`filters/kalman.py:202-229`):
```python
def reset_to_value(self, value: float, variance: float = 2.0, sample_count: int = 500) -> None:
    """Force filter to a specific state (Teacher Forcing)."""
    self.estimate = value
    self.variance = variance
    self.sample_count = sample_count
    self._initialized = True
```

**Effect on Output (with Clamped Fusion)**:
```
Before button training:
  Auto:   1000 samples, estimate=-78dB
  Button: Not initialized
  → expected_rssi returns -78dB (auto fallback)

After button training:
  Auto:   1000 samples, estimate=-78dB (still learning in shadow)
  Button: 500 samples, estimate=-85dB (anchor)
  → Clamped Fusion: auto influence capped at 30%
  → expected_rssi ≈ 0.7*(-85) + 0.3*(-78) = -82.9dB (anchor + polish)
```

### Area Lock Mechanism

**Purpose**: Prevents auto-detection from overriding the user's room selection during and after training.

**Attributes** (`bermuda_device.py:191-199`):
```python
self.area_locked_id: str | None = None        # Locked area ID
self.area_locked_name: str | None = None      # Locked area name
self.area_locked_scanner_addr: str | None = None  # Scanner that was primary when locked
```

**Set by**: `RoomSelect.async_select_option()` when user picks a room
**Cleared by**: `TrainingButton.async_press()` in finally block

**Auto-Unlock Conditions** (handled in coordinator):
- Locked scanner no longer sees device (stamp stale > 60s)
- AND device is seen by other scanners (last_seen fresh)
- If device offline everywhere → keep locked

**USB/BlueZ Scanner Fix**:
USB/BlueZ scanners don't update stamp when RSSI is stable. Fixed by requiring device to be seen elsewhere before unlocking:
```python
if nowstamp - locked_advert.stamp > AREA_LOCK_TIMEOUT_SECONDS:
    if nowstamp - device.last_seen < AREA_LOCK_TIMEOUT_SECONDS:
        # Seen elsewhere but not by locked scanner → unlock
    else:
        # Not seen anywhere → keep locked
```

### UI State Synchronization

**State Ownership:**

| Field | Owner | Purpose |
|-------|-------|---------|
| `_floor_override_name/id` | FloorSelect | Local UI state |
| `_room_override_name/id` | RoomSelect | Local UI state |
| `training_target_floor_id` | BermudaDevice | Shared state for button availability |
| `training_target_area_id` | BermudaDevice | Shared state for button availability |
| `area_locked_*` | BermudaDevice | Prevents auto-detection override |

**Critical Invariant:** Device attributes (`training_target_*`) must be set BEFORE local UI variables to prevent race conditions with coordinator refreshes. See Lesson #8.

**Synchronization via `_handle_coordinator_update()`:**

```python
# In RoomSelect/FloorSelect:
@callback
def _handle_coordinator_update(self) -> None:
    # If device attr was cleared (by button), clear local UI
    if self._device.training_target_area_id is None:
        self._room_override_name = None
        self._room_override_id = None
    super()._handle_coordinator_update()
```

This pattern allows the button to clear the dropdowns indirectly by:
1. Setting `training_target_*` to `None`
2. Triggering a coordinator refresh
3. Each dropdown's `_handle_coordinator_update()` sees `None` and clears itself

### Constants

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `TRAINING_SAMPLE_COUNT` | 20 | `button.py` | Target UNIQUE samples (meets maturity) |
| `TRAINING_MAX_TIME_SECONDS` | 120.0 | `button.py` | Max training duration |
| `TRAINING_POLL_INTERVAL` | 0.3s | `button.py` | Poll interval for new data |
| `EVIDENCE_WINDOW_SECONDS` | - | `const.py` | Max age for RSSI readings |
| `AREA_LOCK_TIMEOUT_SECONDS` | 60 | `const.py` | Stale threshold for auto-unlock |
| `MIN_SAMPLES_FOR_MATURITY` | 30/20 | `scanner_pair.py`/`scanner_absolute.py` | Samples before trusting profile |
| Converged threshold | 5.0 | inline | Variance below which inflation triggers |
| Inflation target | 15.0 | inline | Reset variance value |

## Lessons Learned

> **See also:** [Architecture Decisions & FAQ](#architecture-decisions--faq) for common "Why?" questions about design choices (30% clamping, variance=2.0, device-level reset, etc.)

### 1. State Transitions Need Careful Handling

When tracking state (like `area_changed_at`), consider ALL transition paths:
- Normal: `"Kitchen" → "Office"` ✅
- Initial: `None → "Kitchen"` (first assignment)
- Re-acquisition: `None → "Kitchen"` (after scanner outage)

**Fix**: Check both `old_area is not None` AND `area_changed_at != 0.0`:
```python
if old_area != self.area_name:
    if old_area is not None or self.area_changed_at != 0.0:
        self.area_changed_at = stamp_now
```

### 2. Test Fixtures Must Mirror Production Classes

When adding new attributes/methods to production classes, update ALL test fixtures:
- `FakeDevice` in `test_area_selection_cross_floor_guard.py`
- Any mock objects in other test files

### 3. Kalman Filter Already Uses Fingerprints

The `correlation/` system uses `KalmanFilter` internally:
- `ScannerAbsoluteRssi` wraps `KalmanFilter` for per-scanner RSSI learning
- `ScannerPairCorrelation` uses Kalman for delta tracking

This provides foundation for UKF integration.

### 4. Line Length in Log Messages

Ruff enforces 120 char limit. Split long format strings:
```python
# BAD (too long)
_LOGGER.debug("Stability margin (%s): %s rejected (%.2fm improvement, %.1f%% < required %.1f%% or %.2fm)", ...)

# GOOD (split string)
_LOGGER.debug(
    "Stability margin (%s): %s rejected "
    "(%.2fm, %.1f%% < %.1f%% or %.2fm)",
    ...
)
```

### 5. Python 3.13 Required

The codebase uses Python 3.12+ features like `type` aliases:
```python
type BermudaConfigEntry = "ConfigEntry[BermudaData]"  # Requires Python 3.12+
```

Always use `python3.13 -m venv venv` for the virtual environment.

### 6. Kalman Variance Converges Quickly

Kalman filter variance (uncertainty) converges to a steady state after ~20 samples **per correlation object**:
- Initial variance: 16.0 (high uncertainty)
- After 20 samples: ~2.6 (steady state)
- More samples beyond 20 don't significantly reduce variance

Each `ScannerPairCorrelation` and `ScannerAbsoluteRssi` instance has its own filters that converge independently.

**Implication for Clamped Fusion**: The button filter variance determines its confidence level for BOTH fusion weighting AND z-score matching. We use variance=2.0 (σ≈1.4dB) to balance:
- Fusion: Still much lower than auto variance (~3-16), so button dominates
- Matching: Realistic for BLE signals, accepts normal 2-3dB fluctuations

See Lesson #27 for why artificially low variance breaks matching.

### 7. Trace Full Call Chain for Attribute Precedence

When modifying code that passes objects to other methods, trace the full call chain to understand:
- Which attributes are used
- In what order (precedence/fallback logic)
- Whether your modifications will actually take effect

**Example Bug**: Setting `advert.area_id` to override the area, but `apply_scanner_selection()` reads `advert.scanner_device.area_id` first and only falls back to `advert.area_id` if scanner_device has no area.

**Fix Pattern**: Temporarily nullify the higher-precedence attribute:
```python
# Temporarily clear scanner_device so apply_scanner_selection
# uses our overridden area_id instead of scanner_device.area_id
saved_scanner_device = advert.scanner_device
advert.scanner_device = None
advert.area_id = target_area_id

device.apply_scanner_selection(advert, nowstamp=nowstamp)

advert.scanner_device = saved_scanner_device  # Restore
```

**Checklist before modifying object attributes**:
1. Find all methods that consume the object
2. Check attribute read order in those methods
3. Verify your modification will actually be used
4. Consider side effects of temporarily modifying other attributes

### 8. Coordinator Refresh Race Conditions in Entity State

When entities (Select, Button) maintain both local UI state AND shared device state, coordinator refreshes can cause race conditions.

**The Problem:**

```python
# BAD - Race condition possible!
async def async_select_option(self, option: str) -> None:
    # Step 1: Set local UI variable
    self._room_override_name = option          # Local state set

    # ⚠️ DANGER ZONE: Coordinator refresh can happen here!
    # _handle_coordinator_update() would see training_target_area_id=None
    # and CLEAR _room_override_name!

    # Step 2: Set device attribute (too late!)
    self._device.training_target_area_id = target_area.id
```

**Timeline of the bug:**
```
T0: User selects "Kitchen" in dropdown
T1: async_select_option() sets _room_override_name = "Kitchen"
T2: Coordinator refresh starts (every ~0.9s)
T3: _handle_coordinator_update() runs:
    → Sees training_target_area_id is still None
    → Clears _room_override_name back to None!
T4: User sees empty dropdown (confused!)
T5: async_select_option() finally sets training_target_area_id = "kitchen_id"
    → Too late, UI is already cleared
```

**The Fix - Set shared state FIRST:**

```python
# GOOD - Device attribute first prevents race condition
async def async_select_option(self, option: str) -> None:
    # Step 1: Set device attribute FIRST
    self._device.training_target_area_id = target_area.id

    # Now coordinator refresh is safe - it will see the device attr is set
    # and won't clear local variables

    # Step 2: Set local UI variables
    self._room_override_name = option
    self._room_override_id = target_area.id
```

**General Rule:** When synchronizing state between entities via shared objects (like BermudaDevice), always set the shared/authoritative state BEFORE the local/derived state. The `_handle_coordinator_update()` callback checks the shared state to decide whether to clear local state.

**Checklist for entity state synchronization:**
1. Identify which state is "authoritative" (shared device attrs)
2. Identify which state is "derived" (local UI variables)
3. In setters: Set authoritative state FIRST
4. In `_handle_coordinator_update()`: Check authoritative state to sync derived state

**Test Coverage:** See `tests/test_training_ui_race_condition.py` for comprehensive race condition tests.

### 9. Use try/finally for Robust Cleanup

When a function must clean up state regardless of success/failure, use `try/finally`:

```python
# GOOD - cleanup always happens
async def async_press(self) -> None:
    try:
        for i in range(TRAINING_SAMPLE_COUNT):
            await self.coordinator.async_train_fingerprint(...)
    finally:
        # ALWAYS clear training fields, even if training fails
        self._device.training_target_floor_id = None
        self._device.training_target_area_id = None
        await self.coordinator.async_request_refresh()
```

**Why this matters:** Without `try/finally`, exceptions could leave the UI in an inconsistent state (dropdowns filled, button enabled, but training incomplete).

### 10. Feature Parity Between Code Paths

When adding a new code path (e.g., UKF area selection), ensure it has feature parity with the existing path:

**Bug:** UKF path was missing streak protection for cross-floor switches. Min-distance path had it, UKF didn't.

```python
# BOTH paths need streak protection!
# Min-distance path: _refresh_area_by_min_distance() ✅
# UKF path: _refresh_area_by_ukf() ❌ (was missing)

# FIX: Added same streak logic to UKF path
if floor_changed:
    required_streak = CROSS_FLOOR_STREAK  # 6
else:
    required_streak = SAME_FLOOR_STREAK   # 4
```

**Checklist when adding alternative code paths:**
1. List all features/protections in the original path
2. Verify each exists in the new path
3. Consider extracting shared logic to helper functions

### 11. Backward Compatibility via data.get()

When adding new keys to stored data, prefer `data.get()` over schema migration:

```python
# BAD - requires migration, version bump, complexity
STORAGE_VERSION = 2  # Bump from 1
async def _async_migrate(data):
    if data["version"] == 1:
        data["rooms"] = {}  # Add missing key
        data["version"] = 2
    return data

# GOOD - graceful degradation, no migration needed
STORAGE_VERSION = 1  # Keep as is
rooms = data.get("rooms", {})  # Handle missing key
```

**When to use which:**
- `data.get()`: New optional features, beta code, backward-compatible additions
- Migration: Breaking changes, data format changes, production-critical data

### 12. Debug Logging with Object IDs

When debugging issues where multiple components share objects, log `id(object)` to verify they're using the same instance:

```python
_LOGGER.debug(
    "Setting training_target_area_id for %s: %s (device id: %s)",
    self._device.name,
    target_area.id,
    id(self._device),  # Unique object identifier
)
```

**This helped identify:** Whether RoomSelect, FloorSelect, and TrainingButton were all using the same BermudaDevice instance (they were - the bug was elsewhere).

### 13. Threshold Tuning for ML/Heuristic Features

Initial threshold values are often wrong. Plan for iteration:

**Example - RSSI sanity check for UKF:**
```
Iteration 1: 10 dB threshold → Too strict, blocked valid UKF decisions
Iteration 2: 15 dB threshold + confidence check → Better balance
```

**Key insight:** Add confidence/score checks to allow exceptions:
```python
# Strict check only when UKF is uncertain
if match_score < 0.6 and rssi_delta > 15:
    fallback_to_min_distance()
# High confidence UKF can override RSSI heuristics
```

## UKF + Fingerprint Fusion (Implemented)

### Implementation Status: ✅ Complete (Experimental)

All planned phases have been implemented:

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | UKF core in `filters/ukf.py` | ✅ Complete |
| Phase 2 | Integration with AreaProfile fingerprints | ✅ Complete |
| Phase 3 | Parallel operation with min-distance heuristic | ✅ Complete (fallback) |
| Phase 4 | Configurable toggle | ✅ Complete |

### Architecture Overview

**Standard Mode (Default):**
```
Scanner 1 → Kalman → RSSI₁ ─┐
Scanner 2 → Kalman → RSSI₂ ─┼─→ Min-Distance Heuristic → Room
Scanner 3 → Kalman → RSSI₃ ─┘
```

**UKF Mode (Experimental, opt-in via `use_ukf_area_selection`):**
```
                    ┌─────────────────────────────────────┐
Scanner 1 ──┐       │ UKF State: [rssi₁, rssi₂, rssi₃]   │
Scanner 2 ──┼──────→│ Covariance: P (cross-correlation)  │
Scanner 3 ──┘       │ Process: RSSI drifts slowly        │
                    └────────────────┬────────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────────┐
                    │ Fingerprint Match (Mahalanobis)     │
                    │ D² = (x̂ - μ_area)ᵀ Σ⁻¹ (x̂ - μ_area) │
                    │ Room = argmin_area(D²)              │
                    └────────────────┬────────────────────┘
                                     │
                         ┌───────────┴───────────┐
                         │ Match score ≥ 0.3?    │
                         └───────────┬───────────┘
                              Yes ↓      ↓ No
                         ┌─────────────────────────┐
                         │ Apply UKF │ Fallback to│
                         │ Decision  │ Min-Distance│
                         └─────────────────────────┘
```

### Implementation Details

**Key Files:**
- `filters/ukf.py` - Pure Python UKF implementation (~600 lines)
- `coordinator.py` - Integration: `_refresh_area_by_ukf()`, `device_ukfs` dict
- `const.py` - `CONF_USE_UKF_AREA_SELECTION`, `UKF_MIN_MATCH_SCORE`, `UKF_MIN_SCANNERS`

**Plan Deviations:**
1. **Pure Python vs NumPy**: Implemented without numpy dependency for HA compatibility
   - Custom matrix operations: `_cholesky_decompose`, `_matrix_inverse`, etc.
   - Slightly slower but no extra dependencies
2. **Fallback Integration**: UKF tries first, falls back to min-distance if:
   - Fewer than 2 scanners visible
   - No learned fingerprints for device
   - Match score below threshold (0.3)
3. **Lowercase Naming**: Standard Kalman notation (P, Q, K, R) renamed to `p_cov`, `q_noise`, `k_gain`, `r_noise` per Python conventions

**Configuration:**
```yaml
# In HA UI: Settings → Integrations → Bermuda → Configure → Global Options
use_ukf_area_selection: false  # Default: disabled (experimental)
```

**Constants:**
| Constant | Value | Purpose |
|----------|-------|---------|
| `UKF_MIN_SCANNERS` | 2 | Minimum scanners for UKF decision |
| `UKF_MIN_MATCH_SCORE` | 0.3 | Minimum fingerprint match confidence |

### Benefits Achieved
- Cross-correlation between scanners preserved in covariance matrix
- Partial observations handled gracefully (scanner offline → uncertainty grows)
- Probabilistic room assignment via Mahalanobis distance
- Optimal fusion: UKF uncertainty + fingerprint variance combined

### Variance Floor Fix (Hyper-Precision Paradox)

**Problem:** When both the UKF state and the learned profile have converged after many
samples, their variances become very small (2-5). This causes normal BLE fluctuations
(3-5 dB) to appear as massive statistical deviations (2+ sigma), rejecting correct
rooms in favor of poorly-trained alternatives.

**The "Lagerraum" Bug:**
```
Lagerraum (well-trained):  Profile=-85dB, Current=-82dB, Variance=2.5
  → Deviation: 3dB / sqrt(2.5) = 1.9 sigma
  → D² ≈ 3.6, Score = exp(-3.6/4) ≈ 0.41  (should win but...)

Praxis (poorly-trained):   Profile=-75dB, Current=-82dB, Variance=15.0
  → Deviation: 7dB / sqrt(15) = 1.8 sigma
  → D² ≈ 3.2, Score = exp(-3.2/4) ≈ 0.45  (wins incorrectly!)
```

**Solution:** `UKF_MIN_MATCHING_VARIANCE = 25.0` (sigma = 5 dB) as a floor for the
diagonal elements of the combined covariance matrix in `match_fingerprints()`.

```python
# In ukf.py match_fingerprints():
for k in range(n_sub):
    combined_cov[k][k] = max(combined_cov[k][k], UKF_MIN_MATCHING_VARIANCE)
```

**Key Design Decisions:**

1. **Value 25.0 (sigma = 5 dB)**: Upper end of typical BLE noise, ensures realistic tolerance
2. **Separate from UKF_MEASUREMENT_NOISE (4.0)**: That's for per-sample Kalman updates;
   this floor is for comparing momentary state vs long-term profile average
3. **Uses `max()` not `+=`**: Prevents double-counting if variance is already high
4. **Applied AFTER combining p_sub and fp_var**: Floor acts as safety net, not replacement

**Effect:**

| Deviation | Without Floor (var=5) | With Floor (var=25) |
|-----------|----------------------|---------------------|
| 3 dB | D²=1.8, Score=0.64 | D²=0.36, Score=0.91 |
| 5 dB | D²=5.0, Score=0.29 | D²=1.0, Score=0.78 |
| 10 dB | D²=20, Score=0.007 | D²=4.0, Score=0.37 |
| 15 dB | D²=45, Score≈0 | D²=9.0, Score=0.11 |

**Test Coverage:** See `tests/test_ukf.py`:
- `TestVarianceFloorFix`: Unit tests for floor behavior
- `TestLagerraumScenario`: Integration tests for the specific bug

### Next Steps (Future Work)

1. **Field Testing**: Enable on test installations, compare with min-distance
2. **Tuning**: Adjust `UKF_MIN_MATCH_SCORE` based on real-world data
3. **Diagnostics**: Add UKF state to dump_devices service output
4. **Hybrid Mode**: Combine UKF confidence with min-distance for tiebreaking
5. **Performance**: Profile UKF overhead on large scanner networks

### Rejected Alternative: Student-t Score Function (Phase 2)

**Status:** ❌ Not implemented (variance floor is sufficient)

A proposal was made to replace the Gaussian score function `exp(-D²/(2n))` with a
Student-t kernel to handle "heavy-tailed" BLE RSSI distributions. After mathematical
analysis, this was **rejected** because the variance floor (Phase 1) already solves
the problem, and combining both would make matching too tolerant.

**The Standard Multivariate Student-t Formula:**

According to [Wikipedia](https://en.wikipedia.org/wiki/Multivariate_t-distribution):
```
f(x) ∝ (1 + D²/ν)^(-(ν+p)/2)
```
Where:
- D² = Mahalanobis distance squared
- ν = degrees of freedom (typically 4-5 for robust estimation)
- p = dimension (number of scanners)

**The Proposed (Ad-hoc) Formula:**
```python
avg_d_squared = d_squared / n_sub      # = D²/p
base = 1.0 + (avg_d_squared / NU)      # = 1 + D²/(p·ν)
exponent = -(NU + 1.0) / 2.0           # = -(ν+1)/2
device_score = math.pow(base, exponent)
```

**Critical Differences:**

| Aspect | Standard t | Proposed | Impact |
|--------|------------|----------|--------|
| Base denominator | ν | p·ν | p times smaller |
| Exponent | -(ν+p)/2 | -(ν+1)/2 | Constant, not dimensional |

**Example Calculation (p=3 scanners, D²=9, ν=4):**

| Method | Formula | Score |
|--------|---------|-------|
| Standard t | (1 + 9/4)^(-(4+3)/2) | 0.017 |
| Proposed | (1 + 9/12)^(-(4+1)/2) | 0.25 |
| Current Gaussian | exp(-9/6) | 0.22 |

The proposed formula is **15x more tolerant** than standard Student-t!

**Risk: Over-Tolerance with Both Fixes**

| Config | 5dB deviation score | 12dB deviation score |
|--------|---------------------|----------------------|
| Phase 1 alone | 0.61 | 0.38 |
| Phase 1 + Phase 2 | 0.72 | 0.52 |

With both fixes, a **wrong room** with 12dB deviation gets score 0.52 (should be < 0.3).

**Why Variance Floor Alone is Sufficient:**

1. The "hyper-precision paradox" is caused by converged Kalman variances, not by
   the Gaussian score function itself
2. The variance floor ensures D² stays reasonable (< 5 for normal BLE noise)
3. With reasonable D², the Gaussian function works correctly
4. Adding Student-t on top would reduce discrimination between correct/wrong rooms

**If Student-t is Ever Needed (Extreme Multipath Environments):**

1. Use the **correct** multivariate formula: `(1 + D²/ν)^(-(ν+n)/2)`
2. **Reduce** the variance floor to 10-15 (not 25)
3. Add tests for false-positive rate
4. Verify discrimination ratio between correct and wrong rooms stays > 2.0

**References:**
- [Multivariate t-distribution - Wikipedia](https://en.wikipedia.org/wiki/Multivariate_t-distribution)
- [Statlect: Multivariate Student's t](https://www.statlect.com/probability-distributions/multivariate-student-t-distribution)
- [UWB/IMU Fusion with Mahalanobis Distance](https://pubmed.ncbi.nlm.nih.gov/30322106/)

## Correlation Confidence Architecture

### Problem: Delta-Only Matching Causes False Positives

The original `_get_correlation_confidence()` only checked relative RSSI deltas between scanners (the "vector shape"). This caused false positives when a device far outside the house (-90dB) matched a room learned at close range (-50dB) because the relative scanner relationships happened to align.

```
Outside Device:     Scanner A: -90dB, Scanner B: -85dB  → Delta: 5dB
Learned Kitchen:    Scanner A: -50dB, Scanner B: -45dB  → Delta: 5dB
                    ↑ Same delta shape, but completely wrong magnitude!
```

### Solution: Dual-Check Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                  _get_correlation_confidence()                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Current RSSI ──┬──→ get_z_scores() ──────────→ Delta Z-Scores      │
│  Readings       │    (relative deltas)          (shape match)        │
│                 │                                     │              │
│                 └──→ get_absolute_z_scores() ──→ Absolute Z-Scores  │
│                      (magnitude check)           (level match)       │
│                                                       │              │
│                                    ┌──────────────────┘              │
│                                    ▼                                 │
│                         ┌─────────────────────┐                      │
│                         │ max_abs_z > 3.0?    │                      │
│                         └─────────┬───────────┘                      │
│                              Yes ↓      ↓ No                         │
│                    ┌─────────────────────────────┐                   │
│                    │ Apply exponential │ Normal  │                   │
│                    │ penalty to delta  │ delta   │                   │
│                    │ confidence        │ conf.   │                   │
│                    └─────────────────────────────┘                   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Penalty Formula:**
```python
# Z-score 3.0 = 3 standard deviations from learned mean
# Exponential penalty: halves confidence for each std dev beyond 2
absolute_penalty = 0.5 ** (max_abs_z - 2.0)

# z=3 → 0.5x confidence
# z=4 → 0.25x confidence
# z=5 → 0.125x confidence
```

### Key Insight

Both checks are necessary:
- **Delta Z-Scores**: "Is the relationship between scanners correct?"
- **Absolute Z-Scores**: "Is the overall signal level correct?"

A device must pass BOTH checks to be confidently placed in a room.

## Button Training vs Auto-Learning Architecture

### Problem: Pure Fusion Allows Drift Over Time

The original dual-filter system used unclamped inverse-variance weighted fusion. Even with variance inflation, auto-learning could eventually overwhelm manual corrections over weeks/months.

```
The "Keller-Lager Problem" (with Pure Fusion):
Day 1: User trains "Keller" → Button: -85dB
Week 2: Auto has 10,000 samples at -78dB → Auto starts to dominate
Month 3: Auto has 100,000 samples → User calibration completely lost
→ Room detection drifts despite initial manual training!
```

### Solution: Clamped Bayesian Fusion (Controlled Evolution)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Clamped Fusion Flow                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Button Press ──→ reset_to_value()                                  │
│                          │                                           │
│                          ▼                                           │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Create ANCHOR state:                                          │   │
│  │   - estimate = user's value                                   │   │
│  │   - variance = 2.0 (σ≈1.4dB, realistic for BLE)              │   │
│  │   - sample_count = 500 (massive inertia)                      │   │
│  │   ⚠️ Do NOT use variance < 1.0! (Hyper-Precision Paradox)     │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                          │                                           │
│                          ▼                                           │
│  expected_rssi uses CLAMPED FUSION:                                  │
│    1. Calculate inverse-variance weights                             │
│    2. If auto_weight > 30% → clamp to 30%                           │
│    3. Return weighted average (user ≥70%, auto ≤30%)                │
│                                                                      │
│  z_score() uses SAME variance for matching:                          │
│    - With variance=2.0: 2dB deviation = 1.4 sigma (OK!)             │
│    - With variance=0.1: 2dB deviation = 6.3 sigma (REJECTED!)       │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Result:**
```
Before: Pure fusion allowed auto to overwhelm button over time
After:  Clamped fusion limits auto to 30% influence

Auto:   100,000 samples, estimate=-78dB (clamped to 30% weight)
Button: 500 samples (anchor), estimate=-85dB (at least 70% weight)
→ expected_rssi ≈ 0.7*(-85) + 0.3*(-78) = -82.9dB
→ Room detection stays stable, but adapts slightly to real changes!
```

### Key Design Decisions

1. **Clamped fusion**: Auto influence limited to max 30% (user keeps ≥70%)
2. **Anchor state**: `reset_to_value()` creates high-confidence calibration point
3. **Controlled evolution**: Auto can "polish" the anchor, but never overpower it
4. **Intelligent adaptation**: System responds to real environmental changes within limits
5. **Both correlation classes**: `ScannerPairCorrelation` AND `ScannerAbsoluteRssi` use identical clamped fusion logic
6. **Realistic variance (2.0)**: Avoids "Hyper-Precision Paradox" - variance serves both fusion AND z-score matching, so must be physically realistic (σ≈1.4dB for BLE signals)

## Architecture Decisions & FAQ

This section answers common questions about design choices. Reference this before asking "Why is X done this way?"

### Q1: Why 30% for MAX_AUTO_RATIO in Clamped Fusion?

**Answer:** Heuristic safety value ensuring user retains mathematical majority (70%).

- At 50/50, long-term auto-drift could "uproot" user's anchor
- 30% allows "polishing" (seasonal changes, furniture moves) without room reversal
- Mathematically: user can NEVER be overwhelmed, but system stays adaptive

### Q2: How are Scannerless Rooms Created?

**Answer:** They are NOT auto-detected. They exist only after explicit user training.

```
User selects "Keller" in UI → Presses "Train" button
→ AreaProfile created for area_id with NO corresponding ScannerDevice
→ Now available for UKF fingerprint matching
```

Without training, a scannerless room is invisible to the system.

### Q3: Why Two UKF Thresholds (0.3 Switch vs 0.15 Retention)?

**Answer:** Intentional hysteresis to prevent flickering.

| Action | Threshold | Rationale |
|--------|-----------|-----------|
| **Enter** room (switch) | 0.3 | Strong evidence required |
| **Stay** in room (retention) | 0.15 | Weaker evidence acceptable |

**Not pendling, but "sticking"**: Score drops to 0.2 → stays in room (retention). Only below 0.15 → fallback to min-distance.

### Q4: Why is VELOCITY_TELEPORT_THRESHOLD = 30 (not dynamic)?

**Answer:** Dynamic adjustment is unreliable because update rate depends on advertisement interval (varies per device, e.g., deep sleep).

- A high static value (30) + packet debounce (100ms) is robust "one-size-fits-all"
- Initially 5, then 10, finally 30 after real-world testing
- Lower values caused false teleport detections → broke cross-floor protection

### Q5: What About Devices with >60s Advertisement Intervals vs Area Lock?

**Answer:** Lock expires, but this is acceptable.

- Lock serves ONLY to stabilize during active training (button press)
- Device sleeping >60s sends no data to interfere with learning
- If it wakes at 61s, normal detection logic resumes
- Edge case, not worth complexity of dynamic timeouts

### Q6: Why Not Adaptive Variance in Button Filter?

**Answer:** Adaptive variance in button filter is dangerous.

```
"Quiet environment" → Lower variance to 0.1
Door opens → Environment changes
→ Hyper-Precision Paradox kicks in → Room REJECTED!
```

**variance=2.0 is NOT a measurement, it's a TOLERANCE DEFINITION.**

Even in a shielded cellar with perfect signal, allowing 2.0 tolerance is fine (z-score ≈ 0.01). The problem was only the OTHER direction (too tight tolerance with normal noise).

### Q7: What Test Coverage is Required?

**Answer:** No hard percentage gate, but critical paths MUST be unit-tested.

| Must Test | Example |
|-----------|---------|
| Kalman filter logic | `test_kalman.py` |
| UKF state transitions | `test_ukf.py` |
| Correlation updates | `test_correlation_*.py` |
| Room selection logic | `test_area_selection*.py` |

**Rule:** Any change to room-finding logic requires a test reproducing the scenario.

### Q8: Why Device-Level Reset (not Per-Room)?

**Answer:** "Ghost Scanner" problem often involves INVISIBLE rooms.

- User doesn't know WHICH room has incorrect training
- Problematic scanner may not be visible anymore
- Device-level reset is the "nuclear option" that catches ALL cases
- Granular per-room deletion is future work (requires complex UI)

### Q9: Why Quadratic Formula for Virtual Distance (not Linear)?

**Answer:** Quadratic rewards good matches MORE aggressively.

| Score | Linear (7m base) | Quadratic (7m base) |
|-------|-----------------|---------------------|
| 0.5 | 3.5m | 1.75m |
| 0.3 | 4.9m | 3.43m |

- A "good" fingerprint match (score ≥ 0.3) should STRONGLY compete
- Linear gives mediocre distances that often lose to mid-range scanners
- Quadratic makes fingerprint-trained rooms meaningful competitors
- The 0.7 scale factor ensures even score=0 doesn't exceed `max_radius * 0.7`

### Q10: Why Only Button-Trained Profiles Get Virtual Distance?

**Answer:** Virtual distance represents USER INTENT, not automatic patterns.

- **Auto-learned profiles**: System guesses, may be wrong, shouldn't compete with real measurements
- **Button-trained profiles**: User explicitly said "this device IS in this room"
- Giving virtual distances to auto-learned profiles could cause phantom room detections
- User's explicit training is the ONLY reliable signal for scannerless rooms

**The Flow:**
```
Auto-learned room → No button training → No virtual distance → Invisible to min-distance
Button-trained room → has_button_training=True → Virtual distance → Competes with scanners
```

---

## Cross-Floor Hysteresis Protection

### Problem: Signal Spikes Bypass Floor Protection

BLE signals reflect off walls and furniture, causing momentary signal spikes. The original code allowed a 45% improvement (`cross_floor_escape`) to bypass all history requirements for cross-floor switches.

```
Time 0: Device in Kitchen (Floor 1), stable
Time 1: Signal reflection causes Scanner on Floor 2 to report 50% closer
Time 2: IMMEDIATE floor switch (no history check!)
Time 3: Reflection ends, switch back
→ Rapid flickering between floors
```

### Solution: Strict Cross-Floor Requirements

```
┌─────────────────────────────────────────────────────────────────────┐
│            Cross-Floor Switch Decision Tree                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Challenger on different floor?                                      │
│              │                                                       │
│         Yes ↓                                                        │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │ Path A: sustained_cross_floor                             │       │
│  │ - Both have full history (CROSS_FLOOR_MIN_HISTORY)       │       │
│  │ - Historical min/max confirms challenger consistently     │       │
│  │ - Current pcnt_diff > cross_floor_margin                 │       │
│  └──────────────────────────────────────────────────────────┘       │
│              │                                                       │
│         OR   ↓                                                       │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │ Path B: escape_with_history (NEW - stricter)             │       │
│  │ - pcnt_diff >= 100% (was 45%)                            │       │
│  │ - AND minimum history exists (half of full requirement)  │       │
│  └──────────────────────────────────────────────────────────┘       │
│              │                                                       │
│  Neither path satisfied? → REJECT cross-floor switch                │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Key Changes:**
| Parameter | Before | After | Effect |
|-----------|--------|-------|--------|
| `cross_floor_escape` | 45% | 100% minimum | Signal must be DOUBLE to escape |
| History requirement | None for escape | Half of full | At least some sustained evidence |

### Lessons Learned

### 14. Relative vs Absolute Signal Matching

When matching signals against learned patterns, check BOTH:
- **Relative/Delta**: Shape of signal across multiple sources
- **Absolute/Magnitude**: Overall signal level

**Bug Pattern**: Delta-only matching causes false positives when a far device happens to have the same relative scanner relationships as a close device in a learned room.

**Fix Pattern**: Add absolute z-score check with exponential penalty for large deviations.

```python
# BAD - Only checks shape
confidence = z_scores_to_confidence(delta_z_scores)

# GOOD - Checks shape AND magnitude
delta_confidence = z_scores_to_confidence(delta_z_scores)
if max_absolute_z > 3.0:
    confidence = delta_confidence * (0.5 ** (max_absolute_z - 2.0))
```

### 15. Unclamped Fusion Cannot Preserve Manual Calibration Long-Term

When fusing estimates from multiple sources WITHOUT clamping, the source with more samples eventually dominates:
- Automatic learning accumulates indefinitely → eventually overwhelms manual
- Variance inflation only delays the problem, doesn't solve it
- Manual corrections become ineffective over weeks/months

**Fix Pattern**: Use CLAMPED fusion - limit auto influence to a maximum percentage:

```python
MAX_AUTO_RATIO = 0.30  # Auto can never exceed 30% influence

@property
def estimate(self):
    if not self._manual_filter.is_initialized:
        return self._auto_filter.estimate  # 100% auto fallback

    # Clamped Bayesian Fusion
    w_btn = 1.0 / self._manual_filter.variance
    w_auto = 1.0 / self._auto_filter.variance

    # CLAMP: Auto influence limited to 30%
    if w_auto / (w_btn + w_auto) > MAX_AUTO_RATIO:
        w_auto = w_btn * (MAX_AUTO_RATIO / (1.0 - MAX_AUTO_RATIO))

    total = w_btn + w_auto
    return (manual * w_btn + auto * w_auto) / total
```

**Result**: User retains at least 70% authority, auto can "polish" within limits.

### 16. Hysteresis Escape Hatches Need Guarding

Hysteresis protections (streak requirements, history checks) often have "escape hatches" for obvious cases. These escape hatches can become attack vectors for noise.

**Bug Pattern**:
```python
# Escape hatch bypasses all protection!
if improvement > 45%:
    switch_immediately()  # No history check
```

**Fix Pattern**: Escape hatches should STILL require some evidence:
```python
# Escape requires BOTH high threshold AND some history
if improvement > 100% and has_minimum_history:
    switch_immediately()
```

**Rule of Thumb**: The more severe the action (cross-floor > same-floor > same-room), the stricter the escape hatch should be. Consider whether "impossible" thresholds (100%+) are actually desirable for critical transitions.

## Scannerless Room Detection

### Complete Architecture (Post-BUG 15-19 Fixes)

Scannerless rooms are rooms without their own BLE scanner. They can only be detected through fingerprint matching, not physical proximity to a scanner.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│               Scannerless Room Detection Flow (Complete)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. USER TRAINING (button.py)                                                │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ • User selects floor + room in dropdowns                               │ │
│  │ • Clicks "Learn" button                                                 │ │
│  │ • Button disabled during training (BUG 19 double-click fix)            │ │
│  │ • Waits for 20 UNIQUE samples (real new advertisements)                │ │
│  │ • Max 120s timeout                                                      │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                              │                                               │
│                              ▼                                               │
│  2. FINGERPRINT STORAGE (coordinator.py)                                     │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ • Uses normalized address (BUG 17 fix)                                 │ │
│  │ • Only trains when stamp changed (BUG 19 fix)                          │ │
│  │ • Creates AreaProfile with has_button_training=True                    │ │
│  │ • Profile is_mature=True regardless of sample count (BUG 12 fix)       │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                              │                                               │
│                              ▼                                               │
│  3. AREA DETECTION (coordinator._refresh_area_by_ukf)                        │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ • UKF created dynamically if missing (BUG 16 fix)                      │ │
│  │ • Fingerprint matching via Mahalanobis distance                        │ │
│  │ • Retention threshold 0.15 (vs 0.30 for switching)                     │ │
│  │                                                                         │ │
│  │ If UKF score >= threshold:                                              │ │
│  │   → _apply_ukf_selection()                                              │ │
│  │   → Virtual distance calculated (BUG 18 fix)                           │ │
│  │                                                                         │ │
│  │ If UKF score < threshold:                                               │ │
│  │   → Falls back to _refresh_area_by_min_distance()                      │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                              │                                               │
│                              ▼                                               │
│  4. MIN-DISTANCE FALLBACK (with Virtual Distance)                            │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ • Physical scanners: Real measured distance                            │ │
│  │ • Scannerless rooms: Virtual distance from UKF score (BUG 15 fix)      │ │
│  │                                                                         │ │
│  │   virtual_distance = max_radius × 0.7 × (1 - score)²                   │ │
│  │                                                                         │ │
│  │ • Only button-trained profiles get virtual distance                    │ │
│  │ • Quadratic formula rewards good matches aggressively                  │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  RESULT: Scannerless room can "win" against physical scanners               │
│          by having a better fingerprint match score                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Bug Fix Summary for Scannerless Rooms

| BUG | Problem | Fix |
|-----|---------|-----|
| **12** | Button training profiles "immature" (< 20 samples) | `is_mature=True` if `has_button_training` |
| **15** | Scannerless rooms invisible to min-distance | Virtual distance from UKF score |
| **16** | UKF not created for 1-scanner scenarios | Create UKF dynamically in virtual distance calc |
| **17** | Training stored under wrong key | Use normalized `device.address` |
| **18** | UKF path showed "Unknown" distance | Calculate virtual distance in UKF path too |
| **19** | Training re-read same cached values | Wait for NEW advertisements (stamp changed) |
| **Double-click** | Concurrent training loops | Disable button during training |

### Problem: UKF Blocked by Global Maturity Check

Rooms without their own scanner ("scannerless rooms") can ONLY be detected via UKF fingerprint matching - the min-distance algorithm fails because there's no scanner to report the closest distance.

The original code required `has_mature_profiles` (global RoomProfiles with 30+ samples in 2+ scanner pairs) before allowing UKF:

```python
# OLD - UKF blocked until entire house has mature profiles
if has_mature_profiles and self._refresh_area_by_ukf(device):
    continue
```

**Problem Timeline:**
```
Day 1: User trains scannerless room with button → AreaProfile created
Day 1-14: UKF blocked because global RoomProfiles not mature
Day 14+: Finally works when enough global data accumulated
```

### Solution: Per-Device Profile Check

```
┌─────────────────────────────────────────────────────────────────────┐
│            UKF Enablement Decision                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────┐     ┌─────────────────────────────┐        │
│  │ has_mature_profiles │ OR  │ device_has_correlations     │        │
│  │ (global RoomProfiles│     │ (device-specific AreaProfiles│       │
│  │  with 30+ samples)  │     │  from button training)      │        │
│  └──────────┬──────────┘     └──────────────┬──────────────┘        │
│             │                               │                        │
│             └───────────┬───────────────────┘                        │
│                         │                                            │
│                    Either True?                                      │
│                         │                                            │
│                    Yes ↓      ↓ No                                   │
│             ┌─────────────────────────────┐                          │
│             │ Try UKF  │ Skip to          │                          │
│             │ first    │ min-distance     │                          │
│             └─────────────────────────────┘                          │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**New Timeline:**
```
Day 1: User trains scannerless room with button → AreaProfile created
Day 1: UKF enabled for THIS device (has own correlations)
Day 1: Scannerless room works immediately!
```

**Key Code Change:**
```python
# NEW - Allow UKF if device has its own trained profiles
device_has_correlations = (
    device.address in self.correlations
    and len(self.correlations[device.address]) > 0
)
if (has_mature_profiles or device_has_correlations) and self._refresh_area_by_ukf(device):
    continue
```

### Lesson Learned

### 17. Global vs Per-Entity Feature Gates

When gating advanced features (UKF, ML models, etc.) behind maturity checks, consider:
- **Global gates** block ALL entities until system-wide threshold met
- **Per-entity gates** allow early adopters to benefit immediately

**Bug Pattern**:
```python
# Global gate blocks trained entities
if global_system_ready:
    use_advanced_feature(entity)
```

**Fix Pattern**:
```python
# Per-entity gate allows immediate benefit
if global_system_ready or entity_has_own_data:
    use_advanced_feature(entity)
```

**Rule of Thumb**: If an entity can benefit from an advanced feature using only its OWN data, don't block it waiting for unrelated global data.

## Velocity Reset and Teleport Recovery

### Problem: The "Velocity Trap"

When a device physically moves from one scanner area to another, the calculated velocity can exceed `MAX_VELOCITY` (3 m/s), causing all new readings to be rejected indefinitely. This makes devices unresponsive even with manual training.

```
Timeline of the Velocity Trap:
T0: Device near Scanner A, measured at 12m distance
T1: Device physically moves to Scanner B (1m distance)
T2: New reading arrives: Scanner B reports 1m
T3: Velocity calculated: (12m - 1m) / 0.5s = 22 m/s
T4: 22 m/s > MAX_VELOCITY (3 m/s) → Reading REJECTED
T5: Device stuck at "12m from Scanner A" forever
T6: Even button press doesn't help (velocity history not reset)
```

### Solution: Two-Layer Recovery

```
┌─────────────────────────────────────────────────────────────────────┐
│              Velocity Trap Recovery Mechanisms                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Layer 1: Manual Reset (Immediate)                                   │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │ User presses "Train Room" button                          │       │
│  │         ↓                                                 │       │
│  │ async_train_fingerprint() calls:                         │       │
│  │ device.reset_velocity_history()                          │       │
│  │         ↓                                                 │       │
│  │ All adverts cleared: hist_velocity, hist_distance, etc.  │       │
│  │         ↓                                                 │       │
│  │ Next reading accepted as new baseline                     │       │
│  └──────────────────────────────────────────────────────────┘       │
│                                                                      │
│  Layer 2: Teleport Recovery (Automatic Self-Healing)                │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │ Reading blocked by MAX_VELOCITY                           │       │
│  │         ↓                                                 │       │
│  │ velocity_blocked_count++                                  │       │
│  │         ↓                                                 │       │
│  │ Count >= VELOCITY_TELEPORT_THRESHOLD (10)?               │       │
│  │         ↓ Yes                     ↓ No                    │       │
│  │ Accept reading, reset      Keep blocking, log            │       │
│  │ history (break trap)       (block N/10)                  │       │
│  └──────────────────────────────────────────────────────────┘       │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Files and Methods

| File | Method/Attribute | Purpose |
|------|-----------------|---------|
| `bermuda_device.py` | `reset_velocity_history()` | Clears velocity history on all adverts |
| `coordinator.py` | `async_train_fingerprint()` | Calls reset on manual training |
| `bermuda_advert.py` | `velocity_blocked_count` | Counter for consecutive blocks |
| `bermuda_advert.py` | `calculate_data()` | Teleport recovery logic |
| `const.py` | `VELOCITY_TELEPORT_THRESHOLD` | Blocks before auto-recovery (10) |

### Key Code

**Manual Reset (bermuda_device.py:746-776):**
```python
def reset_velocity_history(self) -> None:
    """Reset velocity-related history on all adverts for this device."""
    for advert in self.adverts.values():
        advert.hist_velocity.clear()
        advert.hist_distance.clear()
        advert.hist_distance_by_interval.clear()
        advert.hist_stamp.clear()
        advert.rssi_kalman.reset()
        advert.velocity_blocked_count = 0
```

**Teleport Recovery (bermuda_advert.py:569-613):**
```python
if abs(velocity) > max_velocity:
    self.velocity_blocked_count += 1

    if self.velocity_blocked_count >= VELOCITY_TELEPORT_THRESHOLD:
        # Accept the new position and reset history
        self.hist_distance_by_interval.clear()
        self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)
        self.hist_distance.clear()
        self.hist_stamp.clear()
        self.hist_velocity.clear()
        self.velocity_blocked_count = 0
    else:
        # Keep blocking, use previous distance
        self.hist_distance_by_interval.insert(0, self.hist_distance_by_interval[0])
else:
    # Velocity acceptable, reset counter
    self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)
    self.velocity_blocked_count = 0
```

### Design Decisions

1. **Threshold of 10 blocks**: Balances quick recovery (teleport scenario) with protection against BLE noise. Initially set to 5, but increased to 10 because noisy RSSI values caused calculated velocities of 100+ m/s, triggering false teleport detections too frequently. This reset distance history, which broke cross-floor protection (requires history). A higher threshold gives the Kalman filter more time to stabilize while still allowing recovery within ~10 seconds (at 1 update/second).

2. **Reset Kalman filter too**: When velocity history is reset, the Kalman filter state is also reset to avoid stale smoothed values contaminating new readings.

3. **Counter per advert, not per device**: Each scanner-device pair has its own counter. This allows teleport recovery to work independently for each scanner relationship.

4. **Manual reset always works**: The button press calls `reset_velocity_history()` directly, bypassing any automatic checks. User intent overrides system heuristics.

### Lesson Learned

### 19. Velocity Guards Need Escape Mechanisms

Velocity-based outlier rejection is essential for filtering noise, but can become a trap when devices legitimately relocate. Always provide both manual override and automatic self-healing.

**Bug Pattern**:
```python
# BAD - No escape from velocity trap
if abs(velocity) > MAX_VELOCITY:
    reject_reading()  # Forever stuck if device teleported
```

**Fix Pattern**:
```python
# GOOD - Two-layer recovery
if abs(velocity) > MAX_VELOCITY:
    self.blocked_count += 1
    if self.blocked_count >= THRESHOLD:
        accept_reading_and_reset()  # Self-healing
    else:
        reject_reading()
else:
    self.blocked_count = 0  # Reset on normal readings

# Plus: Manual reset on user training
def on_manual_training():
    device.reset_velocity_history()  # User intent overrides
```

**Rule of Thumb**: Any guard that can permanently block valid data needs an escape hatch. For velocity guards: count consecutive blocks and accept after N consistent readings, plus always allow manual override.

**Tuning Note**: The threshold N requires tuning. Initially set to 5, it was increased to 10 because BLE noise caused false teleport detections that broke cross-floor protection (see PR #94).

---

## Documentation Standards (Meta)

This section documents HOW to document - enabling continuous improvement of the codebase knowledge.

### Lessons Learned Format

Each lesson should follow this structure:

```markdown
### N. [Concise Title in Imperative Form]

[1-2 sentence problem description]

**Bug Pattern**:
```python
# BAD - brief comment explaining why
problematic_code_example()
```

**Fix Pattern**:
```python
# GOOD - brief comment explaining the fix
corrected_code_example()
```

**Rule of Thumb**: [One memorable sentence that developers can recall when facing similar situations]
```

**Why this format works:**
- **Numbered**: Easy to reference ("See Lesson #14")
- **Imperative title**: Actionable guidance ("Check Both X and Y" not "X and Y Should Be Checked")
- **Bug/Fix patterns**: Concrete code, not abstract advice
- **Rule of Thumb**: Memorable heuristic for quick decisions

### Architecture Documentation Format

For significant subsystems, document:

```markdown
## [System Name] Architecture

### Problem: [What triggered this design]

[Concrete example showing the failure mode]

### Solution: [High-level approach]

```
┌─────────────────────────────────────┐
│  ASCII diagram showing data flow    │
│  or decision tree                   │
└─────────────────────────────────────┘
```

**Key Code:**
```python
# The essential implementation
core_logic_snippet()
```

### Key Design Decisions

1. **[Decision]**: [Rationale]
2. **[Decision]**: [Rationale]
```

**Why this format works:**
- **Problem-first**: Context before solution
- **ASCII diagrams**: Visible in any editor, no external tools needed
- **Code snippets**: Ground truth, not paraphrases
- **Design decisions**: Explain non-obvious choices

### When to Document

| Trigger | Action |
|---------|--------|
| Bug fix with non-obvious cause | Add Lesson Learned |
| New subsystem or algorithm | Add Architecture section |
| Changed behavior that breaks tests | Update relevant sections |
| Repeated question/confusion | Add to FAQ or clarify existing docs |

### Continuous Improvement Process

```
┌─────────────────────────────────────────────────────────────────────┐
│                  Documentation Lifecycle                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Bug/Feature ──→ Implement ──→ Document ──→ Review ──→ Refine       │
│       │              │             │           │           │         │
│       │              │             │           │           │         │
│       │              ▼             ▼           ▼           ▼         │
│       │         Code +        CLAUDE.md    Tests pass?   Merge      │
│       │         Tests         updated      Docs clear?              │
│       │                                                              │
│       └──────────────────────────────────────────────────────────────┤
│                                                                      │
│  IMPORTANT: Documentation is part of the PR, not an afterthought!   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Anti-patterns to avoid:**
- ❌ "I'll document this later" → You won't
- ❌ Documenting WHAT without WHY → Useless for future readers
- ❌ Outdated docs → Worse than no docs (misleading)
- ❌ Prose-only without code examples → Hard to apply

**Patterns to follow:**
- ✅ Document immediately after fixing → Context fresh in mind
- ✅ Include failing scenario → Shows WHEN the lesson applies
- ✅ Update tests AND docs together → Both reflect current behavior
- ✅ Link to commits → Traceable history

### Lesson Learned

### 18. Document the "Why" Immediately, Not Later

Documentation written during implementation captures context that's lost within hours. The "why" behind a decision is obvious to you NOW but will be a mystery in 6 months.

**Bug Pattern**:
```python
# Fix applied (what)
if variance < 5.0:
    variance = 15.0
# No documentation of WHY 5.0 and 15.0 were chosen
```

**Fix Pattern**:
```python
# FIX: Inflate auto-filter variance when converged to allow button training
# to take precedence. Threshold 5.0 = converged state (~20 samples).
# Target 15.0 = initial/unconverged state, giving button ~73% weight.
if variance < 5.0:  # Converged threshold
    variance = 15.0  # Reset to unconverged
# Documented in CLAUDE.md: "Button Training vs Auto-Learning Architecture"
```

**Rule of Thumb**: If you had to think about WHY, write it down. If you didn't have to think, it's probably obvious enough to skip.

### 20. Borrowed Attributes Need Context-Aware Resolution

When a "virtual" or "scannerless" entity borrows attributes from another entity (e.g., a scanner from a different room), lookups on those attributes return values from the WRONG context.

**Bug Pattern**:
```python
# BAD - For scannerless rooms, scanner_device is borrowed from a different room/floor!
# This returns the floor of the SCANNER, not the floor of the AREA we're actually in.
current_floor_id = advert.scanner_device.floor_id

# Only fallback to area registry if scanner has no floor - but it DOES have a floor!
if current_floor_id is None:
    current_floor_id = area_registry.get_floor(area_id)  # Never reached
```

**Fix Pattern**:
```python
# GOOD - For scannerless rooms, ALWAYS resolve from the authoritative source (area registry)
if is_scannerless_area or protect_scannerless_area:
    current_floor_id = area_registry.get_floor(area_id)  # Our actual area
else:
    current_floor_id = advert.scanner_device.floor_id  # Scanner's area
```

**Rule of Thumb**: When an entity borrows components from another context, any attribute lookup on those components returns values from the WRONG context. Always resolve such attributes from the authoritative source (e.g., registry) rather than the borrowed component.

### 21. Ephemeral vs Stable State: Always Use Confirmed State for Logic

When tracking device location, two types of state exist:
- **Ephemeral state**: `device.area_advert` - the LAST received packet (from ANY scanner)
- **Stable state**: `device.area_id` - the CONFIRMED current location (system state)

Using ephemeral state for location logic causes scannerless rooms to flicker because packets from other rooms constantly overwrite the "current area" reference.

**Bug Pattern (FEHLER 1 - UKF Stickiness)**:
```python
# BAD - area_advert points to wherever last packet came from!
# Device in "Virtual Room", packet from "Hallway" scanner arrives
# → current_area_id becomes "Hallway" → stickiness bonus to WRONG room!
current_area_id = getattr(device.area_advert, "area_id", None)
```

**Bug Pattern (FEHLER 2 - Floor Guard)**:
```python
# BAD - scanner floor != device floor for scannerless rooms!
# Device in "Virtual Room" (OG), heard by "Kitchen" scanner (EG)
# → inc_floor_id = EG → cross-floor protection doesn't trigger!
inc_floor_id = getattr(incumbent_scanner, "floor_id", None)
```

**Bug Pattern (FEHLER 3 - Aggressive Fallback)**:
```python
# BAD - Low UKF score triggers immediate min-distance fallback
# Min-distance can't detect scannerless rooms → device jumps to scanner room!
if match_score < UKF_MIN_MATCH_SCORE:
    return False  # Fallback to min-distance
```

**Fix Pattern**:
```python
# GOOD (FEHLER 1) - Use confirmed state, not last packet
current_area_id = device.area_id

# GOOD (FEHLER 2) - Resolve floor from authoritative source
if device.area_id is not None:
    inc_floor_id = self._resolve_floor_id_for_area(device.area_id)
else:
    inc_floor_id = getattr(incumbent_scanner, "floor_id", None)  # Fallback only

# GOOD (FEHLER 3) - Lower threshold for RETAINING current area
is_retention = best_area_id == current_area_id and current_area_id is not None
effective_threshold = UKF_RETENTION_THRESHOLD if is_retention else UKF_MIN_MATCH_SCORE
if effective_match_score < effective_threshold:
    if is_retention:
        # Keep current area to prevent min-distance fallback
        return True
    return False
```

**Key Constants Added**:
| Constant | Value | Purpose |
|----------|-------|---------|
| `UKF_RETENTION_THRESHOLD` | 0.15 | Lower threshold when keeping current area (vs 0.3 for switching) |

**Rule of Thumb**: For any logic that determines "where is the device NOW", always use `device.area_id` (confirmed state), never `device.area_advert.area_id` (ephemeral state from last packet). The difference is crucial for scannerless rooms where packets arrive from scanners in OTHER rooms.

### 22. Parallel Implementations Must Stay in Sync

When two classes serve similar purposes with the same interface pattern, bug fixes in one must be applied to the other. Missing this creates subtle behavioral differences.

**Bug Pattern**:
```python
# ScannerPairCorrelation.update_button() has variance inflation ✅
# ScannerAbsoluteRssi.update_button() is missing it ❌
# Result: Button training works for pair correlations but not absolute RSSI!
```

**Fix Pattern**:
```python
# BOTH classes need the same fix:
# ScannerPairCorrelation.update_button():
if self._kalman_auto.variance < 5.0:
    self._kalman_auto.variance = 15.0

# ScannerAbsoluteRssi.update_button():  # MUST MATCH!
if self._kalman_auto.variance < 5.0:
    self._kalman_auto.variance = 15.0
```

**Checklist for parallel implementations:**
1. Identify all classes with similar interface/purpose (e.g., `Scanner*` correlation classes)
2. When fixing a bug, grep for similar patterns in sibling classes
3. Consider extracting shared logic to a base class or mixin
4. Add tests that cover BOTH implementations

**Rule of Thumb**: When you fix a bug in ClassA.method(), ask: "Does ClassB have the same method? Does it need this fix too?"

### 23. Soft State Transitions Need Protection Too

When protecting state transitions (like room switches), don't forget "soft" states where the incumbent has partially failed but still holds the position. These often lack the same protections as normal state changes.

**Bug Pattern**:
```python
# Cross-floor has protection, same-floor doesn't!
if cross_floor:
    if challenger_history < MIN_HISTORY:
        continue  # Protected
else:
    pass  # No protection - challenger wins immediately!
tests.reason = "WIN - soft incumbent failed"
```

**Fix Pattern**:
```python
if cross_floor:
    if challenger_history < CROSS_FLOOR_MIN_HISTORY:
        continue
else:
    # Same-floor ALSO needs protection against opportunistic challengers
    if incumbent_was_within_range:
        if not (has_significant_advantage or has_minimum_history):
            continue  # Require some evidence before switching

tests.reason = "WIN - soft incumbent failed"
```

**Rule of Thumb**: When an entity is in a "soft" state (partially failed but still valid), challengers should still need to prove themselves. Don't let "soft" become an easy bypass for protections.

### 24. Distinguish Noise from Legitimate Outliers

When filtering outliers, distinguish between "impossible values" (measurement errors) and "unlikely values" (real but unexpected). They should be handled differently.

**Bug Pattern**:
```python
# Treats all high values the same way
if velocity > MAX_VELOCITY:
    blocked_count += 1  # Even 100 m/s spikes count toward recovery!
    if blocked_count >= THRESHOLD:
        accept_and_reset()  # Noise triggers false recovery
```

**Fix Pattern**:
```python
IMPOSSIBLE_THRESHOLD = 10.0  # m/s - physically impossible, pure noise

if velocity > IMPOSSIBLE_THRESHOLD:
    # NOISE: Completely ignore, don't count toward anything
    use_previous_value()
elif velocity > MAX_VELOCITY:
    # UNLIKELY BUT POSSIBLE: Count toward recovery (teleport scenario)
    blocked_count += 1
    if blocked_count >= THRESHOLD:
        accept_and_reset()
else:
    # NORMAL: Accept and reset counter
    accept_value()
    blocked_count = 0
```

**Rule of Thumb**: Classify outliers into tiers. Pure noise (physically impossible) should be ignored entirely. Unlikely-but-possible values can count toward state changes after sustained repetition.

### 25. Deterministic Systems Need Explicit Undo Mechanisms

When replacing probabilistic/self-healing behavior with deterministic/user-controlled behavior, you MUST provide an explicit undo mechanism. Otherwise, user errors become permanent.

**The "Ghost Scanner" Problem:**
```
Before (Probabilistic - Fusion):
  User trains wrong room → Auto-learning eventually corrects it
  Self-healing, but user corrections also get overwritten

After (Deterministic - Hierarchical Priority):
  User trains wrong room → Stays wrong FOREVER
  User calibration persists, but errors persist too!
```

**Bug Pattern**:
```python
# BAD - Deterministic system without undo
def train_room(self, room_id):
    self._frozen_calibration = room_id  # Permanent!
    # No way to undo if user made a mistake
```

**Fix Pattern**:
```python
# GOOD - Deterministic system WITH undo
def train_room(self, room_id):
    self._frozen_calibration = room_id

def reset_training(self):
    """Undo mechanism - clears frozen state, falls back to auto."""
    self._frozen_calibration = None  # Reverts to auto-learning
```

**Why Device-Level Reset (not Room-Level)?**
- "Ghost Scanner" problem often involves rooms that are no longer visible
- User may not know WHICH room has the incorrect training
- Device-level reset is the "nuclear option" that catches all cases

**Rule of Thumb**: When switching from self-healing to user-controlled behavior, always ask: "What happens if the user makes a mistake?" If the answer is "it stays broken forever", you need an undo mechanism.

### 26. Clamped Fusion Balances User Authority with Intelligent Adaptation

When combining user input with automatic learning, the extremes are:
- **Pure override**: User always wins, auto is ignored → No adaptation to real changes
- **Pure fusion**: Weights based on confidence → Auto eventually overwhelms user

**The Middle Path - Clamped Fusion:**
```python
MAX_AUTO_RATIO = 0.30  # Auto influence capped at 30%

# Calculate weights, then clamp
if auto_weight / total_weight > MAX_AUTO_RATIO:
    auto_weight = btn_weight * (0.30 / 0.70)
```

**Benefits:**
1. User retains majority control (≥70%)
2. Auto can "polish" the anchor (adapt to small changes)
3. Long-term drift is mathematically impossible
4. Seasonal/environmental changes are partially accommodated

**When to use Clamped Fusion:**
- User sets a baseline that should be respected
- System should adapt intelligently within limits
- Changes should be gradual, not abrupt

**Rule of Thumb**: When user and auto-learning conflict, ask: "Should auto be able to completely override the user over time?" If no, clamp the auto influence to a maximum percentage (30% is a good default).

### 27. Hyper-Precision Paradox: Variance Dual-Use Trap

When a single variance value serves TWO purposes (weighting AND matching), optimizing for one can destroy the other.

**The Bug (Scannerless Room Detection):**
```
User trains "Keller" (cellar) at -80dB with variance=0.1 (σ≈0.3dB)
Reality: Signal fluctuates to -82dB (normal BLE noise)
Deviation: 2dB / 0.3dB = 6.7 sigma
Result: Z-score matching says "impossible!" → Room REJECTED as measurement error
```

**Why It Happens:**

| Purpose | Ideal Variance | Why |
|---------|---------------|-----|
| Fusion weighting | LOW (0.1) | Maximizes button's weight vs auto |
| Z-score matching | HIGH (2.0+) | Accepts realistic BLE fluctuations |

When both use the SAME variance, you can't optimize for both.

**The Solution:**

With Clamped Fusion, we don't need artificially low variance to "win" the weighting battle - the explicit 30% cap handles that. So we can use a physically realistic variance:

```python
# BEFORE (broken): variance=0.1 → 2dB = 6.7 sigma → REJECTED
# AFTER (fixed):   variance=2.0 → 2dB = 1.4 sigma → ACCEPTED

def update_button(self, rssi: float) -> float:
    self._kalman_button.reset_to_value(
        value=rssi,
        variance=2.0,       # σ≈1.4dB - realistic for BLE
        sample_count=500,
    )
```

**Why variance=2.0 (σ≈1.4dB)?**
- BLE signals typically fluctuate 2-5dB
- 2dB deviation / 1.4dB σ ≈ 1.4 sigma (acceptable)
- 5dB deviation / 1.4dB σ ≈ 3.5 sigma (borderline but reasonable)
- Still MUCH lower than auto variance (~16), so button dominates fusion

**Bug Pattern:**
```python
# BAD - Optimizing for weighting destroys matching
variance = 0.01  # "Maximum confidence!"
# But z_score(observed) becomes absurdly high for normal data
```

**Fix Pattern:**
```python
# GOOD - Separate concerns or use realistic shared value
variance = 2.0  # Realistic for BLE, still << auto variance
# Clamped Fusion handles weighting dominance explicitly
```

**Rule of Thumb**: When a parameter serves multiple purposes, verify it works for ALL of them. If optimizing for one breaks another, either separate the concerns or find a balanced middle value that works for both.

### 28. Temporary Object Mutation Requires Full Restoration

When temporarily modifying an object's attributes for a specific operation, ALL modified attributes must be restored afterward. Partial restoration leaves the object in a "dirty" state that can corrupt subsequent operations.

**Bug Pattern:**
```python
# BAD - Partial restoration leaves object tainted
saved_scanner = advert.scanner_device
advert.scanner_device = None
advert.area_id = new_area_id       # Modified but not saved!
advert.area_name = new_area_name   # Modified but not saved!

do_operation(advert)

advert.scanner_device = saved_scanner  # Only partial restore!
# area_id and area_name still have wrong values!
```

**Fix Pattern:**
```python
# GOOD - Save ALL modified attributes, use try/finally
saved_scanner = advert.scanner_device
saved_area_id = advert.area_id
saved_area_name = advert.area_name

try:
    advert.scanner_device = None
    advert.area_id = new_area_id
    advert.area_name = new_area_name
    do_operation(advert)
finally:
    # Restore ALL modified attributes
    advert.scanner_device = saved_scanner
    advert.area_id = saved_area_id
    advert.area_name = saved_area_name
```

**Rule of Thumb**: For every attribute you modify temporarily, you must save and restore it. Use try/finally to guarantee restoration even if the operation raises an exception.

### 29. Prune Operations Must Clean ALL Related Data Structures

When removing an entity from a primary data structure, all secondary data structures that reference that entity must also be cleaned. Otherwise, orphaned entries accumulate as memory leaks.

**Bug Pattern:**
```python
# BAD - Only cleans primary structure
for address in prune_list:
    del self.devices[address]
    # self.device_ukfs[address] is now orphaned!
    # self.correlations[address] may also be orphaned!
```

**Fix Pattern:**
```python
# GOOD - Clean ALL related structures
for address in prune_list:
    del self.devices[address]
    self.device_ukfs.pop(address, None)    # Clean secondary structure
    self.correlations.pop(address, None)   # Clean tertiary structure if applicable
```

**Checklist for prune/delete operations:**
1. List all data structures that store per-entity data
2. Ensure ALL are cleaned when an entity is removed
3. Use `.pop(key, None)` to safely handle missing keys

**Rule of Thumb**: Search your codebase for all uses of the entity key. Each dict/list that stores per-entity data needs cleanup in the prune operation.

### 30. Guards Prevent Changes, Overrides Apply Them

When implementing a "lock" mechanism (user wants to pin a device to a specific state), distinguish between:
- **Guard**: Prevents automatic changes (blocks other logic)
- **Override**: Actively applies the desired state

A guard-only approach leaves the system in an inconsistent state where the user's intent is "locked" but not "applied".

**Bug Pattern:**
```python
# BAD - Guard only: prevents changes but doesn't apply them
if device.locked_area_id is not None:
    return  # Blocks automatic detection, but area_id is still wrong!
```

**Fix Pattern:**
```python
# GOOD - Guard + Override: prevents changes AND applies desired state
if device.locked_area_id is not None:
    device.update_area_and_floor(device.locked_area_id)  # ACTIVE OVERRIDE
    return  # Then block automatic detection
```

**Rule of Thumb**: When a user explicitly sets a desired state (via UI selection, button press, API call), APPLY that state immediately before blocking automatic changes. The lock should enforce the user's intent, not just freeze the old state.

### 31. Maturity Thresholds Must Respect User Intent

When using sample-count-based thresholds to determine data "maturity" or "trustworthiness", ensure that USER-PROVIDED data bypasses or satisfies these thresholds, even if it has fewer samples than auto-collected data.

**Bug Pattern:**
```python
# BAD - User training blocked by sample count threshold
MIN_SAMPLES_FOR_MATURITY = 20

def is_mature(self):
    return self.sample_count >= MIN_SAMPLES_FOR_MATURITY  # Button training has 10 samples → never mature!

def match_profiles(self):
    if profile.is_mature:  # User-trained profile skipped!
        use_profile()
```

**Fix Pattern:**
```python
# GOOD - User intent trumps sample count
@property
def has_button_training(self):
    return self._kalman_button.is_initialized

@property
def is_mature(self):
    # User training = explicit intent, trust it regardless of sample count
    if self.has_button_training:
        return True
    # Standard threshold for auto-learning
    return self.sample_count >= MIN_SAMPLES_FOR_MATURITY
```

**Rule of Thumb**: Auto-learning noise needs statistical validation (sample thresholds). User actions represent deliberate intent and should be trusted immediately, even with minimal samples.

### 32. Physical Proximity Overrides Heuristic Methods

When a strong physical indicator exists (very close proximity to a sensor), it should override heuristic/statistical methods that may have accumulated errors.

**Bug Pattern:**
```python
# BAD - Fingerprint matching ignores physical reality
best_room = fingerprint_match(current_readings)  # Based on learned patterns
return best_room  # Device at 1.6m from kitchen sensor → returns "Bedroom" (2 floors up)!
```

**Fix Pattern:**
```python
# GOOD - Physical proximity as sanity check
best_room = fingerprint_match(current_readings)

# Sanity check: is there a sensor VERY close?
if nearest_sensor_distance < 2.0:  # meters
    if nearest_sensor_room != best_room:
        if is_cross_floor:
            return None  # Fall back to distance-based method
        if match_confidence < 0.85:
            return None  # Low confidence + close sensor → trust proximity
return best_room
```

**Key Insight**: Statistical/heuristic methods accumulate errors over time (bad training, environmental changes). Physical proximity (< 2m) is almost certain proof of location. Use proximity as a "reality check" for heuristic decisions.

**Rule of Thumb**: When heuristic methods contradict strong physical evidence, trust the physics. Especially for cross-floor decisions where heuristic errors are most damaging.

### 33. Converged Estimators Need Variance Floors for Matching

When comparing two well-trained statistical estimators (e.g., UKF state vs profile mean),
both may have very low variance after convergence. This creates a "hyper-precision paradox"
where normal measurement noise causes statistical rejection.

**Bug Pattern:**
```python
# BAD - Converged variances make normal noise look like outliers
combined_cov = ukf_variance + profile_variance  # Both ≈ 2.5 after training
# 3dB deviation: z = 3 / sqrt(5) = 1.3 sigma → looks borderline
# But with many scanners: D² = n * z² = 3 * 1.7 = 5.1 → Score = 0.28 (rejected!)
```

**Fix Pattern:**
```python
# GOOD - Apply variance floor to ensure realistic tolerance
MIN_MATCHING_VARIANCE = 25.0  # sigma = 5 dB (typical BLE noise)

combined_cov = ukf_variance + profile_variance
combined_cov = max(combined_cov, MIN_MATCHING_VARIANCE)  # Floor, not addition!
# 3dB deviation: z = 3 / sqrt(25) = 0.6 sigma → D² = 1.1 → Score = 0.76 (accepted!)
```

**Key Insight**: Kalman filter variance measures estimation error, not measurement noise.
After convergence, both approach zero, but the underlying signal still fluctuates.
The floor represents this irreducible physical noise.

**Rule of Thumb**: When comparing two estimators that can both converge to low variance,
apply a variance floor representing the physical measurement noise of the underlying signal.

### 34. Invisible Entities Need Synthetic Competition Metrics

When an algorithm (like min-distance) can only "see" entities with a specific property (like physical scanners), entities without that property become invisible and can never win—even when other metrics (like fingerprint matching) strongly suggest they should.

**Bug Pattern:**
```python
# BAD - Scannerless rooms are invisible to min-distance
distances = {}
for scanner in physical_scanners:
    distances[scanner.area_id] = scanner.distance
# Scannerless rooms never appear in distances → can never win!
winner = min(distances, key=distances.get)
```

**Fix Pattern:**
```python
# GOOD - Create synthetic metric for invisible entities
distances = {}
for scanner in physical_scanners:
    distances[scanner.area_id] = scanner.distance

# Add virtual distances for scannerless rooms
for area_id, profile in button_trained_profiles.items():
    if not has_scanner(area_id):
        # Convert fingerprint score to virtual distance
        score = get_fingerprint_score(area_id)
        distances[area_id] = score_to_virtual_distance(score)

winner = min(distances, key=distances.get)  # Now scannerless rooms can compete!
```

**Key Design Decisions for Synthetic Metrics:**
1. **Only for user-intent entities**: Don't create virtual metrics for auto-learned data
2. **Scale appropriately**: Ensure synthetic values are in the same range as real values
3. **Prefer quadratic over linear**: Rewards good matches more aggressively
4. **Add minimum threshold**: Prevent phantom matches from very weak signals

**Rule of Thumb**: When an entity can't participate in a competition due to missing properties, ask: "Is there another metric that could represent this entity's 'fitness'?" If yes, convert that metric to a compatible scale and include it in the competition.

### 35. Test Fixtures Must Mirror Production Data Structures

When production code adds new data structures (like `device_ukfs`), ALL test fixtures that mock the coordinator must be updated to include these structures—even if the specific tests don't directly use them.

**Bug Pattern:**
```python
# Test fixture created before device_ukfs existed
def make_coordinator():
    coord = Coordinator.__new__(Coordinator)
    coord.devices = {}
    coord.correlations = {}
    # device_ukfs not added!
    return coord

# Production code later added:
def new_feature(self):
    if device.address not in self.device_ukfs:  # AttributeError!
        ...
```

**Fix Pattern:**
```python
# GOOD - Include all production attributes
def make_coordinator():
    coord = Coordinator.__new__(Coordinator)
    coord.devices = {}
    coord.correlations = {}
    coord.device_ukfs = {}  # Added when production added it
    coord._scanners = set()  # Include related structures
    return coord
```

**Checklist when adding new coordinator attributes:**
1. Add to `coordinator.__init__()` or initialization
2. Search for `_make_coordinator`, `_build_coord`, `@pytest.fixture` in tests
3. Add the new attribute to ALL coordinator fixtures
4. Run full test suite to verify no AttributeError

**Rule of Thumb**: Production coordinator attributes and test fixture attributes must stay in sync. When you add an attribute to production, grep for test fixtures and update them too.

### 36. Shared Resources Must Be Created at Point of Use, Not Just in Primary Path

When multiple code paths need a shared resource (like UKF state), the resource must be created at the point of use, not only in one "primary" path. Otherwise, fallback paths fail when the primary path exits early.

**Bug Pattern:**
```python
# BAD - Resource only created in primary path
def primary_path(device):
    if early_exit_condition:
        return False  # UKF NEVER created!

    if device not in self.shared_resource:
        self.shared_resource[device] = Resource()  # Only created here

    # ... use resource ...
    return True

def fallback_path(device):
    if device not in self.shared_resource:
        return {}  # Silently fails! Resource doesn't exist
    # ... try to use resource ...
```

**Fix Pattern:**
```python
# GOOD - Resource created at point of use in ALL paths
def primary_path(device):
    if early_exit_condition:
        return False  # OK to exit, fallback_path will handle it

    if device not in self.shared_resource:
        self.shared_resource[device] = Resource()
    # ... use resource ...

def fallback_path(device):
    # Create resource here too if needed
    if device not in self.shared_resource:
        self.shared_resource[device] = Resource()

    resource = self.shared_resource[device]
    resource.update(current_data)  # Initialize with current data
    # ... use resource ...
```

**Rule of Thumb**: If Path A and Path B both need resource R, and Path B is a fallback when Path A fails, then Path B MUST be able to create R independently. Don't assume the "happy path" always runs first.

### 37. Use Canonical Keys for Shared Dictionaries

When multiple code paths access a shared dictionary (like `self.correlations`), all paths MUST use the same key format. Using different key formats (e.g., normalized vs raw address) causes data to be stored under one key but looked up under another.

**Bug Pattern:**
```python
# BAD - Training uses raw parameter, lookup uses normalized attribute
async def train(self, device_address: str):  # Raw parameter, might be uppercase
    device = self.get_device(device_address)  # device.address is normalized
    self.data[device_address] = {...}  # Stores under raw key!

def lookup(self, device):
    return self.data.get(device.address, {})  # Looks up with normalized key!
    # If device_address was uppercase, this returns empty dict!
```

**Fix Pattern:**
```python
# GOOD - Both use normalized key from canonical source
async def train(self, device_address: str):
    device = self.get_device(device_address)
    normalized_key = device.address  # Always use the canonical normalized form
    self.data[normalized_key] = {...}

def lookup(self, device):
    return self.data.get(device.address, {})  # Same normalized key
```

**Rule of Thumb**: For dictionaries keyed by identifiers (addresses, IDs), always normalize the key at the point of access. Use a canonical source (`device.address`) rather than raw parameters. This prevents subtle key mismatches that cause "phantom data loss."

### 38. Async Operations Need Re-Entry Guards

Long-running async operations (like training loops) can be triggered multiple times if the UI allows it. Without re-entry guards, concurrent executions cause race conditions and data corruption.

**Bug Pattern:**
```python
# BAD - No guard against double-click
async def async_press(self) -> None:
    self._is_running = True  # Set flag, but never checked!
    try:
        for i in range(20):
            await do_work()  # Takes 60+ seconds total
            await asyncio.sleep(0.5)
    finally:
        self._is_running = False
```

**Fix Pattern:**
```python
# GOOD - Two-layer protection
@property
def available(self) -> bool:
    if self._is_running:
        return False  # Button disabled in UI
    return super().available

async def async_press(self) -> None:
    if self._is_running:  # Safety guard
        return
    self._is_running = True
    try:
        # ... long operation ...
    finally:
        self._is_running = False
```

**Why Two Layers?**
1. **`available` property**: Disables button in UI → clear visual feedback
2. **Guard in method**: Catches edge cases where UI might still send events

**Rule of Thumb**: Any async operation that takes more than a few seconds should have both UI-level disabling AND method-level re-entry guards.

### 39. Polling Must Wait for Real Data Changes, Not Just Time

When collecting multiple samples from a data source, polling at fixed intervals can re-read the same cached value multiple times. This causes artificial confidence from duplicate data.

**Bug Pattern:**
```python
# BAD - Polls faster than data source updates
SAMPLE_COUNT = 20
SAMPLE_DELAY = 0.5  # seconds

for i in range(SAMPLE_COUNT):
    rssi = get_cached_rssi()  # Same value returned 5-10 times!
    kalman.update(rssi)  # Each counted as "new" measurement
    await asyncio.sleep(SAMPLE_DELAY)

# Result: 20 "samples" but only 3 unique values
# Kalman filter has artificial confidence from duplicates
```

**Fix Pattern:**
```python
# GOOD - Wait for actual new data
last_stamps: dict[str, float] = {}

while successful_samples < SAMPLE_COUNT:
    if elapsed > MAX_TIMEOUT:
        break

    current_stamps = get_current_timestamps()
    has_new_data = any(
        current_stamps.get(k, 0) > last_stamps.get(k, 0)
        for k in current_stamps
    )

    if has_new_data:
        rssi = get_cached_rssi()
        kalman.update(rssi)  # Only real new measurements
        successful_samples += 1
        last_stamps = current_stamps

    await asyncio.sleep(POLL_INTERVAL)  # Short poll, but only count new data
```

**Key Insight**: The data source (BLE advertisements) has its own update rate (1-10 seconds). Polling faster than this rate just re-reads cached values. Track timestamps to detect actual changes.

**Rule of Thumb**: When collecting samples from a cached data source, track the data's timestamp (not just "is it fresh enough?") and only count samples when the timestamp actually changes.

### 40. Feature Parity: Parallel Code Paths Must Produce Consistent Results

When two code paths (e.g., UKF path and min-distance path) can produce the same type of output (e.g., area selection), they must handle all edge cases consistently. Otherwise, users see different behavior depending on which path runs.

**Bug Pattern (BUG 18):**
```python
# Min-distance path: calculates virtual distance for scannerless rooms ✓
if scannerless_room:
    device.area_distance = calculate_virtual_distance(score)

# UKF path: forgets to handle scannerless rooms ✗
if scannerless_room:
    device.area_distance = None  # Shows "Unknown" in UI!
```

**Fix Pattern:**
```python
# BOTH paths handle scannerless rooms the same way
def _apply_area_selection(self, ..., scannerless_room: bool, match_score: float):
    if scannerless_room:
        # Same formula in both paths
        device.area_distance = max_radius * SCALE * ((1.0 - match_score) ** 2)
```

**Checklist for Parallel Code Paths:**
1. List all outputs/side effects of Path A
2. Verify Path B produces the same outputs
3. Check edge cases (None values, empty collections, boundary conditions)
4. Consider extracting shared logic to a helper function

**Rule of Thumb**: When you fix a bug in one code path, ask: "Is there a parallel path that handles the same scenario? Does it need the same fix?"

---

## Architectural Notes (Thread Safety & Numerical Stability)

These notes document intentional design decisions that may appear problematic but are actually correct.

### Note 1: Clamped Fusion Division is Safe (Not a Bug)

The Clamped Fusion calculation in `scanner_absolute.py:180` appears to risk division by zero:

```python
current_auto_ratio = w_auto / (w_btn + w_auto)
```

**Why this is safe:**

Lines 171-172 guarantee positive weights:
```python
var_btn = max(self._kalman_button.variance, 1e-6)
var_auto = max(self._kalman_auto.variance, 1e-6)
```

Since both variances are at least `1e-6`:
- `w_btn = 1/var_btn` is at most `1e6` (and positive)
- `w_auto = 1/var_auto` is at most `1e6` (and positive)
- The sum `w_btn + w_auto` is guaranteed > 0

**No additional guard needed.** The `max(..., 1e-6)` protection is sufficient.

### Note 2: No Race Condition Between area_id and area_advert

Code in `coordinator.py` checks `device.area_id` for logic but uses `device.area_advert` for action:

```python
current_device_area_id = device.area_id  # Check this
# ... logic ...
device.apply_scanner_selection(device.area_advert, ...)  # Use this
```

**Why this is safe:**

Python `asyncio` runs in a **single-threaded event loop**. Between these two lines:
1. There is no `await` statement (no yield point)
2. No other coroutine can execute
3. The state cannot change mid-execution

This is **atomically consistent** - not a race condition. The pattern is intentional:
- `area_id` is the authoritative source of truth (what the system believes)
- `area_advert` is the object needed for the operation (contains scanner reference)

### Note 3: UKF Retention Threshold Hysteresis is Intentional

The two UKF thresholds (0.3 for switching, 0.15 for retention) are not a bug but **intentional hysteresis**:

| Action | Threshold | Purpose |
|--------|-----------|---------|
| Enter new room | 0.3 | Prevent premature switches |
| Stay in current room | 0.15 | Keep device "sticky" |

**Designed Behavior:**
- A device with score 0.25 will NOT switch to a new room (< 0.3)
- A device already in a room with score 0.16 will STAY (> 0.15)
- This prevents flickering for scannerless rooms where signals are naturally weaker

See FAQ Q3 for the complete rationale.

---

## Key Constants (Extended)

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `VELOCITY_NOISE_MULTIPLIER` | 3.0 | `const.py` | Multiplier for dynamic noise threshold (`max_velocity * 3`) |
| `VELOCITY_TELEPORT_THRESHOLD` | 10 | `const.py` | Consecutive blocks before accepting teleport |
| `MAX_AUTO_RATIO` | 0.30 | `scanner_*.py` | Max auto-learning influence in Clamped Fusion |
| `VIRTUAL_DISTANCE_SCALE` | 0.7 | `const.py` | Virtual distance scaling (30% shorter than pure quadratic) |
| `VIRTUAL_DISTANCE_MIN_SCORE` | 0.05 | `const.py` | Minimum UKF score for virtual distance generation |

**Dynamic Noise Threshold Calculation:**
```python
noise_velocity_threshold = max_velocity * VELOCITY_NOISE_MULTIPLIER
# Default (3 m/s): noise > 9 m/s
# Vehicle (20 m/s): noise > 60 m/s
```

---

## Refactoring Notes (PR Review Feedback)

Changes made based on peer review (2026-01-21):

1. **`VELOCITY_NOISE_MULTIPLIER`**: Replaced static `VELOCITY_NOISE_THRESHOLD=10.0` with dynamic calculation. Now adapts to user's `max_velocity` config (e.g., vehicle tracking with higher speeds).

2. **`async_reset_device_training()` Error Handling**: Added try/except around `correlation_store.async_save()`. On failure, logs warning but still returns True (in-memory reset succeeded).

3. **`KalmanFilter.restore_state()`**: New method for clean deserialization. Replaces direct `_initialized` access from external code. Used by `ScannerAbsoluteRssi.from_dict()` and `ScannerPairCorrelation.from_dict()`.
