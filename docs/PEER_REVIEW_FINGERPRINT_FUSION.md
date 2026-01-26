# Peer Review: Fingerprint-Fusion-Architektur

**Gutachter:** Perspektive eines Statistikers und Physikers
**Datum:** 2026-01-26
**Gegenstand:** Zusammenführung von User-gelernten Fingerabdrücken verschiedener Geräte zu Raum-Fingerabdrücken

---

## Executive Summary

Die aktuelle Implementierung zeigt **solide ingenieurtechnische Lösungen**, weist jedoch aus streng statistischer Sicht **mehrere Abweichungen von optimaler Bayes'scher Inferenz** auf. Diese Abweichungen sind größtenteils **bewusste pragmatische Kompromisse**, die in einem Echtzeitsystem mit begrenzten Ressourcen gerechtfertigt sind.

**Gesamtbewertung:** 7/10 - Funktional robust, aber mit Optimierungspotential

---

## 1. Multi-Gerät-Aggregation für Raum-Fingerabdrücke

### 1.1 Physikalische Annahme (RoomProfile)

**Aktuelle Implementierung:**
```
Scanner A, B: Gerät 1 misst δ₁ = RSSI_A - RSSI_B = 5dB
Scanner A, B: Gerät 2 misst δ₂ = RSSI_A - RSSI_B = 4dB
Scanner A, B: Gerät 3 misst δ₃ = RSSI_A - RSSI_B = 6dB

RoomProfile lernt: E[δ] ≈ 5dB, Var[δ] ≈ 1dB²
```

**Physikalische Kritik:**

Die Annahme "RSSI-Delta ist geräteunabhängig" ist **näherungsweise korrekt**, aber ignoriert:

1. **Geräte-spezifische Antennencharakteristik:**
   - Smartphones haben unterschiedliche Antennen-Positionen
   - Dipol vs. Patch-Antennen haben verschiedene Abstrahldiagramme
   - Bei gleicher Position können zwei Geräte um 3-8dB abweichen

2. **Frequenz-Hopping-Effekte:**
   - BLE nutzt 40 Kanäle (2402-2480 MHz)
   - Fading ist frequenzabhängig
   - Verschiedene Geräte scannen verschiedene Kanäle zu verschiedenen Zeiten

3. **Multipath-Interferenz:**
   - Konstruktive/destruktive Interferenz ist positions- und frequenzabhängig
   - Verschiebung um λ/4 ≈ 3cm kann ±10dB Änderung bewirken

**Statistische Konsequenz:**

Die naiven Kalman-Updates mit `measurement_noise = 25.0 dB²` (σ ≈ 5dB) erfassen diese Variabilität **implizit aber nicht optimal**. Eine bessere Modellierung wäre:

```python
# Vorschlag: Heteroskedastisches Modell
total_variance = physical_variance + device_variance[device_type] + position_variance
```

**Empfehlung:**
- **Kurzfristig (akzeptabel):** Die aktuelle Implementierung ist für 5m-Genauigkeit ausreichend
- **Langfristig (optimal):** Geräte-Bias-Schätzung durch Scanner-Kalibrierung einführen

### 1.2 Aggregationsmethode

**Aktuelle Implementierung:**
- Jedes Gerät aktualisiert denselben `ScannerPairCorrelation`-Kalman-Filter
- Implizite Gleichgewichtung aller Geräte

**Statistische Kritik:**

Dies ist eine **Online-Mittelwertschätzung mit exponentieller Glättung**, nicht ein korrektes hierarchisches Bayes-Modell. Der korrekte Ansatz wäre:

```
Hierarchisches Modell:
  θ_room ~ N(μ_prior, σ²_prior)              # Raum-Parameter
  θ_device|θ_room ~ N(θ_room, σ²_device)     # Geräte-Offset
  y_obs|θ_device ~ N(θ_device, σ²_noise)      # Messung
```

**Jedoch:** Der implementierte Ansatz konvergiert für große Stichproben gegen den korrekten Mittelwert und ist **recheneffizienter**.

---

## 2. Button-Training vs. Auto-Learning Fusion

### 2.1 Clamped Bayesian Fusion: Statistische Analyse

**Mathematische Formulierung:**
```
Standard Bayes:
  w_btn = 1/σ²_btn
  w_auto = 1/σ²_auto
  μ_fused = (w_btn·μ_btn + w_auto·μ_auto) / (w_btn + w_auto)

Clamped Fusion (implementiert):
  If w_auto/(w_btn + w_auto) > 0.30:
      w_auto_clamped = w_btn · (0.30/0.70)
  μ_fused = (w_btn·μ_btn + w_auto_clamped·μ_auto) / (w_btn + w_auto_clamped)
```

**Statistische Kritik:**

Die Clamped Fusion **verletzt die Axiome der Bayes'schen Inferenz**:

1. **Verletzung der Likelihood-Prinzip:**
   - Echte Bayes-Inferenz: Posterior ∝ Likelihood × Prior
   - Hier: Posterior wird künstlich beschränkt

2. **Informationsverlust:**
   - Auto-Lernen akkumuliert Information (sinkende Varianz)
   - Diese Information wird bei >30% Gewicht verworfen

3. **Inkonsistente Posterior:**
   - Die resultierende "Posterior" ist keine gültige Wahrscheinlichkeitsverteilung im Bayes'schen Sinn

**JEDOCH - Pragmatische Rechtfertigung:**

Die Clamped Fusion löst ein **reales Problem**, das reine Bayes nicht löst:

```
Problem: Langsamer Modell-Drift

Realität (Woche 1):     Raum A bei -85dB
Realität (Woche 52):    Raum A bei -85dB (unverändert)
Auto-Learning (52 Wo):  -80dB (akkumulierte Fehler durch falsche Raum-Zuweisungen)

Ohne Clamping: μ_fused → -80dB (User-Training verloren)
Mit Clamping:  μ_fused ≈ -83.5dB (User-Intent bleibt dominant)
```

**Empfehlung:**

Die Clamped Fusion ist ein **guter Engineering-Kompromiss**. Alternative (komplexer):

```python
# Robuste Bayes-Fusion mit Outlier-Modell
class RobustBayesFusion:
    def fuse(self, button_samples, auto_samples):
        # Student-t statt Normal: erlaubt Ausreißer
        # df = 3-5 für robuste Schätzung
        posterior = student_t_inference(
            button_samples, weight=0.7,
            auto_samples, weight=0.3,
            df=4
        )
```

### 2.2 Varianz-Handling: Kritische Analyse

**Identifiziertes Problem (BUG 11 & BUG 27):**

```
ALT (Fehlerhaft):
  reset_to_value(rssi, variance=0.1, sample_count=500)
  → Hyper-Precision Paradox: 2dB Abweichung = 6σ = "unmöglich"

NEU (Korrigiert):
  _kalman_button.update(rssi)  # Akkumuliert echte 60 Samples
  → Varianz konvergiert natürlich auf ~2.5-3.0 dB²
```

**Statistische Bewertung:**

Die Korrektur ist **statistisch korrekt**. Die vorherige Implementierung war ein **Kategorienfehler**: Die künstlich niedrige Varianz (0.1) repräsentierte nicht die tatsächliche Unsicherheit, sondern war ein Versuch, "hohe Konfidenz" zu erzwingen.

**Variance Floor (AUTO_LEARNING_VARIANCE_FLOOR = 4.0):**

Dies verhindert das "Variance Starvation"-Problem:

```
Nach 10.000 Samples: Kalman-Varianz → ~0.01 dB²
Normale BLE-Fluktuation: ±3 dB
z-Score: 3 / 0.1 = 30σ = "physikalisch unmöglich"

Mit Floor: Varianz ≥ 4.0 dB²
z-Score: 3 / 2.0 = 1.5σ = "normal"
```

**Empfehlung:** Der Variance Floor ist **physikalisch motiviert und korrekt**.

---

## 3. Kreuz-Korrelationen: Kritische Lücke

### 3.1 Das Problem

Die aktuelle Implementierung speichert **marginale Verteilungen**:

```
AreaProfile:
  _absolute_profiles["scanner_A"] = KalmanFilter(μ=-75, σ²=4)
  _absolute_profiles["scanner_B"] = KalmanFilter(μ=-80, σ²=5)
  _absolute_profiles["scanner_C"] = KalmanFilter(μ=-82, σ²=6)
```

Die **Kreuz-Kovarianz** zwischen Scannern wird **nicht gespeichert**:

```
Fehlend:
  Cov(RSSI_A, RSSI_B) = ?
  Cov(RSSI_A, RSSI_C) = ?
  Cov(RSSI_B, RSSI_C) = ?
```

### 3.2 Physikalische Relevanz

Scanner-RSSI-Werte sind **physikalisch korreliert**:

1. **Positive Korrelation:** Wenn Gerät sich zur Raummitte bewegt, steigen alle RSSI
2. **Negative Korrelation:** Wenn Gerät sich zu Scanner A bewegt, sinkt RSSI bei B
3. **Bedingte Unabhängigkeit:** Bei fester Geräteposition sind die Fading-Prozesse unabhängig

**Konsequenz für Mahalanobis-Distanz:**

Die aktuelle Berechnung verwendet:

```python
# Kombinierte Kovarianz: UKF-State + Profil-Varianz
combined_cov[i][j] = p_cov[i][j] + (fp_var[i] if i == j else 0.0)
#                    ↑ UKF hat Kreuz-Kovarianz
#                                    ↑ Profil hat NUR Diagonale!
```

**Problem:** Die Profil-Varianz ist **nur auf der Diagonale**. Die Kreuz-Kovarianzen des Profils fehlen.

### 3.3 Empfehlung

**Option A (Minimal-Änderung):**
```python
# Annahme: Schwache Korrelation, Diagonale dominiert
# Status quo beibehalten, UKF-Kovarianz kompensiert teilweise
```

**Option B (Theoretisch korrekt):**
```python
@dataclass
class AreaProfileWithCovariance:
    # Statt N einzelner Kalman-Filter:
    # Ein N-dimensionaler Kalman-Filter
    _multivariate_kalman: MultivariateKalmanFilter

    # Speichert volle N×N Kovarianzmatrix
    # Erhöht Speicherbedarf: O(N) → O(N²)
```

**Empfehlung:** Option A für jetzt, Option B als zukünftige Erweiterung falls Präzision unzureichend.

---

## 4. Konkurrierende Fingerabdrücke als Lernhilfe

### 4.1 Aktuelle Situation

Fingerabdrücke verschiedener Räume werden **unabhängig** gelernt und verglichen:

```
Raum-Fingerabdrücke konkurrieren im Matching:

Kitchen:  μ = [-70, -80, -85], score = 0.75
Office:   μ = [-75, -78, -90], score = 0.68
Bedroom:  μ = [-82, -75, -72], score = 0.45

Gewinner: Kitchen (höchster Score)
```

### 4.2 Fehlende Nutzung: Relative Likelihood

**Observation:** Wenn ein Gerät **sicher nicht** in Raum B ist, ist das Information über Raum A!

**Bayes-Theorem mit allen Räumen:**
```
P(Raum A | RSSI) = P(RSSI | Raum A) · P(Raum A) / P(RSSI)

Wobei: P(RSSI) = Σ_i P(RSSI | Raum_i) · P(Raum_i)
```

Die aktuelle Implementierung berechnet **nur den Zähler**, nicht den Nenner.

### 4.3 Konkrete Verbesserungsvorschläge

#### 4.3.1 Log-Likelihood-Ratios für Diskriminierung

```python
def get_discriminative_scores(readings: dict, all_profiles: dict) -> dict:
    """
    Berechnet relative Scores statt absolute.

    Vorteil: Ein Raum mit sehr ähnlichem Fingerabdruck zu vielen anderen
    bekommt niedrigeren Score, auch wenn der absolute Match gut ist.
    """
    likelihoods = {}
    for area_id, profile in all_profiles.items():
        likelihoods[area_id] = profile.get_likelihood(readings)

    total = sum(likelihoods.values())

    # Posterior (relative Wahrscheinlichkeit)
    posteriors = {area: lik/total for area, lik in likelihoods.items()}

    return posteriors
```

#### 4.3.2 Kontrastives Lernen

**Idee:** Wenn Gerät in Raum A bestätigt wird, sind die RSSI-Werte **Positiv-Beispiele** für A und **Negativ-Beispiele** für alle anderen Räume.

```python
def update_contrastive(self, readings: dict, confirmed_area: str):
    """
    Kontrastives Update: Verstärkt Unterschiede zwischen Räumen.
    """
    for area_id, profile in self.profiles.items():
        if area_id == confirmed_area:
            # Positiv: Profil näher an Readings ziehen
            profile.update(readings, learning_rate=+0.1)
        else:
            # Negativ: Profil von Readings wegschieben
            profile.update_negative(readings, learning_rate=-0.02)
```

**Vorsicht:** Negativ-Updates müssen **viel schwächer** sein als Positiv-Updates, um Instabilität zu vermeiden.

#### 4.3.3 Fisher's Linear Discriminant

Statt individuelle Kalman-Filter pro Raum, könnte man **diskriminierende Features** lernen:

```python
def compute_fisher_features(profiles: dict) -> np.ndarray:
    """
    Findet Projektionsrichtung die Räume maximal trennt.

    Maximiert: (μ_A - μ_B)² / (σ²_A + σ²_B)
    """
    # Between-class scatter matrix
    S_B = compute_between_class_scatter(profiles)

    # Within-class scatter matrix
    S_W = compute_within_class_scatter(profiles)

    # Fisher-Kriterium: w = S_W^(-1) · (μ_A - μ_B)
    discriminant_vector = np.linalg.solve(S_W, mean_diff)

    return discriminant_vector
```

### 4.4 Empfehlung für Konkurrierende Fingerabdrücke

| Vorschlag | Implementierungsaufwand | Erwarteter Nutzen |
|-----------|------------------------|-------------------|
| Log-Likelihood-Ratios | Niedrig | Hoch |
| Kontrastives Lernen | Mittel | Mittel |
| Fisher Discriminant | Hoch | Mittel-Hoch |

**Empfehlung:** Log-Likelihood-Ratios als erstes implementieren, da:
- Geringer Implementierungsaufwand
- Keine Änderung der Speicherstruktur
- Mathematisch korrekte Normalisierung

---

## 5. Zusammenfassung: Bewertungsmatrix

| Aspekt | Aktuelle Implementierung | Statistisch optimal | Empfehlung |
|--------|--------------------------|---------------------|------------|
| Multi-Gerät-Aggregation | Implizite Gleichgewichtung | Hierarchisches Bayes | ✓ Akzeptabel |
| Button/Auto Fusion | Clamped (30%) | Robuste Bayes | ✓ Guter Kompromiss |
| Varianz-Handling | Floor + Akkumulation | Korrekt | ✓ Korrekt |
| Kreuz-Kovarianz | Ignoriert | Vollständige Matrix | ⚠️ Verbesserungspotential |
| Konkurrenz-Nutzung | Unabhängig | Diskriminativ | ⚠️ Stark empfohlen |

---

## 6. Priorisierte Aktionsliste

### Priorität 1: Log-Likelihood-Ratios (Niedrig-hängende Frucht)

```python
# In area_selection.py, bei match_fingerprints():

def normalize_scores(scores: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Konvertiere absolute Scores zu Posterior-Wahrscheinlichkeiten."""
    total = sum(s for _, s in scores)
    if total < 1e-10:
        return scores  # Avoid division by zero
    return [(area, score/total) for area, score in scores]
```

### Priorität 2: Adaptive Varianz-Schätzung

Statt konstantem `measurement_noise = 25.0`, adaptive Schätzung:

```python
def estimate_measurement_noise(recent_samples: list[float]) -> float:
    """Schätze aktuelle Messrausch-Varianz aus Residuen."""
    if len(recent_samples) < 5:
        return 25.0  # Default
    residuals = np.diff(recent_samples)
    return np.var(residuals) / 2  # Allan variance approximation
```

### Priorität 3: Dokumentation der Abweichungen

CLAUDE.md um Abschnitt "Statistische Annahmen und Grenzen" erweitern, der die bewussten Abweichungen von optimaler Bayes-Inferenz dokumentiert.

---

## Fazit

Die Fingerprint-Fusion-Architektur ist **praktisch effektiv** und löst das Kernproblem der User-Intent-Preservierung. Die statistischen Abweichungen (Clamped Fusion, fehlende Kreuz-Kovarianz) sind **bewusste Kompromisse** zugunsten von Einfachheit und Recheneffizienz.

Die größte ungenutzte Verbesserungsmöglichkeit liegt in der **diskriminativen Nutzung konkurrierender Fingerabdrücke** durch Log-Likelihood-Normalisierung.

**Signatur:** Statistisch-Physikalisches Peer Review, 2026-01-26
