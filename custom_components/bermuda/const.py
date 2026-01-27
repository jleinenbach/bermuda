"""Constants for Bermuda BLE Trilateration."""

# Base component constants
from __future__ import annotations

import logging
from enum import Enum
from typing import Final

from homeassistant.const import Platform

from .log_spam_less import BermudaLogSpamLess

NAME = "Bermuda BLE Trilateration"
DOMAIN = "bermuda"
DOMAIN_DATA = f"{DOMAIN}_data"
# Inter-Integration constants
DOMAIN_GOOGLEFINDMY = "googlefindmy"
DATA_EID_RESOLVER = "eid_resolver"
# Version gets updated by github workflow during release.
# The version in the repository should always be 0.0.0 to reflect
# that the component has been checked out from git, not pulled from
# an officially built release. HACS will use the git tag (or the zip file,
# either way it works).
VERSION = "0.0.0"

ATTRIBUTION = "Data provided by http://jsonplaceholder.typicode.com/"
ISSUE_URL = "https://github.com/agittins/bermuda/issues"

# Icons
ICON = "mdi:format-quote-close"
ICON_DEFAULT_AREA: Final = "mdi:land-plots-marker"
ICON_DEFAULT_FLOOR: Final = "mdi:selection-marker"  # "mdi:floor-plan"
# Issue/repair translation keys. If you change these you MUST also update the key in the translations/xx.json files.
REPAIR_SCANNER_WITHOUT_AREA = "scanner_without_area"

# Device classes
BINARY_SENSOR_DEVICE_CLASS = "connectivity"

# Platforms
PLATFORMS = [
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    # Platform.SWITCH,
    # Platform.BINARY_SENSOR
]

# Should probably retreive this from the component, but it's in "DOMAIN" *shrug*
DOMAIN_PRIVATE_BLE_DEVICE = "private_ble_device"

# Signal names we are using:
SIGNAL_DEVICE_NEW = f"{DOMAIN}-device-new"
SIGNAL_SCANNERS_CHANGED = f"{DOMAIN}-scanners-changed"

UPDATE_INTERVAL = 1.05  # Seconds between bluetooth data processing cycles
# Note: this is separate from the CONF_UPDATE_INTERVAL which allows the
# user to indicate how often sensors should update. We need to check bluetooth
# stats often to get good responsiveness for beacon approaches and to make
# the smoothing algo's easier. But sensor updates should bear in mind how
# much data it generates for databases and browser traffic.

LOGSPAM_INTERVAL = 22
# Some warnings, like not having an area assigned to a scanner, are important for
# users to see and act on, but we don't want to spam them on every update. This
# value in seconds is how long we wait between emitting a particular error message
# when encountering it - primarily for our update loop.

DISTANCE_TIMEOUT = 30  # seconds to wait before marking a sensor distance measurement
# as unknown/none/stale/away. Separate from device_tracker.
DISTANCE_INFINITE = 999  # arbitrary distance for infinite/unknown rssi range

AREA_MAX_AD_AGE_DEFAULT: Final = 60.0
# Default maximum age for adverts in distance-based selection when adaptive timeout unavailable.
# The actual timeout is adaptive per-advert based on observed advertisement intervals.
# See BermudaAdvert.adaptive_timeout for the per-device adaptive logic (uses MAX of intervals x 2).
AREA_MAX_AD_AGE_LIMIT: Final = 360.0
# Absolute maximum (6 minutes) for adaptive timeout - covers deep sleep scenarios.
# Using 360s instead of 300s provides margin for timing jitter and BLE stack delays.
AREA_MAX_AD_AGE: Final = AREA_MAX_AD_AGE_DEFAULT
# Backward compatibility alias - used by bermuda_device.py for area_is_stale checks.
AREA_RETENTION_SECONDS: Final = 15 * 60
# Keep the last known area/distance/floor for low-advertising trackers for a reasonable
# window, independent of selection freshness.
DISTANCE_RETENTION_SECONDS: Final = AREA_RETENTION_SECONDS
# Distance is retained only when the winning scanner/area remain the same within this window.
# Evidence window for adverts to participate in selection/fallback. Prevents immortal stale adverts.
EVIDENCE_WINDOW_SECONDS: Final = AREA_RETENTION_SECONDS
CROSS_FLOOR_MIN_HISTORY: Final = 8  # Minimum history length before cross-floor wins via historical checks.
SAME_FLOOR_MIN_HISTORY: Final = 3  # Minimum history length before same-floor wins can occur.
SAME_FLOOR_STREAK: Final = 4  # Consecutive wins needed before applying a same-floor switch.
CROSS_FLOOR_STREAK: Final = 6  # Consecutive wins needed before applying a cross-floor switch.

# Incumbent stability margin - challenger must be significantly closer to even compete
# This prevents flickering when distances are nearly equal
INCUMBENT_MARGIN_PERCENT: Final = 0.08  # 8% closer required to challenge incumbent
INCUMBENT_MARGIN_METERS: Final = 0.20  # OR 0.2m closer required (whichever is easier to meet)

# Dwell time based stability - margin increases with time in area
# This makes it harder to switch rooms the longer a device stays stationary
DWELL_TIME_MOVING_SECONDS: Final = 120  # 0-2 min: recently moved, lower threshold
DWELL_TIME_SETTLING_SECONDS: Final = 600  # 2-10 min: settling in, normal threshold

# Area lock auto-unlock - if the locked scanner hasn't seen the device for this long,
# the lock is released and auto-detection resumes (device probably left the room)
AREA_LOCK_TIMEOUT_SECONDS: Final = 60  # 60 seconds without signal from locked scanner

# Scanner activity timeout - used by _area_has_active_scanner() to determine if a scanner
# is actively providing data. This is much shorter than EVIDENCE_WINDOW_SECONDS (15 min)
# because we want to quickly detect when a scanner goes offline (proxy reboot, network loss)
# and treat its area as "scannerless" so UKF can make decisions based on other scanners.
# 30 seconds is long enough to bridge brief network hiccups but short enough to react
# quickly to actual scanner outages.
SCANNER_ACTIVITY_TIMEOUT: Final = 30.0  # seconds

# After SETTLING: stationary, higher threshold

# Movement state constants
MOVEMENT_STATE_MOVING: Final = "moving"  # Recently changed rooms
MOVEMENT_STATE_SETTLING: Final = "settling"  # Been in room a while
MOVEMENT_STATE_STATIONARY: Final = "stationary"  # Been in room long time

# Stability margins for each movement state (LEGACY - replaced by variance-based)
MARGIN_MOVING_PERCENT: Final = 0.05  # 5% - easier to switch when moving
MARGIN_SETTLING_PERCENT: Final = 0.08  # 8% - normal threshold (same as base)
MARGIN_STATIONARY_PERCENT: Final = 0.15  # 15% - harder to switch when stationary
MARGIN_STATIONARY_METERS: Final = 0.30  # 0.3m - also increase absolute threshold

# =============================================================================
# Variance-Based Stability Margin (replaces fixed percentage margins)
# =============================================================================
# Uses Kalman filter variance propagation for scientifically correct thresholds.
# Distance improvement must exceed combined uncertainty * sigma factor.
# Formula: threshold = sigma_factor * sqrt(var_incumbent + var_challenger)

# Sigma factors (movement-aware) - how many standard deviations required
# Peer Review: MOVING set to 2.0 sigma (not 1.5 sigma) because Q isn't dynamically scaled
STABILITY_SIGMA_MOVING: Final = 2.0  # 2 sigma - allows switching while moving
STABILITY_SIGMA_SETTLING: Final = 2.0  # 2 sigma - standard threshold
STABILITY_SIGMA_STATIONARY: Final = 3.0  # 3 sigma - very stable (99.7% confidence)

# RSSI Variance floors (dBm^2) - prevent over/under-confidence
# Based on BLE_RSSI_TYPICAL_STDDEV ~ 3-4 dBm in indoor environments
VARIANCE_FLOOR_COLD_START: Final = 9.0  # std=3dB, for sample_count < 5
VARIANCE_FLOOR_CONVERGED: Final = 4.0  # std=2dB, after Kalman convergence
VARIANCE_FALLBACK_UNINIT: Final = 25.0  # std=5dB, if Kalman not initialized
VARIANCE_COLD_START_SAMPLES: Final = 5  # Samples below which cold start floor applies

# Distance variance bounds (m^2) - prevent numerical issues
MIN_DISTANCE_FOR_VARIANCE: Final = 0.5  # Below this: use fixed variance
NEAR_FIELD_DISTANCE_VARIANCE: Final = 0.1  # m^2, fixed variance for near-field
MAX_DISTANCE_VARIANCE: Final = 4.0  # m^2, cap for far-field (std=2m max)
MIN_VIRTUAL_VARIANCE: Final = 0.25  # m^2, floor for virtual distances (std=0.5m)

# Virtual distance variance base (for scannerless rooms)
# Formula: virtual_variance = BASE * (2 - score)^2 ensures variance > 0 even at score=1.0
VIRTUAL_DISTANCE_BASE_VARIANCE: Final = 0.25  # m^2, minimum even for perfect score

# Default attenuation (path loss exponent) when not configured
DEFAULT_ATTENUATION: Final = 2.0  # Typical for line-of-sight indoor

# Physical RSSI Priority - prevents offset-boosted signals from winning over physically closer sensors
MIN_DISTANCE: Final = 0.1  # Minimum distance in metres (prevents multiple sensors at "0m")
CONF_USE_PHYSICAL_RSSI_PRIORITY = "use_physical_rssi_priority"
DEFAULT_USE_PHYSICAL_RSSI_PRIORITY: Final = True  # Enabled by default; set False to revert to old behavior
RSSI_CONSISTENCY_MARGIN_DB: Final = 8.0  # dB - max allowed RSSI disadvantage for distance winner
RSSI_HISTORY_SAMPLES: Final = 5  # Samples for median calculation
RSSI_CONSECUTIVE_WINS: Final = 2  # Consecutive cycles required before switching

# UKF (Unscented Kalman Filter) Area Selection - experimental multi-scanner fusion
CONF_USE_UKF_AREA_SELECTION = "use_ukf_area_selection"
DEFAULT_USE_UKF_AREA_SELECTION: Final = True  # Enabled by default; uses fingerprints when available
UKF_MIN_MATCH_SCORE: Final = 0.3  # Minimum match score (0-1) to consider a fingerprint match
# FIX: FEHLER 3 - Lower threshold for RETAINING the current area (prevents aggressive fallback to min-distance)
# When UKF winner matches current device.area_id, use this lower threshold instead of UKF_MIN_MATCH_SCORE.
# This keeps the device "sticky" in scannerless rooms even with noisy signals.
UKF_RETENTION_THRESHOLD: Final = 0.15  # Much lower threshold when retaining current area
UKF_MIN_SCANNERS: Final = 2  # Minimum scanners needed for UKF to make a decision

# FIX: Sticky Virtual Rooms - Prevent flickering for scannerless rooms
# When device is already in a UKF-detected area, add bonus to prevent marginal switches
UKF_STICKINESS_BONUS: Final = 0.15  # 15% bonus added to current area's match score
# Minimum distance (meters) required for scanner-based room to override UKF-detected area
UKF_WEAK_SCANNER_MIN_DISTANCE: Final = 3.0

# UKF RSSI Sanity Check constants
# When UKF picks a room with significantly weaker signal, verify the match confidence
UKF_RSSI_SANITY_MARGIN: Final = 15.0  # dB threshold - signal must be this much weaker to trigger check
UKF_LOW_CONFIDENCE_THRESHOLD: Final = 0.6  # Only apply sanity check when score is below this

# Virtual Distance for Scannerless Rooms
# When UKF score is below threshold, scannerless rooms can still compete in min-distance
# by calculating a "virtual distance" based on their fingerprint match quality.
# Formula: virtual_distance = max_radius * SCALE * (1 - score)²
# The quadratic formula rewards medium scores (0.3-0.5) more aggressively than linear,
# allowing scannerless rooms to compete against physical scanners through walls.
VIRTUAL_DISTANCE_SCALE: Final = 0.7  # Scaling factor (0.7 = 30% shorter than pure quadratic)
VIRTUAL_DISTANCE_MIN_SCORE: Final = 0.05  # Minimum score to consider (below = no virtual distance)

# Cross-floor hysteresis protection constants
# These control how strict the protection is against false cross-floor switches
CROSS_FLOOR_MARGIN_BASE: Final = 0.25  # Base percentage margin for cross-floor switching
CROSS_FLOOR_ESCAPE_BASE: Final = 0.45  # Base escape threshold percentage (now requires 100%+)
HISTORY_WINDOW: Final = 5  # Number of historical samples to consider for hysteresis

# Soft incumbent protection - when primary scanner stops reporting, challenger needs evidence
SOFT_INC_MIN_DISTANCE_ADVANTAGE: Final = 0.5  # Meters advantage needed without history
SOFT_INC_MIN_HISTORY_DIVISOR: Final = 2  # Divide CROSS_FLOOR_MIN_HISTORY by this for soft incumbent

# Near-field detection constants
NEAR_FIELD_THRESHOLD: Final = 3.0  # Meters - near-field zone for cross-floor ratio checks
NEAR_FIELD_CUTOFF: Final = 1.0  # Meters - cutoff for absolute wins
NEAR_FIELD_ABS_WIN_METERS: Final = 0.08  # Meters improvement required for near-field win

# Hysteresis percentage thresholds
PDIFF_OUTRIGHT: Final = 0.30  # 30% difference for outright win
PDIFF_HISTORICAL: Final = 0.15  # 15% difference for historical win

# RSSI fallback constants (when no distance contenders exist)
RSSI_FALLBACK_MARGIN: Final = 3.0  # dB advantage needed for RSSI fallback switch
RSSI_FALLBACK_CROSS_FLOOR_MARGIN: Final = 6.0  # dB advantage needed for cross-floor RSSI switch

# Co-visibility and correlation confidence thresholds
CONFIDENCE_WINNER_MIN: Final = 0.7  # Minimum winner confidence before doubling streak
CONFIDENCE_WINNER_MARGIN: Final = 0.2  # Incumbent must be this much better to double streak
CORR_CONFIDENCE_WINNER_MIN: Final = 0.5  # Correlation confidence threshold
CORR_CONFIDENCE_WINNER_MARGIN: Final = 0.3  # Correlation confidence margin

# UKF sanity check constants
UKF_RSSI_SIGMA_MULTIPLIER: Final = 3.0  # Standard deviations for RSSI variance check
UKF_MIN_RSSI_VARIANCE: Final = 4.0  # Minimum variance floor for RSSI checks
UKF_PROXIMITY_THRESHOLD_METERS: Final = 2.0  # Very close = almost certainly in that room
UKF_HIGH_CONFIDENCE_OVERRIDE: Final = 0.85  # Score needed to override proximity check

# Correlation z-score penalty constants
CORRELATION_Z_PENALTY_BASE: Final = 0.5  # Base for exponential penalty calculation
CORRELATION_Z_PENALTY_OFFSET: Final = 2.0  # Offset subtracted from z-score

# Profile maturity thresholds
MATURE_PROFILE_MIN_PAIRS: Final = 2  # Minimum scanner pairs for mature room profile
MATURE_ABSOLUTE_MIN_COUNT: Final = 2  # Minimum absolute correlations for maturity check
ABSOLUTE_Z_SCORE_MAX: Final = 2.0  # Max average z-score for absolute profile confidence

# Auto-learning quality improvements
# =============================================================================
# These constants control the quality filters for automatic fingerprint learning.
# See CLAUDE.md "Auto-Learning Quality System" for documentation.
#
# All features are implemented at the COORDINATOR level in:
#   AreaSelectionHandler._update_device_correlations() (area_selection.py)
#
# Feature 1: New Data Check - timestamp tracking (inline logic)
# Feature 2: AUTO_LEARNING_MIN_INTERVAL - reduces autocorrelation
# Feature 3: AUTO_LEARNING_MIN_CONFIDENCE - confidence filter threshold
# Feature 4: AUTO_LEARNING_VARIANCE_FLOOR - prevents z-score explosion
# Feature 5: Quality filters (velocity, RSSI variance, dwell time)
# =============================================================================

# Feature 2: Minimum interval between auto-learning updates
# Reduces autocorrelation from rho=0.95 to rho=0.82, improving effective sample size
AUTO_LEARNING_MIN_INTERVAL: Final = 5.0  # seconds

# Feature 4: Variance floor prevents unbounded convergence
# Without this, z-scores explode after thousands of samples (Hyper-Precision Paradox)
AUTO_LEARNING_VARIANCE_FLOOR: Final = 4.0  # dB^2 (std_dev = 2 dB)

# Feature 3: Minimum confidence for room assignment before learning
# Only learn from high-confidence assignments to avoid polluting fingerprints with noise
# Implemented in: AreaSelectionHandler._update_device_correlations()
AUTO_LEARNING_MIN_CONFIDENCE: Final = 0.5  # 50% minimum confidence

# Feature 5: Quality filters for sample rejection
# Implemented in: AreaSelectionHandler._update_device_correlations()
# Skip learning during rapid movement (RSSI changes too quickly)
AUTO_LEARNING_MAX_VELOCITY: Final = 1.0  # m/s - walking speed threshold
# Skip learning if recent RSSI variance is too high (unstable signal)
AUTO_LEARNING_MAX_RSSI_VARIANCE: Final = 16.0  # dB^2 (std_dev = 4 dB)
# Minimum time in room before learning (device must be settled)
AUTO_LEARNING_MIN_DWELL_TIME: Final = 30.0  # seconds

# Sentinel values for distance/RSSI
DISTANCE_INFINITE_SENTINEL: Final = 999.0  # Sentinel value for unknown/infinite distance
RSSI_INVALID_SENTINEL: Final = -999.0  # Sentinel value for invalid/missing RSSI

# Min-distance algorithm thresholds
MINDIST_SIGNIFICANT_IMPROVEMENT: Final = 0.30  # 30% improvement for same-floor fast-track
MINDIST_PENDING_IMPROVEMENT: Final = 0.20  # 20% improvement to reset pending area
DISTANCE_TIE_THRESHOLD: Final = 0.01  # Threshold for distance equality (RSSI tiebreak)

# Streak counting thresholds
STREAK_LOW_CONFIDENCE_THRESHOLD: Final = 0.5  # Below this, don't count low-confidence toward streak

# Dynamic cross-floor margin increments
FLOOR_WITNESS_MARGIN_INCREMENT: Final = 0.10  # Additional margin per floor witness
FLOOR_IMBALANCE_MARGIN: Final = 0.15  # Margin for witness count imbalance
FLOOR_RATIO_MARGIN: Final = 0.20  # Margin based on distance ratio
FLOOR_SANDWICH_MARGIN_BASE: Final = 0.30  # Base margin when incumbent sandwiched
FLOOR_SANDWICH_MARGIN_INCREMENT: Final = 0.05  # Additional margin per sandwich floor
FLOOR_SKIP_MARGIN: Final = 0.35  # Margin per skipped floor

# Cross-floor margin caps (maximum values)
FLOOR_MARGIN_CAP_60: Final = 0.60  # Cap for witness-based margin
FLOOR_MARGIN_CAP_70: Final = 0.70  # Cap for imbalance-based margin
FLOOR_MARGIN_CAP_75: Final = 0.75  # Cap for sandwich-based margin
FLOOR_MARGIN_CAP_80: Final = 0.80  # Cap for ratio/skip-based margin
FLOOR_ESCAPE_CAP_80: Final = 0.80  # Escape cap for witness-based
FLOOR_ESCAPE_CAP_85: Final = 0.85  # Escape cap for imbalance-based
FLOOR_ESCAPE_CAP_90: Final = 0.90  # Escape cap for sandwich-based
FLOOR_ESCAPE_CAP_95: Final = 0.95  # Escape cap for ratio/skip-based
FLOOR_DISTANCE_RATIO_THRESHOLD: Final = 1.5  # Distance ratio triggering extra margin

# Beacon-handling constants. Source devices are tracked by MAC-address and are the
# originators of beacon-like data. We then create a "meta-device" for the beacon's
# uuid. Other non-static-mac protocols should use this method as well, by adding their
# own BEACON_ types.
METADEVICE_TYPE_IBEACON_SOURCE: Final = "beacon source"  # The source-device sending a beacon packet (MAC-tracked)
METADEVICE_IBEACON_DEVICE: Final = "beacon device"  # The meta-device created to track the beacon
METADEVICE_TYPE_PRIVATE_BLE_SOURCE: Final = "private_ble_src"  # current (random) MAC of a private ble device
METADEVICE_PRIVATE_BLE_DEVICE: Final = "private_ble_device"  # meta-device create to track private ble device
METADEVICE_TYPE_FMDN_SOURCE: Final = "fmdn_source"
METADEVICE_FMDN_DEVICE: Final = "fmdn_device"

# Protocol constants
SERVICE_UUID_FMDN = "0000feaa-0000-1000-8000-00805f9b34fb"

METADEVICE_SOURCETYPES: Final = {
    METADEVICE_TYPE_IBEACON_SOURCE,
    METADEVICE_TYPE_PRIVATE_BLE_SOURCE,
    METADEVICE_TYPE_FMDN_SOURCE,
}
METADEVICE_DEVICETYPES: Final = {METADEVICE_IBEACON_DEVICE, METADEVICE_PRIVATE_BLE_DEVICE, METADEVICE_FMDN_DEVICE}

# Bluetooth Device Address Type - classify MAC addresses
BDADDR_TYPE_UNKNOWN: Final = "bd_addr_type_unknown"  # uninitialised
BDADDR_TYPE_OTHER: Final = "bd_addr_other"  # Default 48bit MAC
BDADDR_TYPE_RANDOM_RESOLVABLE: Final = "bd_addr_random_resolvable"
BDADDR_TYPE_RANDOM_UNRESOLVABLE: Final = "bd_addr_random_unresolvable"
BDADDR_TYPE_RANDOM_STATIC: Final = "bd_addr_random_static"
BDADDR_TYPE_RESERVED: Final = "bd_addr_reserved"  # Reserved address range (0x80-0xBF)
BDADDR_TYPE_NOT_MAC48: Final = "bd_addr_not_mac48"
# Non-bluetooth address types - for our metadevice entries
ADDR_TYPE_IBEACON: Final = "addr_type_ibeacon"
ADDR_TYPE_PRIVATE_BLE_DEVICE: Final = "addr_type_private_ble_device"
ADDR_TYPE_FMDN_DEVICE: Final = "addr_type_fmdn_device"


class IrkTypes(Enum):
    """
    Enum of IRK Types.

    Values used to mark if a device matches a known IRK, or is yet to be checked.
    Since IRK's are 16-bytes (128bits) long and the spec requires that IRKs be validated
    against https://doi.org/10.6028/NIST.SP.800-22r1a we can be confident that our use of
    some short ints must not be capable of matching any valid IRK as they would fail
    most of the required tests (such as longest run of ones)

    If the irk field does not match any of these values, then it is a valid IRK.
    """

    ADRESS_NOT_EVALUATED = bytes.fromhex("0000")  # default
    NOT_RESOLVABLE_ADDRESS = bytes.fromhex("0001")  # address is not a resolvable private address.
    NO_KNOWN_IRK_MATCH = bytes.fromhex("0002")  # none of the known keys match this address.

    @classmethod
    def unresolved(cls) -> list[bytes]:
        return [bytes(k.value) for k in IrkTypes.__members__.values()]


# Device entry pruning. Letting the gathered list of devices grow forever makes the
# processing loop slower. It doesn't seem to have as much impact on memory, but it
# would certainly use up more, and gets worse in high "traffic" areas.
#
# Pruning ignores tracked devices (ie, ones we keep sensors for) and scanners. It also
# avoids pruning the most recent IRK for a known private device.
#
# IRK devices typically change their MAC every 15 minutes, so 96 addresses/day.
#
# Accoring to the backend comments, BlueZ times out adverts at 180 seconds, and HA
# expires adverts at 195 seconds to avoid churning.
#
PRUNE_MAX_COUNT = 1000  # How many device entries to allow at maximum
PRUNE_TIME_INTERVAL = 180  # Every 3m, prune stale devices
# ### Note about timeouts: Bluez and HABT cache for 180 or 195 seconds. Setting
# timeouts below that may result in prune/create/prune churn, but as long as
# we only re-create *fresh* devices the risk is low.
PRUNE_TIME_DEFAULT = 86400  # Max age of regular device entries (1day)
PRUNE_TIME_UNKNOWN_IRK = 240  # Resolvable Private addresses change often, prune regularly.
# see Bluetooth Core Spec, Vol3, Part C, Appendix A, Table A.1: Defined GAP timers
PRUNE_TIME_KNOWN_IRK: Final[int] = 16 * 60  # spec "recommends" 15 min max address age. Round up to 16 :-)
PRUNE_TIME_FMDN: Final[int] = 20 * 60  # Aggressive pruning for rotating FMDN source MACs

PRUNE_TIME_REDACTIONS: Final[int] = 10 * 60  # when to discard redaction data

SAVEOUT_COOLDOWN = 10  # seconds to delay before re-trying config entry save.

DOCS: dict[str, str | tuple[str, ...]] = {}


HIST_KEEP_COUNT = 10  # How many old timestamps, rssi, etc to keep for each device/scanner pairing.

# Config entry DATA entries

CONFDATA_SCANNERS = "scanners"
DOCS[CONFDATA_SCANNERS] = "Persisted set of known scanners (proxies)"

# Configuration and options

CONF_DEVICES = "configured_devices"
DOCS[CONF_DEVICES] = "Identifies which bluetooth devices we wish to expose"

CONF_SCANNERS = "configured_scanners"

CONF_FMDN_MODE = "fmdn_mode"
FMDN_MODE_RESOLVED_ONLY = "resolved_only"
FMDN_MODE_SOURCES_ONLY = "sources_only"
FMDN_MODE_BOTH = "both"
DEFAULT_FMDN_MODE = FMDN_MODE_RESOLVED_ONLY
CONF_FMDN_EID_FORMAT = "fmdn_eid_format"
FMDN_EID_FORMAT_STRIP_FRAME_20 = "strip_frame_20"
FMDN_EID_FORMAT_STRIP_FRAME_ALL = "strip_frame_all"
FMDN_EID_FORMAT_AUTO = "auto"
DEFAULT_FMDN_EID_FORMAT = FMDN_EID_FORMAT_AUTO
FMDN_EID_CANDIDATE_LENGTHS: Final[tuple[int, ...]] = (20, 32)

CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS = "max_area_radius", 20
DOCS[CONF_MAX_RADIUS] = "For simple area-detection, max radius from receiver"

CONF_MAX_VELOCITY, DEFAULT_MAX_VELOCITY = "max_velocity", 3
DOCS[CONF_MAX_VELOCITY] = (
    "In metres per second - ignore readings that imply movement away faster than",
    "this limit. 3m/s (10km/h) is good.",
)

# FIX: Teleport Recovery - Number of consecutive velocity-blocked measurements
# before accepting the new position anyway (self-healing mechanism)
VELOCITY_TELEPORT_THRESHOLD: Final = 10
"""
Number of consecutive measurements blocked by MAX_VELOCITY before accepting anyway.

When a device physically moves to a new location (e.g., from Scanner A at 12m to
Scanner B at 1m), the calculated velocity may exceed MAX_VELOCITY, causing all
new readings to be rejected. This threshold allows the system to self-heal:
after N consecutive blocks from the SAME scanner, accept the new position.

FIX: Increased from 5 to 10 to reduce false teleport detections caused by BLE noise.
With noisy RSSI values causing calculated velocities of 100+ m/s, a threshold of 5
triggered too frequently, constantly resetting the distance history. This caused
cross-floor protection (which requires history) to fail. A higher threshold gives
the Kalman filter more time to stabilize while still allowing recovery within
reasonable time (~10 seconds with 1 update/second).
"""

# FIX: BLE Noise Spike Filter - Multiplier for calculating dynamic noise threshold
# The actual threshold is: max_velocity * VELOCITY_NOISE_MULTIPLIER
VELOCITY_NOISE_MULTIPLIER: Final = 3.0
"""
Multiplier to calculate the noise velocity threshold from user's max_velocity config.

The noise threshold = max_velocity * VELOCITY_NOISE_MULTIPLIER

This makes the noise filter adapt to user configuration:
- Default (max_velocity=3 m/s): noise threshold = 9 m/s
- Vehicle tracking (max_velocity=20 m/s): noise threshold = 60 m/s
- Conservative (max_velocity=1 m/s): noise threshold = 3 m/s

Values between max_velocity and the noise threshold are considered
"plausible fast movement" and count toward teleport recovery. Values above
the noise threshold are physically impossible and treated as measurement errors.

Tier classification (with default max_velocity=3 m/s):
- <= max_velocity (3 m/s): Normal movement, accept reading
- > max_velocity, <= noise_threshold (9 m/s): Plausible fast, count toward teleport
- > noise_threshold (9 m/s): Impossible spike, ignore completely
"""

CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT = "devtracker_nothome_timeout", 30
DOCS[CONF_DEVTRACK_TIMEOUT] = "Timeout in seconds for setting devices as `Not Home` / `Away`."  # fmt: skip

# Two-Slope Path Loss Model Constants
# Based on research: PMC6165244, Wikipedia Path Loss models
# Indoor BLE propagation has two distinct regions with different characteristics.

PATH_LOSS_EXPONENT_NEAR: Final = 1.8
"""
Near-field path loss exponent for distances below the breakpoint.

Scientifically derived value based on indoor BLE propagation research.
In the near-field (Fresnel zone clear), signal propagation exhibits
waveguiding effects with lower attenuation than free space (n=2.0).
Typical indoor values range from 1.5-2.0. Value of 1.8 provides good
accuracy for most residential/office environments.

References:
- PMC6165244: "Indoor Positioning Algorithm Based on Improved RSSI Distance Model"
- Two-slope models show 4.9 dB std dev vs 17.2 dB for single-slope
"""

TWO_SLOPE_BREAKPOINT_METRES: Final = 6.0
"""
Breakpoint distance (metres) separating near-field and far-field regions.

At this distance, the propagation model transitions from near-field
(lower attenuation due to waveguiding) to far-field (higher attenuation
due to multipath, obstacles, and environmental factors).

Research indicates breakpoints typically occur at 5-10m for indoor BLE.
Value of 6.0m provides good balance for residential environments.
For large open spaces, consider 8-10m; for cluttered spaces, 4-5m.

References:
- Wikipedia: Two-ray ground-reflection model
- Near-ground path loss measurements at 2.4 GHz
"""

CONF_ATTENUATION, DEFAULT_ATTENUATION = "attenuation", 3.5
DOCS[CONF_ATTENUATION] = (
    "Far-field path loss exponent for distances beyond ~6m. "
    "Higher values = faster signal decay with distance. "
    "Typical: 3.0 (open space) to 4.5 (cluttered/walls). Default: 3.5"
)
CONF_REF_POWER, DEFAULT_REF_POWER = "ref_power", -55.0
DOCS[CONF_REF_POWER] = "Default RSSI for signal at 1 metre."

CONF_SAVE_AND_CLOSE = "save_and_close"
CONF_SCANNER_INFO = "scanner_info"
CONF_RSSI_OFFSETS = "rssi_offsets"

CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL = "update_interval", 10
DOCS[CONF_UPDATE_INTERVAL] = (
    "Maximum time between sensor updates in seconds. Smaller intervals",
    "means more data, bigger database.",
)

CONF_SMOOTHING_SAMPLES, DEFAULT_SMOOTHING_SAMPLES = "smoothing_samples", 20
DOCS[CONF_SMOOTHING_SAMPLES] = (
    "How many samples to average distance smoothing. Bigger numbers"
    " make for slower distance increases. 10 or 20 seems good."
)

# Defaults
DEFAULT_NAME = DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__package__)
_LOGGER_SPAM_LESS = BermudaLogSpamLess(_LOGGER, LOGSPAM_INTERVAL)


STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
