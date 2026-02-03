"""
Helper classes for area selection algorithms.

Extracted from area_selection.py for improved testability and reduced complexity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from .bermuda_advert import BermudaAdvert
    from .bermuda_device import BermudaDevice


@runtime_checkable
class AdvertAnalyzerProtocol(Protocol):
    """
    Protocol for AdvertAnalyzer to enable dependency injection in tests.

    This allows tests to create mock implementations without depending on
    the full BermudaDevice/BermudaAdvert class hierarchy.
    """

    @property
    def device(self) -> BermudaDevice:
        """The device being analyzed."""
        ...

    @property
    def nowstamp(self) -> float:
        """Current monotonic timestamp."""
        ...

    @property
    def evidence_cutoff(self) -> float:
        """Timestamp cutoff for valid evidence."""
        ...

    @property
    def max_radius(self) -> float:
        """Maximum distance radius for consideration."""
        ...

    def effective_distance(self, advert: BermudaAdvert | None) -> float | None:
        """Get the cached effective distance for an advert."""
        ...

    def belongs(self, advert: BermudaAdvert | None) -> bool:
        """Check if advert belongs to this device's advertisement collection."""
        ...

    def within_evidence(self, advert: BermudaAdvert | None) -> bool:
        """Check if advert timestamp is within the evidence window."""
        ...

    def has_area(self, advert: BermudaAdvert | None) -> bool:
        """Check if advert has a valid area assignment."""
        ...

    def area_candidate(self, advert: BermudaAdvert | None) -> bool:
        """Check if advert can be considered for area selection."""
        ...

    def is_distance_contender(self, advert: BermudaAdvert | None) -> bool:
        """Check if advert qualifies as a distance contender."""
        ...

    def has_distance_contender(self) -> bool:
        """Check if any advert for this device is a distance contender."""
        ...

    def get_floor_id(self, advert: BermudaAdvert | None) -> str | None:
        """Get floor_id from advert's scanner device."""
        ...

    def is_cross_floor(
        self,
        current: BermudaAdvert | None,
        candidate: BermudaAdvert | None,
    ) -> bool:
        """Check if switching from current to candidate would cross floors."""
        ...

    def get_visible_scanner_addresses(self) -> set[str]:
        """Get addresses of all scanners currently seeing this device."""
        ...

    def get_all_known_scanners_for_area(self, area_id: str) -> set[str]:
        """Get all scanner addresses that have ever seen this device in this area."""
        ...


class AdvertAnalyzer:
    """
    Helper class for analyzing BLE advertisements in area selection.

    Extracted from _refresh_area_by_min_distance to reduce cyclomatic complexity
    and improve testability. Encapsulates common predicates for advertisement
    validation and distance calculation.

    Usage:
        analyzer = AdvertAnalyzer(
            device=device,
            nowstamp=nowstamp,
            evidence_cutoff=nowstamp - EVIDENCE_WINDOW_SECONDS,
            max_radius=max_radius,
            effective_distance_fn=lambda adv: self.effective_distance(adv, nowstamp),
        )

        for advert in device.adverts.values():
            if analyzer.is_distance_contender(advert):
                distance = analyzer.effective_distance(advert)
                # ... process contender ...
    """

    __slots__ = (
        "_device",
        "_effective_cache",
        "_effective_distance_fn",
        "_evidence_cutoff",
        "_max_radius",
        "_nowstamp",
    )

    def __init__(
        self,
        device: BermudaDevice,
        nowstamp: float,
        evidence_cutoff: float,
        max_radius: float,
        effective_distance_fn: Callable[[BermudaAdvert | None], float | None],
    ) -> None:
        """
        Initialize the analyzer with current context.

        Args:
        ----
            device: The device being analyzed
            nowstamp: Current monotonic timestamp
            evidence_cutoff: Timestamp cutoff for valid evidence
            max_radius: Maximum distance radius for consideration
            effective_distance_fn: Function to calculate effective distance
                                   (delegates to AreaSelectionHandler.effective_distance)

        """
        self._device = device
        self._nowstamp = nowstamp
        self._evidence_cutoff = evidence_cutoff
        self._max_radius = max_radius
        self._effective_distance_fn = effective_distance_fn
        self._effective_cache: dict[int, float | None] = {}

    @property
    def device(self) -> BermudaDevice:
        """The device being analyzed."""
        return self._device

    @property
    def nowstamp(self) -> float:
        """Current monotonic timestamp."""
        return self._nowstamp

    @property
    def evidence_cutoff(self) -> float:
        """Timestamp cutoff for valid evidence."""
        return self._evidence_cutoff

    @property
    def max_radius(self) -> float:
        """Maximum distance radius for consideration."""
        return self._max_radius

    def effective_distance(self, advert: BermudaAdvert | None) -> float | None:
        """
        Get the cached effective distance for an advert.

        Uses memoization to avoid redundant distance calculations during
        a single area selection cycle.

        Args:
        ----
            advert: The advertisement to get distance for

        Returns:
        -------
            The effective distance in meters, or None if unavailable

        """
        if advert is None:
            return None

        advert_id = id(advert)
        if advert_id not in self._effective_cache:
            self._effective_cache[advert_id] = self._effective_distance_fn(advert)
        return self._effective_cache[advert_id]

    def belongs(self, advert: BermudaAdvert | None) -> bool:
        """
        Check if advert belongs to this device's advertisement collection.

        Args:
        ----
            advert: The advertisement to check

        Returns:
        -------
            True if advert is in device.adverts.values()

        """
        return advert is not None and advert in self._device.adverts.values()

    def within_evidence(self, advert: BermudaAdvert | None) -> bool:
        """
        Check if advert timestamp is within the evidence window.

        Args:
        ----
            advert: The advertisement to check

        Returns:
        -------
            True if advert.stamp >= evidence_cutoff

        """
        return advert is not None and advert.stamp is not None and advert.stamp >= self._evidence_cutoff

    def has_area(self, advert: BermudaAdvert | None) -> bool:
        """
        Check if advert has a valid area assignment.

        Args:
        ----
            advert: The advertisement to check

        Returns:
        -------
            True if advert.area_id is not None

        """
        return advert is not None and advert.area_id is not None

    def area_candidate(self, advert: BermudaAdvert | None) -> bool:
        """
        Check if advert can be considered for area selection.

        An advert is a candidate if it belongs to the device and has an area.

        Args:
        ----
            advert: The advertisement to check

        Returns:
        -------
            True if advert is a valid area candidate

        """
        return self.belongs(advert) and self.has_area(advert)

    def has_valid_distance(self, advert: BermudaAdvert | None) -> bool:
        """
        Check if advert has a valid distance calculation (ignoring max_radius).

        This is used for incumbent stability: an incumbent should only become
        "soft" if it has NO distance data, not just because distance > max_radius.
        Temporary RSSI fluctuations can cause distance to exceed max_radius, but
        the scanner is still actively providing data.

        Args:
        ----
            advert: The advertisement to check

        Returns:
        -------
            True if advert has a valid effective distance (regardless of max_radius)

        """
        if not self.area_candidate(advert):
            return False
        if advert is None:  # Type narrowing
            return False
        if not self.within_evidence(advert):
            return False
        eff_dist = self.effective_distance(advert)
        return eff_dist is not None

    def is_distance_contender(self, advert: BermudaAdvert | None) -> bool:
        """
        Check if advert qualifies as a distance contender.

        A contender must:
        - Be an area candidate (belongs to device, has area)
        - Be within the evidence time window
        - Have a valid effective distance within max_radius

        Args:
        ----
            advert: The advertisement to check

        Returns:
        -------
            True if advert is a valid distance contender

        """
        if not self.area_candidate(advert):
            return False
        if advert is None:  # Type narrowing
            return False
        if not self.within_evidence(advert):
            return False
        eff_dist = self.effective_distance(advert)
        return eff_dist is not None and eff_dist <= self._max_radius

    def has_distance_contender(self) -> bool:
        """
        Check if any advert for this device is a distance contender.

        Returns
        -------
            True if at least one advert qualifies as a distance contender

        """
        return any(self.is_distance_contender(advert) for advert in self._device.adverts.values())

    def get_floor_id(self, advert: BermudaAdvert | None) -> str | None:
        """
        Get floor_id from advert's scanner device.

        Args:
        ----
            advert: The advertisement to get floor from

        Returns:
        -------
            Floor ID string or None if unavailable

        """
        if advert is None or advert.scanner_device is None:
            return None
        return getattr(advert.scanner_device, "floor_id", None)

    def is_cross_floor(
        self,
        current: BermudaAdvert | None,
        candidate: BermudaAdvert | None,
    ) -> bool:
        """
        Check if switching from current to candidate would cross floors.

        Args:
        ----
            current: The current incumbent advert
            candidate: The challenger advert

        Returns:
        -------
            True if the switch would be cross-floor

        """
        cur_floor = self.get_floor_id(current)
        cand_floor = self.get_floor_id(candidate)
        return cur_floor is not None and cand_floor is not None and cur_floor != cand_floor

    def get_visible_scanner_addresses(self) -> set[str]:
        """
        Get addresses of all scanners currently seeing this device.

        Only includes scanners with adverts that qualify as distance contenders.

        Returns
        -------
            Set of scanner addresses

        """
        visible: set[str] = set()
        for adv in self._device.adverts.values():
            if self.is_distance_contender(adv) and adv.scanner_device is not None:
                visible.add(adv.scanner_device.address)
        return visible

    def get_all_known_scanners_for_area(self, area_id: str) -> set[str]:
        """
        Get all scanner addresses that have ever seen this device in this area.

        Uses the device's co_visibility_stats to find historically known scanners.

        Args:
        ----
            area_id: The area to query

        Returns:
        -------
            Set of scanner addresses, empty if no stats available

        """
        if area_id not in self._device.co_visibility_stats:
            return set()
        return set(self._device.co_visibility_stats[area_id].keys())
