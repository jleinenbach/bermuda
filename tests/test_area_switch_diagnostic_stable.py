"""
The area switch diagnostic sensor only changes when the decision changes.

The sensor used to publish the full ``sensortext()`` and ``to_dict()`` of the
last ``AreaTests``. Both carry per-cycle numbers (UKF score, streak counter,
distances, advert age), so every coordinator cycle fired ``state_changed`` and
wrote a ``states`` row, although the attributes are excluded from the recorder.

These tests pin the stable contract: numbers are masked in the state and in
the attributes, volatile fields are not published, and a real decision change
(path, verdict, area) still changes the state. ``bermuda.dump_devices`` keeps
the full detail through ``diag_area_switch`` and ``to_dict()``.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from pytest_homeassistant_custom_component.common import MockEntityPlatform

from custom_components.bermuda import area_selection
from custom_components.bermuda.area_selection import AreaTests
from custom_components.bermuda.sensor import BermudaSensorAreaSwitchReason

from .test_coordinator_methods import _make_coordinator

AREA_SELECTION_PATH = Path(inspect.getfile(area_selection))

# Placeholders in the ``tests.reason`` f-strings that render a number without a
# format spec. A placeholder with a format spec always renders a number.
NUMBER_EXPRESSIONS = frozenset(
    {
        "device.pending_streak",
        "streak_target",
        "len(rssi_readings)",
        "UKF_MIN_SCANNERS",
        "dist_adv_str",
        "soft_inc_min_distance_advantage",
        "len(challenger_hist)",
        "soft_inc_min_history",
    }
)
# Placeholders that render text. They keep the same value in both variants.
TEXT_EXPRESSIONS = frozenset(
    {
        "scannerless_indicator",
        "floor_indicator",
        "floor_type",
        "offline_context",
        "target_area_floor_id",
        "self.resolve_area_name(best_area_id)",
    }
)

STABLE_KEYS = {
    "decision_path",
    "reason",
    "from_area",
    "to_area",
    "same_area",
    "ukf_retention_mode",
    "ukf_stickiness_applied",
    "profile_source",
    "profile_has_button_training",
    "is_scannerless_room",
    "sanity_proximity_passed",
    "sanity_topological_passed",
    "sanity_rssi_passed",
    "offline_scanners_count",
    "offline_scanner_addrs",
    "auto_learning_blocked_offline",
    "from_floor",
    "to_floor",
}
VOLATILE_KEYS = {
    "ukf_score",
    "ukf_current_score",
    "ukf_threshold",
    "profile_samples",
    "virtual_distance_m",
    "distance_incumbent_m",
    "distance_challenger_m",
    "distance_diff_percent",
    "nearest_scanner_m",
    "nearest_scanner_area",
    "winner_advert_age_s",
    "top_candidates",
    "coverage_penalty",
    "ukf_anchor_score",
}

PROBE_ADDRESS = "aa:bb:cc:dd:ee:21"
PROBE_ENTITY_ID = "sensor.bermuda_probe_area_switch_diagnostic"


def _apply(device: Any, tests: AreaTests) -> None:
    """Set both diagnostic fields the way the production code did before the fix."""
    device.area_tests = tests
    device.diag_area_switch = tests.sensortext()


def _setter() -> Callable[[Any, AreaTests], None]:
    """Production helper when it exists, otherwise the pre-fix contract."""
    helper: Callable[[Any, AreaTests], None] | None = getattr(area_selection, "_set_area_diagnostics", None)
    return helper or _apply


def _ukf_tests(**overrides: Any) -> AreaTests:
    """The UKF decision from the field observation (cross-floor pending streak)."""
    values: dict[str, Any] = {
        "decision_path": "UKF",
        "reason": "PENDING - cross-floor streak 1/6 (score 0.73)",
        "areas": ("kuche", "praxis"),
        "ukf_match_score": 0.73,
        "profile_source": "BUTTON_TRAINED",
        "profile_has_button": True,
        "profile_sample_count": 2471,
    }
    values.update(overrides)
    return AreaTests(**values)


def _volatile_ukf_tests() -> AreaTests:
    """Same decision as ``_ukf_tests()``, only the per-cycle numbers differ."""
    return _ukf_tests(
        reason="PENDING - cross-floor streak 2/6 (score 0.41)",
        ukf_match_score=0.41,
        profile_sample_count=2480,
        distance=(2.5, 3.75),
        pcnt_diff=0.33,
        winner_advert_age=30.0,
    )


def _full_tests(**overrides: Any) -> AreaTests:
    """An AreaTests with every optional block filled."""
    values: dict[str, Any] = {
        "decision_path": "UKF",
        "reason": "WIN - same area refresh (score 0.82)",
        "areas": ("buero", "buero"),
        "same_area": True,
        "floors": ("EG", "EG"),
        "ukf_match_score": 0.82,
        "ukf_current_area_score": 0.79,
        "ukf_threshold_used": 0.15,
        "ukf_retention_mode": True,
        "ukf_stickiness_applied": True,
        "profile_source": "MIXED",
        "profile_sample_count": 120,
        "profile_has_button": True,
        "is_scannerless_room": False,
        "virtual_distance": 1.5,
        "distance": (1.2, 2.4),
        "pcnt_diff": 0.5,
        "passed_proximity_check": True,
        "passed_topological_check": True,
        "passed_rssi_sanity": True,
        "nearest_scanner_distance": 1.1,
        "nearest_scanner_area": "buero",
        "winner_advert_age": 12.0,
        "top_candidates": [{"area": "buero", "score": 0.82, "type": "ukf"}],
        "offline_scanners_count": 1,
        "offline_scanner_addrs": "aa:bb:cc:dd:ee:99",
        "coverage_penalty_applied": 0.2,
        "auto_learning_blocked_offline": True,
        "ukf_anchor_score": 0.71,
    }
    values.update(overrides)
    return AreaTests(**values)


def _volatile_full_tests() -> AreaTests:
    """Same decision as ``_full_tests()``, every volatile field changed."""
    return _full_tests(
        reason="WIN - same area refresh (score 0.64)",
        ukf_match_score=0.64,
        ukf_current_area_score=0.61,
        top_candidates=[{"area": "buero", "score": 0.64, "type": "ukf"}, {"area": "flur", "score": 0.2}],
        coverage_penalty_applied=0.4,
        ukf_anchor_score=0.38,
        nearest_scanner_distance=2.7,
        virtual_distance=3.25,
        distance=(1.9, 3.1),
        pcnt_diff=0.8,
        ukf_threshold_used=0.3,
        nearest_scanner_area="flur",
        profile_sample_count=180,
        winner_advert_age=44.0,
    )


def _sensor(tests: AreaTests | None) -> BermudaSensorAreaSwitchReason:
    """A sensor bound to a bare device that carries ``tests``."""
    device = SimpleNamespace(area_tests=None, diag_area_switch=None)
    if tests is not None:
        _setter()(device, tests)
    sensor = object.__new__(BermudaSensorAreaSwitchReason)
    sensor._device = device  # type: ignore[assignment]
    return sensor


def _state(tests: AreaTests) -> str | None:
    """The published state for ``tests``."""
    value = _sensor(tests).native_value
    assert value is None or isinstance(value, str)
    return value


def _attributes(tests: AreaTests) -> dict[str, Any]:
    """The published attributes for ``tests``."""
    attrs = _sensor(tests).extra_state_attributes
    assert attrs is not None
    return dict(attrs)


def _reason_templates() -> list[tuple[int, ast.JoinedStr]]:
    """Every f-string assigned to ``<x>.reason`` in area_selection.py."""
    tree = ast.parse(AREA_SELECTION_PATH.read_text(encoding="utf-8"))
    found: list[tuple[int, ast.JoinedStr]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.JoinedStr):
            continue
        if any(isinstance(target, ast.Attribute) and target.attr == "reason" for target in node.targets):
            found.append((node.lineno, node.value))
    return sorted(found, key=lambda item: item[0])


def _render(template: ast.JoinedStr, variant: int) -> str:
    """Render a reason f-string with numbers set by ``variant`` and fixed text."""
    out: list[str] = []
    number = 0
    for part in template.values:
        if isinstance(part, ast.Constant):
            out.append(str(part.value))
            continue
        assert isinstance(part, ast.FormattedValue)
        expr = ast.unparse(part.value)
        if isinstance(part.format_spec, ast.JoinedStr):
            spec = "".join(str(v.value) for v in part.format_spec.values if isinstance(v, ast.Constant))
            number += 1
            value = 1.25 * number if variant == 1 else -37.5 * number
            out.append(format(value, spec))
        elif expr in NUMBER_EXPRESSIONS:
            number += 1
            out.append(str(number + 2 if variant == 1 else -11 * number))
        elif expr in TEXT_EXPRESSIONS:
            out.append(f"<{expr}>")
        else:
            pytest.fail(f"Placeholder {expr!r} is neither in NUMBER_EXPRESSIONS nor in TEXT_EXPRESSIONS")
    return "".join(out)


REASON_TEMPLATES = _reason_templates()


def test_pin_reason_fstring_count() -> None:
    """The template inventory the stable text was designed against (18 at 0.8.7)."""
    assert len(REASON_TEMPLATES) == 18


def test_t1_state_ignores_volatile_numbers() -> None:
    """The state masks numbers in the reason and drops scores, distances, counts and age."""
    expected = "🎯UKF: PENDING - cross-floor streak #/# (score #) | 📍kuche→praxis | UKF(S) | Prof:BTN"

    assert _state(_ukf_tests()) == expected
    assert _state(_volatile_ukf_tests()) == expected


def test_t1_b_numbers_are_masked_before_the_reason_is_cut() -> None:
    """A long reason stays stable when a number before the 50-character cut grows."""
    template = "WIN - cross-floor switch (scannerless) (streak {}/6, score 0.73)"
    short = _ukf_tests(reason=template.format(1))
    longer = _ukf_tests(reason=template.format(12))

    assert len(short.reason or "") > 50
    assert _state(short) == _state(longer)


def test_t2_attributes_ignore_volatile_fields() -> None:
    """The attributes keep the stable keys only and do not change with volatile fields."""
    base = _attributes(_full_tests())
    changed = _attributes(_volatile_full_tests())

    assert set(base) == STABLE_KEYS
    assert not set(base) & VOLATILE_KEYS
    assert base == changed
    assert base["reason"] == "WIN - same area refresh (score #)"


def test_t3_area_change_changes_state_and_attributes() -> None:
    """A different target area is a real decision change."""
    before = _ukf_tests()
    after = _ukf_tests(areas=("kuche", "flur"))

    assert _state(before) != _state(after)
    assert _attributes(before) != _attributes(after)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(
            {"reason": "WIN - bootstrap (score 0.73)"},
            {"reason": "PENDING - streak 1/6 (score 0.73)"},
            id="win-pending",
        ),
        pytest.param(
            {"reason": "WIN - bootstrap (score 0.73)"},
            {"reason": "REJECT - score too low (0.73 < 0.30)"},
            id="win-reject",
        ),
        pytest.param({"decision_path": "UKF"}, {"decision_path": "MIN_DISTANCE"}, id="ukf-min-distance"),
        # Without a UKF score only the path itself tells the two decisions apart.
        pytest.param(
            {"decision_path": "MIN_DISTANCE", "ukf_match_score": None},
            {"decision_path": "VIRTUAL", "ukf_match_score": None},
            id="min-distance-virtual",
        ),
    ],
)
def test_t4_verdict_and_path_change_the_state(first: dict[str, Any], second: dict[str, Any]) -> None:
    """Verdict and decision path stay visible in the state."""
    assert _state(_ukf_tests(**first)) != _state(_ukf_tests(**second))


@pytest.mark.parametrize(
    "template",
    [pytest.param(template, id=f"line{lineno}") for lineno, template in REASON_TEMPLATES],
)
def test_t5_every_reason_template_is_stable(template: ast.JoinedStr) -> None:
    """Each reason template renders the same stable text for different numbers of either sign."""
    first = _render(template, 1)
    second = _render(template, 2)
    has_number = any(
        isinstance(part, ast.FormattedValue)
        and (part.format_spec is not None or ast.unparse(part.value) in NUMBER_EXPRESSIONS)
        for part in template.values
    )
    assert (first != second) is has_number

    assert AreaTests(reason=first).stable_sensortext() == AreaTests(reason=second).stable_sensortext()


def test_t6_full_diagnostic_text_is_kept() -> None:
    """``diag_area_switch`` (read by dump_devices) keeps the score."""
    device = SimpleNamespace(area_tests=None, diag_area_switch=None)
    _setter()(device, _ukf_tests())

    assert "(score 0.73)" in device.diag_area_switch


def test_t7_a_area_diagnostics_are_set_in_one_place() -> None:
    """Both fields are assigned only inside the helper, and the helper is called at every site."""
    tree = ast.parse(AREA_SELECTION_PATH.read_text(encoding="utf-8"))
    helper = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_set_area_diagnostics"
    )
    helper_nodes = {id(node) for node in ast.walk(helper)}
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr in {"diag_area_switch", "area_tests"} for t in node.targets)
    ]
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_set_area_diagnostics"
        and ast.unparse(node) == "_set_area_diagnostics(device, tests)"
    ]

    assert len(assignments) == 2
    assert all(id(node) in helper_nodes for node in assignments)
    assert len(calls) == 9


def test_t7_b_helper_sets_both_fields_from_one_decision() -> None:
    """The helper stores the decision and its full text together."""
    device = SimpleNamespace()
    tests = _ukf_tests()

    area_selection._set_area_diagnostics(device, tests)  # type: ignore[arg-type]

    assert device.area_tests is tests
    assert device.diag_area_switch == tests.sensortext()


async def test_t8_volatile_cycles_fire_no_state_changed(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three cycles with one area change and two volatile updates fire one state_changed."""
    coordinator = _make_coordinator(hass)
    coordinator.last_update_success = True
    monkeypatch.setattr(coordinator, "async_add_listener", MagicMock(return_value=lambda: None))
    device = coordinator._get_or_create_device(PROBE_ADDRESS)
    # The sensor class reads this property; the _attr_ field alone is overridden by BermudaSensor.
    monkeypatch.setattr(BermudaSensorAreaSwitchReason, "entity_registry_enabled_default", property(lambda self: True))
    sensor = BermudaSensorAreaSwitchReason(coordinator, SimpleNamespace(options={}), PROBE_ADDRESS)  # type: ignore[arg-type]
    sensor.entity_id = PROBE_ENTITY_ID
    platform = MockEntityPlatform(hass, domain="sensor", platform_name="bermuda")
    await platform.async_add_entities([sensor])
    await hass.async_block_till_done()
    assert hass.states.get(PROBE_ENTITY_ID) is not None

    events: list[Event[EventStateChangedData]] = []
    hass.bus.async_listen(
        EVENT_STATE_CHANGED,
        lambda event: events.append(event) if event.data.get("entity_id") == PROBE_ENTITY_ID else None,
    )

    for tests in (
        _ukf_tests(),
        _volatile_ukf_tests(),
        _ukf_tests(reason="PENDING - cross-floor streak 3/6 (score 0.55)", ukf_match_score=0.55),
    ):
        _setter()(device, tests)
        sensor._handle_coordinator_update()
        await hass.async_block_till_done()

    assert len(events) == 1


def test_t9_a_state_is_cut_to_255_characters() -> None:
    """The sensor itself enforces the HA state length limit."""
    device = SimpleNamespace(area_tests=AreaTests(), diag_area_switch="x" * 256)
    sensor = object.__new__(BermudaSensorAreaSwitchReason)
    sensor._device = device  # type: ignore[assignment]

    with patch.object(AreaTests, "stable_sensortext", create=True, return_value="x" * 256):
        value = sensor.native_value

    assert isinstance(value, str)
    assert len(value) == 255


def test_t9_b_digits_in_area_names_are_kept() -> None:
    """Areas that differ only by a digit stay distinguishable."""
    assert _state(_ukf_tests(areas=("", "buero2"))) != _state(_ukf_tests(areas=("", "buero3")))
