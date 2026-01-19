# Bermuda Development Guide

## Environment Requirements

- **Python 3.13** is required (not 3.11 or 3.12)
- **Home Assistant 2025.10+** or later (2026.x recommended)

## Quick Setup

```bash
# Create virtual environment with Python 3.13
python3.13 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt -r requirements_dev.txt -r requirements_test.txt
```

## Critical: Type-Checking & Dependency Discipline

- Do **NOT** suppress `import-not-found` or `import-untyped` errors by weakening `mypy.ini` or adding blanket `# type: ignore` markers.
- When mypy reports missing stubs (e.g., `Library stubs not installed for "aiofiles"`), add the matching `types-*` package to `requirements_dev.txt`.
- When mypy reports `import-not-found` for a library, ensure the package is declared in `requirements_test.txt` or `requirements.txt` and installed.

## Local Validation (Run Before Committing)

```bash
# Linting and formatting
python -m ruff check --fix
python -m ruff format

# Type checking (strict mode)
python -m mypy --strict --install-types --non-interactive

# Tests
python -m pytest --cov -q
```

## Code Style & Linting

- Ruff for linting and formatting
- mypy for strict type checking
- codespell for typo checks
- Follow PEP 8/257 and strict typing (PEP 695 generics where helpful)

## Architecture Overview

- **BermudaDataUpdateCoordinator** (`coordinator.py`): Drives all Bluetooth processing, subscribes to HA Bluetooth manager, tracks scanners, prunes stale devices, redacts diagnostics
- **BermudaDevice** (`bermuda_device.py`): Represents each Bluetooth address, normalizes MACs, classifies address types, caches area/floor metadata
- **Metadevices**: Group rotating identities (IRK, iBeacon sources) so changing MACs map back to stable logical devices
- **Entities**: `sensor.py`, `binary_sensor.py`, `number.py`, `device_tracker.py` read state from coordinator

## Testing Standards

- Cover new features with pytest
- Place tests in the `tests/` directory
- Use `pytest-homeassistant-custom-component` for HA integration tests
- Add regression tests for bug fixes

## Home Assistant Integration Notes

- Keep `manifest.json` aligned with HA guidance
- Prefer storing config entry state on `entry.runtime_data` with typed structures
- Inject shared session via `async_get_clientsession(hass)`
- Store tokens/state via `helpers.storage.Store` with throttled writes

## Clean & Secure Coding

- **Logging**: Use lazy `%`-style logging (ruff G004)
- **Exceptions**: Raise precise types, chain with `raise … from …`, avoid broad `except`
- **Security**: No `eval`/`exec`, avoid `shell=True`, prefer `yaml.safe_load`, redact secrets/PII
- **Async**: Keep code non-blocking, use `asyncio.to_thread` for blocking ops, handle `CancelledError`
- **File I/O**: Prefer `pathlib`, atomic writes, batched operations
