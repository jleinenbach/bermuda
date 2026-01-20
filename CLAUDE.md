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

### Two-Pool Kalman Fusion (Weighted Learning)

The correlation classes (`ScannerPairCorrelation`, `ScannerAbsoluteRssi`) use a dual-filter architecture to balance automatic learning with manual button training:

```
                    ┌─────────────────────────────────────┐
Automatic Learning ─┼─→ _kalman_auto ──┐                  │
                    │                  │ Inverse-Variance │
                    │                  ├─→ Fused Estimate │
Button Training ────┼─→ _kalman_button─┘     Weighting    │
                    └─────────────────────────────────────┘
```

**Why Two Pools?**
- Auto learning adapts to environment changes (furniture, obstacles)
- Button training preserves deliberate user corrections
- Neither overwrites the other - they're fused mathematically

**Inverse-Variance Weighting (Optimal Bayesian Fusion):**
```python
# weight = 1 / variance (lower variance = higher confidence = more weight)
auto_var = max(self._kalman_auto.variance, MIN_VARIANCE)
button_var = max(self._kalman_button.variance, MIN_VARIANCE)

auto_weight = 1.0 / auto_var
button_weight = 1.0 / button_var
total_weight = auto_weight + button_weight

fused_estimate = (auto_estimate * auto_weight + button_estimate * button_weight) / total_weight
fused_variance = 1.0 / total_weight  # Combined uncertainty
```

**Kalman Variance Behavior (per Correlation Object):**

Each `ScannerPairCorrelation` and `ScannerAbsoluteRssi` has its own Kalman filters that converge independently:

| Samples | Variance | Interpretation |
|---------|----------|----------------|
| 1 | 16.0 | High uncertainty (initial) |
| 3 | 5.6 | Still uncertain |
| 10 | 2.8 | Converging |
| 20+ | ~2.6 | Steady state (converged) |

Example structure showing independent sample counts:
```
Area "Wohnzimmer" → AreaProfile:
  ├─ ScannerPairCorrelation (A↔B): auto=25 samples, button=0
  ├─ ScannerPairCorrelation (A↔C): auto=18 samples, button=5
  ├─ ScannerAbsoluteRssi (A): auto=30 samples, button=3
  ├─ ScannerAbsoluteRssi (B): auto=22 samples, button=0
  └─ ScannerAbsoluteRssi (C): auto=15 samples, button=0
```

**Key Constants:**
| Constant | Value | Purpose |
|----------|-------|---------|
| `MIN_VARIANCE` | 0.001 | Prevents division by zero |

**Practical Effect:**
- Converged filter (many samples, low variance) dominates over new filter (few samples, high variance)
- Consistent button training naturally dominates over noisy auto learning
- System self-regulates: quality matters more than quantity

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

### Test Fixture Updates
- Added `correlations`, `_correlations_loaded`, `_last_correlation_save`, `correlation_store` to coordinator mocks
- Added `scanner_address` to FakeAdvert, `address` to FakeDevice
- Added `get_movement_state()` and `area_changed_at` to FakeDevice
- Added `area_locked_id`, `area_locked_name`, `area_locked_scanner_addr` to FakeDevice

### Manual Fingerprint Training Feature
- **Problem**: Auto-detection constantly overwrites manual room corrections
- **Solution**: Select entities for Room/Floor training + Area Lock mechanism
- **Files**: `select.py`, `coordinator.py`, `bermuda_device.py`, `const.py`

**Components:**
1. `BermudaTrainingRoomSelect` - Room dropdown (EntityCategory.CONFIG)
2. `BermudaTrainingFloorSelect` - Floor dropdown (filters rooms by floor)
3. Area Lock - Prevents auto-detection from overriding trained room

**Area Lock Logic:**
```python
# In BermudaDevice:
self.area_locked_id: str | None = None        # Locked area ID
self.area_locked_name: str | None = None      # Locked area name
self.area_locked_scanner_addr: str | None = None  # Scanner that trained it
```

**Auto-Unlock Conditions:**
- Locked scanner no longer sees device (stamp stale > 60s)
- AND device is seen by other scanners (last_seen fresh)
- If device offline everywhere → keep locked

**USB/BlueZ Scanner Fix:**
USB/BlueZ scanners don't update stamp when RSSI is stable. Fixed by requiring device to be seen elsewhere before unlocking:
```python
if nowstamp - locked_advert.stamp > AREA_LOCK_TIMEOUT_SECONDS:
    if nowstamp - device.last_seen < AREA_LOCK_TIMEOUT_SECONDS:
        # Seen elsewhere but not by locked scanner → unlock
    else:
        # Not seen anywhere → keep locked
```

### Training UI System Architecture

The training UI uses a coordinated system of Select entities (dropdowns) and a Button entity:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Training UI Flow                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  FloorSelect ──────────────────┐                                        │
│  (dropdown)                    │                                        │
│       │                        │                                        │
│       │ on_floor_changed()     │                                        │
│       ▼                        │                                        │
│  RoomSelect ───────────────────┼──► BermudaDevice                       │
│  (dropdown)                    │    • training_target_floor_id          │
│                                │    • training_target_area_id           │
│                                │    • area_locked_id/name/scanner_addr  │
│                                │                                        │
│  TrainingButton ◄──────────────┘                                        │
│  (available when both set)                                              │
│       │                                                                  │
│       │ async_press()                                                   │
│       ▼                                                                  │
│  coordinator.async_train_fingerprint()                                  │
│       │                                                                  │
│       │ clears training_target_* fields                                 │
│       ▼                                                                  │
│  coordinator.async_request_refresh()                                    │
│       │                                                                  │
│       │ triggers _handle_coordinator_update() on all entities           │
│       ▼                                                                  │
│  Dropdowns clear (see training_target_* = None)                         │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

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

## Lessons Learned

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

**Implication for inverse-variance weighting**: The weight difference between filters comes from their convergence state, not sample count. A filter with 100 samples has nearly the same variance as one with 1000 samples, but both have much lower variance than a filter with only 3 samples.

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

### Next Steps (Future Work)

1. **Field Testing**: Enable on test installations, compare with min-distance
2. **Tuning**: Adjust `UKF_MIN_MATCH_SCORE` based on real-world data
3. **Diagnostics**: Add UKF state to dump_devices service output
4. **Hybrid Mode**: Combine UKF confidence with min-distance for tiebreaking
5. **Performance**: Profile UKF overhead on large scanner networks

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

### Problem: Inverse-Variance Weighting Favors Quantity

The dual-filter system uses inverse-variance weighting to fuse auto and button estimates:

```python
weight = 1 / variance  # Lower variance = higher weight
```

Kalman filter variance converges quickly (~20 samples to steady state ~2.6). After thousands of auto-samples, the auto-filter has extremely low variance and dominates any button training.

```
Auto:   1000 samples, variance=2.6, weight=0.385
Button: 10 samples,   variance=5.6, weight=0.179
→ Auto gets 68% weight, Button gets 32% weight
→ Button training is nearly ineffective!
```

### Solution: Variance Inflation on Button Training

```
┌─────────────────────────────────────────────────────────────────────┐
│                     update_button() Flow                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Button Press ──→ Check auto variance                                │
│                          │                                           │
│              ┌───────────┴───────────┐                               │
│              │ variance < 5.0?       │                               │
│              │ (converged)           │                               │
│              └───────────┬───────────┘                               │
│                   Yes ↓      ↓ No                                    │
│         ┌─────────────────────────────┐                              │
│         │ Inflate to 15.0 │ Keep as-is│                              │
│         │ (unconverged)   │           │                              │
│         └─────────────────────────────┘                              │
│                          │                                           │
│                          ▼                                           │
│              Update button Kalman filter                             │
│                          │                                           │
│                          ▼                                           │
│              Fuse with inflated auto                                 │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**After Inflation:**
```
Auto:   1000 samples, variance=15.0 (inflated), weight=0.067
Button: 10 samples,   variance=5.6,              weight=0.179
→ Button gets 73% weight, Auto gets 27% weight
→ Button training now dominates!
```

### Key Design Decisions

1. **Threshold 5.0**: Only inflate if auto is converged (variance < 5.0)
2. **Target 15.0**: Reset to approximately initial/unconverged state
3. **One-time inflation**: Once variance >= 5.0, don't inflate again
4. **Auto can recover**: Continued auto-learning will reconverge naturally

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

### 15. Inverse-Variance Weighting Needs Manual Override

When fusing estimates from multiple sources using inverse-variance weighting, the source with lowest variance (highest confidence) dominates. This is mathematically optimal BUT:
- Automatic learning accumulates indefinitely → extremely low variance
- Manual corrections are limited samples → higher variance
- Manual corrections become ineffective!

**Fix Pattern**: When manual input occurs, inflate the automatic source's variance to "forget" some confidence:

```python
def update_manual(self, value):
    # Reset auto confidence when user provides correction
    if self._auto_filter.variance < CONVERGED_THRESHOLD:
        self._auto_filter.variance = INITIAL_VARIANCE
    self._manual_filter.update(value)
```

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
