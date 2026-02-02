# Änderungs-Review: Recorder-Datenbank-Optimierung

**Datum:** 2026-02-02
**Grundlage:** RECORDER_PEER_REVIEW.md + User-Feedback
**Branches:** `claude/fix-recorder-database-bloat-QOYPP`

---

## Grundsätze (User-Vorgaben)

1. **Keine funktionalen Daten löschen** - Alles was für Entfernungsmessung, Fingerprinting und EID benötigt wird, bleibt erhalten.
2. **Live-Daten bleiben vollständig** - Alle Entities, Attribute und Sensoren funktionieren unverändert in Echtzeit.
3. **Konfigurationsschalter** statt pauschaler Löschung - User soll wählen können.
4. **Debug-Modus** optional verfügbar - Volle History bei Bedarf.

---

## Wichtige Klarstellung: `_unrecorded_attributes`

**`_unrecorded_attributes` beeinflusst NUR die History-Datenbank.** Es hat keine Auswirkung auf:

| Bereich | Betroffen? | Erklärung |
|---------|------------|-----------|
| Live-Sensorwerte in UI | Nein | State wird weiterhin in Echtzeit angezeigt |
| Automationen / Trigger | Nein | `state_changed`-Events werden weiterhin ausgelöst |
| Template-Sensoren | Nein | Attribute sind weiterhin live verfügbar |
| Bermuda-interne Verarbeitung | Nein | **Bestätigt: Einweg-Datenfluss** (Coordinator → Entities) |
| Fingerprinting / Korrelation | Nein | Arbeitet auf In-Memory `BermudaDevice`-Objekten |
| EID-Auflösung | Nein | Arbeitet im `fmdn/`-Modul auf In-Memory-Daten |
| History-Graphen (HA Logbook) | **JA** | Ausgeschlossene Attribute erscheinen nicht in der History |
| Langzeit-Statistiken | Indirekt | Betrifft `SensorStateClass` (separates Thema) |

**Einweg-Datenfluss bestätigt:** Grep über die gesamte Codebase zeigt, dass keine interne
Bermuda-Logik jemals Entity-State oder -Attribute aus der HA-Datenbank zurückliest.
Die Verarbeitungspipeline (Coordinator, AreaSelection, MetadeviceManager, Korrelation, UKF,
EID-Resolver) operiert ausschließlich auf In-Memory `BermudaDevice`/`BermudaAdvert`-Objekten.

---

## Änderungsplan: 3 Stufen

### Stufe 1: `area_state_metadata()` aus Recorder ausschließen (KRITISCH)

**Problem:** 3 Float-Attribute ändern sich bei jedem Coordinator-Zyklus (~1.05s) und
erzwingen einen neuen DB-Eintrag pro Zyklus pro Entität. Betrifft 3 standardmäßig
aktivierte Entitäten pro Gerät (Area, Floor, Distance).

**Berechnung (10 Geräte):**
```
3 Entitäten × 10 Geräte × 86.400s/Tag ÷ 1.05s = ~2.469.000 DB-Writes/Tag
```

**Ständig wechselnde Attribute (Problem):**
- `last_good_area_age_s` - Alter der letzten Area-Erkennung (steigt monoton)
- `last_good_distance_age_s` - Alter der letzten Distanzmessung (steigt monoton)
- `area_retention_seconds_remaining` - Countdown (fällt monoton)

**Stabile Attribute (kein Problem, wertvoll für History):**
- `area_id` - Area-ID (ändert sich nur bei Raumwechsel)
- `area_name` - Area-Name (ändert sich nur bei Raumwechsel)
- `floor_id`, `floor_name`, `floor_level` - Stockwerk (selten)
- `area_is_stale` - Bool (selten)
- `area_retained` - Bool (selten)
- `area_source` - String (selten)
- `current_mac` - MAC-Adresse (bei Metadevices: bei MAC-Rotation)

**Lösung:** Die 3 zeitbasierten Attribute als `_unrecorded_attributes` deklarieren.

```python
# In BermudaSensor (sensor.py):
_unrecorded_attributes = frozenset({
    "last_good_area_age_s",
    "last_good_distance_age_s",
    "area_retention_seconds_remaining",
})
```

**Wirkung:**
- Live: Attribute weiterhin in UI und Automationen verfügbar
- History: Nur stabile Attribute (area_id, area_name, etc.) werden gespeichert
- Reduktion: ~2.5 Mio DB-Writes/Tag → nur bei tatsächlichen Raumwechseln
- **Funktional:** Kein Datenverlust, da diese Werte reine Echtzeit-Diagnostik sind

**Kein Schalter nötig:** Diese Änderung ist immer korrekt. Die zeitbasierten Attribute
haben in der History keinen Informationswert (sie zeigen nur an, wie alt der letzte
Datenpunkt war - was aus dem Zeitstempel des DB-Eintrags selbst ablesbar ist).

---

### Stufe 2: Konfigurationsschalter `Recorder-Friendly Mode`

**Neuer Config-Option:** `CONF_RECORDER_FRIENDLY` (Boolean, Default: `True`)

**Platzierung:** Options-Flow → `globalopts`-Step (neben `use_ukf_area_selection`)

```python
# const.py
CONF_RECORDER_FRIENDLY = "recorder_friendly"
DEFAULT_RECORDER_FRIENDLY = True

# config_flow.py - globalopts step
vol.Optional(
    CONF_RECORDER_FRIENDLY,
    default=self.options.get(CONF_RECORDER_FRIENDLY, DEFAULT_RECORDER_FRIENDLY),
): bool,
```

**Wenn aktiviert (Standard):**

| Maßnahme | Entität(en) | Effekt |
|-----------|-------------|--------|
| `state_class` auf `None` setzen | Distance, RSSI, per-Scanner | Keine Long-Term-Statistics |
| `state_class` auf `None` setzen | Globale Sensoren (Proxy/Device Count) | Keine Long-Term-Statistics |
| `_unrecorded_attributes = MATCH_ALL` | `BermudaSensorScannerRange` | Keine per-Scanner-Attribute in History |
| `_unrecorded_attributes = MATCH_ALL` | `BermudaSensorScannerRangeRaw` | Keine per-Scanner-Attribute in History |
| Rate-Limit auf Raw-Scanner | `BermudaSensorScannerRangeRaw` | `_cached_ratelimit()` anwenden |

**Wenn deaktiviert (Debug-Modus):**

- Alle Sensoren behalten `SensorStateClass.MEASUREMENT`
- Alle Attribute werden in der History gespeichert
- Per-Scanner-Entitäten schreiben ohne zusätzliches Rate-Limit
- Nützlich für: Kalibrierung, Fehlersuche, Monitoring-Dashboards

**Was sich NICHT ändert (unabhängig vom Schalter):**

| Bereich | Begründung |
|---------|-----------|
| Area/Floor/Distance State-Werte | Das sind die Kernwerte - müssen immer aufgezeichnet werden |
| device_tracker State | Home/Away muss in History sein |
| Stabile Attribute (area_id, area_name, floor_*) | Wertvoll für History-Analyse |
| `current_mac` Attribut | Relevant für Metadevice-Debugging |
| Area Switch Diagnostic | Bereits `disabled_by_default`, Entity-Category DIAGNOSTIC |

---

### Stufe 3: `SensorStateClass.MEASUREMENT` bedingt entfernen

**Problem:** `SensorStateClass.MEASUREMENT` veranlasst HA, 5-Minuten Short-Term-Statistics
UND stündliche Long-Term-Statistics zu berechnen. Für Distance- und RSSI-Sensoren, die sich
kontinuierlich ändern, erzeugt das erheblichen Overhead.

**Betroffene Sensoren:**

| Sensor | Datei | Zeilen | Aktuell |
|--------|-------|--------|---------|
| `BermudaSensorRange` (Distance) | sensor.py | 356-359 | `MEASUREMENT` |
| `BermudaSensorRssi` (Nearest RSSI) | sensor.py | 318-321 | `MEASUREMENT` |
| `BermudaSensorScannerRange` | sensor.py | erbt von Range | `MEASUREMENT` |
| `BermudaSensorScannerRangeRaw` | sensor.py | erbt von Range | `MEASUREMENT` |
| `BermudaTotalProxyCount` | sensor.py | 520 | `MEASUREMENT` |
| `BermudaActiveProxyCount` | sensor.py | 545 | `MEASUREMENT` |
| `BermudaTotalDeviceCount` | sensor.py | 570 | `MEASUREMENT` |
| `BermudaVisibleDeviceCount` | sensor.py | 595 | `MEASUREMENT` |

**Lösung:** `state_class`-Property abhängig von `CONF_RECORDER_FRIENDLY` machen.

```python
# In BermudaSensorRange:
@property
def state_class(self) -> str | None:
    if self.coordinator.options.get(CONF_RECORDER_FRIENDLY, DEFAULT_RECORDER_FRIENDLY):
        return None  # Keine Long-Term-Statistics
    return SensorStateClass.MEASUREMENT
```

**Konsequenz bei Aktivierung:**
- Keine Graphen in der Standard-History-Ansicht (da state_class fehlt)
- **ABER:** State-Werte werden weiterhin aufgezeichnet und sind über SQL/Logbook abrufbar
- **ABER:** Automationen basierend auf numerischen Vergleichen funktionieren weiterhin
- Benutzer die Graphen brauchen, deaktivieren den Schalter

**Konsequenz bei Deaktivierung (Debug):**
- Volle Graphen-Unterstützung in HA Energy/History-Dashboard
- Long-Term-Statistics für Langzeitanalyse
- Nützlich für: Kalibrierung, Signal-Monitoring, Performance-Analyse

---

## Zusammenfassung der Änderungen

### Dateien die geändert werden

| Datei | Änderung |
|-------|----------|
| `const.py` | `CONF_RECORDER_FRIENDLY`, `DEFAULT_RECORDER_FRIENDLY` hinzufügen |
| `config_flow.py` | Toggle im `globalopts`-Step hinzufügen |
| `sensor.py` | `_unrecorded_attributes` für zeitbasierte Attribute (immer) |
| `sensor.py` | `state_class` bedingt auf `None` (wenn recorder_friendly) |
| `sensor.py` | `_unrecorded_attributes = MATCH_ALL` für per-Scanner (wenn recorder_friendly) |
| `sensor.py` | `_cached_ratelimit()` für `ScannerRangeRaw` (wenn recorder_friendly) |
| `entity.py` | Keine Änderung nötig |
| `translations/*.json` | Label und Beschreibung für neuen Schalter (8 Sprachen) |

### Geschätzter DB-Impact

Szenario: 10 Geräte, 5 Scanner, `recorder_friendly = True` (Standard)

| Quelle | Vorher (Writes/Tag) | Nachher (Writes/Tag) | Reduktion |
|--------|---------------------|----------------------|-----------|
| `area_state_metadata()` zeitbasiert | ~2.469.000 | 0 | **100%** |
| Distance/RSSI Long-Term-Stats | ~288.000 | 0 | **100%** |
| Per-Scanner Attributes | ~864.000 | 0 | **100%** |
| Per-Scanner Raw (kein Rate-Limit) | ~823.000 | ~82.300 | **90%** |
| Area/Floor State (Raumwechsel) | ~14.400 | ~14.400 | 0% |
| **Gesamt** | **~4.458.000** | **~96.700** | **~97.8%** |

### Was erhalten bleibt (auch bei `recorder_friendly = True`)

- Area-Name in History (wann war das Gerät wo?)
- Floor-Name in History
- Distance-Wert in History (bei echten Änderungen, Rate-Limited)
- RSSI-Wert in History (bei echten Änderungen, Rate-Limited)
- device_tracker State (Home/Away)
- Stabile Attribute: area_id, area_name, floor_id, floor_name, floor_level
- Stabile Attribute: area_is_stale, area_retained, area_source
- current_mac Attribut

---

## Implementierungsreihenfolge

1. **Stufe 1 (sofort):** `_unrecorded_attributes` für die 3 zeitbasierten Attribute
   - Keine Config-Änderung nötig, immer korrekt
   - Größter Einzeleffekt (~2.5 Mio Writes/Tag weniger)

2. **Stufe 2 (in gleicher PR):** Config-Option `CONF_RECORDER_FRIENDLY`
   - Default: `True` (optimiert)
   - Toggle in Options-Flow
   - Translations in allen 8 Sprachen

3. **Stufe 3 (in gleicher PR):** Bedingte `state_class` und per-Scanner-Optimierung
   - Abhängig von Stufe 2 Config-Option
   - Rate-Limit für Raw-Scanner-Entitäten

---

## Risikobewertung

| Risiko | Wahrscheinlichkeit | Auswirkung | Mitigation |
|--------|-------------------|------------|------------|
| User verliert History-Graphen | Mittel | Niedrig | Default ist `True` → User muss bewusst Debug aktivieren. Dokumentation. |
| Automationen brechen | Sehr niedrig | Mittel | `_unrecorded_attributes` beeinflusst KEINE Live-Attribute. `state_class=None` beeinflusst nur Statistik-Generierung, nicht State-Tracking. |
| Bestehende Long-Term-Statistics verschwinden | Niedrig | Niedrig | HA behält existierende Statistiken, generiert nur keine neuen. |
| Debug-Schalter vergessen | Niedrig | Niedrig | Standard ist der sichere Modus (recorder_friendly). |

---

## Vergleich mit Upstream

| Maßnahme | Upstream (agittins) | Unser Fork | Unterschied |
|-----------|--------------------|-----------|----|
| `area_state_metadata()` | Nicht vorhanden | Vorhanden → entfernen aus Recorder | Fork-spezifisch |
| `SensorStateClass.MEASUREMENT` | Auf allen Distance/RSSI | Identisch → bedingt entfernen | Beide betroffen |
| `_unrecorded_attributes` | Nicht verwendet | Wird hinzugefügt | Verbesserung |
| Config-Switch | Nicht vorhanden | Wird hinzugefügt | Fork-Feature |
| Per-Scanner Rate-Limit | Raw hat keines | Wird hinzugefügt (bedingt) | Verbesserung |
