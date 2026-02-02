# HA-Recorder Peer Review: Bermuda BLE Trilateration

**Datum:** 2026-02-02
**Kontext:** User berichtet ~1 GB/Tag Datenbankwachstum auf RPi4 mit SD-Karte. Bermuda-Sensoren sind Top-Verursacher.
**Scope:** Fork (`jleinenbach/bermuda`) UND Upstream (`agittins/bermuda`)

---

## Executive Summary

Die Datenbankbloat-Problematik ist **zu ~60% ein Upstream-Problem** und **zu ~40% ein Fork-spezifisches Problem**. Upstream verwendet bereits `SensorStateClass.MEASUREMENT` auf hochfrequenten Sensoren, was Long-Term-Statistics erzwingt. Der Fork verschärft dies durch kontinuierlich wechselnde `extra_state_attributes` (Altersberechnungen), die bei jedem Coordinator-Zyklus (~1.05s) einen neuen Datenbankeintrag erzwingen.

| Kategorie | Upstream | Fork | Schwere |
|-----------|----------|------|---------|
| `MEASUREMENT` auf Distance/RSSI | Ja | Ja | HOCH |
| Per-Scanner Entities (N×M×2) | Ja | Ja | HOCH |
| `area_state_metadata()` in Attributen | **Nein** | **Ja** | KRITISCH |
| `_unrecorded_attributes` fehlt | Ja | Ja | MITTEL |
| `_handle_coordinator_update` immer | Ja | Ja | MITTEL |
| Global-Sensoren MEASUREMENT | Ja | Ja | NIEDRIG |

---

## 1. Befunde im Detail

### 1.1 KRITISCH (Fork-spezifisch): `area_state_metadata()` erzwingt DB-Writes

**Datei:** `sensor.py:227-228`, `bermuda_device.py:848-868`

```python
# sensor.py:227-228 - Wird für Area, Floor UND Distance aufgerufen
if self.name in ["Area", "Floor", "Distance"]:
    attribs.update(self._device.area_state_metadata())
```

`area_state_metadata()` gibt 6 Attribute zurück, von denen 3 sich **bei jedem Aufruf** ändern:

| Attribut | Typ | Ändert sich | Problem |
|----------|-----|-------------|---------|
| `last_good_area_age_s` | float | **Jede Sekunde** | Monoton steigend |
| `last_good_distance_age_s` | float | **Jede Sekunde** | Monoton steigend |
| `area_retention_seconds_remaining` | float | **Jede Sekunde** | Monoton fallend |
| `area_is_stale` | bool | Selten | OK |
| `area_retained` | bool | Selten | OK |
| `area_source` | str | Selten | OK |

**Auswirkung:** HA hasht `extra_state_attributes` zur Deduplizierung. Da sich die float-Werte
jede Sekunde ändern, ergibt jeder Hash ein neues Ergebnis → neuer `state_attributes`-Eintrag
in der DB. Das betrifft 3 standardmäßig aktivierte Entitäten pro Gerät (Area, Floor, Distance).

**Rechnung:**
- 10 Geräte × 3 Entitäten × 1 Write/1.05s × 86.400s/Tag = **~247.000 DB-Writes/Tag**
- Bei ~4 KB pro Eintrag (Attribute JSON) = ~1 GB/Tag ✓

**Upstream-Vergleich:** Upstream hat `area_state_metadata()` NICHT. Die Attribute dort sind
statisch (area_id, area_name, floor_id, floor_name, floor_level, current_mac) und ändern
sich nur bei tatsächlichem Raumwechsel.

### 1.2 HOCH (Upstream-Problem): `SensorStateClass.MEASUREMENT` auf BLE-Sensoren

**Dateien:** `sensor.py:319-321` (RSSI), `sensor.py:357-359` (Distance)

```python
# Beide Sensoren:
@property
def state_class(self) -> str:
    return SensorStateClass.MEASUREMENT
```

HA erzeugt für `MEASUREMENT`-Sensoren:
- **Short-Term Statistics:** Alle 5 Minuten (min/max/mean)
- **Long-Term Statistics:** Stündlich (min/max/mean)
- Zusätzlich zu den normalen `states`-Tabellen-Einträgen

Für BLE-RSSI und -Distance sind Long-Term-Statistics sinnlos:
- RSSI schwankt ständig um 3-5 dB → Statistiken sind Rauschen
- Distance schwankt ebenso → min/max sind bedeutungslos
- Kein User grapht BLE-RSSI über Wochen

**HA Best Practice:** `SensorStateClass.MEASUREMENT` sollte nur für Sensoren verwendet werden,
bei denen Langzeit-Statistiken sinnvoll sind (Temperatur, Energieverbrauch, etc.).
Für volatile BLE-Daten: `state_class = None` (kein Attribut setzen).

### 1.3 HOCH (Upstream-Problem): Per-Scanner Entities skalieren O(N×M)

**Datei:** `sensor.py:100-130`

Für jedes (Gerät, Scanner)-Paar werden 2 Entitäten erzeugt:
- `BermudaSensorScannerRange` (gefiltert)
- `BermudaSensorScannerRangeRaw` (ungefiltert)

| Setup | Geräte (N) | Scanner (M) | Entitäten (2×N×M) | Enabled by Default? |
|-------|-----------|------------|-------------------|-------------------|
| Klein | 5 | 3 | 30 | Nein |
| Mittel | 20 | 5 | 200 | Nein |
| Groß | 50 | 10 | 1.000 | Nein |

**Positiv:** Diese Entitäten sind standardmäßig DEAKTIVIERT (`entity_registry_enabled_default`
gibt nur für "Area", "Distance", "Floor" `True` zurück). ABER: Sobald ein User auch nur einen
per-Scanner-Sensor aktiviert, bekommt er `SensorStateClass.MEASUREMENT` mit voller Statistik-Last.

**`BermudaSensorScannerRangeRaw` hat KEIN Rate-Limiting** (`sensor.py:431-441`):
```python
def native_value(self) -> str | None:
    # Kein _cached_ratelimit() Aufruf!
    distance = getattr(devscanner, "rssi_distance_raw", None)
    if distance is not None:
        return round(distance, 3)
```

### 1.4 MITTEL (Upstream-Problem): Fehlende `_unrecorded_attributes`

HA bietet seit September 2023 das `_unrecorded_attributes` Klassen-Attribut:

```python
from homeassistant.const import MATCH_ALL

class MyEntity(SensorEntity):
    # Schließt ALLE extra_state_attributes vom Recording aus
    _unrecorded_attributes = frozenset({MATCH_ALL})
```

Bermuda nutzt dieses Feature NICHT. Alle Attribute werden vollständig in die DB geschrieben.

**HA Best Practice (seit 2024-06):** Volatile Attribute, die nur für die aktuelle
Anzeige benötigt werden, MÜSSEN über `_unrecorded_attributes` ausgeschlossen werden.

Referenz: [HA Dev Blog - Excluding State Attributes](https://developers.home-assistant.io/blog/2024/06/22/excluding-state-attributes-from-recording-match-all/)

### 1.5 MITTEL (Upstream-Problem): `_handle_coordinator_update` schreibt immer

**Datei:** `entity.py:97-111`

```python
@callback
def _handle_coordinator_update(self) -> None:
    # ...
    self.async_write_ha_state()  # Zeile 111 - IMMER aufgerufen
```

Bei jedem Coordinator-Zyklus (~1.05s) wird `async_write_ha_state()` für JEDE Entität
aufgerufen. HA's State-Machine filtert zwar Writes, bei denen sich State+Attribute nicht
geändert haben, aber durch die Fork-spezifischen zeitbasierten Attribute (1.1) wird
dieser Filter umgangen.

### 1.6 NIEDRIG (Upstream-Problem): Global-Sensoren mit MEASUREMENT

**Datei:** `sensor.py:520-595`

4 Global-Sensoren (Proxy Count, Active Proxy Count, Device Count, Visible Device Count)
verwenden `SensorStateClass.MEASUREMENT`. Diese haben aber 60s Rate-Limiting und ändern sich
selten → geringer Einfluss.

---

## 2. Hochrechnung: DB-Impact

### Szenario: 15 Geräte, 5 Scanner, Fork

| Entität | Anzahl | Enabled | Freq | State Class | Writes/Tag | GB/Tag |
|---------|--------|---------|------|-------------|-----------|--------|
| Area + Metadata* | 15 | Ja | 1.05s | None | 1.234.286 | ~0.35 |
| Floor + Metadata* | 15 | Ja | 1.05s | None | 1.234.286 | ~0.35 |
| Distance + Metadata* | 15 | Ja | 10s | MEASUREMENT | 129.600 | ~0.12 |
| RSSI | 15 | **Nein** | 10s | MEASUREMENT | 0 | 0 |
| Scanner | 15 | **Nein** | 10s | None | 0 | 0 |
| Per-Scanner Distance | 150 | **Nein** | 10s | MEASUREMENT | 0 | 0 |
| Device Tracker | 15 | Ja | change | None | ~1.500 | <0.01 |
| Global Sensors | 4 | Ja | 60s | MEASUREMENT | 5.760 | <0.01 |
| **Gesamt** | | | | | **~2.605.432** | **~0.82** |

*\*Metadata = `area_state_metadata()` mit zeitbasierten Attributen, erzwingt Write bei jedem Zyklus*

**Ohne Fork-Metadata (wie Upstream):** Area/Floor würden nur bei Raumwechsel schreiben → ~0.12 GB/Tag statt ~0.82 GB/Tag.

---

## 3. HA Best Practices Checkliste

| Best Practice | Status | Empfehlung |
|--------------|--------|------------|
| `_unrecorded_attributes` für volatile Attribute | ❌ Fehlt | `MATCH_ALL` oder selektiv |
| `state_class` nur wo Statistik sinnvoll | ❌ MEASUREMENT auf BLE-Daten | Entfernen für Distance/RSSI |
| Diagnostic Entities disabled by default | ✅ AreaSwitchReason | OK |
| Per-Scanner disabled by default | ✅ Über name-Check | OK |
| Rate-Limiting für hochfrequente Sensoren | ⚠️ Teilweise | Raw-Range hat kein Limiting |
| Keine zeitbasierten Attribute in `extra_state_attributes` | ❌ Fork-spezifisch | In separate Diagnostic-Entity oder `_unrecorded_attributes` |
| `entity_category = DIAGNOSTIC` für Debug-Sensoren | ✅ Teilweise | Erweitern |

---

## 4. Empfehlungen (priorisiert)

### P0: Sofort-Fixes (Upstream-kompatibel)

#### 4.1 `_unrecorded_attributes` mit `MATCH_ALL` für volatile Entitäten

```python
from homeassistant.const import MATCH_ALL

class BermudaSensor(BermudaEntity, SensorEntity):
    _unrecorded_attributes = frozenset({MATCH_ALL})
```

**Warum `MATCH_ALL`?** Bermuda-Attribute sind primär für die Live-UI gedacht, nicht für
historische Auswertung. `area_id`, `area_name` etc. werden über den State-Wert des
Area-Sensors abgedeckt. Die Attribute in der Historie zu speichern bringt keinen Mehrwert,
verbraucht aber massiv Speicher.

**Betroffene Klassen:**
- `BermudaSensor` (und alle Subklassen)
- `BermudaDeviceTracker`
- `BermudaSensorScannerRange`

**Ausnahme:** `BermudaSensorAreaSwitchReason` ist bereits disabled by default.

#### 4.2 `SensorStateClass.MEASUREMENT` entfernen für BLE-Sensoren

```python
class BermudaSensorRange(BermudaSensor):
    # KEIN state_class → keine Long-Term-Statistics
    # State-History bleibt erhalten (normaler Recorder)

class BermudaSensorRssi(BermudaSensor):
    # KEIN state_class → keine Long-Term-Statistics
```

**Begründung:** BLE RSSI und Distance sind nicht für Langzeit-Statistiken geeignet:
- Stochastisches Rauschen (3-5 dB Schwankung)
- Kein User benötigt "durchschnittlicher RSSI der letzten Woche"
- Spart 3 × N Statistik-Einträge pro 5-Minuten-Intervall

**Alternative:** State-Class als Konfigurations-Option anbieten (siehe 4.5).

#### 4.3 Fork-spezifisch: `area_state_metadata()` aus Attributen entfernen

Die zeitbasierten Attribute (`last_good_area_age_s`, `area_retention_seconds_remaining`)
müssen aus `extra_state_attributes` entfernt werden, da sie bei JEDEM Zyklus einen
neuen DB-Write erzwingen.

**Optionen:**
1. **Komplett entfernen** und in separate Diagnostic-Entity verschieben (disabled by default)
2. **Über `_unrecorded_attributes` ausschließen** (4.1 erledigt das bereits mit `MATCH_ALL`)
3. **Nur nicht-zeitbasierte Attribute behalten** (`area_source`, `area_is_stale`, `area_retained`)

Empfehlung: Option 2 (wird durch P0 Fix 4.1 automatisch gelöst).

### P1: Mittelfristig

#### 4.4 Rate-Limiting für `BermudaSensorScannerRangeRaw`

```python
class BermudaSensorScannerRangeRaw(BermudaSensorScannerRange):
    @property
    def native_value(self) -> str | None:
        devscanner = self._device.get_scanner(self._scanner.address)
        distance = getattr(devscanner, "rssi_distance_raw", None)
        if distance is not None:
            return self._cached_ratelimit(round(distance, 3))  # ← Fehlte!
        return None
```

#### 4.5 Konfigurations-Option: "Datensparsam" / "Recorder-Friendly"

Ein globaler Schalter in den Bermuda-Optionen:

```python
CONF_RECORDER_FRIENDLY = "recorder_friendly"

# In config_flow.py / options_flow:
vol.Optional(CONF_RECORDER_FRIENDLY, default=True): bool,
```

Wenn aktiviert:
- `state_class` wird auf `None` gesetzt (keine Long-Term-Statistics)
- `_unrecorded_attributes = frozenset({MATCH_ALL})` (keine Attribute in DB)
- Area/Floor/Distance updaten nur bei tatsächlicher Änderung (nicht zeitbasiert)

Wenn deaktiviert:
- Verhalten wie bisher (für Power-User mit SSD und viel Speicher)

**Empfehlung:** Default auf `True` (datensparsam), da die meisten User die
Statistiken nicht benötigen und die SD-Karten-Lebensdauer kritisch ist.

### P2: Langfristig

#### 4.6 `_handle_coordinator_update` nur bei Änderung

```python
@callback
def _handle_coordinator_update(self) -> None:
    # Nur schreiben wenn sich State ODER relevante Attribute geändert haben
    if self._has_meaningful_change():
        self.async_write_ha_state()
```

Dies erfordert Tracking des letzten geschriebenen States pro Entity. Komplexer, aber
verhindert unnötige Writes auch für Upstream-kompatible Attribute.

---

## 5. Erwartete Verbesserung

| Fix | Reduktion Writes | Reduktion GB/Tag | Schwierigkeit |
|-----|-----------------|-----------------|---------------|
| 4.1 `_unrecorded_attributes` | ~70% | ~0.57 GB | Trivial (1 Zeile pro Klasse) |
| 4.2 `state_class` entfernen | ~10% | ~0.08 GB | Trivial (2 Properties löschen) |
| 4.3 `area_state_metadata()` | (durch 4.1 abgedeckt) | | |
| 4.4 Raw Rate-Limiting | ~5% (wenn aktiviert) | ~0.04 GB | Trivial |
| 4.5 Config-Option | Variabel | Variabel | Mittel |
| **Gesamt P0** | **~80%** | **~0.65 GB** | **< 1 Stunde** |

**Ergebnis:** Von ~0.82 GB/Tag auf ~0.17 GB/Tag (bei 15 Geräten, 5 Scannern).

---

## 6. Workaround für User (sofort)

Bis die Fixes implementiert sind, können User in `configuration.yaml`:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.*_bermuda_*_area
      - sensor.*_bermuda_*_floor
      - sensor.*_bermuda_*_distance
      - sensor.*_bermuda_*_rssi
      - sensor.*_bermuda_*_range*
```

**Warnung:** Dies entfernt ALLE Bermuda-Sensoren aus der Historie. Für die
Raumverfolgung selbst ist das unproblematisch (funktioniert über den Coordinator),
aber History-Graphen sind dann nicht mehr verfügbar.

---

## 7. Upstream-Meldung

Die Befunde 1.2-1.6 betreffen Upstream gleichermaßen. Es empfiehlt sich:
1. Issue bei `agittins/bermuda` eröffnen mit Verweis auf `_unrecorded_attributes`
2. PR für `state_class`-Entfernung auf Distance/RSSI vorschlagen
3. PR für `_unrecorded_attributes = frozenset({MATCH_ALL})` auf BermudaSensor

---

## Anhang: Referenzen

- [HA Dev: Excluding State Attributes (2023-09)](https://developers.home-assistant.io/blog/2023/09/20/excluding-state-attributes-from-recording/)
- [HA Dev: MATCH_ALL for Excluding Attributes (2024-06)](https://developers.home-assistant.io/blog/2024/06/22/excluding-state-attributes-from-recording-match-all/)
- [HA Dev: Sensor Entity - State Class](https://developers.home-assistant.io/docs/core/entity/sensor/)
- [HA Architecture Discussion #964](https://github.com/home-assistant/architecture/discussions/964)
- [HA Dev: Entity - Excluding Attributes](https://developers.home-assistant.io/docs/core/entity/#excluding-state-attributes-from-recorder-history)
