# Verbesserungsvorschläge: Fingerprint-Architektur

**Ziel:** Rating von 7/10 auf 9/10 verbessern
**Fokus:** Erkennungsgenauigkeit und Raum-Unterscheidbarkeit

---

## Priorisierte Verbesserungen (Impact-Effort-Matrix)

```
                        Impact (Erkennungsgenauigkeit)
                        Hoch                    Niedrig
                    ┌─────────────────────────────────────┐
          Niedrig   │  ★★★ P1: Log-Likelihood  │  P3: Docs │
                    │      Normalisierung       │           │
  Aufwand           ├─────────────────────────────────────┤
                    │  ★★  P2: Konfidenz-      │           │
          Hoch      │      gewichtetes Lernen  │  P4: Full │
                    │                          │  Covariance│
                    └─────────────────────────────────────┘
```

---

## P1: Log-Likelihood-Ratio Normalisierung ★★★

### Problem (Rating-Verlust: -1.5 Punkte)

Aktuell werden absolute Scores verglichen:

```
Messung: RSSI = [-72, -78, -85]

Küche:     μ = [-70, -80, -85], Score = 0.75
Wohnzimmer: μ = [-71, -79, -84], Score = 0.72  ← Fast identisch!
Schlafzimmer: μ = [-90, -60, -70], Score = 0.15

Problem: Küche und Wohnzimmer sind kaum unterscheidbar!
         Der Score sagt nicht "wie viel besser ist Küche?"
```

### Lösung: Posterior-Wahrscheinlichkeiten

```python
# Vorher (absolut):
scores = {"kitchen": 0.75, "living": 0.72, "bedroom": 0.15}
winner = "kitchen"  # Knapper Sieg

# Nachher (normalisiert):
total = 0.75 + 0.72 + 0.15  # = 1.62
posteriors = {
    "kitchen": 0.75/1.62,   # = 0.463 (46.3%)
    "living": 0.72/1.62,    # = 0.444 (44.4%)
    "bedroom": 0.15/1.62    # = 0.093 (9.3%)
}
# Jetzt sichtbar: Küche vs Wohnzimmer ist unsicher!
# → Höheren Threshold für Wechsel anwenden
```

### Implementierung

**Datei:** `custom_components/bermuda/filters/ukf.py`

```python
def match_fingerprints(
    self,
    profiles: dict[str, AreaProfile],
    current_area_id: str | None = None,
) -> list[tuple[str, float, float]]:
    """
    Match current UKF state against area fingerprints.

    Returns:
        List of (area_id, raw_score, posterior_probability)
        sorted by posterior descending.
    """
    raw_scores: list[tuple[str, float]] = []

    for area_id, profile in profiles.items():
        # ... existing Mahalanobis distance calculation ...
        raw_scores.append((area_id, score))

    # NEU: Normalize to posteriors
    posteriors = self._normalize_to_posteriors(raw_scores)

    # Combine raw and posterior for downstream decisions
    results = [
        (area_id, raw, posteriors.get(area_id, 0.0))
        for area_id, raw in raw_scores
    ]

    return sorted(results, key=lambda x: x[2], reverse=True)

def _normalize_to_posteriors(
    self,
    scores: list[tuple[str, float]]
) -> dict[str, float]:
    """
    Convert raw likelihood scores to posterior probabilities.

    Uses softmax-style normalization with temperature scaling
    to handle score magnitude differences.
    """
    if not scores:
        return {}

    # Numerical stability: subtract max before exp
    max_score = max(s for _, s in scores)
    if max_score < 1e-10:
        # All scores near zero - uniform distribution
        n = len(scores)
        return {area: 1.0/n for area, _ in scores}

    # Temperature parameter controls "sharpness"
    # Higher T = more uniform, Lower T = winner-take-all
    T = 1.0  # Can be tuned

    exp_scores = [(area, math.exp((s - max_score) / T)) for area, s in scores]
    total = sum(e for _, e in exp_scores)

    return {area: e / total for area, e in exp_scores}
```

**Datei:** `custom_components/bermuda/area_selection.py`

```python
def _refresh_area_by_ukf(self, device: BermudaDevice) -> bool:
    """UKF-based area selection with posterior probabilities."""

    # Get matches with posteriors
    matches = ukf.match_fingerprints(profiles, device.area_id)

    if not matches:
        return False

    best_area, raw_score, posterior = matches[0]
    second_area, _, second_posterior = matches[1] if len(matches) > 1 else (None, 0, 0)

    # NEU: Entscheidungslogik basierend auf Posterior-Differenz
    posterior_margin = posterior - second_posterior

    # Unsichere Entscheidung? (< 20% Vorsprung)
    if posterior_margin < 0.20:
        # Höherer Threshold für Room-Wechsel
        effective_threshold = UKF_MIN_MATCH_SCORE * 1.5  # 0.45 statt 0.30
        device.area_tests.ukf_decision_uncertain = True
        device.area_tests.ukf_posterior_margin = posterior_margin
    else:
        effective_threshold = UKF_MIN_MATCH_SCORE
        device.area_tests.ukf_decision_uncertain = False

    # Store for diagnostics
    device.area_tests.ukf_posterior = posterior
    device.area_tests.ukf_posterior_margin = posterior_margin

    if raw_score < effective_threshold:
        return False  # Fall back to min-distance

    # ... rest of existing logic ...
```

### Erwarteter Impact

| Szenario | Vorher | Nachher |
|----------|--------|---------|
| Ähnliche Räume (Küche/Wohnzimmer) | Häufiges Flackern | Stabiler (höherer Threshold bei Unsicherheit) |
| Eindeutige Räume | Score 0.85 gewinnt | Posterior 0.75 zeigt Dominanz |
| Drei-Raum-Entscheidung | Nur Top-2 sichtbar | Volle Wahrscheinlichkeitsverteilung |

**Geschätzte Rating-Verbesserung:** +1.0 bis +1.5 Punkte

---

## P2: Konfidenz-gewichtetes Auto-Learning ★★

### Problem (Rating-Verlust: -0.5 Punkte)

Auto-Learning aktualisiert mit konstanter Rate, unabhängig von der Entscheidungs-Konfidenz:

```
Szenario A: Gerät klar in Küche (Posterior = 0.85)
  → Auto-Learning Update: ✓ Korrekt

Szenario B: Gerät unsicher zwischen Küche/Wohnzimmer (Posterior = 0.52)
  → Auto-Learning Update: ⚠️ 48% Chance auf falsches Lernen!
```

### Lösung: Posterior-basiertes Learning Gate

```python
# In area_selection.py

# Konstante
AUTO_LEARNING_MIN_CONFIDENCE = 0.65  # Nur bei >65% Konfidenz lernen

def _update_auto_learning(
    self,
    device: BermudaDevice,
    area_id: str,
    posterior: float,
    readings: dict[str, float],
) -> bool:
    """
    Update auto-learning only when confident.

    Returns True if update was performed.
    """
    # Gate 1: Minimum confidence
    if posterior < AUTO_LEARNING_MIN_CONFIDENCE:
        self._auto_learning_stats.record_update(
            performed=False,
            reason="low_confidence",
            confidence=posterior,
            device_address=device.address,
        )
        return False

    # Gate 2: Minimum interval (existing)
    if not self._check_min_interval(device):
        return False

    # Gate 3: New data check (existing)
    if not self._has_new_advert_data(device):
        return False

    # Perform weighted update
    # Higher confidence = more influence
    learning_weight = self._calculate_learning_weight(posterior)

    profile = self._get_or_create_profile(device.address, area_id)
    profile.update_auto(readings, weight=learning_weight)

    return True

def _calculate_learning_weight(self, posterior: float) -> float:
    """
    Map posterior probability to learning weight.

    Uses sigmoid-like function:
    - posterior = 0.65 → weight = 0.3 (minimal learning)
    - posterior = 0.80 → weight = 0.7 (normal learning)
    - posterior = 0.95 → weight = 1.0 (full learning)
    """
    # Linear interpolation between min/max
    MIN_POSTERIOR = 0.65
    MAX_POSTERIOR = 0.95
    MIN_WEIGHT = 0.3
    MAX_WEIGHT = 1.0

    if posterior <= MIN_POSTERIOR:
        return MIN_WEIGHT
    if posterior >= MAX_POSTERIOR:
        return MAX_WEIGHT

    # Linear interpolation
    t = (posterior - MIN_POSTERIOR) / (MAX_POSTERIOR - MIN_POSTERIOR)
    return MIN_WEIGHT + t * (MAX_WEIGHT - MIN_WEIGHT)
```

### Kalman-Filter Erweiterung für gewichtetes Update

**Datei:** `custom_components/bermuda/filters/kalman.py`

```python
def update(
    self,
    measurement: float,
    timestamp: float | None = None,
    weight: float = 1.0,
) -> float:
    """
    Update with optional confidence weight.

    Args:
        measurement: The observed value
        timestamp: Optional timestamp for time-aware filtering
        weight: Confidence weight (0.0-1.0), affects measurement noise
    """
    # Weight < 1.0 increases effective measurement noise
    # → Less trust in this measurement
    effective_measurement_noise = self.measurement_noise / max(weight, 0.1)

    # ... rest of existing Kalman update with effective_measurement_noise ...
```

### Erwarteter Impact

| Szenario | Vorher | Nachher |
|----------|--------|---------|
| Eindeutige Zuweisung (>80%) | Normales Lernen | Schnelles Lernen (weight=0.7-1.0) |
| Unsichere Zuweisung (65-80%) | Normales Lernen | Vorsichtiges Lernen (weight=0.3-0.7) |
| Grenzfall (<65%) | Fehlerhaftes Lernen möglich | Kein Lernen |

**Geschätzte Rating-Verbesserung:** +0.5 Punkte

---

## P3: Diagnostik-Erweiterung für Posteriors

### Erweiterung von AreaTests

**Datei:** `custom_components/bermuda/area_selection.py`

```python
@dataclass(slots=True)
class AreaTests:
    """Diagnostic information for area selection decisions."""

    # ... existing fields ...

    # NEU: Posterior-Diagnostik
    ukf_posterior: float | None = None
    ukf_posterior_margin: float | None = None
    ukf_decision_uncertain: bool = False
    ukf_all_posteriors: list[tuple[str, float]] | None = None

    # NEU: Auto-Learning Diagnostik
    auto_learning_skipped_low_confidence: bool = False
    auto_learning_confidence: float | None = None
    auto_learning_weight: float | None = None

    def sensortext(self) -> str:
        """Generate sensor display text."""
        parts = [self.decision_path]

        # ... existing parts ...

        # NEU: Posterior info
        if self.ukf_posterior is not None:
            parts.append(f"P:{self.ukf_posterior:.0%}")
            if self.ukf_posterior_margin is not None:
                parts.append(f"Δ:{self.ukf_posterior_margin:.0%}")

        # Uncertainty warning
        if self.ukf_decision_uncertain:
            parts.append("⚠️UNSICHER")

        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for Home Assistant attributes."""
        result = {
            # ... existing fields ...
        }

        # NEU: Posterior fields
        if self.ukf_posterior is not None:
            result["ukf_posterior"] = round(self.ukf_posterior, 3)
        if self.ukf_posterior_margin is not None:
            result["ukf_posterior_margin"] = round(self.ukf_posterior_margin, 3)
        if self.ukf_decision_uncertain:
            result["ukf_decision_uncertain"] = True
        if self.ukf_all_posteriors:
            result["ukf_all_posteriors"] = [
                {"area": a, "posterior": round(p, 3)}
                for a, p in self.ukf_all_posteriors[:5]  # Top 5
            ]

        # Auto-learning diagnostics
        if self.auto_learning_skipped_low_confidence:
            result["auto_learning_skipped"] = "low_confidence"
            result["auto_learning_confidence"] = self.auto_learning_confidence

        return result
```

---

## P4: Vollständige Kreuz-Kovarianz (Zukunft)

### Problem (Rating-Verlust: -0.5 Punkte)

Aktuell: Nur Diagonalelemente (marginale Varianzen) gespeichert.

```
Profil speichert:
  Scanner A: μ=-75, σ²=4
  Scanner B: μ=-80, σ²=5
  Scanner C: μ=-82, σ²=6

Fehlend:
  Cov(A,B) = ?  (typisch: -2 bis +2)
  Cov(A,C) = ?
  Cov(B,C) = ?
```

### Analyse: Wann ist Kreuz-Kovarianz wichtig?

| Situation | Diagonale ausreichend? | Kreuz-Kovarianz nötig? |
|-----------|------------------------|------------------------|
| 2 Scanner | ✓ Meist ok | Hilft bei Grenzfällen |
| 3+ Scanner | ⚠️ Suboptimal | ✓ Signifikant besser |
| Scanner nah beieinander | ✓ Hohe Korrelation ignorierbar | Würde Präzision erhöhen |
| Scanner weit verteilt | ✓ Geringe Korrelation | Minimaler Benefit |

### Vorgeschlagene Implementierung (Mittelfristig)

```python
@dataclass
class AreaProfileWithCovariance:
    """
    Area profile with full covariance tracking.

    Uses incremental covariance update (Welford's algorithm)
    to avoid storing all samples.
    """

    _scanner_order: list[str]  # Fixed order for matrix indices
    _mean: list[float]  # N-dimensional mean
    _cov: list[list[float]]  # N×N covariance matrix
    _sample_count: int

    # Separate button/auto as before
    _mean_button: list[float] | None = None
    _cov_button: list[list[float]] | None = None
    _sample_count_button: int = 0

    def update_auto(self, readings: dict[str, float]) -> None:
        """
        Incremental covariance update using Welford's algorithm.

        For each new sample x:
          n += 1
          delta = x - mean
          mean += delta / n
          delta2 = x - mean
          M2 += outer(delta, delta2)  # For covariance
          cov = M2 / (n - 1)
        """
        x = self._readings_to_vector(readings)
        if x is None:
            return

        self._sample_count += 1
        n = self._sample_count

        delta = [x[i] - self._mean[i] for i in range(len(x))]
        self._mean = [self._mean[i] + delta[i] / n for i in range(len(x))]
        delta2 = [x[i] - self._mean[i] for i in range(len(x))]

        # Update covariance matrix (Welford)
        for i in range(len(x)):
            for j in range(len(x)):
                self._M2[i][j] += delta[i] * delta2[j]

        if n > 1:
            self._cov = [
                [self._M2[i][j] / (n - 1) for j in range(len(x))]
                for i in range(len(x))
            ]
```

### Speicher-Impact

| Scanner-Anzahl | Diagonal (aktuell) | Voll (vorgeschlagen) | Overhead |
|----------------|--------------------|-----------------------|----------|
| 3 | 3 floats | 9 floats | 3× |
| 5 | 5 floats | 25 floats | 5× |
| 10 | 10 floats | 100 floats | 10× |

**Empfehlung:** Für typische Installationen (3-5 Scanner) ist der Overhead akzeptabel.

---

## Implementierungsplan

### Phase 1: P1 - Log-Likelihood (1-2 Tage)

1. `ukf.py`: `_normalize_to_posteriors()` hinzufügen
2. `ukf.py`: `match_fingerprints()` erweitern
3. `area_selection.py`: Posterior in Entscheidungslogik einbauen
4. `area_selection.py`: AreaTests um Posterior-Felder erweitern
5. Tests schreiben
6. Dokumentation aktualisieren

### Phase 2: P2 - Konfidenz-Lernen (1 Tag)

1. `kalman.py`: `weight` Parameter hinzufügen
2. `area_selection.py`: `_update_auto_learning()` mit Confidence-Gate
3. `const.py`: `AUTO_LEARNING_MIN_CONFIDENCE` hinzufügen
4. Tests erweitern

### Phase 3: P3 - Diagnostik (0.5 Tage)

1. `area_selection.py`: AreaTests erweitern
2. `sensor.py`: extra_state_attributes aktualisieren
3. Tests aktualisieren

### Phase 4: P4 - Kreuz-Kovarianz (Optional, 2-3 Tage)

1. Neues `area_profile_cov.py` oder bestehende erweitern
2. Migration bestehender Profile
3. UKF-Integration
4. Performance-Tests

---

## Erwartete Gesamtverbesserung

| Verbesserung | Rating-Beitrag | Kumulativ |
|--------------|----------------|-----------|
| Ausgangszustand | 7.0 | 7.0 |
| P1: Log-Likelihood | +1.0 bis +1.5 | 8.0-8.5 |
| P2: Konfidenz-Lernen | +0.5 | 8.5-9.0 |
| P3: Diagnostik | +0.0 (UX only) | 8.5-9.0 |
| P4: Kreuz-Kovarianz | +0.5 | 9.0-9.5 |

**Erreichbares Ziel:** 9/10 mit P1+P2+P3

---

## Risiko-Analyse

### P1 (Log-Likelihood)
- **Risiko:** Niedrig
- **Grund:** Additive Änderung, bestehende Scores bleiben verfügbar
- **Rollback:** Einfach (Posterior ignorieren)

### P2 (Konfidenz-Lernen)
- **Risiko:** Mittel
- **Grund:** Ändert Lernverhalten, könnte anfangs langsamer konvergieren
- **Mitigation:** Konservative Schwellwerte (65%), ausführliches Logging

### P4 (Kreuz-Kovarianz)
- **Risiko:** Hoch
- **Grund:** Fundamentale Datenstruktur-Änderung, Migration nötig
- **Mitigation:** Nur wenn P1+P2 nicht ausreichen

---

**Erstellt:** 2026-01-26
**Status:** Vorschlag zur Implementierung
