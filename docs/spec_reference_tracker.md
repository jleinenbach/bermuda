# Pflichtenheft: Referenz-Tracker für Auto-Learning

**Version**: 1.0
**Datum**: 2026-02-08
**Status**: Entwurf
**Feature-Branch**: `claude/reference-tracker-room-changes-AIPWp`

---

## 1. Lastenheft (Was soll erreicht werden?)

### 1.1 Motivation

BLE-Fingerprints können durch Umgebungsänderungen (Personen, Möbel, Türen),
Scanner-Ausfälle oder fehlerhafte Auto-Zuordnungen verfälscht werden. In solchen
Situationen werden Raumzuordnungen mehrdeutig. Ein stationärer BLE-Tracker mit
bekanntem Standort liefert **kontinuierliche Ground Truth** für das Auto-Learning.

### 1.2 Anforderungen (Lastenheft)

| ID   | Anforderung | Priorität |
|------|-------------|-----------|
| L-01 | User kann ein oder mehrere Geräte als "Referenz-Tracker" markieren | MUSS |
| L-02 | Referenz-Tracker beeinflussen **ausschließlich** das Auto-Learning | MUSS |
| L-03 | Referenz-Tracker haben eine höhere Konfidenz als normale Geräte (0.80) | MUSS |
| L-04 | Mehrere Referenz-Tracker im selben Raum wirken wie **ein** Tracker | MUSS |
| L-05 | RSSI-Werte aus mehreren Trackern werden per Median aggregiert | MUSS |
| L-06 | Pro Raum, pro Koordinator-Zyklus gibt es maximal **ein** aggregiertes Learning-Update | MUSS |
| L-07 | Alle existierenden Quality-Filter (4-7) bleiben aktiv | MUSS |
| L-08 | Nur Quality-Filter 3 (Movement-State, 10 Min stationär) wird für Referenz-Tracker umgangen | MUSS |
| L-09 | Falsch konfigurierte Referenz-Tracker dürfen das System nicht dauerhaft beschädigen | MUSS |
| L-10 | Feature ist abwärtskompatibel (keine Migration, keine Breaking Changes) | MUSS |
| L-11 | UI-Konfiguration über den existierenden Options-Flow | SOLL |
| L-12 | Diagnostik zeigt Referenz-Tracker-Status und Aggregation | SOLL |
| L-13 | Referenz-Tracker-Daten werden nicht separat persistiert (nutzen existierenden CorrelationStore) | SOLL |

### 1.3 Nicht-Ziele (Scope-Begrenzung)

| ID   | Ausschluss | Begründung |
|------|------------|------------|
| NZ-1 | Referenz-Tracker beeinflussen **nicht** die Raumwechsel-Entscheidung direkt | Vermeidet zirkuläre Feedback-Loops |
| NZ-2 | Kein separater Persistenz-Layer für Referenz-Daten | Nutzt existierenden CorrelationStore |
| NZ-3 | Keine per-Device-Konfiguration (Raum-Zuweisung etc.) | Referenz-Tracker nutzt den aktuell erkannten Raum |
| NZ-4 | Kein automatisches Erkennen von "stationären" Geräten | User muss explizit markieren |
| NZ-5 | Keine Änderung an Button-Training, UKF-Matching oder Min-Distance | Feature wirkt nur auf Auto-Learning-Pipeline |

---

## 2. Pflichtenheft (Wie wird es umgesetzt?)

### 2.1 Architektur-Übersicht

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    Referenz-Tracker Auto-Learning Flow                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Phase 0: KONFIGURATION (config_flow.py)                                        │
│  ┌────────────────────────────────────────────────────────────────────────────┐ │
│  │ Options-Flow → async_step_selectdevices()                                  │ │
│  │ Neues Multi-Select: CONF_REFERENCE_TRACKERS                                │ │
│  │ → Speichert Liste von Device-Adressen in self.options                     │ │
│  └────────────────────────────────────────────────────────────────────────────┘ │
│                                │                                                 │
│                                ▼                                                 │
│  Phase 1: DEVICE-MARKIERUNG (bermuda_device.py)                                 │
│  ┌────────────────────────────────────────────────────────────────────────────┐ │
│  │ In calculate_data():                                                        │ │
│  │   reference_trackers = options.get(CONF_REFERENCE_TRACKERS, [])            │ │
│  │   self.is_reference_tracker = self.address in reference_trackers           │ │
│  └────────────────────────────────────────────────────────────────────────────┘ │
│                                │                                                 │
│                                ▼                                                 │
│  Phase 2: AGGREGATION (area_selection.py — NEU)                                 │
│  ┌────────────────────────────────────────────────────────────────────────────┐ │
│  │ _aggregate_reference_tracker_readings()                                    │ │
│  │                                                                             │ │
│  │ Schritt 1: Alle Referenz-Tracker pro Raum gruppieren                       │ │
│  │   ref_by_area = {                                                           │ │
│  │     "area.kitchen": [device1, device2, device3],                           │ │
│  │     "area.office":  [device4],                                              │ │
│  │   }                                                                         │ │
│  │                                                                             │ │
│  │ Schritt 2: Pro Raum, pro Scanner RSSI-Median berechnen                     │ │
│  │   area "kitchen" hat 3 Tracker, jeder sieht Scanner A und B:              │ │
│  │     Scanner A: [-52, -58, -55] → median = -55                             │ │
│  │     Scanner B: [-71, -68, -73] → median = -71                             │ │
│  │                                                                             │ │
│  │ Schritt 3: Stärksten Scanner als primary bestimmen                        │ │
│  │   primary_rssi = -55 (Scanner A), primary_addr = "scanner_a"              │ │
│  │   other_readings = {"scanner_b": -71}                                      │ │
│  │                                                                             │ │
│  │ Schritt 4: Timestamps aggregieren (neuester pro Scanner)                   │ │
│  │   current_stamps = {"scanner_a": max(stamp_a1, stamp_a2, stamp_a3), ...}  │ │
│  └────────────────────────────────────────────────────────────────────────────┘ │
│                                │                                                 │
│                                ▼                                                 │
│  Phase 3: LEARNING (area_selection.py — bestehend, minimal modifiziert)         │
│  ┌────────────────────────────────────────────────────────────────────────────┐ │
│  │ _update_device_correlations() wird für JEDEN Referenz-Tracker aufgerufen  │ │
│  │ → Aber: Ref-Tracker werden in Phase 2 schon aggregiert!                   │ │
│  │                                                                             │ │
│  │ STATTDESSEN: Neuer Einsprungpunkt _update_reference_tracker_learning()    │ │
│  │ → Ruft _update_device_correlations() EINMAL pro Raum auf                  │ │
│  │ → Mit aggregierten RSSI-Werten                                             │ │
│  │ → Mit confidence = REFERENCE_TRACKER_CONFIDENCE (0.80)                     │ │
│  │ → Mit speziellem Device-Key: "ref:<area_id>" (virtuelle Identität)        │ │
│  │                                                                             │ │
│  │ Quality-Filter-Verhalten:                                                  │ │
│  │ ┌──────────────────────────────────────────────────────────────────────┐   │ │
│  │ │ Filter 2 (Confidence ≥ 0.5):  PASS (0.80 > 0.50)     ← normal     │   │ │
│  │ │ Filter 3 (10 Min stationär):  BYPASS                  ← einzig     │   │ │
│  │ │ Filter 4 (Velocity < 1 m/s):  AKTIV                   ← Schutz     │   │ │
│  │ │ Filter 5 (RSSI Varianz):      AKTIV                   ← Schutz     │   │ │
│  │ │ Filter 6 (Ambiguity):         AKTIV                   ← Schutz     │   │ │
│  │ │ Filter 7 (Scanner Offline):   AKTIV                   ← Schutz     │   │ │
│  │ └──────────────────────────────────────────────────────────────────────┘   │ │
│  └────────────────────────────────────────────────────────────────────────────┘ │
│                                │                                                 │
│                                ▼                                                 │
│  Phase 4: ERGEBNIS — Fingerprint im CorrelationStore                            │
│  ┌────────────────────────────────────────────────────────────────────────────┐ │
│  │ correlations["ref:area.kitchen"]["area.kitchen"] = AreaProfile(...)        │ │
│  │                                                                             │ │
│  │ → Wird von UKF match_fingerprints() wie jedes andere AreaProfile           │ │
│  │   gelesen, aber NICHT als Device-spezifisch betrachtet                     │ │
│  │ → Fließt über den normalen Weg in die Fingerprint-Matching-Pipeline       │ │
│  └────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Komponenten-Änderungen

#### 2.2.1 `const.py` — Neue Konstanten

```python
# Reference Tracker Configuration
CONF_REFERENCE_TRACKERS = "reference_trackers"          # Options key: list of addresses
REFERENCE_TRACKER_CONFIDENCE = 0.80                     # Konfidenz-Floor (80%)
REFERENCE_TRACKER_DEVICE_PREFIX = "ref:"                # Prefix für virtuelle Device-Keys
```

**Begründung der Werte:**

| Konstante | Wert | Begründung |
|-----------|------|------------|
| `REFERENCE_TRACKER_CONFIDENCE` | 0.80 | Über normalem Gate (0.50), unter Button-Training (~0.95). Siehe Abschnitt 3.1 |
| `REFERENCE_TRACKER_DEVICE_PREFIX` | `"ref:"` | Verhindert Kollision mit echten MAC-Adressen |

#### 2.2.2 `bermuda_device.py` — Neues Attribut

**Änderung in `__init__()` (~Zeile 232):**
```python
self.is_reference_tracker: bool = False
```

**Änderung in `calculate_data()` (~Zeile 1340):**
```python
reference_trackers_list = self.options.get(CONF_REFERENCE_TRACKERS, [])
self.is_reference_tracker = self.address in reference_trackers_list
```

**Kein neues Property, kein neuer Code-Pfad** — nur ein einfaches Boolean-Flag.

#### 2.2.3 `config_flow.py` — UI-Erweiterung

**Änderung in `async_step_selectdevices()` (~Zeile 407):**

Nach dem bestehenden `CONF_DEVICES` Multi-Select wird ein zweites Multi-Select
hinzugefügt, das nur aus den bereits konfigurierten Geräten wählen lässt:

```python
vol.Optional(
    CONF_REFERENCE_TRACKERS,
    default=self.options.get(CONF_REFERENCE_TRACKERS, []),
): SelectSelector(
    SelectSelectorConfig(
        options=configured_device_options,  # Nur bereits konfigurierte Geräte
        multiple=True,
        mode=SelectSelectorMode.DROPDOWN,
    )
),
```

**Abwärtskompatibilität:** `self.options.get(CONF_REFERENCE_TRACKERS, [])` — fehlendes
Key gibt leere Liste zurück, kein Migration nötig.

#### 2.2.4 `area_selection.py` — Kernlogik (Hauptänderung)

##### Neue Methode: `_aggregate_reference_tracker_readings()`

```python
def _aggregate_reference_tracker_readings(
    self,
    nowstamp: float,
) -> dict[str, tuple[float, str | None, dict[str, float], dict[str, float]]]:
    """
    Aggregate RSSI readings from all reference trackers, grouped by area.

    For each area that has one or more reference trackers:
    1. Collect RSSI readings from ALL reference trackers in that area
    2. Compute per-scanner MEDIAN RSSI
    3. Determine primary scanner (strongest median)
    4. Aggregate timestamps (newest per scanner)

    Returns:
        Dict mapping area_id to tuple of:
        (primary_rssi, primary_scanner_addr, other_readings, current_stamps)

    Only includes areas where at least one reference tracker has fresh data.
    """
```

**Algorithmus:**

```
Eingabe: Alle Geräte mit is_reference_tracker == True

Schritt 1: Gruppierung nach area_id
  ref_by_area: dict[str, list[BermudaDevice]] = {}
  Für jedes Gerät mit is_reference_tracker und area_id != None:
    ref_by_area[area_id].append(device)

Schritt 2: Pro Raum, pro Scanner RSSI sammeln
  Für jeden area_id in ref_by_area:
    scanner_rssi_lists: dict[str, list[float]] = {}
    scanner_stamps: dict[str, float] = {}

    Für jedes device in ref_by_area[area_id]:
      Für jedes advert in device.adverts.values():
        Wenn advert.rssi != None UND advert.stamp frisch:
          scanner_rssi_lists[scanner_addr].append(advert.rssi)
          scanner_stamps[scanner_addr] = max(stamp, vorheriger)

Schritt 3: Median berechnen
  scanner_medians: dict[str, float] = {}
  Für jeden scanner in scanner_rssi_lists:
    scanner_medians[scanner] = statistics.median(rssi_list)

Schritt 4: Primary bestimmen + Rückgabe
  primary_addr = argmax(scanner_medians)  (stärkster Median)
  primary_rssi = scanner_medians[primary_addr]
  other_readings = {k: v for k, v in scanner_medians.items() if k != primary_addr}

  Rückgabe: {area_id: (primary_rssi, primary_addr, other_readings, scanner_stamps)}
```

##### Neue Methode: `_update_reference_tracker_learning()`

```python
def _update_reference_tracker_learning(self, nowstamp: float) -> None:
    """
    Perform one aggregated auto-learning update per area from reference trackers.

    Called once per coordinator cycle, BEFORE individual device learning.
    Ensures that N reference trackers in the same room produce exactly
    ONE learning update (not N updates).
    """
```

**Algorithmus:**

```
Schritt 1: Aggregierte Readings holen
  aggregated = self._aggregate_reference_tracker_readings(nowstamp)

Schritt 2: Pro Raum EIN Learning-Update
  Für jeden area_id, (primary_rssi, primary_addr, other_readings, stamps):
    device_key = REFERENCE_TRACKER_DEVICE_PREFIX + area_id  # z.B. "ref:area.kitchen"

    self._update_device_correlations(
      device=<virtuelles FakeDevice mit device_key>,
      area_id=area_id,
      primary_rssi=primary_rssi,
      primary_scanner_addr=primary_addr,
      other_readings=other_readings,
      nowstamp=nowstamp,
      confidence=REFERENCE_TRACKER_CONFIDENCE,  # 0.80
    )
```

##### Änderung in `_update_device_correlations()`: Filter 3 Bypass

```python
# Quality Filter: Feature 5 - Movement State (Line ~1086)
# CHANGE: Skip movement state check for reference tracker devices
is_reference_device = device.address.startswith(REFERENCE_TRACKER_DEVICE_PREFIX)

if not is_reference_device:
    movement_state = self._check_movement_state_for_learning(device, nowstamp)
    if movement_state != MOVEMENT_STATE_STATIONARY:
        # ... existing skip logic ...
        return
```

##### Änderung in `refresh_areas_by_min_distance()`: Aufruf-Punkt

```python
def refresh_areas_by_min_distance(self) -> None:
    nowstamp = monotonic_time_coarse()
    self._update_scanner_online_status(nowstamp)
    self._cycle_offline_addrs = self._get_offline_scanner_addrs()

    # NEU: Referenz-Tracker Learning VOR individuellem Device-Processing
    self._update_reference_tracker_learning(nowstamp)

    # Bestehend: Individuelle Device-Verarbeitung
    for device in self.coordinator.devices.values():
        # ... existing logic ...
```

##### Auswirkung auf individuelle Referenz-Tracker

Referenz-Tracker durchlaufen weiterhin den normalen Area-Selection-Algorithmus
(UKF + Min-Distance) für ihre eigene Raumzuordnung. Ihr individuelles Auto-Learning
wird NICHT unterdrückt — sie lernen wie jedes andere Gerät auch (mit normaler
Konfidenz ~0.55-0.65).

Das aggregierte Referenz-Update (`ref:<area_id>`) ist ein **zusätzliches**
Learning-Update mit höherer Konfidenz (0.80), das die Fingerprints des Raums
stabilisiert.

#### 2.2.5 `area_selection.py` — Virtuelles Device für Aggregation

Die `_update_device_correlations()` erwartet ein `BermudaDevice`-Objekt. Für die
Aggregation wird ein leichtgewichtiges Hilfsobjekt verwendet:

```python
@dataclass
class _ReferenceTrackerProxy:
    """Lightweight proxy for aggregated reference tracker data.

    Mimics the minimal BermudaDevice interface needed by
    _update_device_correlations() without creating a full BermudaDevice.
    """

    address: str                    # "ref:<area_id>"
    name: str                       # "Reference Tracker (<area_name>)"
    area_id: str | None
    area_changed_at: float = 0.0    # Immer 0 → STATIONARY (aber Filter 3 bypassed)
    adverts: dict = field(default_factory=dict)
    co_visibility_stats: dict = field(default_factory=dict)
    co_visibility_min_samples: int = 50

    def get_movement_state(self, *, stamp_now: float | None = None) -> str:
        return MOVEMENT_STATE_STATIONARY

    def get_dwell_time(self, *, stamp_now: float | None = None) -> float:
        return 86400.0  # 24 Stunden — immer "stationär"
```

#### 2.2.6 `diagnostics.py` — Referenz-Tracker-Status

Im Diagnostics-Output erscheint ein neuer Abschnitt:

```python
"reference_trackers": {
    "configured_count": 3,
    "configured_addresses": ["aa:bb:...", "bb:cc:...", "cc:dd:..."],
    "aggregation_by_area": {
        "area.kitchen": {
            "tracker_count": 2,
            "last_update_stamp": 1234567.89,
            "aggregated_readings": {
                "scanner_a": -55.0,  # Median
                "scanner_b": -71.0,
            },
        },
    },
}
```

#### 2.2.7 `translations/en.json` und `translations/de.json`

```json
{
  "config": {
    "step": {
      "selectdevices": {
        "data": {
          "reference_trackers": "Reference Trackers (stationary devices with known room)"
        },
        "data_description": {
          "reference_trackers": "Select devices that are permanently placed in a specific room. Their signal data improves fingerprint quality. Multiple trackers in the same room are automatically aggregated."
        }
      }
    }
  }
}
```

### 2.3 Datenfluss-Diagramm

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Koordinator-Zyklus (~1.05s)                                                  │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ① Scanner-Status aktualisieren                                              │
│     _update_scanner_online_status(nowstamp)                                  │
│         │                                                                     │
│         ▼                                                                     │
│  ② Referenz-Tracker Learning (NEU)                                           │
│     _update_reference_tracker_learning(nowstamp)                             │
│         │                                                                     │
│         ├── Referenz-Tracker nach area_id gruppieren                         │
│         ├── Pro Raum: RSSI-Mediane berechnen                                 │
│         ├── Pro Raum: EIN _update_device_correlations() Aufruf               │
│         │     ├── Filter 2 (Confidence 0.80 ≥ 0.50): ✓ PASS                 │
│         │     ├── Filter 3 (Movement State): ✓ BYPASS (ref: prefix)          │
│         │     ├── Filter 4 (Velocity): ✓ AKTIV → blockiert bei Bewegung      │
│         │     ├── Filter 5 (RSSI Var): ✓ AKTIV → blockiert bei Instabilität  │
│         │     ├── Filter 6 (Ambiguity): ✓ AKTIV → erkennt Fehlconfig         │
│         │     └── Filter 7 (Scanner Offline): ✓ AKTIV → schützt Profile      │
│         │                                                                     │
│         └── Ergebnis: correlations["ref:<area_id>"][area_id] aktualisiert    │
│                                                                               │
│         ▼                                                                     │
│  ③ Individuelle Device-Verarbeitung (bestehend, unverändert)                 │
│     Für jedes Device (inklusive Referenz-Tracker als normale Geräte):        │
│         ├── Area Selection (UKF → Min-Distance Fallback)                     │
│         ├── Individuelles Auto-Learning (normale Konfidenz)                   │
│         └── Entity-Updates (Sensor, DeviceTracker)                           │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.4 Fehlerfälle und Schutzmechanismen

#### 2.4.1 Falsch konfigurierter Referenz-Tracker

**Szenario:** User markiert Tracker als "Referenz in Küche", Tracker steht aber im Büro.

**Schutz:**
1. Der Tracker wird normal getrackt → Area-Selection erkennt "Büro"
2. Der aggregierte Referenz-Update geht in `correlations["ref:area.office"]`
3. Quality Filter 6 (Ambiguity) erkennt: "Büro-RSSI sieht aus wie Küche" → blockiert
4. Da nur das Auto-Learning betroffen ist (nicht die Raumzuordnung), ist der Schaden begrenzt
5. Andere Geräte im Büro lernen weiterhin mit normaler Konfidenz

**Worst Case:** Der falsche Referenz-Tracker verbessert den Büro-Fingerprint geringfügig mit
einem leicht falschen Signal. Bei Konfidenz 0.80 (nicht 0.99) ist der Einfluss begrenzt und
wird über Zeit von korrekten Geräte-Messungen überlagert.

#### 2.4.2 Referenz-Tracker wird bewegt

**Szenario:** User bewegt den Tracker von der Küche ins Schlafzimmer.

**Schutz:**
1. Quality Filter 4 (Velocity > 1 m/s): Blockiert Learning während der Bewegung
2. Nach der Bewegung: Tracker wird dem Schlafzimmer zugeordnet
3. Aggregiertes Learning geht nun in `correlations["ref:area.schlafzimmer"]`
4. Das alte Küchen-Referenz-Profil wird nicht mehr aktualisiert, aber auch nicht gelöscht
5. Es veraltet langsam (Auto-Learning der anderen Geräte überschreibt es mit der Zeit)

#### 2.4.3 Alle Referenz-Tracker eines Raums fallen aus

**Szenario:** Referenz-Tracker hat leere Batterie oder wird entfernt.

**Schutz:**
1. `_aggregate_reference_tracker_readings()` findet keine frischen Adverts → kein Update
2. Das bestehende Referenz-Profil bleibt erhalten (nicht gelöscht)
3. Normales Auto-Learning der anderen Geräte läuft weiter
4. System degradiert graceful auf den Zustand ohne Referenz-Tracker

#### 2.4.4 Zehn Referenz-Tracker im gleichen Raum (Sample-Rate-Bias)

**Schutz (Kernziel L-04/L-06):**

```
10 Tracker im Raum → 10 RSSI-Werte pro Scanner
                   → 1 Median pro Scanner
                   → 1 Learning-Update mit 1 Median-Satz
                   → Identische Lernrate wie 1 Tracker

Vergleich mit naiver Implementierung:
  Naiv:   10 × update(rssi, confidence=0.80) → 10× Lernrate
  Design: 1 × update(median(rssi_1..10), confidence=0.80) → 1× Lernrate
```

### 2.5 Persistenz

**Keine Änderungen am Speicher-Format.** Referenz-Tracker-Profile werden als normale
`AreaProfile`-Objekte im bestehenden `CorrelationStore` gespeichert:

```python
# Speicher-Struktur (store.py):
{
    "devices": {
        "aa:bb:cc:dd:ee:ff": { ... },          # Normales Gerät
        "ref:area.kitchen": {                   # Referenz-Profil (NEU)
            "area.kitchen": AreaProfile(...)
        },
    },
    "rooms": { ... },                           # Unverändert
}
```

Der `ref:` Prefix verhindert Kollisionen mit echten MAC-Adressen (die immer das
Format `XX:XX:XX:XX:XX:XX` haben).

**Abwärtskompatibilität:** Bestehende Installationen ohne Referenz-Tracker haben
keine `ref:` Einträge → keine Auswirkung.

---

## 3. Entscheidungsbegründungen

### 3.1 Warum Konfidenz 0.80?

```
┌───────────────────────────────────────────────────────────┐
│ Konfidenz-Skala und Fehlertoleranz                       │
├───────────────────────────────────────────────────────────┤
│                                                           │
│ 0.50 ─── Gate (darunter = kein Learning)                 │
│          ↕ 0.30 Abstand                                  │
│ 0.80 ─── Referenz-Tracker                                │
│          "Ziemlich sicher, aber 20% Zweifel"             │
│          ↕ 0.15 Abstand                                  │
│ 0.95 ─── Button-Training                                 │
│          "User bestätigt JETZT im Raum"                  │
│                                                           │
│ Warum nicht höher?                                        │
│ • 0.90: Zu nah an Button-Training                        │
│ • 0.99: Fehlkonfiguration wäre fast unkorrigierbar       │
│ • 0.80: 3 normale Geräte (3×0.60=1.80) > 1×0.80         │
│         → Fehler eines Trackers wird von Mehrheit        │
│           der normalen Geräte korrigiert                 │
│                                                           │
│ Warum nicht niedriger?                                    │
│ • 0.60: Kaum Unterschied zu normalem stationärem Gerät   │
│ • 0.70: Zu nah am Gate, geringer Mehrwert                │
│ • 0.80: Deutlich über normalem Gerät (~0.55-0.65),       │
│         gibt erkennbaren Fingerprint-Stabilitäts-Boost   │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

### 3.2 Warum Median statt Mittelwert?

| Eigenschaft | Mittelwert | Median |
|-------------|------------|--------|
| Ausreißer-Robustheit | Schlecht | Gut |
| Bei 1 Tracker | = Median | = Mittelwert |
| Bei Fehlkonfiguration (1 von 5 falsch) | Verschiebt um ~20% | Ignoriert Ausreißer |
| Rechenaufwand | O(n) | O(n log n) — vernachlässigbar |

### 3.3 Warum `ref:` Prefix statt echtem Device?

| Ansatz | Vorteil | Nachteil |
|--------|---------|----------|
| Echte Tracker-Adresse | Einfach, kein Proxy | N Tracker = N Updates (Sample-Rate-Bias) |
| `ref:<area_id>` | 1 Update pro Raum | Braucht Proxy-Objekt |
| Separater Store | Sauber getrennt | Mehr Code, Migration nötig |

### 3.4 Warum nur Filter 3 bypassen?

Filter 3 (Movement State) prüft, ob ein Gerät 10+ Minuten im gleichen Raum war.
Dies ist für einen markierten Referenz-Tracker redundant — der User hat explizit
gesagt, dass das Gerät stationär ist.

Alle anderen Filter schützen vor realen Problemen, die auch bei Referenz-Trackern
auftreten können:
- Filter 4: Tracker wird bewegt
- Filter 5: Signal gestört (Mikrowelle, Metall)
- Filter 6: Falscher Raum konfiguriert
- Filter 7: Scanner kaputt

---

## 4. Abnahmekriterien und Tests

### 4.1 Test-Datei: `tests/test_reference_tracker.py`

#### 4.1.1 Konfiguration

| Test-ID | Beschreibung | Erwartetes Ergebnis |
|---------|-------------|---------------------|
| T-CFG-01 | `CONF_REFERENCE_TRACKERS` in Options leer (Default) | `is_reference_tracker = False` für alle Geräte |
| T-CFG-02 | Device-Adresse in `CONF_REFERENCE_TRACKERS` | `is_reference_tracker = True` für dieses Gerät |
| T-CFG-03 | Unbekannte Adresse in `CONF_REFERENCE_TRACKERS` | Kein Fehler, wird ignoriert |
| T-CFG-04 | `CONF_REFERENCE_TRACKERS` Key fehlt komplett | Default `[]`, kein Fehler |

#### 4.1.2 Aggregation

| Test-ID | Beschreibung | Erwartetes Ergebnis |
|---------|-------------|---------------------|
| T-AGG-01 | 1 Referenz-Tracker in Raum → Aggregation | RSSI-Werte = Einzelwerte des Trackers |
| T-AGG-02 | 3 Referenz-Tracker in Raum → Median | Pro Scanner: Median der 3 RSSI-Werte |
| T-AGG-03 | 10 Referenz-Tracker in Raum → 1 Update | `_update_device_correlations()` wird genau 1× aufgerufen |
| T-AGG-04 | 2 Räume mit je 2 Trackern → 2 Updates | Pro Raum genau 1 Update |
| T-AGG-05 | Referenz-Tracker ohne area_id → übersprungen | Tracker ohne Raumzuordnung wird ignoriert |
| T-AGG-06 | Referenz-Tracker mit veralteten Adverts → übersprungen | Keine Aggregation bei stale Daten |
| T-AGG-07 | Median bei gerader Tracker-Anzahl (2, 4) | Python `statistics.median()` gibt Mittelwert der mittleren zwei |
| T-AGG-08 | Tracker sehen unterschiedliche Scanner-Sets | Median nur über Scanner die ≥1 Tracker sieht |
| T-AGG-09 | Timestamps werden pro Scanner als Maximum aggregiert | `current_stamps[scanner] = max(alle Tracker-Stamps)` |

#### 4.1.3 Quality-Filter-Interaktion

| Test-ID | Beschreibung | Erwartetes Ergebnis |
|---------|-------------|---------------------|
| T-FLT-01 | Referenz-Update Konfidenz = 0.80 | Passiert Filter 2 (Gate 0.50) |
| T-FLT-02 | Referenz-Update bypassed Filter 3 (Movement State) | Learning auch ohne 10 Min Wartezeit |
| T-FLT-03 | Normales Gerät durchläuft Filter 3 weiterhin | Kein Bypass für is_reference_tracker=False |
| T-FLT-04 | Referenz-Tracker Velocity > 1 m/s → Filter 4 blockiert | Aggregat-RSSI-Varianz wird geprüft |
| T-FLT-05 | Referenz-Tracker RSSI Varianz > 16 dB² → Filter 5 blockiert | Learning blockiert |
| T-FLT-06 | Referenz-Tracker Signal ambig → Filter 6 blockiert | Learning blockiert |
| T-FLT-07 | Trainierter Scanner offline → Filter 7 blockiert | Learning blockiert |

#### 4.1.4 Lernrate-Invarianz (Kern-Anforderung L-04/L-06)

| Test-ID | Beschreibung | Erwartetes Ergebnis |
|---------|-------------|---------------------|
| T-RATE-01 | 1 Tracker, 100 Zyklen → Profile-Varianz V₁ | Baseline messen |
| T-RATE-02 | 5 Tracker gleicher Raum, 100 Zyklen → Varianz V₅ | V₅ ≈ V₁ (±10%) |
| T-RATE-03 | 10 Tracker gleicher Raum, 100 Zyklen → Varianz V₁₀ | V₁₀ ≈ V₁ (±10%) |
| T-RATE-04 | Raum A (5 Tracker) vs Raum B (1 Tracker), 100 Zyklen | Beide Profile gleich reif |

#### 4.1.5 Fehlerszenarien

| Test-ID | Beschreibung | Erwartetes Ergebnis |
|---------|-------------|---------------------|
| T-ERR-01 | Falsch konfigurierter Tracker (Büro statt Küche) | Lernt unter erkanntem Raum, Ambiguity-Filter kann greifen |
| T-ERR-02 | Tracker wird bewegt → Velocity-Filter | Learning stoppt während Bewegung |
| T-ERR-03 | Tracker-Batterie leer → keine Adverts | Kein Learning, kein Crash |
| T-ERR-04 | Alle Tracker eines Raums entfernt | Bestehende Profile bleiben, kein Learning |
| T-ERR-05 | Tracker ohne Adverts in Aggregation | Tracker wird übersprungen |
| T-ERR-06 | `CONF_REFERENCE_TRACKERS` enthält ungültige Einträge | Werden ignoriert, kein Crash |

#### 4.1.6 Abwärtskompatibilität

| Test-ID | Beschreibung | Erwartetes Ergebnis |
|---------|-------------|---------------------|
| T-BWC-01 | Options ohne `CONF_REFERENCE_TRACKERS` | System verhält sich exakt wie vorher |
| T-BWC-02 | `ref:` Prefix in CorrelationStore laden | Wird korrekt deserialisiert |
| T-BWC-03 | Existierende Korrelationsdaten unverändert | Keine Daten gehen verloren |
| T-BWC-04 | Kein Referenz-Tracker konfiguriert, 1000 Zyklen | `_update_reference_tracker_learning()` ist No-Op |

#### 4.1.7 Integration

| Test-ID | Beschreibung | Erwartetes Ergebnis |
|---------|-------------|---------------------|
| T-INT-01 | Referenz-Profil beeinflusst UKF-Matching | `correlations["ref:area.kitchen"]` wird von `match_fingerprints()` gelesen |
| T-INT-02 | Referenz-Tracker individuelles Learning läuft parallel | Tracker lernt auch unter seiner eigenen Adresse (normale Konfidenz) |
| T-INT-03 | Referenz-Profil überlebt Koordinator-Restart | In CorrelationStore gespeichert und geladen |
| T-INT-04 | Diagnostik zeigt Referenz-Tracker-Status | `async_get_config_entry_diagnostics()` enthält Referenz-Daten |

### 4.2 Test-Implementierungsmuster

```python
"""Tests for Reference Tracker feature."""

import statistics
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from custom_components.bermuda.area_selection import AreaSelectionHandler
from custom_components.bermuda.const import (
    CONF_REFERENCE_TRACKERS,
    REFERENCE_TRACKER_CONFIDENCE,
    REFERENCE_TRACKER_DEVICE_PREFIX,
    MOVEMENT_STATE_STATIONARY,
)


# ---- Fixtures ----

@dataclass
class FakeKalman:
    is_initialized: bool = True
    variance: float = 4.0
    last_update_time: float = 1000.0


@dataclass
class FakeAdvert:
    scanner_address: str
    rssi: float
    stamp: float
    rssi_distance: float = 5.0
    hist_velocity: list[float] = field(default_factory=list)
    rssi_kalman: FakeKalman = field(default_factory=FakeKalman)
    area_id: str | None = None
    area_name: str | None = None
    scanner_device: object = None
    name: str = ""
    hist_distance_by_interval: list[float] = field(default_factory=list)

    def median_rssi(self) -> float:
        return self.rssi

    def get_distance_variance(self, nowstamp: float | None = None) -> float:
        return 1.0


@dataclass
class FakeRefDevice:
    """Fake device for reference tracker tests."""

    address: str
    name: str
    is_reference_tracker: bool = True
    area_id: str | None = None
    area_name: str | None = None
    area_changed_at: float = 0.0
    adverts: dict = field(default_factory=dict)
    co_visibility_stats: dict = field(default_factory=dict)
    co_visibility_min_samples: int = 50

    def get_movement_state(self, *, stamp_now: float | None = None) -> str:
        return MOVEMENT_STATE_STATIONARY

    def get_dwell_time(self, *, stamp_now: float | None = None) -> float:
        return 86400.0


def _make_handler() -> AreaSelectionHandler:
    """Create handler with mock coordinator."""
    coordinator = MagicMock()
    coordinator.options = {}
    coordinator.correlations = {}
    coordinator.room_profiles = {}
    coordinator.device_ukfs = {}
    coordinator._scanners = set()
    coordinator.ar = None
    coordinator.devices = {}
    handler = AreaSelectionHandler(coordinator)
    return handler


# ---- Test: Aggregation (T-AGG) ----

class TestAggregation:
    """Tests for RSSI aggregation across multiple reference trackers."""

    def test_single_tracker_passthrough(self) -> None:
        """T-AGG-01: Single tracker RSSI values are passed through unchanged."""
        handler = _make_handler()
        device = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Ref 1",
            area_id="area.kitchen",
        )
        device.adverts = {
            "scanner_a": FakeAdvert(scanner_address="scanner_a", rssi=-55.0, stamp=1000.0),
            "scanner_b": FakeAdvert(scanner_address="scanner_b", rssi=-71.0, stamp=1000.0),
        }
        handler.coordinator.devices = {device.address: device}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        assert "area.kitchen" in result
        primary_rssi, primary_addr, other, stamps = result["area.kitchen"]
        assert primary_rssi == -55.0
        assert primary_addr == "scanner_a"
        assert other["scanner_b"] == -71.0

    def test_three_trackers_median(self) -> None:
        """T-AGG-02: Three trackers produce per-scanner median."""
        handler = _make_handler()
        devices = []
        rssi_sets = [
            {"scanner_a": -52.0, "scanner_b": -71.0},
            {"scanner_a": -58.0, "scanner_b": -68.0},
            {"scanner_a": -55.0, "scanner_b": -73.0},
        ]
        for i, rssis in enumerate(rssi_sets):
            dev = FakeRefDevice(
                address=f"aa:bb:cc:dd:ee:{i:02x}",
                name=f"Ref {i}",
                area_id="area.kitchen",
            )
            dev.adverts = {
                addr: FakeAdvert(scanner_address=addr, rssi=rssi, stamp=1000.0)
                for addr, rssi in rssis.items()
            }
            devices.append(dev)

        handler.coordinator.devices = {d.address: d for d in devices}
        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        primary_rssi, _, other, _ = result["area.kitchen"]
        # Median of [-52, -58, -55] = -55, Median of [-71, -68, -73] = -71
        assert primary_rssi == statistics.median([-52.0, -58.0, -55.0])
        assert other["scanner_b"] == statistics.median([-71.0, -68.0, -73.0])

    def test_ten_trackers_single_update(self) -> None:
        """T-AGG-03: Ten trackers produce exactly one learning update."""
        handler = _make_handler()
        devices = {}
        for i in range(10):
            dev = FakeRefDevice(
                address=f"aa:bb:cc:dd:{i:02x}:ff",
                name=f"Ref {i}",
                area_id="area.kitchen",
            )
            dev.adverts = {
                "scanner_a": FakeAdvert(
                    scanner_address="scanner_a",
                    rssi=-55.0 + i * 0.5,
                    stamp=1000.0,
                ),
            }
            devices[dev.address] = dev
        handler.coordinator.devices = devices

        result = handler._aggregate_reference_tracker_readings(nowstamp=1001.0)

        # Exactly 1 area entry (not 10)
        assert len(result) == 1
        assert "area.kitchen" in result


class TestLearningRateInvariance:
    """Tests ensuring N trackers produce same learning rate as 1 tracker."""

    def test_one_vs_five_trackers_same_rate(self) -> None:
        """T-RATE-02: 5 trackers should produce same profile maturity as 1 tracker."""
        # ... setup with controlled RSSI, run 100 cycles each
        # ... compare profile.sample_count and profile.variance
        pass  # Implementation detail nach Code-Review

    def test_one_vs_ten_trackers_same_rate(self) -> None:
        """T-RATE-03: 10 trackers should produce same profile maturity as 1."""
        pass  # Same pattern as T-RATE-02


class TestFilterBehavior:
    """Tests for quality filter interaction with reference trackers."""

    def test_filter3_bypassed_for_reference(self) -> None:
        """T-FLT-02: Movement state filter bypassed for ref: devices."""
        # ... verify that ref: prefix skips movement check
        pass

    def test_filter3_active_for_normal_devices(self) -> None:
        """T-FLT-03: Normal devices still go through filter 3."""
        # ... verify is_reference_tracker=False still requires 10 min
        pass

    def test_velocity_filter_blocks_reference(self) -> None:
        """T-FLT-04: High velocity blocks reference tracker learning."""
        # ... aggregated adverts with high velocity history
        pass


class TestErrorScenarios:
    """Tests for edge cases and error scenarios."""

    def test_no_reference_trackers_is_noop(self) -> None:
        """T-BWC-04: No reference trackers configured = no effect."""
        handler = _make_handler()
        handler.coordinator.devices = {}

        # Should complete without error
        handler._update_reference_tracker_learning(nowstamp=1000.0)

        # No correlations created
        assert len(handler.correlations) == 0

    def test_tracker_without_area_skipped(self) -> None:
        """T-AGG-05: Tracker without area_id is ignored."""
        handler = _make_handler()
        dev = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Unplaced Ref",
            area_id=None,
        )
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1000.0)
        assert len(result) == 0

    def test_tracker_battery_dead_no_crash(self) -> None:
        """T-ERR-03: Tracker with no adverts doesn't crash."""
        handler = _make_handler()
        dev = FakeRefDevice(
            address="aa:bb:cc:dd:ee:01",
            name="Dead Battery",
            area_id="area.kitchen",
            adverts={},
        )
        handler.coordinator.devices = {dev.address: dev}

        result = handler._aggregate_reference_tracker_readings(nowstamp=1000.0)
        assert "area.kitchen" not in result
```

### 4.3 Abnahmekriterien (Definition of Done)

| Nr | Kriterium | Prüfmethode |
|----|-----------|-------------|
| A-01 | Alle 32 Tests bestehen | `python -m pytest tests/test_reference_tracker.py -v` |
| A-02 | Mypy strict ohne Fehler | `python -m mypy --strict` |
| A-03 | Ruff ohne Fehler | `python -m ruff check` |
| A-04 | Bestehende Tests unverändert grün | `python -m pytest --cov -q` |
| A-05 | Code-Coverage ≥ 90% für neue Dateien | `python -m pytest --cov=custom_components.bermuda.area_selection tests/test_reference_tracker.py` |
| A-06 | Kein neues File > 400 Zeilen | Manueller Review |
| A-07 | CLAUDE.md aktualisiert mit Feature-Dokumentation | Manueller Review |
| A-08 | Translations für DE + EN vorhanden | Prüfe `translations/{de,en}.json` |
| A-09 | Diagnostik-Output enthält Referenz-Tracker-Sektion | Manueller Test mit `dump_devices` |
| A-10 | Abwärtskompatibel: Bestandsinstallation ohne Config-Änderung funktioniert | T-BWC-01 bis T-BWC-04 |

---

## 5. Implementierungsplan (Reihenfolge)

| Phase | Aufgabe | Dateien | Geschätzter Umfang |
|-------|---------|---------|-------------------|
| 1 | Konstanten + Device-Flag | `const.py`, `bermuda_device.py` | ~10 Zeilen |
| 2 | Config-Flow UI-Erweiterung | `config_flow.py`, `translations/*.json` | ~30 Zeilen |
| 3 | `_ReferenceTrackerProxy` Dataclass | `area_selection.py` | ~25 Zeilen |
| 4 | `_aggregate_reference_tracker_readings()` | `area_selection.py` | ~80 Zeilen |
| 5 | `_update_reference_tracker_learning()` | `area_selection.py` | ~40 Zeilen |
| 6 | Filter 3 Bypass in `_update_device_correlations()` | `area_selection.py` | ~5 Zeilen |
| 7 | Aufruf in `refresh_areas_by_min_distance()` | `area_selection.py` | ~3 Zeilen |
| 8 | Diagnostik-Erweiterung | `diagnostics.py` | ~20 Zeilen |
| 9 | Tests (komplett) | `tests/test_reference_tracker.py` | ~400 Zeilen |
| 10 | CLAUDE.md Dokumentation | `CLAUDE.md` | ~100 Zeilen |
| **Total** | | | **~715 Zeilen** |

---

## 6. Offene Fragen

| Nr | Frage | Vorschlag | Status |
|----|-------|-----------|--------|
| F-01 | Soll das `ref:<area_id>` Profil im UKF-Matching mit dem Device-spezifischen Profil des gleichen Geräts verschmelzen? | Nein — es ist ein separates, raum-gebundenes Profil | Entschieden |
| F-02 | Soll der Referenz-Tracker in den Diagnostik-Dump als eigenes "Gerät" erscheinen? | Nein — nur als Aggregat im Diagnostik-Abschnitt | Entschieden |
| F-03 | Maximale Anzahl Referenz-Tracker pro Raum begrenzen? | Nein — Aggregation macht die Anzahl irrelevant | Entschieden |
| F-04 | Soll `REFERENCE_TRACKER_CONFIDENCE` konfigurierbar sein (UI)? | Nein — Expertenwert, nicht für den User | Entschieden |
| F-05 | Soll ein Warnhinweis erscheinen, wenn ein Referenz-Tracker den Raum wechselt? | Ja (HA Repair Issue) — aber in Phase 2 | Offen |
