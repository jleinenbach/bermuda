# FMDN API Implementation Plan

## Detaillierter Implementierungsplan für High- und Medium-Priority Action Items

**Datum:** 2026-01-24
**Version:** 1.0
**Status:** Bereit zur Umsetzung

---

## Übersicht

Dieses Dokument enthält einen vollständigen, risikofreien Implementierungsplan für die API-Optimierungen der GoogleFindMy-HA Integration in Bermuda.

### Action Items (priorisiert)

| Priorität | Action Item | Datei(en) | Komplexität |
|-----------|-------------|-----------|-------------|
| **HIGH** | CLAUDE.md API-Dokumentation aktualisieren | `CLAUDE.md` | Niedrig |
| **MEDIUM** | Typisiertes EIDMatch hinzufügen | `integration.py` | Mittel |
| **MEDIUM** | Diagnostik-Logging für ungenutzte Felder | `integration.py`, `manager.py` | Niedrig |

---

## 1. HIGH: CLAUDE.md API-Dokumentation aktualisieren

### 1.1 Ziel

Die bestehende FMDN-Dokumentation in CLAUDE.md um eine vollständige EID Resolver API-Referenz erweitern, damit zukünftige Entwickler die exakten Schnittstellen verstehen.

### 1.2 Recherche-Ergebnisse (GoogleFindMy-HA v1.7.0-3)

#### EIDMatch NamedTuple (vollständige Definition)

```python
class EIDMatch(NamedTuple):
    """Resolved mapping between an EID and a Home Assistant device."""

    device_id: str        # HA Device Registry ID (eindeutig pro Account)
    config_entry_id: str  # HA Config Entry ID
    canonical_id: str     # Google UUID (geteilt bei shared trackers)
    time_offset: int      # EID Window Offset in Sekunden
    is_reversed: bool     # Ob EID-Bytes reversed sind
```

#### resolve_eid Signatur

```python
def resolve_eid(self, eid_bytes: bytes) -> EIDMatch | None:
    """Resolve a scanned payload to a Home Assistant device registry ID.

    For shared devices (same tracker across multiple accounts), this returns
    the match with the smallest time_offset (best match).
    Use resolve_eid_all() to get all matches.
    """
```

#### resolve_eid_all Signatur

```python
def resolve_eid_all(self, eid_bytes: bytes) -> list[EIDMatch]:
    """Resolve a scanned payload to all matching Home Assistant device registry IDs.

    This method supports shared devices: when the same physical tracker
    is shared between accounts, all accounts' matches are returned.
    Each match represents a different Home Assistant device registry entry
    for the same physical tracker.

    Args:
        eid_bytes: Raw EID bytes from a BLE advertisement.

    Returns:
        List of EIDMatch entries for all accounts that share this device.
        Empty list if no match found.
    """
```

#### Resolver-Zugriff

```python
# Konstanten
DOMAIN = "googlefindmy"
DATA_EID_RESOLVER = "eid_resolver"

# Zugriffsmuster
resolver = hass.data[DOMAIN][DATA_EID_RESOLVER]
```

### 1.3 Implementierungsdetails

**Ort der Änderung:** `CLAUDE.md`, Sektion "FMDN / GoogleFindMy-HA Integration Architecture"

**Neue Sektion hinzufügen nach "GoogleFindMy-HA API Contract (v1.7.0+)":**

```markdown
### GoogleFindMy-HA EID Resolver API Reference (v1.7.0-3)

#### EIDMatch NamedTuple

The resolver returns `EIDMatch` objects with 5 fields:

| Field | Type | Description | Bermuda Usage |
|-------|------|-------------|---------------|
| `device_id` | `str` | HA Device Registry ID | ✅ PRIMARY - Used for metadevice address |
| `config_entry_id` | `str` | HA Config Entry ID | ❌ Currently unused |
| `canonical_id` | `str` | Google UUID | ✅ Used for cache fallback |
| `time_offset` | `int` | EID window offset (seconds) | ❌ Currently unused (diagnostic value) |
| `is_reversed` | `bool` | EID byte order flag | ❌ Currently unused (diagnostic value) |

**Important:** `device_id` is unique per HA device entry (account-scoped), while `canonical_id`
is the Google UUID shared across all accounts. For shared trackers, always use `device_id` as
the primary identifier to avoid collisions.

#### Method Signatures

**resolve_eid(eid_bytes: bytes) -> EIDMatch | None**
- Returns the single BEST match (smallest `time_offset`)
- Use for simple single-account scenarios
- Returns `None` if no match found

**resolve_eid_all(eid_bytes: bytes) -> list[EIDMatch]**
- Returns ALL matches (important for shared trackers)
- Each match represents a different HA device entry
- Returns empty list if no match found
- **Bermuda uses this as primary method** with fallback to `resolve_eid`

#### Resolver Access Pattern

```python
# Access from hass.data
bucket = hass.data.get("googlefindmy")
if isinstance(bucket, dict):
    resolver = bucket.get("eid_resolver")
    if resolver and callable(getattr(resolver, "resolve_eid", None)):
        # Ready to use
```

#### Error Handling

The resolver can raise various exceptions during resolution:
- `ValueError` - Invalid EID format
- `TypeError` - Wrong parameter type
- `AttributeError` - Internal resolver error
- `KeyError` - Missing data

Bermuda wraps all resolver calls in try/except with appropriate logging.
```

### 1.4 Checkliste vor Implementierung

- [x] EIDMatch vollständige Felddefinition recherchiert
- [x] Methodensignaturen dokumentiert
- [x] Zugriffsmuster validiert
- [x] Bermuda-Usage-Status für jedes Feld geklärt
- [x] Exakter Einfügeort in CLAUDE.md identifiziert

### 1.5 Risikobewertung

**Risiko: MINIMAL** - Reine Dokumentationsänderung, keine Code-Änderungen.

---

## 2. MEDIUM: Typisiertes EIDMatch hinzufügen

### 2.1 Ziel

Das aktuelle `Protocol` mit `Any`-Rückgabetypen durch typisierte Strukturen ersetzen, um:
- Bessere IDE-Unterstützung zu ermöglichen
- Type-Checker-Warnungen zu aktivieren
- Versehentliche Feldnamen-Typos zu verhindern

### 2.2 Aktuelle Implementierung (Problem)

```python
# integration.py:33-40
class EidResolver(Protocol):
    """Protocol for the googlefindmy EID resolver."""

    def resolve_eid(self, eid: bytes) -> Any | None:
        """Resolve an EID to a device match."""

    def resolve_eid_all(self, eid: bytes) -> list[Any]:
        """Resolve an EID to all matching devices (for shared trackers)."""
```

**Probleme:**
1. `Any`-Rückgabetypen verhindern Type-Checking der Feldnamen
2. `getattr(match, "device_id", None)` wird nicht validiert
3. Keine IDE-Autovervollständigung für EIDMatch-Felder

### 2.3 Geplante Lösung

**Neue Datenstruktur in `integration.py`:**

```python
from typing import NamedTuple

class EIDMatch(NamedTuple):
    """
    Local type definition matching GoogleFindMy-HA's EIDMatch structure.

    This provides type safety for Bermuda's EID resolution code without
    creating a hard dependency on GoogleFindMy-HA's internal types.

    See: https://github.com/jleinenbach/GoogleFindMy-HA/blob/1.7.0-3/
         custom_components/googlefindmy/eid_resolver.py
    """

    device_id: str
    """HA Device Registry ID - unique per account, PRIMARY identifier."""

    config_entry_id: str
    """HA Config Entry ID for the GoogleFindMy integration."""

    canonical_id: str
    """Google UUID - shared across accounts for the same physical device."""

    time_offset: int
    """EID time window offset in seconds (used for match ranking)."""

    is_reversed: bool
    """Whether EID bytes are in reversed order."""
```

**Aktualisiertes Protocol:**

```python
class EidResolver(Protocol):
    """Protocol for the googlefindmy EID resolver."""

    def resolve_eid(self, eid: bytes) -> EIDMatch | None:
        """Resolve an EID to a device match (best match for shared trackers)."""

    def resolve_eid_all(self, eid: bytes) -> list[EIDMatch]:
        """Resolve an EID to all matching devices (for shared trackers)."""
```

### 2.4 Zu ändernde Code-Stellen

| Zeile | Aktuelle Verwendung | Neue Verwendung |
|-------|---------------------|-----------------|
| `integration.py:36` | `-> Any \| None` | `-> EIDMatch \| None` |
| `integration.py:39` | `-> list[Any]` | `-> list[EIDMatch]` |
| `integration.py:194` | `-> Any \| None` | `-> EIDMatch \| None` |
| `integration.py:202` | `tuple[Any \| None, ...]` | `tuple[EIDMatch \| None, ...]` |
| `integration.py:250` | `tuple[list[Any], ...]` | `tuple[list[EIDMatch], ...]` |
| `integration.py:334` | `match: Any` | `match: EIDMatch` |

### 2.5 Kompatibilitäts-Betrachtung

**WICHTIG:** Die externe Resolver-Instanz gibt weiterhin `Any` zurück (weil es ein externes Modul ist). Wir müssen zwischen **lokaler Typsicherheit** und **Runtime-Flexibilität** unterscheiden:

```python
# Option A: Strenge Typisierung mit Cast (EMPFOHLEN)
def process_resolution(self, eid_bytes: bytes) -> EIDMatch | None:
    resolver = self.get_resolver()
    if resolver is None:
        return None

    raw_match = resolver.resolve_eid(eid_bytes)  # Returns Any at runtime
    if raw_match is None:
        return None

    # Validiere und konvertiere zu lokalem Typ
    try:
        return EIDMatch(
            device_id=str(getattr(raw_match, "device_id", "")),
            config_entry_id=str(getattr(raw_match, "config_entry_id", "")),
            canonical_id=str(getattr(raw_match, "canonical_id", "")),
            time_offset=int(getattr(raw_match, "time_offset", 0)),
            is_reversed=bool(getattr(raw_match, "is_reversed", False)),
        )
    except (TypeError, ValueError, AttributeError) as ex:
        _LOGGER.debug("Failed to convert resolver match to EIDMatch: %s", ex)
        return None
```

**Option B: Nur Type Hints ohne Konvertierung (ALTERNATIVE)**

```python
# Behält getattr() Pattern, aber mit besseren Type Hints
def process_resolution(self, eid_bytes: bytes) -> EIDMatch | None:
    ...
    match = resolver.resolve_eid(eid_bytes)
    if match is None:
        return None
    # Type cast für IDE-Unterstützung, aber ohne Runtime-Validierung
    return cast(EIDMatch, match)
```

### 2.6 Empfohlene Implementierung

**Option A (strenge Typisierung mit Konvertierung)** wird empfohlen, weil:
1. Fehlende/fehlerhafte Felder werden zur Laufzeit abgefangen
2. Default-Werte verhindern Crashes bei API-Änderungen
3. Vollständige Type-Safety für alle nachfolgenden Code-Pfade

### 2.7 Implementierungsreihenfolge

1. **Schritt 1:** `EIDMatch` NamedTuple hinzufügen (Zeile ~33)
2. **Schritt 2:** `EidResolver` Protocol aktualisieren (Zeile ~43)
3. **Schritt 3:** Hilfsfunktion `_convert_to_eid_match()` erstellen
4. **Schritt 4:** `process_resolution_with_status()` aktualisieren
5. **Schritt 5:** `process_resolution_all_with_status()` aktualisieren
6. **Schritt 6:** `register_source()` Signatur aktualisieren
7. **Schritt 7:** Bestehende `getattr()`-Aufrufe durch direkte Feldnamen ersetzen
8. **Schritt 8:** Tests anpassen

### 2.8 Code-Diff-Vorschau

```python
# VORHER (integration.py:341)
def register_source(self, source_device: BermudaDevice, metadevice_address: str, match: Any) -> None:
    fmdn_device_id = getattr(match, "device_id", None)
    canonical_id = getattr(match, "canonical_id", None)

# NACHHER
def register_source(self, source_device: BermudaDevice, metadevice_address: str, match: EIDMatch) -> None:
    fmdn_device_id = match.device_id  # Direkter Zugriff, Type-Safe!
    canonical_id = match.canonical_id  # Direkter Zugriff, Type-Safe!
```

### 2.9 Checkliste vor Implementierung

- [x] Alle `Any`-Verwendungen in integration.py identifiziert
- [x] Konvertierungsstrategie gewählt (Option A)
- [x] Default-Werte für alle Felder definiert
- [x] Implementierungsreihenfolge festgelegt
- [x] Abwärtskompatibilität geprüft (getattr mit defaults)

### 2.10 Risikobewertung

**Risiko: NIEDRIG**
- NamedTuple ist Standard-Python
- Konvertierungsfunktion fängt Fehler ab
- Keine Breaking Changes für externe Aufrufer
- Tests decken Regressions ab

---

## 3. MEDIUM: Diagnostik-Logging für ungenutzte Felder

### 3.1 Ziel

Die Felder `time_offset` und `is_reversed` für Diagnosezwecke loggen, um:
- Debugging bei EID-Resolution-Problemen zu erleichtern
- Verständnis der Resolver-Interna zu verbessern
- Potenzielle zukünftige Nutzung vorzubereiten

### 3.2 Aktuelle Situation

```python
# Felder werden komplett ignoriert:
# - time_offset: Könnte Hinweise auf "abgelaufene" EIDs geben
# - is_reversed: Könnte Byte-Order-Probleme erklären
```

### 3.3 Geplante Implementierung

#### 3.3.1 Logging in process_resolution_with_status()

```python
# integration.py - Nach erfolgreicher Resolution
if match is not None:
    # Diagnostik-Logging für ungenutzte Felder
    time_offset = match.time_offset  # Mit typisiertem EIDMatch
    is_reversed = match.is_reversed

    if time_offset != 0:
        _LOGGER.debug(
            "FMDN resolution time_offset=%d for EID (non-zero may indicate stale match)",
            time_offset,
        )
    if is_reversed:
        _LOGGER.debug(
            "FMDN resolution is_reversed=True for EID (byte order reversed)",
        )

    return match, EidResolutionStatus.NOT_EVALUATED
```

#### 3.3.2 Erweiterung des SeenEid Dataclass in manager.py

```python
@dataclass
class SeenEid:
    """Stores an EID along with its resolution result and metadata."""

    eid: bytes
    first_seen: float
    last_seen: float
    source_mac: str
    resolution_status: EidResolutionStatus | str
    device_id: str | None = None
    canonical_id: str | None = None
    check_count: int = 1
    # NEU: Diagnostik-Felder
    time_offset: int | None = None
    is_reversed: bool | None = None
```

#### 3.3.3 Aktualisierung von record_resolution_success()

```python
def record_resolution_success(
    self,
    eid: bytes,
    source_mac: str,
    device_id: str,
    canonical_id: str | None = None,
    time_offset: int | None = None,      # NEU
    is_reversed: bool | None = None,     # NEU
) -> None:
    """Record a successful EID resolution."""
    self.record_eid_seen(
        eid,
        source_mac,
        resolution_status="RESOLVED",
        device_id=device_id,
        canonical_id=canonical_id,
        time_offset=time_offset,          # NEU
        is_reversed=is_reversed,          # NEU
    )
```

#### 3.3.4 Erweiterung der Diagnostik-Ausgabe

```python
# In get_diagnostics_no_redactions()
entry: dict[str, Any] = {
    "status": status_out,
    "source_mac": seen.source_mac,
    "expires_in": floor(seen.last_seen + PRUNE_TIME_FMDN - nowstamp),
    "check_count": seen.check_count,
    "eid_length": len(seen.eid),
}

# NEU: Diagnostik-Felder einschließen wenn vorhanden
if seen.time_offset is not None:
    entry["time_offset"] = seen.time_offset
if seen.is_reversed is not None:
    entry["is_reversed"] = seen.is_reversed
```

### 3.4 Zu ändernde Dateien

| Datei | Änderung |
|-------|----------|
| `fmdn/integration.py` | Debug-Logging nach erfolgreicher Resolution |
| `fmdn/integration.py` | Übergabe von time_offset/is_reversed an manager |
| `fmdn/manager.py` | SeenEid Dataclass erweitern |
| `fmdn/manager.py` | record_resolution_success() erweitern |
| `fmdn/manager.py` | record_eid_seen() erweitern |
| `fmdn/manager.py` | get_diagnostics_no_redactions() erweitern |

### 3.5 Implementierungsreihenfolge

1. **Schritt 1:** `SeenEid` Dataclass in manager.py erweitern
2. **Schritt 2:** `record_eid_seen()` Parameter hinzufügen
3. **Schritt 3:** `record_resolution_success()` Parameter hinzufügen
4. **Schritt 4:** `get_diagnostics_no_redactions()` erweitern
5. **Schritt 5:** `integration.py` - Felder an Manager übergeben
6. **Schritt 6:** Debug-Logging in integration.py hinzufügen

### 3.6 Logging-Level-Entscheidung

| Bedingung | Level | Begründung |
|-----------|-------|------------|
| `time_offset != 0` | DEBUG | Informativ, aber nicht kritisch |
| `is_reversed == True` | DEBUG | Informativ, selten relevant |
| Beide kombiniert | DEBUG | Keine Warnung, nur Diagnose |

**Rationale:** Diese Felder sind rein diagnostisch und haben keinen Einfluss auf die Funktion. DEBUG-Level ist angemessen, um Logs nicht zu überfluten.

### 3.7 Checkliste vor Implementierung

- [x] Alle Stellen identifiziert wo Felder gesetzt werden müssen
- [x] Dataclass-Erweiterung abwärtskompatibel (defaults=None)
- [x] Logging-Level entschieden (DEBUG)
- [x] Diagnostik-Format definiert
- [x] Implementierungsreihenfolge festgelegt

### 3.8 Risikobewertung

**Risiko: MINIMAL**
- Nur additive Änderungen (neue optionale Felder)
- Nur DEBUG-Level Logging
- Keine Änderung an der Resolution-Logik
- Abwärtskompatibel durch default=None

---

## 4. Test-Plan

### 4.1 Bestehende Tests

Die bestehenden Tests in `test_fmdn_shared_tracker.py` und `test_fmdn_end_to_end.py` decken bereits:
- Shared Tracker Collision Prevention
- Cache-Lookup-Logik
- Config Flow Visibility

### 4.2 Neue Tests für Typisierung

```python
# tests/test_fmdn_eid_match.py (NEU)

class TestEIDMatchTyping:
    """Tests for the local EIDMatch type definition."""

    def test_eid_match_fields_accessible(self):
        """Verify all EIDMatch fields are accessible."""
        match = EIDMatch(
            device_id="test_device_id",
            config_entry_id="test_entry_id",
            canonical_id="test_canonical_id",
            time_offset=120,
            is_reversed=False,
        )
        assert match.device_id == "test_device_id"
        assert match.config_entry_id == "test_entry_id"
        assert match.canonical_id == "test_canonical_id"
        assert match.time_offset == 120
        assert match.is_reversed is False

    def test_convert_from_external_match(self):
        """Test conversion from external resolver match."""
        # Simulate external match object
        class ExternalMatch:
            device_id = "ext_device"
            config_entry_id = "ext_entry"
            canonical_id = "ext_canonical"
            time_offset = 60
            is_reversed = True

        external = ExternalMatch()
        local = _convert_to_eid_match(external)

        assert local is not None
        assert local.device_id == "ext_device"
        assert local.time_offset == 60
        assert local.is_reversed is True

    def test_convert_handles_missing_fields(self):
        """Test conversion when external match has missing fields."""
        class PartialMatch:
            device_id = "partial_device"
            # Missing other fields

        partial = PartialMatch()
        local = _convert_to_eid_match(partial)

        assert local is not None
        assert local.device_id == "partial_device"
        assert local.config_entry_id == ""  # Default
        assert local.time_offset == 0  # Default
```

### 4.3 Neue Tests für Diagnostik-Logging

```python
# tests/test_fmdn_diagnostics.py (ergänzen)

class TestEIDDiagnostics:
    """Tests for EID resolution diagnostics."""

    def test_seen_eid_includes_diagnostic_fields(self):
        """Verify SeenEid stores diagnostic fields."""
        manager = BermudaFmdnManager()
        manager.record_resolution_success(
            eid=b"\x01\x02\x03",
            source_mac="AA:BB:CC:DD:EE:FF",
            device_id="test_device",
            canonical_id="test_canonical",
            time_offset=120,
            is_reversed=True,
        )

        diagnostics = manager.get_diagnostics_no_redactions()
        resolved = diagnostics["resolved_eids"]

        # Find our entry
        eid_hex = b"\x01\x02\x03".hex()
        assert eid_hex in resolved
        assert resolved[eid_hex]["time_offset"] == 120
        assert resolved[eid_hex]["is_reversed"] is True

    def test_diagnostics_omits_none_fields(self):
        """Verify None diagnostic fields are not included."""
        manager = BermudaFmdnManager()
        manager.record_resolution_success(
            eid=b"\x01\x02\x03",
            source_mac="AA:BB:CC:DD:EE:FF",
            device_id="test_device",
            # time_offset and is_reversed not provided
        )

        diagnostics = manager.get_diagnostics_no_redactions()
        resolved = diagnostics["resolved_eids"]
        eid_hex = b"\x01\x02\x03".hex()

        # Should NOT have these keys when None
        assert "time_offset" not in resolved[eid_hex]
        assert "is_reversed" not in resolved[eid_hex]
```

---

## 5. Implementierungs-Checkliste

### Phase 1: CLAUDE.md Dokumentation (HIGH)

- [ ] Sektion "GoogleFindMy-HA EID Resolver API Reference" erstellen
- [ ] EIDMatch Feldtabelle einfügen
- [ ] Methodensignaturen dokumentieren
- [ ] Zugriffsmuster dokumentieren
- [ ] Error-Handling dokumentieren
- [ ] Commit: "docs: Add complete EID Resolver API reference to CLAUDE.md"

### Phase 2: Typisiertes EIDMatch (MEDIUM)

- [ ] `EIDMatch` NamedTuple in integration.py erstellen
- [ ] `_convert_to_eid_match()` Hilfsfunktion erstellen
- [ ] `EidResolver` Protocol aktualisieren
- [ ] `process_resolution_with_status()` aktualisieren
- [ ] `process_resolution_all_with_status()` aktualisieren
- [ ] `register_source()` Signatur aktualisieren
- [ ] `getattr()`-Aufrufe durch direkte Feldnamen ersetzen
- [ ] Tests in test_fmdn_eid_match.py erstellen
- [ ] Commit: "feat(fmdn): Add typed EIDMatch for improved type safety"

### Phase 3: Diagnostik-Logging (MEDIUM)

- [ ] `SeenEid` Dataclass erweitern
- [ ] `record_eid_seen()` Parameter hinzufügen
- [ ] `record_resolution_success()` Parameter hinzufügen
- [ ] `get_diagnostics_no_redactions()` erweitern
- [ ] Integration.py - Felder an Manager übergeben
- [ ] Debug-Logging hinzufügen
- [ ] Tests in test_fmdn_diagnostics.py erstellen
- [ ] Commit: "feat(fmdn): Add diagnostic logging for time_offset and is_reversed"

### Finale Validierung

- [ ] `python -m ruff check --fix`
- [ ] `python -m ruff format`
- [ ] `python -m mypy --strict`
- [ ] `python -m pytest tests/test_fmdn*.py -v`
- [ ] Alle Tests bestehen
- [ ] Push to branch

---

## 6. Rollback-Plan

Falls nach Implementierung Probleme auftreten:

### 6.1 Typisierung (Phase 2)

Die Typisierung ist intern und hat keine externen Abhängigkeiten. Rollback:
```bash
git revert <commit-hash>
```

### 6.2 Diagnostik-Logging (Phase 3)

Diagnostik-Felder haben default=None und sind optional. Rollback:
```bash
git revert <commit-hash>
```

### 6.3 CLAUDE.md (Phase 1)

Reine Dokumentation, kein funktionales Risiko. Bei Fehlern einfach korrigieren.

---

## 7. Zusammenfassung

| Action Item | Risiko | Komplexität | Abhängigkeiten |
|-------------|--------|-------------|----------------|
| CLAUDE.md Doku | MINIMAL | Niedrig | Keine |
| Typisiertes EIDMatch | NIEDRIG | Mittel | Phase 1 |
| Diagnostik-Logging | MINIMAL | Niedrig | Phase 2 |

**Empfohlene Reihenfolge:** Phase 1 → Phase 2 → Phase 3

**Geschätzte Implementierungszeit:**
- Phase 1: ~15 Minuten
- Phase 2: ~45 Minuten
- Phase 3: ~30 Minuten
- Tests: ~30 Minuten
- **Gesamt: ~2 Stunden**

---

## 8. Freigabe zur Implementierung

Dieser Plan ist vollständig recherchiert und enthält alle notwendigen Details für eine risikofreie Implementierung. Alle Code-Stellen sind identifiziert, Abhängigkeiten geklärt und Rollback-Optionen definiert.

**Status: BEREIT ZUR UMSETZUNG**
