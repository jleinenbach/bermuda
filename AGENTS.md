# Rules of Engagement

## CRITICAL: Type-Checking & Dependency Discipline
- Do **NOT** suppress `import-not-found` or `import-untyped` errors by weakening `mypy.ini` or adding blanket `# type: ignore` markers.
- When mypy reports missing stubs (e.g., `Library stubs not installed for "aiofiles"`), add the matching `types-*` package to `requirements_dev.txt` so the environment is fixed instead of the diagnostics being hidden.
- When mypy reports `import-not-found` for a library (e.g., `habluetooth`), ensure the package is declared in `requirements_test.txt` or `requirements.txt` and installed; assume the environment is incomplete before assuming the code is wrong.

## Primary Directive
- Always read README.md and manifest.json first to understand the integration's purpose and dependencies.
- Review docs/ for any technical documentation relevant to the change.

## Repository Orientation
- README.md outlines Bermuda's Bluetooth trilateration goals, supported hardware (ESPHome proxies, Shelly Plus, USB Bluetooth), generated entities (area/distance sensors, device_tracker), and developer tips (e.g., `bermuda.dump_devices` service). Read it to understand the feature set and setup expectations before modifying code.
- Technical documentation, if present, resides under docs/. Consult it when implementing or changing features.

## Code Style & Linting
- Enforce Ruff for linting and formatting.
- Enforce mypy for strict type checking.
- Use codespell for typo checks.

## Environment Awareness
- Development happens in a Dev Container.
- Respect the settings in .vscode/settings.json regarding line lengths or auto-formatting.

## Local Validation
- Before committing, run: `python -m ruff check --fix`, `python -m mypy --strict --install-types --non-interactive`, and `python -m pytest --cov -q` (tests live in tests/). These commands keep linting, typing, and coverage aligned with project expectations.

## Testing Standards
- Cover new features with pytest.
- Place tests in the `tests/` directory.

## Architecture Philosophy
- Bermuda logic should remain decoupled from specific hardware where possible.
- Use Metadevices for logical grouping of rotating MAC addresses.

## Home Assistant Integration Notes
- Keep `manifest.json` aligned with Home Assistant guidance: set realistic `iot_class` values and declare `"quality_scale": "platinum"` when adding or updating the integration metadata.
- Prefer storing config entry state on `entry.runtime_data` with typed structures instead of module-level globals or `hass.data` buckets.

## Architecture Orientation
- The `BermudaDataUpdateCoordinator` (`custom_components/bermuda/coordinator.py`) drives all Bluetooth processing: it subscribes to Home Assistant’s Bluetooth manager, tracks scanners, prunes stale devices, redacts diagnostics, and fires dispatcher signals (`SIGNAL_DEVICE_NEW`, `SIGNAL_SCANNERS_CHANGED`) to entities.
- Each Bluetooth address (scanner or target) is represented by a `BermudaDevice` (`custom_components/bermuda/bermuda_device.py`). These objects normalize MACs, classify address types (standard, iBeacon, IRK), register PBLE callbacks for IRK rotation, and cache area/floor metadata for distance/area calculations.
- Entities read state from the coordinator:
  - `sensor.py`, `binary_sensor.py`, and `number.py` expose distance/area/diagnostic controls.
  - `device_tracker.py` surfaces presence for `Person` linking and honors configurable timeouts.
  - `diagnostics.py` redacts addresses using the coordinator’s redaction helpers.
- Metadevices group rotating identities: IRK and iBeacon sources are merged so their changing MACs map back to a stable logical device before entity updates.
- The `bermuda.dump_devices` service (declared in `services.yaml`) returns the coordinator’s cached device graph for troubleshooting; outputs may change between releases.

## Clean & Secure Coding Standard (Python 3.13 + Home Assistant 2025.10)
- **Logging (ruff G004):** Use lazy `%`-style logging; never suppress `G004` above debug level.
- **PEP 8/257 and typing:** Follow docstring conventions and strict typing (PEP 695 generics where helpful).
- **Exceptions:** Raise precise types and chain with `raise … from …`; avoid broad `except`.
- **Security hygiene:** No `eval`/`exec`, avoid `shell=True`, prefer `yaml.safe_load`, validate archive paths, and redact secrets/PII in logs.
- **Async discipline:** Keep code non-blocking; offload work with `asyncio.to_thread`, use `asyncio.TaskGroup` when appropriate, and handle `CancelledError` on cancel.
- **File system/I/O:** Prefer `pathlib`, atomic writes, and batched operations; cache pure computations with clear invalidation.
- **Guardrails:** Validate inputs, ranges, and resolved paths; enforce safe timeouts/backoff for network calls; ensure decrypted payload helpers return `bytes`.
- **Home Assistant specifics:** Inject the shared session via `async_get_clientsession(hass)`, use `get_url` helpers, centralize fetches in `DataUpdateCoordinator`, provide repairs/diagnostics with redaction, and store tokens/state via `helpers.storage.Store` with throttled writes.
- **Testing & local checks:** Add regression tests for fixes (under `tests/`); run `python -m ruff check --fix`, `python -m mypy --strict --install-types --non-interactive`, and `python -m pytest --cov -q` before committing.

## Environment prerequisites (typing/tests)
- Install development dependencies with `python -m pip install -r requirements_dev.txt` so `homeassistant` and `pytest-homeassistant-custom-component` are available.
- Run mypy with `--install-types --non-interactive` if new stubs are needed; missing type stubs will otherwise cause failures.
- Pytest relies on Home Assistant test helpers; ensure the packages from `requirements_test.txt` are present before running isolated tests.
- When exercising optional integrations (e.g., googlefindmy), ensure their dependencies are installed locally or use the absence-safe code paths and associated tests so mypy/pytest do not fail when the provider is missing.
- Do not hide type or test failures with `ignore_errors` or similar blanket suppressions in tooling configs; fix or narrowly annotate issues so CI reflects real coverage.
