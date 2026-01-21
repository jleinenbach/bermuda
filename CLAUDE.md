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

**Button Anchor via `reset_to_value()`:**
```python
def update_button(self, rssi: float) -> float:
    # Create high-confidence anchor state
    # IMPORTANT: variance=2.0 (σ≈1.4dB) is physically realistic for BLE
    # Do NOT use variance < 1.0! See "Hyper-Precision Paradox" in Lessons Learned
    self._kalman_button.reset_to_value(
        value=rssi,
        variance=2.0,       # High confidence but realistic (σ≈1.4dB)
        sample_count=500,   # Massive inertia as base
    )
    return self.expected_rssi  # Returns fused value
```

**Example: Controlled Evolution**
```
Day 1: User trains device in "Keller" at -85dB
       → Button (anchor): -85dB, 70-100% weight
       → Auto: Empty

Weeks later: Environment changes slightly
       → Auto drifts to -80dB (environment change detected)
       → Button: Still -85dB (anchor)
       → Auto influence clamped to 30%
       → expected_rssi ≈ 0.7 * (-85) + 0.3 * (-80) = -83.5dB
       → Room detection stays stable, but adapts slightly!
```

**Key Constants:**
| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_AUTO_RATIO` | 0.30 | Auto influence capped at 30% |
| Anchor variance | 2.0 | High confidence (σ≈1.4dB) but realistic for BLE |
| Anchor sample_count | 500 | Massive inertia as base |
| `MIN_VARIANCE` | 0.001 | Prevents division by zero |

**⚠️ IMPORTANT: Hyper-Precision Paradox**

Do NOT set anchor variance < 1.0! Variance serves TWO purposes:
1. **Fusion weighting**: Lower variance = higher weight (OK to be low)
2. **Z-Score matching**: Variance defines acceptable deviation (must be realistic!)

With variance=0.1 (σ≈0.3dB), a normal 2dB BLE fluctuation becomes a "6 sigma event" → room is REJECTED as impossible, even though it's correct!

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
│  │ • async_press(): 10x async_train_fingerprint() in try/finally      ││
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

**Availability Conditions** (`button.py:91-117`):
```python
@property
def available(self) -> bool:
    # Button enabled ONLY when BOTH floor AND room selected
    floor_ok = self._device.training_target_floor_id is not None
    area_ok = self._device.training_target_area_id is not None
    return super().available and floor_ok and area_ok
```

**Press Handler** (`button.py:139-213`):
```python
async def async_press(self) -> None:
    try:
        for i in range(TRAINING_SAMPLE_COUNT):  # 10 samples
            await self.coordinator.async_train_fingerprint(
                device_address=self.address,
                target_area_id=target_area_id,
            )
    finally:
        # ALWAYS cleanup, even on exception
        self._device.training_target_floor_id = None
        self._device.training_target_area_id = None
        self._device.area_locked_id = None
        self._device.area_locked_name = None
        self._device.area_locked_scanner_addr = None
        await self.coordinator.async_request_refresh()
```

### Fingerprint Training Process

**Step 1: Velocity Reset** (`coordinator.py:733-738`)

Breaks the "Velocity Trap" where calculated velocity > MAX_VELOCITY causes all readings to be rejected:
```python
device.reset_velocity_history()
# Clears: hist_velocity, hist_distance, hist_stamp on ALL adverts
# Resets: Kalman filters, velocity_blocked_count
```

**Step 2: RSSI Collection** (`coordinator.py:740-771`)
```python
for advert in device.adverts.values():
    if (advert.rssi is not None
        and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS):
        rssi_readings[advert.scanner_address] = advert.rssi
        # Track strongest signal as "primary"
        if advert.rssi > primary_rssi:
            primary_rssi = advert.rssi
            primary_scanner_addr = advert.scanner_address
```

**Step 3: Profile Updates** (`coordinator.py:787-797`)
```python
# Device-specific profile (AreaProfile)
self.correlations[device_address][target_area_id].update_button(
    primary_rssi=primary_rssi,
    other_readings=other_readings,
    primary_scanner_addr=primary_scanner_addr,
)

# Device-independent profile (RoomProfile)
self.room_profiles[target_area_id].update_button(rssi_readings)
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
| `TRAINING_SAMPLE_COUNT` | 10 | `button.py:22` | Samples per button press |
| `EVIDENCE_WINDOW_SECONDS` | - | `const.py` | Max age for RSSI readings |
| `AREA_LOCK_TIMEOUT_SECONDS` | 60 | `const.py` | Stale threshold for auto-unlock |
| `MIN_SAMPLES_FOR_MATURITY` | 30/20 | `scanner_pair.py`/`scanner_absolute.py` | Samples before trusting profile |
| Converged threshold | 5.0 | inline | Variance below which inflation triggers |
| Inflation target | 15.0 | inline | Reset variance value |

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
