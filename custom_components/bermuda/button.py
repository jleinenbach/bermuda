"""Create Button entities for manual fingerprint training."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import _LOGGER, DOMAIN, SIGNAL_DEVICE_NEW
from .entity import BermudaEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator

# Number of unique training samples to collect
# 60 samples with 82% efficiency (5s interval) = ~49 effective samples
# This exceeds the n>=30 threshold for Central Limit Theorem reliability
TRAINING_SAMPLE_COUNT = 60

# Maximum time to wait for training to complete (seconds)
# 60 samples x 5s interval = 300s, plus buffer for missed packets
TRAINING_MAX_TIME_SECONDS = 300.0

# Minimum time between training samples (seconds)
# 5s interval reduces autocorrelation (rho=0.10) for 82% statistical efficiency
# Shorter intervals cause highly correlated samples that add little information
TRAINING_MIN_SAMPLE_INTERVAL = 5.0

# How often to poll for new advertisement data (seconds)
# Short interval to catch new data quickly, actual sample timing controlled by MIN_SAMPLE_INTERVAL
TRAINING_POLL_INTERVAL = 0.3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Load Button entities for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    # FIX 5: Use set instead of list for O(1) lookup and no duplicates
    created_devices: set[str] = set()

    @callback
    def device_new(address: str) -> None:
        """Create entities for newly-found device."""
        if address not in created_devices:
            # Check for duplicate entities with old address formats before creating new ones
            old_address = coordinator.check_for_duplicate_entities(address)
            if old_address:
                coordinator.cleanup_old_entities_for_device(old_address, address)

            entities: list[ButtonEntity] = []
            entities.append(BermudaTrainingButton(coordinator, entry, address))
            entities.append(BermudaResetTrainingButton(coordinator, entry, address))
            async_add_devices(entities, False)
            created_devices.add(address)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))


class BermudaTrainingButton(BermudaEntity, ButtonEntity):
    """
    Button to trigger fingerprint training.

    This button is only available (enabled) when:
    1. A room has been selected in the Training Room dropdown
    2. The device has valid RSSI readings from scanners

    Pressing the button trains the fingerprint with the current RSSI readings
    for the selected room, applying multiple samples for stronger weight.

    During training, the icon changes to a timer/hourglass to indicate progress.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "training_learn"
    _attr_entity_category = EntityCategory.CONFIG

    # Icons for different states
    ICON_IDLE = "mdi:brain"
    ICON_TRAINING = "mdi:timer-sand"

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        """Initialize the training button."""
        super().__init__(coordinator, entry, address)
        self._is_training = False
        self._attr_icon = self.ICON_IDLE
        _LOGGER.debug(
            "Training button created for %s (device id: %s)",
            self._device.name,
            id(self._device),
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass - verify listener registration."""
        await super().async_added_to_hass()
        _LOGGER.debug(
            "Training button added to hass for %s: floor=%s, area=%s (device id: %s)",
            self._device.name,
            self._device.training_target_floor_id,
            self._device.training_target_area_id,
            id(self._device),
        )

    @property
    def available(self) -> bool:
        """Return True if button should be enabled (floor AND room selected, not training)."""
        # Check parent availability first
        if not super().available:
            # FIX 7: Guard debug logging with isEnabledFor to avoid string formatting overhead
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Training button unavailable for %s: coordinator not ready",
                    self._device.name,
                )
            return False

        # Disable button while training is in progress (prevent double-click)
        if self._is_training:
            return False

        # Button available when BOTH training floor AND room have been selected.
        # Uses training_target_* fields which are ONLY set by select entities
        # and NEVER cleared by coordinator - ensuring button stays enabled.
        floor_ok = self._device.training_target_floor_id is not None
        area_ok = self._device.training_target_area_id is not None
        result = floor_ok and area_ok

        # FIX 7: Guard debug logging - available() is called frequently by UI
        if not result and _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Training button unavailable for %s: floor=%s, area=%s (device id: %s)",
                self._device.name,
                self._device.training_target_floor_id,
                self._device.training_target_area_id,
                id(self._device),
            )

        return result

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update - explicitly update availability."""
        # FIX 3: Include _is_training in availability check to match available() property
        parent_available = self.coordinator.last_update_success
        floor_ok = self._device.training_target_floor_id is not None
        area_ok = self._device.training_target_area_id is not None
        not_training = not self._is_training
        should_be_available = parent_available and floor_ok and area_ok and not_training

        # FIX 7: Guard debug logging
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Button coordinator update for %s: parent=%s, floor=%s, area=%s, "
                "training=%s, available=%s (device id: %s)",
                self._device.name,
                parent_available,
                self._device.training_target_floor_id,
                self._device.training_target_area_id,
                self._is_training,
                should_be_available,
                id(self._device),
            )
        super()._handle_coordinator_update()

    async def async_press(self) -> None:
        """Handle the button press - trigger fingerprint training."""
        # Guard against double-click (training already in progress)
        if self._is_training:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Training button pressed but training already in progress for %s",
                    self._device.name,
                )
            return

        # Double-check that a training room is selected
        if self._device.training_target_area_id is None:
            _LOGGER.warning(
                "Training button pressed but no room selected for %s",
                self._device.name,
            )
            return

        # Check if device has any adverts (RSSI readings)
        if not self._device.adverts:
            _LOGGER.warning(
                "Training button pressed but no scanner data for %s",
                self._device.name,
            )
            return

        target_area_id = self._device.training_target_area_id
        target_area_name = self._device.area_locked_name or target_area_id

        # FIX 6: Validate that area_id actually exists in Home Assistant
        # This guards against stale or invalid area references
        area_registry = ar.async_get(self.coordinator.hass)
        if area_registry.async_get_area(target_area_id) is None:
            _LOGGER.error(
                "Training aborted for %s: area '%s' does not exist",
                self._device.name,
                target_area_id,
            )
            return

        # Show loading indicator
        self._is_training = True
        self._attr_icon = self.ICON_TRAINING
        self.async_write_ha_state()

        _LOGGER.info(
            "Training fingerprint for %s in room %s (waiting for %d unique samples, max %.0fs)...",
            self._device.name,
            target_area_name,
            TRAINING_SAMPLE_COUNT,
            TRAINING_MAX_TIME_SECONDS,
        )

        # Create unique notification ID for this device
        notification_id = f"{DOMAIN}_training_{self._device.unique_id}"

        # Show start notification
        async_create(
            self.coordinator.hass,
            message=(
                f"Training fingerprint for **{self._device.name}** in **{target_area_name}**.\n\n"
                f"Collecting {TRAINING_SAMPLE_COUNT} samples (max {TRAINING_MAX_TIME_SECONDS / 60:.0f} min).\n\n"
                f"Keep the device in the target room during training."
            ),
            title="Bermuda: Training Started",
            notification_id=notification_id,
        )

        try:
            # BUG 19 FIX: Wait for REAL new advertisements instead of re-reading cached values
            # BLE trackers typically advertise every 1-10 seconds. Polling faster than that
            # would read the same cached RSSI value multiple times, causing over-confidence
            # in the Kalman filter without adding real information.
            #
            # We track timestamps from previous samples and only count a sample as "successful"
            # when at least one scanner has NEW data (stamp changed since last sample).
            successful_samples = 0
            last_stamps: dict[str, float] = {}
            # FIX 1: Use time.monotonic() instead of deprecated asyncio.get_event_loop().time()
            start_time = time.monotonic()

            while successful_samples < TRAINING_SAMPLE_COUNT:
                # Check timeout
                elapsed = time.monotonic() - start_time
                if elapsed >= TRAINING_MAX_TIME_SECONDS:
                    _LOGGER.warning(
                        "Training timeout for %s after %.0fs (%d/%d samples)",
                        self._device.name,
                        elapsed,
                        successful_samples,
                        TRAINING_SAMPLE_COUNT,
                    )
                    break

                # FIX 2: Guard - verify coordinator and device are still valid
                if self.coordinator is None or self._device is None:
                    _LOGGER.warning(
                        "Training aborted for %s: coordinator or device became unavailable",
                        self._device.name if self._device else "unknown",
                    )
                    break

                # FIX 2: Try to get a training sample with exception handling
                try:
                    success, current_stamps = await self.coordinator.async_train_fingerprint(
                        device_address=self.address,
                        target_area_id=target_area_id,
                        last_stamps=last_stamps,
                    )
                except Exception:  # noqa: BLE001 - Intentional broad catch for training resilience
                    _LOGGER.exception(
                        "Training sample failed for %s (sample %d)",
                        self._device.name,
                        successful_samples + 1,
                    )
                    # Continue trying - don't abort on single sample failure
                    success = False
                    current_stamps = last_stamps

                if success:
                    successful_samples += 1
                    last_stamps = current_stamps
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "Training sample %d/%d collected for %s (%.0fs elapsed)",
                            successful_samples,
                            TRAINING_SAMPLE_COUNT,
                            self._device.name,
                            elapsed,
                        )
                elif current_stamps:
                    # No new data yet - update stamps anyway for next comparison
                    # (in case device is offline, stamps stay empty and we keep waiting)
                    last_stamps = current_stamps

                # Short poll interval - we're waiting for new BLE advertisements
                await asyncio.sleep(TRAINING_POLL_INTERVAL)

            # Calculate training duration
            training_duration = time.monotonic() - start_time

            if successful_samples > 0:
                _LOGGER.info(
                    "Fingerprint training complete for %s in %s (%d/%d samples)",
                    self._device.name,
                    target_area_name,
                    successful_samples,
                    TRAINING_SAMPLE_COUNT,
                )
                # FIX: BUG 10 - After successful training, SET the device's area to the trained room.
                # Without this, the area lock is cleared, UKF runs, and if the score is < 0.3
                # (switching threshold), it falls back to min-distance which might pick wrong room.
                # By setting the area HERE, the device starts in the trained room after refresh,
                # and UKF retention threshold (0.15) will help keep it there.
                self._device.update_area_and_floor(target_area_id)

                # Calculate quality index based on sample efficiency
                # Effective samples considering autocorrelation (rho=0.10 for 5s interval)
                # n_eff = n * (1-rho)/(1+rho) = n * 0.82
                autocorr_factor = 0.82  # For 5s sampling interval
                effective_samples = successful_samples * autocorr_factor
                # Quality: percentage of effective samples vs target (30 for CLT)
                clt_target = 30
                quality_percent = min(100.0, (effective_samples / clt_target) * 100.0)

                # Determine quality rating
                if quality_percent >= 100:
                    quality_rating = "Excellent"
                    quality_icon = "✓"
                elif quality_percent >= 70:
                    quality_rating = "Good"
                    quality_icon = "○"
                elif quality_percent >= 50:
                    quality_rating = "Moderate"
                    quality_icon = "△"
                else:
                    quality_rating = "Poor"
                    quality_icon = "✗"

                # Show success notification with quality index
                async_dismiss(self.coordinator.hass, notification_id)
                async_create(
                    self.coordinator.hass,
                    message=(
                        f"Training complete for **{self._device.name}** in **{target_area_name}**.\n\n"
                        f"**Samples:** {successful_samples}/{TRAINING_SAMPLE_COUNT} "
                        f"({effective_samples:.0f} effective)\n"
                        f"**Duration:** {training_duration:.0f}s\n"
                        f"**Quality:** {quality_icon} {quality_rating} ({quality_percent:.0f}%)\n\n"
                        f"The device location has been set to **{target_area_name}**."
                    ),
                    title="Bermuda: Training Complete",
                    notification_id=notification_id,
                )
            else:
                _LOGGER.warning(
                    "Fingerprint training failed for %s - no valid samples",
                    self._device.name,
                )

                # Show failure notification
                async_dismiss(self.coordinator.hass, notification_id)
                async_create(
                    self.coordinator.hass,
                    message=(
                        f"Training failed for **{self._device.name}** in **{target_area_name}**.\n\n"
                        f"No valid samples collected after {training_duration:.0f}s.\n\n"
                        f"Possible causes:\n"
                        f"- Device is not sending BLE advertisements\n"
                        f"- No scanners can see the device\n"
                        f"- Device moved out of range during training"
                    ),
                    title="Bermuda: Training Failed",
                    notification_id=notification_id,
                )

        # FIX 4: Handle CancelledError (e.g., Home Assistant shutdown during training)
        except asyncio.CancelledError:
            _LOGGER.info(
                "Training cancelled for %s (shutdown or reload)",
                self._device.name,
            )
            # Show cancellation notification
            async_dismiss(self.coordinator.hass, notification_id)
            async_create(
                self.coordinator.hass,
                message=(
                    f"Training cancelled for **{self._device.name}**.\n\n"
                    f"The training was interrupted (Home Assistant shutdown or reload)."
                ),
                title="Bermuda: Training Cancelled",
                notification_id=notification_id,
            )
            raise  # CancelledError must be re-raised per asyncio contract
        finally:
            # ALWAYS clear training state and fields, even if training fails or throws exception
            self._is_training = False
            self._attr_icon = self.ICON_IDLE

            # FIX 7: Guard debug logging
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Clearing training fields for %s (floor=%s, area=%s)",
                    self._device.name,
                    self._device.training_target_floor_id,
                    self._device.training_target_area_id,
                )
            self._device.training_target_floor_id = None
            self._device.training_target_area_id = None
            # Also clear the area lock
            self._device.area_locked_id = None
            self._device.area_locked_name = None
            self._device.area_locked_scanner_addr = None

            # Trigger refresh so select entities clear their dropdowns
            # This also updates the button's icon back to idle state
            await self.coordinator.async_request_refresh()

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_training_learn"


class BermudaResetTrainingButton(BermudaEntity, ButtonEntity):
    """
    Button to reset all training data for a device.

    This is the "nuclear option" for fixing incorrect manual training.
    It clears ALL user-trained fingerprint data (Frozen Layers) for this device
    across ALL rooms, reverting to automatic learning (Shadow Learning) only.

    Use cases:
    - "Ghost Scanner" problem: Device was trained in wrong/invisible room
    - User wants to start fresh with automatic learning
    - Incorrect training that can't be fixed by re-training

    The auto-learned data is preserved, providing immediate fallback.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "reset_training"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:eraser"

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        """Initialize the reset training button."""
        super().__init__(coordinator, entry, address)
        _LOGGER.debug(
            "Reset training button created for %s",
            self._device.name,
        )

    async def async_press(self) -> None:
        """Handle the button press - reset all training data for this device."""
        _LOGGER.info(
            "Resetting all training data for %s...",
            self._device.name,
        )

        success = await self.coordinator.async_reset_device_training(self.address)

        if success:
            _LOGGER.info(
                "Successfully reset all training data for %s",
                self._device.name,
            )
        else:
            _LOGGER.info(
                "No training data found for %s - nothing to reset",
                self._device.name,
            )

        # Trigger refresh to update entity states
        await self.coordinator.async_request_refresh()

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        return f"{self._device.unique_id}_reset_training"
