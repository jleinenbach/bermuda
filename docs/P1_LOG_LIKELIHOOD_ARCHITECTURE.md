# P1: Relative Margin Architektur (Vereinfacht)

## Kernidee

Eine einzige Frage entscheidet über Stabilität:

> **"Wie viel besser ist der beste Kandidat gegenüber dem zweitbesten?"**

```python
margin = (best_score - second_score) / best_score
```

---

## Architektur-Diagramm

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Relative Margin Entscheidungslogik                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  UKF match_fingerprints() liefert:                                           │
│    [(Küche, 0.75), (Wohnzimmer, 0.72), (Schlafzimmer, 0.15)]                │
│                                                                              │
│                              │                                               │
│                              ▼                                               │
│               ┌──────────────────────────────┐                               │
│               │  Margin berechnen            │                               │
│               │                              │                               │
│               │  best   = 0.75 (Küche)       │                               │
│               │  second = 0.72 (Wohnzimmer)  │                               │
│               │                              │                               │
│               │  margin = (0.75 - 0.72)      │                               │
│               │           ─────────────      │                               │
│               │              0.75            │                               │
│               │                              │                               │
│               │        = 0.04 (4%)           │                               │
│               └──────────────┬───────────────┘                               │
│                              │                                               │
│                              ▼                                               │
│               ┌──────────────────────────────┐                               │
│               │  margin ≥ 15%?               │                               │
│               └──────────────┬───────────────┘                               │
│                      │               │                                       │
│                 JA   │               │  NEIN (unsicher)                      │
│                      ▼               ▼                                       │
│         ┌─────────────────┐  ┌─────────────────────────────────┐            │
│         │ SICHERE         │  │ UNSICHERE Entscheidung          │            │
│         │ Entscheidung    │  │                                 │            │
│         │                 │  │ Ist aktueller Raum unter Top-2? │            │
│         │ Normal-         │  └─────────────┬───────────────────┘            │
│         │ Threshold:      │          │             │                        │
│         │ 0.30            │     JA   │             │  NEIN                  │
│         └────────┬────────┘          ▼             ▼                        │
│                  │         ┌─────────────┐  ┌─────────────────┐             │
│                  │         │ BEHALTEN    │  │ Erhöhter        │             │
│                  │         │ (Hysterese) │  │ Threshold: 0.50 │             │
│                  │         └─────────────┘  └────────┬────────┘             │
│                  │                                   │                      │
│                  └───────────────┬───────────────────┘                      │
│                                  │                                          │
│                                  ▼                                          │
│                   ┌──────────────────────────────┐                          │
│                   │  Score ≥ Threshold?          │                          │
│                   └──────────────┬───────────────┘                          │
│                          │               │                                  │
│                     JA   │               │  NEIN                            │
│                          ▼               ▼                                  │
│                   ┌─────────────┐  ┌─────────────┐                          │
│                   │ UKF-Winner  │  │ Fallback:   │                          │
│                   │ anwenden    │  │ Min-Distance│                          │
│                   └─────────────┘  └─────────────┘                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Implementierung

### Neue Funktion in `area_selection.py`

```python
def _calculate_decision_margin(
    self,
    scores: list[tuple[str, float]],
) -> tuple[float, bool]:
    """
    Berechne relative Margin zwischen Top-2 Kandidaten.

    Returns:
        (margin, is_confident)
        margin: 0.0-1.0, wie viel besser ist #1 vs #2
        is_confident: True wenn margin >= UKF_MIN_DECISION_MARGIN
    """
    if len(scores) < 2:
        # Nur ein Kandidat: volle Konfidenz (keine Alternative)
        return (1.0, True)

    best_score = scores[0][1]
    second_score = scores[1][1]

    if best_score <= 0:
        return (0.0, False)

    margin = (best_score - second_score) / best_score
    is_confident = margin >= UKF_MIN_DECISION_MARGIN

    return (margin, is_confident)
```

### Angepasste UKF-Entscheidungslogik

```python
def _refresh_area_by_ukf(self, device: BermudaDevice) -> bool:
    """UKF-basierte Raumauswahl mit Margin-Stabilisierung."""

    # 1. Fingerprint-Matching
    matches = ukf.match_fingerprints(profiles)
    if not matches:
        return False

    best_area, best_score = matches[0][0], matches[0][2]

    # 2. Margin berechnen
    margin, is_confident = self._calculate_decision_margin(
        [(m[0], m[2]) for m in matches]
    )

    # 3. Threshold bestimmen
    if is_confident:
        # Sichere Entscheidung: normaler Threshold
        effective_threshold = UKF_MIN_MATCH_SCORE  # 0.30
    else:
        # Unsichere Entscheidung
        current_in_top2 = device.area_id in [matches[0][0], matches[1][0]]

        if current_in_top2:
            # Aktueller Raum ist Kandidat → BEHALTEN
            device.area_tests.reason = "UKF unsicher, behalte aktuellen Raum"
            return True  # Keine Änderung
        else:
            # Aktueller Raum nicht unter Top-2 → erhöhter Threshold
            effective_threshold = UKF_UNCERTAIN_THRESHOLD  # 0.50

    # 4. Score-Prüfung
    if best_score < effective_threshold:
        return False  # Fallback zu Min-Distance

    # 5. Diagnostik speichern
    device.area_tests.ukf_margin = margin
    device.area_tests.ukf_margin_confident = is_confident

    # 6. Anwenden
    return self._apply_ukf_selection(device, best_area, best_score, ...)
```

---

## Konstanten

```python
# In const.py

# Minimum-Margin für "sichere" Entscheidung
# 15% bedeutet: Best muss mindestens 15% besser sein als Second
UKF_MIN_DECISION_MARGIN = 0.15

# Erhöhter Threshold bei unsicherer Entscheidung
# Nur wenn aktueller Raum NICHT unter Top-2
UKF_UNCERTAIN_THRESHOLD = 0.50

# Bestehende Konstanten (unverändert)
UKF_MIN_MATCH_SCORE = 0.30
UKF_RETENTION_THRESHOLD = 0.15
```

---

## Beispiele

### Beispiel 1: Sichere Entscheidung

```
Scores: Küche=0.75, Wohnzimmer=0.50, Schlafzimmer=0.15
Margin: (0.75 - 0.50) / 0.75 = 33%

33% ≥ 15% → SICHER
Threshold: 0.30 (normal)
0.75 ≥ 0.30 → Küche gewinnt
```

### Beispiel 2: Unsicher, aktueller Raum in Top-2

```
Aktueller Raum: Wohnzimmer
Scores: Küche=0.75, Wohnzimmer=0.72, Schlafzimmer=0.15
Margin: (0.75 - 0.72) / 0.75 = 4%

4% < 15% → UNSICHER
Wohnzimmer in Top-2? JA → BEHALTEN
Ergebnis: Bleibt in Wohnzimmer (keine Änderung)
```

### Beispiel 3: Unsicher, aktueller Raum NICHT in Top-2

```
Aktueller Raum: Schlafzimmer
Scores: Küche=0.75, Wohnzimmer=0.72, Schlafzimmer=0.15
Margin: (0.75 - 0.72) / 0.75 = 4%

4% < 15% → UNSICHER
Schlafzimmer in Top-2? NEIN → Erhöhter Threshold
Threshold: 0.50
0.75 ≥ 0.50 → Küche gewinnt (trotz Unsicherheit)
```

---

## Diagnostik (AreaTests Erweiterung)

```python
@dataclass(slots=True)
class AreaTests:
    # ... bestehende Felder ...

    # NEU: Margin-Diagnostik (nur 2 Felder!)
    ukf_margin: float | None = None           # 0.0-1.0
    ukf_margin_confident: bool | None = None  # True wenn ≥15%
```

### Sensor-Anzeige

```
SICHER:    "UKF | Küche | Score:0.75 | Margin:33%"
UNSICHER:  "UKF | Küche | Score:0.75 | Margin:4% ⚠️"
BEHALTEN:  "UKF-HOLD | Wohnzimmer | Margin:4% (Top-2)"
```

---

## Vergleich: Alt vs. Neu

| Aspekt | Softmax-Posterior (verworfen) | Relative Margin (neu) |
|--------|------------------------------|----------------------|
| Codezeilen | ~50 | ~15 |
| Neue Konstanten | 4 | 2 |
| Edge Cases | 4 | 1 |
| Mathematik | exp, log, Summen | Division |
| Verständlichkeit | Mittel | Hoch |
| Ergebnis | Identisch | Identisch |

---

## Zusammenfassung

**Eine Formel, eine Entscheidung:**

```python
margin = (best - second) / best

if margin < 0.15 and current_room in top_2:
    keep_current_room()  # Stabilität
```

Keine Fallbacks. Keine Softmax. Keine Temperatur-Parameter.

---

**Erstellt:** 2026-01-26
**Status:** Bereit für Implementierung
