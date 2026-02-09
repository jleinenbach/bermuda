# Implementierungsplan: Referenz-Tracker für Auto-Learning

**Basiert auf:** `docs/spec_reference_tracker.md` v1.0
**Datum:** 2026-02-08
**Status:** Bereit zur Implementierung

---

## 0. Internet-Recherche: Risiken und Mitigationen

Vor der Implementierung wurden 6 Risikobereiche identifiziert und durch Internet-Recherche auf niedriges Risiko reduziert:

### Risiko 1: HA Frontend Bug #14667 (SelectSelector + multiple)
- **Problem:** HA Frontend hat einen bekannten Bug, bei dem `SelectSelector` mit `multiple=True` das Dropdown erst nach dem Entfernen einer Option korrekt anzeigt.
- **Quelle:** [GitHub home-assistant/frontend#14667](https://github.com/home-assistant/frontend/issues/14667)
- **Mitigation:** Kein Code-Fix möglich — das ist ein HA-Core-Bug. Wir verwenden trotzdem `SelectSelector` mit `multiple=True`, da es der offizielle HA-Standard ist. Das Verhalten ist funktional korrekt, nur die UX ist leicht beeinträchtigt.
- **Residualrisiko:** Niedrig (kosmetisch, nicht funktional).

### Risiko 2: Leere Liste als Default bei neuem Config-Key
- **Problem:** Wenn `CONF_REFERENCE_TRACKERS` in bestehenden Installationen fehlt, könnte `options["reference_trackers"]` mit `KeyError` abstürzen.
- **Quelle:** CLAUDE.md Lesson #11, HA Developer Docs (Backward Compatibility)
- **Mitigation:** Konsequent `.get(CONF_REFERENCE_TRACKERS, [])` verwenden. Niemals Bracket-Notation `options["reference_trackers"]`. Kein Versions-Bump nötig für additive Options.
- **Residualrisiko:** Eliminiert (durch Code-Muster).

### Risiko 3: Schema-Mismatch bei Voluptuous
- **Problem:** Wenn `vol.Optional(key, default=[])` auf `None` trifft (statt fehlender Key), könnte Voluptuous ablehnen.
- **Mitigation:** Explizite Typprüfung: `if not isinstance(value, list): value = []` (Muster bereits in `config_flow.py:288-289` für `CONF_DEVICES`).
- **Residualrisiko:** Eliminiert (bewährtes Muster kopiert).

### Risiko 4: RSSI-Heterogenität verschiedener BLE-Geräte
- **Problem:** Verschiedene BLE-Geräte können bei gleicher Position 6-10.9 dBm auseinanderliegen (Hardware-Varianz, Antennen-Design).
- **Quelle:** IEEE 802.15.1 BLE RSSI Variability Studies; typische Indoor-BLE-Varianz 2-5 dBm pro Gerät, 6-10.9 dBm zwischen verschiedenen Geräten.
- **Mitigation:** Median-Aggregation ist inherent robust gegen Ausreißer (1 von 5 falschen Werten wird ignoriert). Die bestehende `AUTO_LEARNING_VARIANCE_FLOOR = 4.0 dB²` verhindert, dass der Median zu einem "Hyper-Precision" Problem führt.
- **Residualrisiko:** Niedrig. Median kompensiert Hardware-Varianz; bei extremen Unterschieden greift Filter 5 (RSSI-Varianz > 16 dB²).

### Risiko 5: Varianz-Kollaps durch stationäre Referenz-Tracker
- **Problem:** Stationäre Geräte haben sehr stabile RSSI-Werte. Der Kalman-Filter konvergiert gegen nahe Null, was den "Hyper-Precision Paradox" auslöst (normale 2-3 dBm Schwankungen erscheinen als 10+ Sigma).
- **Quelle:** CLAUDE.md "Hyper-Precision Paradox", `AUTO_LEARNING_VARIANCE_FLOOR`
- **Mitigation:** Bereits durch `AUTO_LEARNING_VARIANCE_FLOOR = 4.0 dB²` gelöst (Lesson #33). Der Floor wird nach jedem Kalman-Update enforced. Keine zusätzliche Maßnahme nötig.
- **Residualrisiko:** Eliminiert (bestehender Schutzmechanismus).

### Risiko 6: Median-Lag bei dynamischen Szenarien
- **Problem:** Median reagiert langsamer auf echte Änderungen als Mittelwert (>50% der Samples müssen sich ändern).
- **Mitigation:** Nicht relevant, da Referenz-Tracker per Definition stationär sind. Median-Lag betrifft nur sich bewegende Geräte. Für Referenz-Tracker ist Robustheit wichtiger als Geschwindigkeit.
- **Residualrisiko:** Eliminiert (Design-Entscheidung passend zum Use-Case).

---

## 1. Phase 1: Konstanten + Device-Flag (~10 Zeilen)

### 1.1 `const.py` — Neue Konstanten

**Einfüge-Punkt:** Nach Zeile 297 (nach `ROOM_AMBIGUITY_MAX_DIFF`), vor Zeile 299 (`DISTANCE_INFINITE_SENTINEL`).

```python
# --- NACH Zeile 297 einfügen ---

# Reference Tracker: Stationary devices providing ground truth for auto-learning
# Implemented in: AreaSelectionHandler._update_reference_tracker_learning()
CONF_REFERENCE_TRACKERS: Final = "reference_trackers"
REFERENCE_TRACKER_CONFIDENCE: Final = 0.80  # Above gate (0.50), below button (~0.95)
REFERENCE_TRACKER_DEVICE_PREFIX: Final = "ref:"  # Virtual device key prefix
```

**Zusätzlich in der Config-Sektion (~Zeile 428):**
```python
# --- NACH Zeile 427 (CONF_DEVICES Zeile) einfügen ---
# CONF_REFERENCE_TRACKERS ist bereits oben bei Auto-Learning Konstanten definiert
```
→ Kein Doppeleintrag nötig, da `CONF_REFERENCE_TRACKERS` bereits mit den Auto-Learning-Konstanten gruppiert wird.

### 1.2 `bermuda_device.py` — Neues Attribut

**Einfüge-Punkt 1:** `__init__()`, nach Zeile 196 (`create_all_done`):

```python
        self.is_reference_tracker: bool = False
```

**Einfüge-Punkt 2:** `calculate_data()`, nach Zeile 1363 (`self.create_sensor = False`):

```python
        # Reference Tracker: Mark device if user configured it as a reference tracker
        reference_trackers_option = self.options.get(CONF_REFERENCE_TRACKERS, [])
        if not isinstance(reference_trackers_option, list):
            reference_trackers_option = []
        reference_trackers = {
            normalize_address(addr) for addr in reference_trackers_option if isinstance(addr, str)
        }
        self.is_reference_tracker = self.address in reference_trackers
```

**Import-Erweiterung:** Am Anfang der Datei `CONF_REFERENCE_TRACKERS` aus `const` importieren.

**Risiko-Mitigation:** Exakt das Muster von Zeile 1337-1340 kopiert (`CONF_DEVICES`), nur mit anderem Options-Key.

---

## 2. Phase 2: Config-Flow UI-Erweiterung (~30 Zeilen)

### 2.1 `config_flow.py` — `async_step_selectdevices()`

**Änderung 1: User-Input-Verarbeitung (Zeile 273-282)**

NACH Zeile 281 (`if isinstance(addr, str) and ...`), VOR Zeile 282 (`self.options.update(user_input)`):

```python
            # Normalize reference tracker addresses (same pattern as CONF_DEVICES)
            if user_input.get(CONF_REFERENCE_TRACKERS):
                user_input[CONF_REFERENCE_TRACKERS] = [
                    normalize_address(addr)
                    for addr in user_input[CONF_REFERENCE_TRACKERS]
                    if isinstance(addr, str)
                ]
            else:
                user_input[CONF_REFERENCE_TRACKERS] = []
```

**Änderung 2: Default-Selektion und Schema (Zeile 404-413)**

NACH Zeile 406, VOR Zeile 408 (`data_schema = {`):

```python
        # Reference tracker selection - only from already-configured devices
        reference_trackers_option = self.options.get(CONF_REFERENCE_TRACKERS, [])
        if not isinstance(reference_trackers_option, list):
            reference_trackers_option = []
        configured_reference_trackers = {
            normalize_address(addr) for addr in reference_trackers_option if isinstance(addr, str)
        }

        # Build options list restricted to configured + auto-configured devices
        ref_options_list: list[SelectOptionDict] = []
        for opt in options_list:
            addr = normalize_address(opt["value"])
            if addr in configured_devices or addr in auto_configured_addresses:
                ref_options_list.append(opt)
```

**Schema erweitern (Zeile 408-413):** Die `data_schema` Variable um ein zweites Feld ergänzen:

```python
        data_schema = {
            vol.Optional(
                CONF_DEVICES,
                default=default_selection,
            ): SelectSelector(SelectSelectorConfig(options=options_list, multiple=True)),
            vol.Optional(
                CONF_REFERENCE_TRACKERS,
                default=sorted(configured_reference_trackers),
            ): SelectSelector(SelectSelectorConfig(options=ref_options_list, multiple=True)),
        }
```

**Import-Erweiterung:** `CONF_REFERENCE_TRACKERS` aus `const` importieren.

**Risiko-Mitigation:**
- Zweites Multi-Select nutzt nur `ref_options_list` (auf konfigurierte Geräte beschränkt).
- Default nutzt `.get(CONF_REFERENCE_TRACKERS, [])` für Abwärtskompatibilität.
- Leer-Default `[]` wenn Key nicht existiert.

### 2.2 Translations

**`translations/en.json` — `selectdevices` Schritt (Zeile 57-60)**

Der `selectdevices`-Schritt hat aktuell KEINE `data`/`data_description` Keys (nur `title` und `description`). Hinzufügen:

```json
      "selectdevices": {
        "title": "Select Devices",
        "description": "Choose which devices you wish to track. ...(bestehend)...",
        "data": {
          "configured_devices": "Devices to Track",
          "reference_trackers": "Reference Trackers (stationary devices)"
        },
        "data_description": {
          "configured_devices": "Select which Bluetooth devices or Beacons to track with Sensors.",
          "reference_trackers": "Select devices that are permanently placed in a specific room. Their signal data improves room fingerprint quality. Multiple trackers in the same room are automatically aggregated via median."
        }
      },
```

**`translations/de.json`:** Analoge Übersetzung ins Deutsche.

**Wichtig:** Der Key `configured_devices` muss dem `vol.Optional(CONF_DEVICES, ...)` Key entsprechen, also `CONF_DEVICES = "configured_devices"` (Zeile 426). Prüfen, dass `selectdevices` Step jetzt `data` Keys bekommt — bisher hatte nur `globalopts` welche.

---

## 3. Phase 3: `_ReferenceTrackerProxy` Dataclass (~25 Zeilen)

### 3.1 `area_selection.py` — Proxy-Klasse

**Einfüge-Punkt:** Vor der Klasse `AreaSelectionHandler` (z.B. nach den bestehenden Dataclass-Definitionen wie `ScannerOnlineStatus`). Die Datei hat bereits Dataclasses am Anfang.

```python
@dataclass
class _ReferenceTrackerProxy:
    """Lightweight proxy for aggregated reference tracker data.

    Mimics the minimal BermudaDevice interface needed by
    _update_device_correlations() without creating a full BermudaDevice.
    """

    address: str  # "ref:<area_id>"
    name: str  # "Reference Tracker (<area_name>)"
    area_id: str | None = None
    area_changed_at: float = 0.0
    adverts: dict[str, Any] = field(default_factory=dict)
    co_visibility_stats: dict[str, Any] = field(default_factory=dict)
    co_visibility_min_samples: int = 50

    def get_movement_state(self, *, stamp_now: float | None = None) -> str:
        """Always stationary — reference trackers don't move."""
        return MOVEMENT_STATE_STATIONARY

    def get_dwell_time(self, *, stamp_now: float | None = None) -> float:
        """Always long dwell — reference trackers are permanently placed."""
        return 86400.0  # 24 hours
```

**Mypy-Risiko:** `_update_device_correlations()` nimmt `device: BermudaDevice`. Die Proxy-Klasse ist kein echtes `BermudaDevice` und wird daher mypy-strict verletzen.

**Lösung:** Entweder:
1. Ein `Protocol` definieren (z.B. `CorrelationDevice`) mit den genutzten Attributen
2. Oder `device: BermudaDevice | _ReferenceTrackerProxy` als Union-Type
3. Oder `cast()` am Aufruf-Ort

**Empfehlung:** Option 1 (Protocol) ist am saubersten, aber erfordert ~20 Zeilen mehr. Option 3 (cast) ist am einfachsten für den initialen PR:

```python
from typing import cast
# Am Aufruf-Ort:
self._update_device_correlations(
    device=cast(BermudaDevice, proxy),
    ...
)
```

---

## 4. Phase 4: `_aggregate_reference_tracker_readings()` (~80 Zeilen)

### 4.1 `area_selection.py` — Neue Methode

**Einfüge-Punkt:** In der Klasse `AreaSelectionHandler`, nach `__init__` und vor den Property-Accessors (~Zeile 452). Oder besser: Vor `_update_reference_tracker_learning()` (wird in Phase 5 erstellt), also nahe dem Haupteinsprungpunkt `refresh_areas_by_min_distance()` (~Zeile 1435).

**Empfehlung:** Direkt VOR `refresh_areas_by_min_distance()` (Zeile 1437), da die Methode dort aufgerufen wird.

```python
    def _aggregate_reference_tracker_readings(
        self,
        nowstamp: float,
    ) -> dict[str, tuple[float, str | None, dict[str, float], dict[str, float]]]:
        """Aggregate RSSI from reference trackers, grouped by area. One entry per area."""
        result: dict[str, tuple[float, str | None, dict[str, float], dict[str, float]]] = {}

        # Step 1: Group reference trackers by area_id
        ref_by_area: dict[str, list[BermudaDevice]] = {}
        for device in self.devices.values():
            if not getattr(device, "is_reference_tracker", False):
                continue
            if device.area_id is None:
                continue
            ref_by_area.setdefault(device.area_id, []).append(device)

        if not ref_by_area:
            return result

        # Step 2: Per area, collect all RSSI readings and compute median
        for area_id, trackers in ref_by_area.items():
            scanner_rssi_lists: dict[str, list[float]] = {}
            scanner_stamps: dict[str, float] = {}

            for tracker in trackers:
                for advert in tracker.adverts.values():
                    if (
                        advert.rssi is not None
                        and advert.stamp is not None
                        and nowstamp - advert.stamp < EVIDENCE_WINDOW_SECONDS
                        and advert.scanner_address is not None
                    ):
                        scanner_rssi_lists.setdefault(advert.scanner_address, []).append(advert.rssi)
                        scanner_stamps[advert.scanner_address] = max(
                            scanner_stamps.get(advert.scanner_address, 0.0),
                            advert.stamp,
                        )

            if not scanner_rssi_lists:
                continue

            # Step 3: Compute per-scanner median
            scanner_medians: dict[str, float] = {
                addr: statistics.median(rssis) for addr, rssis in scanner_rssi_lists.items()
            }

            # Step 4: Determine primary scanner (strongest median)
            primary_addr = max(scanner_medians, key=scanner_medians.get)
            primary_rssi = scanner_medians[primary_addr]
            other_readings = {k: v for k, v in scanner_medians.items() if k != primary_addr}

            result[area_id] = (primary_rssi, primary_addr, other_readings, scanner_stamps)

        return result
```

**Import:** `import statistics` am Anfang der Datei.

**Risiko-Mitigation:**
- `statistics.median()` ist in der Python stdlib, kein Extra-Dependency.
- `EVIDENCE_WINDOW_SECONDS` ist bereits in `const.py` definiert und im File verwendet.
- `getattr(device, "is_reference_tracker", False)` ist defensiv — falls ein Device das Attribut noch nicht hat (cold start), wird es ignoriert.

---

## 5. Phase 5: `_update_reference_tracker_learning()` (~40 Zeilen)

### 5.1 `area_selection.py` — Neue Methode

**Einfüge-Punkt:** Direkt nach `_aggregate_reference_tracker_readings()`.

```python
    def _update_reference_tracker_learning(self, nowstamp: float) -> None:
        """Perform one aggregated auto-learning update per area from reference trackers.

        Called once per coordinator cycle, BEFORE individual device learning.
        N reference trackers in the same room produce exactly ONE learning update.
        """
        aggregated = self._aggregate_reference_tracker_readings(nowstamp)

        if not aggregated:
            return

        for area_id, (primary_rssi, primary_addr, other_readings, stamps) in aggregated.items():
            device_key = f"{REFERENCE_TRACKER_DEVICE_PREFIX}{area_id}"

            # Resolve area name for logging/diagnostics
            area_name = self.resolve_area_name(area_id) or area_id

            # Create lightweight proxy that mimics BermudaDevice interface
            proxy = _ReferenceTrackerProxy(
                address=device_key,
                name=f"Reference Tracker ({area_name})",
                area_id=area_id,
            )

            # Call shared learning method with elevated confidence
            # Filter 3 (Movement State) will be bypassed via ref: prefix check
            self._update_device_correlations(
                device=cast(BermudaDevice, proxy),
                area_id=area_id,
                primary_rssi=primary_rssi,
                primary_scanner_addr=primary_addr,
                other_readings=other_readings,
                nowstamp=nowstamp,
                confidence=REFERENCE_TRACKER_CONFIDENCE,
            )
```

**Import:** `from typing import cast` (falls noch nicht importiert).

### 5.2 Neue Instance-Variable in `__init__`

**Einfüge-Punkt:** Zeile 451 (nach `self._cycle_offline_addrs`):

```python
        # Reference tracker diagnostic data (last aggregation results)
        self._last_ref_tracker_aggregation: dict[str, tuple[float, str | None, dict[str, float], dict[str, float]]] = {}
```

Diese Variable speichert die letzte Aggregation für Diagnostik-Zwecke.

---

## 6. Phase 6: Filter 3 Bypass (~5 Zeilen)

### 6.1 `area_selection.py` — `_update_device_correlations()`

**Änderung an Zeile 1086:** Der bestehende Code:

```python
        skip_reason = self._check_movement_state_for_learning(device, nowstamp)
        if skip_reason is not None:
```

Wird ersetzt durch:

```python
        # Reference Tracker Proxy devices bypass the movement state check.
        # They are explicitly marked as stationary by the user (L-08).
        is_reference_device = device.address.startswith(REFERENCE_TRACKER_DEVICE_PREFIX)
        if not is_reference_device:
            skip_reason = self._check_movement_state_for_learning(device, nowstamp)
            if skip_reason is not None:
                if nowstamp is not None:
                    self._auto_learning_stats.record_update(
                        performed=False,
                        stamp=nowstamp,
                        device_address=device.address,
                        skip_reason=skip_reason,
                    )
                return
```

**Risiko-Mitigation:**
- Nur `device.address.startswith("ref:")` wird geprüft — kein Attribut-Zugriff auf das Device.
- Alle anderen Filter (4-7) bleiben unverändert aktiv.
- Normaler `is_reference_tracker=True` Devices durchlaufen Filter 3 weiterhin (nur das aggregierte `ref:` Proxy-Device bypassed).

**Wichtig:** Der Bypass gilt NUR für das Proxy-Device (`ref:area.kitchen`), NICHT für die echten Referenz-Tracker-Devices (`aa:bb:cc:...`). Die echten Geräte durchlaufen alle Filter normal — sie lernen separat mit normaler Konfidenz (~0.55-0.65).

---

## 7. Phase 7: Aufruf in Entry-Point (~3 Zeilen)

### 7.1 `area_selection.py` — `refresh_areas_by_min_distance()`

**Einfüge-Punkt:** Zeile 1448 (nach `self._cycle_offline_addrs = ...`), VOR Zeile 1450 (`has_mature_profiles`):

```python
        # Reference Tracker: Aggregated learning BEFORE individual device processing.
        # This ensures N trackers in the same room produce exactly ONE learning update.
        self._update_reference_tracker_learning(nowstamp)
```

**Begründung der Reihenfolge:**
1. Scanner-Status aktualisieren (`_update_scanner_online_status`) — Phase 0
2. Offline-Addrs cachen (`_get_offline_scanner_addrs`) — für Filter 7
3. **NEU: Referenz-Tracker Learning** — nutzt Scanner-Status für Filter 7
4. Individuelle Devices verarbeiten (bestehend)

---

## 8. Phase 8: Diagnostik (~20 Zeilen)

### 8.1 `area_selection.py` — Neue Methode

```python
    def get_reference_tracker_diagnostics(self) -> dict[str, Any]:
        """Return diagnostic info about reference tracker state."""
        configured = self.options.get(CONF_REFERENCE_TRACKERS, [])
        if not isinstance(configured, list):
            configured = []

        aggregation: dict[str, Any] = {}
        for area_id, (primary_rssi, primary_addr, other_readings, stamps) in self._last_ref_tracker_aggregation.items():
            tracker_count = sum(
                1 for d in self.devices.values()
                if getattr(d, "is_reference_tracker", False) and d.area_id == area_id
            )
            aggregation[area_id] = {
                "tracker_count": tracker_count,
                "primary_scanner": primary_addr,
                "primary_rssi": primary_rssi,
                "other_readings": other_readings,
            }

        return {
            "configured_count": len(configured),
            "configured_addresses": configured,
            "aggregation_by_area": aggregation,
        }
```

**Und in `_update_reference_tracker_learning()`:** Cache aktualisieren:

```python
        # Am Ende der Methode, nach der for-Schleife:
        self._last_ref_tracker_aggregation = aggregated
```

### 8.2 `diagnostics.py` — Einbindung

**Einfüge-Punkt:** Zeile 37 (nach `auto_learning`), VOR `devices`:

```python
        "reference_trackers": coordinator.area_selection.get_reference_tracker_diagnostics(),
```

---

## 9. Phase 9: Tests (~400 Zeilen)

### 9.1 `tests/test_reference_tracker.py`

Die Test-Struktur ist in der Spezifikation (Abschnitt 4.1-4.2) vollständig definiert:

| Test-Klasse | Anzahl Tests | Abdeckung |
|-------------|-------------|-----------|
| `TestConfiguration` | 4 (T-CFG-01 bis T-CFG-04) | Device-Flag, Options-Defaults |
| `TestAggregation` | 9 (T-AGG-01 bis T-AGG-09) | Median, Timestamps, Edge-Cases |
| `TestFilterBehavior` | 7 (T-FLT-01 bis T-FLT-07) | Filter-Bypass, Filter-Aktiv |
| `TestLearningRateInvariance` | 4 (T-RATE-01 bis T-RATE-04) | 1 vs N Tracker |
| `TestErrorScenarios` | 6 (T-ERR-01 bis T-ERR-06) | Fehlkonfiguration, Ausfall |
| `TestBackwardCompatibility` | 4 (T-BWC-01 bis T-BWC-04) | Keine Regression |
| `TestIntegration` | 4 (T-INT-01 bis T-INT-04) | End-to-End |
| **Total** | **38 Tests** | |

**Fixture-Muster:** Aus der Spezifikation (Zeile 670-733). Nutzt `FakeRefDevice`, `FakeAdvert`, `FakeKalman` und `_make_handler()`.

**Kritischer Test:** `test_one_vs_five_trackers_same_rate` (T-RATE-02) validiert die Kern-Anforderung L-04/L-06 (Lernrate-Invarianz).

---

## 10. Phase 10: CLAUDE.md Dokumentation (~100 Zeilen)

### 10.1 Neuer Abschnitt "Reference Tracker System"

- Architektur-Übersicht (ASCII-Diagramm)
- Datenfluss: Config → Device-Flag → Aggregation → Learning
- Konstanten-Tabelle
- Dateien und Methoden
- Lesson Learned (falls nötig)

---

## Zusammenfassung: Geänderte Dateien

| Datei | Änderungstyp | Umfang | Risiko |
|-------|-------------|--------|--------|
| `const.py` | 3 neue Konstanten | ~5 Zeilen | Minimal |
| `bermuda_device.py` | 1 neues Attribut + Flag-Setting | ~10 Zeilen | Minimal |
| `config_flow.py` | Zweites Multi-Select | ~25 Zeilen | Niedrig (Risiko 1-3 mitigiert) |
| `area_selection.py` | Proxy + 3 neue Methoden + Filter-Bypass | ~180 Zeilen | Mittel (Kernlogik) |
| `diagnostics.py` | 1 neue Zeile | ~1 Zeile | Minimal |
| `translations/en.json` | `data`/`data_description` für selectdevices | ~8 Zeilen | Minimal |
| `translations/de.json` | Deutsche Übersetzung | ~8 Zeilen | Minimal |
| `tests/test_reference_tracker.py` | Neue Test-Datei | ~400 Zeilen | Minimal |
| `CLAUDE.md` | Dokumentation | ~100 Zeilen | Minimal |
| **Total** | | **~740 Zeilen** | |

---

## Abhängigkeitsreihenfolge (kritischer Pfad)

```
Phase 1 (const.py, bermuda_device.py)
    ↓ Phase 2 braucht CONF_REFERENCE_TRACKERS
Phase 2 (config_flow.py, translations)
    ↓ Phase 3-7 brauchen UI für Konfiguration
Phase 3 (_ReferenceTrackerProxy)
    ↓ Phase 5 braucht Proxy-Klasse
Phase 4 (_aggregate_reference_tracker_readings)
    ↓ Phase 5 ruft Aggregation auf
Phase 5 (_update_reference_tracker_learning)
    ↓ Phase 7 ruft Learning auf
Phase 6 (Filter 3 Bypass)
    ↓ unabhängig, aber Phase 5 braucht es
Phase 7 (Aufruf in refresh_areas_by_min_distance)
    ↓ aktiviert das Feature
Phase 8 (Diagnostik) — unabhängig
Phase 9 (Tests) — nach allen Code-Änderungen
Phase 10 (CLAUDE.md) — zuletzt
```

**Empfehlung:** Phasen 1-7 sequentiell, dann 8-10 parallel.

---

## Validierungs-Checklist (vor jedem Commit)

```bash
# 1. Linting und Formatierung
python -m ruff check --fix
python -m ruff format

# 2. Type-Checking
python -m mypy --strict --install-types --non-interactive

# 3. Tests
python -m pytest tests/test_reference_tracker.py -v  # Neue Tests
python -m pytest --cov -q                             # Alle Tests

# 4. Bestehende Tests unverändert grün
python -m pytest tests/ -k "not test_reference_tracker" -q
```
