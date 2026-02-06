# Scanner Calibration Guide

Bermuda estimates how far away a Bluetooth device is by measuring the strength of its radio signal. This conversion from signal strength to distance depends on calibration parameters. Proper calibration gives you more accurate distances and better room detection.

> **Key terms used in this guide:**
> - **Scanner** -- An ESP32, Shelly, or similar device that listens for Bluetooth signals and reports them to Home Assistant.
> - **Signal strength (dBm)** -- How strong a Bluetooth signal is when it reaches a scanner. Measured in dBm (decibels relative to one milliwatt). Always negative: **-50 dBm is strong** (close), **-90 dBm is weak** (far away).
> - **dB (decibel)** -- A unit of relative measurement. When used for offsets or corrections in this guide, it means "shift the reading by this many decibels." A +3 dB offset makes a scanner read stronger (closer); a -3 dB offset makes it read weaker (farther).
> - **Reference Power (ref_power)** -- The expected signal strength at exactly 1 meter distance. This is the baseline for all distance calculations.
> - **Attenuation** -- How quickly the signal weakens with distance. Higher values mean the signal fades faster (walls, furniture, people absorb signal).

There are two calibration steps, and they build on each other:

| Step | What it does | When to do it |
|------|-------------|---------------|
| **Calibration 1: Global** | Sets the baseline distance model (reference power and signal decay) for your entire home. | Once, when you first set up Bermuda. Repeat if you change scanner hardware. |
| **Calibration 2: Per-Scanner Offsets** | Compensates for hardware differences between individual scanners (some ESP32 boards receive signals louder or quieter than others). | After Calibration 1. Bermuda also provides automatic suggestions. |

> **Do you need to calibrate?** If your room detection is already working well with the defaults, you can skip calibration. It primarily improves the accuracy of the distance values. If you use fingerprint training for room detection, calibration is not required at all -- fingerprints use raw signal values and are unaffected by calibration settings. See the [Fingerprint Training Guide](fingerprint-training.md) for details.

---

## Table of Contents

- [How it works (plain language)](#how-it-works-plain-language)
- [What the parameters mean](#what-the-parameters-mean)
- [Automatic calibration suggestions](#automatic-calibration-suggestions)
- [Prerequisites](#prerequisites)
- [Step-by-step: Global calibration (Calibration 1)](#step-by-step-global-calibration-calibration-1)
- [Step-by-step: Per-scanner offsets (Calibration 2)](#step-by-step-per-scanner-offsets-calibration-2)
- [Worked example: Calibration 2 with numbers](#worked-example-calibration-2-with-numbers)
- [Understanding the auto-calibration suggestions](#understanding-the-auto-calibration-suggestions)
- [Frequently asked questions](#frequently-asked-questions)
- [Troubleshooting](#troubleshooting)

---

## How it works (plain language)

Bermuda converts the signal strength a scanner measures into a distance estimate. Two parameters control this conversion:

1. **Reference Power** answers: *"How strong is the signal at exactly 1 meter?"* This is the starting point. If you hold a device 1 meter from a scanner, the signal strength you see should match this value. The default is **-55 dBm**.

2. **Attenuation** answers: *"How fast does the signal fade as you move farther away?"* In an open room, signals fade slowly (low attenuation). In a cluttered room with thick walls and lots of furniture, signals fade quickly (high attenuation). The default is **3.0**.

Together, these two values let Bermuda translate any signal reading into an approximate distance. If the distances Bermuda reports do not match reality, adjusting these parameters will bring them in line.

<details>
<summary><strong>Technical detail: the distance formula</strong></summary>

Bermuda uses the log-distance path loss model:

```
distance = 10 ^ ((ref_power - signal_strength) / (10 * attenuation))
```

Where:
- `ref_power` is the expected signal strength at 1 meter (e.g., -55 dBm)
- `signal_strength` is the actual signal measured by the scanner right now
- `attenuation` is the path loss exponent (typically 2.0 to 4.0 indoors)

You do not need to understand this formula to calibrate Bermuda. The step-by-step instructions below guide you through the process using trial and error.

</details>

---

## What the parameters mean

| Parameter | What it controls | How to think about it | Default |
|-----------|------------------|----------------------|---------|
| **Reference Power** | The expected signal strength at exactly 1 meter. | "If I hold the device 1 m from the scanner, what signal do I see?" A higher (less negative) value means the device appears closer. | -55.0 dBm |
| **Attenuation** | How fast the signal decays with distance. Higher = faster decay = shorter calculated distances. | Thick walls, lots of furniture, and water (humans!) increase attenuation. Open spaces decrease it. | 3.0 |
| **Per-scanner offset** | A correction (in dB) applied to one specific scanner to compensate for hardware differences. | "This scanner consistently reads 5 dB louder than all others, so subtract 5." Positive offset = scanner reads closer. Negative offset = scanner reads farther. | 0 |

---

## Automatic calibration suggestions

Bermuda can automatically calculate suggested offsets for your scanners. This works because your scanners can "see" each other.

**How it works (simplified):**

Each ESPHome Bluetooth proxy scanner broadcasts its own Bluetooth signal (called an iBeacon advertisement). Other scanners pick up this signal, just like they pick up signals from your phone or a beacon tag. This means Scanner A sees Scanner B's signal, and Scanner B sees Scanner A's signal.

If Scanner A consistently reports Scanner B's signal as much stronger than Scanner B reports Scanner A's signal, then one of them has a more sensitive receiver than the other. Bermuda uses this difference to calculate correction offsets.

**In practice:**
1. Your scanners automatically see each other in the background. You do not need to do anything.
2. Bermuda tracks these measurements and calculates what offset each scanner needs.
3. After enough data (typically 2-4 hours after startup), suggestions appear in the Calibration 2 dialog.
4. Suggestions are only shown when Bermuda is at least 70% confident in the result.

> **Note:** Auto-calibration data is recalculated fresh after every Home Assistant restart. Suggestions may take a few hours to appear after a reboot.

---

## Prerequisites

Before calibrating:

1. **All your scanners are online and assigned to rooms.** Calibration requires active scanners with Bluetooth data flowing.
2. **You have a measuring tape or know the approximate distance.** For Calibration 1, you will place a device at 1 meter and then at 5+ meters from a scanner.
3. **You have at least one device selected in Bermuda.** Go to **Settings > Integrations > Bermuda > Configure > Select Devices** and choose a device you can physically carry (a phone or a beacon tag).

---

## Step-by-step: Global calibration (Calibration 1)

This example calibrates the global distance model using a phone and a kitchen scanner.

### 1. Open the calibration dialog

Go to **Settings > Integrations > Bermuda BLE Trilateration**. Click **Configure**. In the menu, select **"Calibration 1: Global"**.

### 2. Select a reference pair

You will see two dropdown fields:

- **Reference device:** Select the Bluetooth device you will carry (e.g., your phone).
- **Reference scanner:** Select the scanner you will calibrate against (e.g., "Kitchen ESP32").

Choose a scanner in a room where you have enough space to stand 1 meter away and then move to 5+ meters away.

### 3. Calibrate at 1 meter

1. Hold the device at **exactly 1 meter** from the selected scanner.
2. Click **Submit**.
3. A results table appears showing the last 5 signal readings and their calculated distances.

Look at the **Estimate (m)** row. If the values are not close to 1.0 m, adjust the **Reference Power** field:
- If the distance reads **too far** (e.g., 2.5 m), **increase** reference power (make it less negative, e.g., -55 to -50).
- If the distance reads **too close** (e.g., 0.3 m), **decrease** reference power (make it more negative, e.g., -55 to -60).

Click **Submit** again to see the updated distances. Repeat until the 1-meter reading is approximately correct.

### 4. Calibrate at 5+ meters

1. Measure a distance of 5 meters (or more) from the scanner using a tape measure.
2. Hold the device at that distance.
3. Click **Submit** and check the results table.

Now adjust the **Attenuation** field:
- If the distance reads **too far** (e.g., 8 m when actual is 5 m), **increase** attenuation (e.g., 3.0 to 3.5).
- If the distance reads **too close** (e.g., 3 m when actual is 5 m), **decrease** attenuation (e.g., 3.0 to 2.5).

Click **Submit** again to verify. Repeat until the 5-meter reading is approximately correct.

> **Tip:** After adjusting attenuation, go back and verify the 1-meter reading is still correct. The two parameters interact slightly. You may need to iterate a few times between steps 3 and 4.

### 5. Save

> **Important:** Your changes are NOT saved until you check the **"Save and Close"** checkbox. If you just click Submit without this checkbox, Bermuda will show you updated results but will not persist your changes.

Check the **"Save and Close"** checkbox and click **Submit**. The calibration is saved.

---

## Step-by-step: Per-scanner offsets (Calibration 2)

This step compensates for hardware differences between individual scanners. Some ESP32 boards have more sensitive receivers than others, even if they are the same model.

### 1. Open the calibration dialog

Go to **Settings > Integrations > Bermuda BLE Trilateration**. Click **Configure**. In the menu, select **"Calibration 2: Scanner RSSI Offsets"**.

### 2. Select a device first

> **The screen will look mostly empty at first. This is expected.** Bermuda needs to know which device's signal data to use before it can show any distances. Until you select a device, there is nothing to display.

Click the **Device** dropdown and select a tracked Bluetooth device (e.g., your phone). Then click **Submit**.

After submitting, a **results table** appears showing the estimated distance from the selected device to each scanner. The table has 5 columns (samples 0-4) showing recent distance estimates.

### 3. Review the auto-calibration suggestions (if available)

Below the distance table, you may see an **"Auto-Calibration Suggestions"** table. This shows:

| Column | Meaning |
|--------|---------|
| **Scanner** | The scanner name |
| **Current** | Your current offset for this scanner (in dB) |
| **Suggested** | The offset Bermuda recommends based on scanner cross-visibility data |
| **Confidence** | How confident the suggestion is (0-100%). Only values above 70% are shown. |

If suggestions are available, you can apply them by typing the suggested values into the scanner offset fields.

> **No suggestions yet?** Auto-calibration needs 2-4 hours of data after each Home Assistant restart. You also need at least 2 scanners that can see each other. If you just rebooted, wait a few hours and check again.

### 4. Adjust offsets manually (if needed)

If auto-calibration suggestions are not available, or you want to fine-tune:

1. Pick one scanner as your **reference scanner** (leave its offset at 0).
2. Place the device at a known distance from the reference scanner and note the reported distance.
3. Move to another scanner at approximately the same known distance and compare.
   - If this scanner reports **too close**, apply a **negative** offset (e.g., -3).
   - If this scanner reports **too far**, apply a **positive** offset (e.g., +3).
4. Click **Submit** to see the updated distances.
5. Repeat for each scanner.

### 5. Save

> **Important:** Your changes are NOT saved until you check the **"Save and Close"** checkbox. If you just click Submit without this checkbox, Bermuda will show you updated results but will not persist your changes.

Check the **"Save and Close"** checkbox and click **Submit**. The offsets are saved.

---

## Worked example: Calibration 2 with numbers

Here is a concrete example showing how to interpret the results table and apply offsets.

**Setup:** You have 3 scanners and a phone. You place the phone **3 meters** from each scanner (one at a time) and note the reported distances.

| Scanner | Reported distance | Actual distance | Problem |
|---------|-------------------|-----------------|---------|
| Kitchen ESP32 (reference) | 3.1 m | 3 m | Close enough -- leave at 0. |
| Bedroom ESP32 | 4.8 m | 3 m | Reports too far. Scanner reads too weak. |
| Living Room Shelly | 2.0 m | 3 m | Reports too close. Scanner reads too strong. |

**Applying offsets:**

1. **Kitchen ESP32** -- offset stays at **0** (reference scanner).
2. **Bedroom ESP32** -- reports 4.8 m instead of 3 m (too far). The scanner's receiver is less sensitive, so signals appear weaker than they actually are. Apply a **positive** offset to correct this. Try **+3** and check.
3. **Living Room Shelly** -- reports 2.0 m instead of 3 m (too close). The scanner's receiver is more sensitive, so signals appear stronger than they actually are. Apply a **negative** offset. Try **-3** and check.

After applying offsets and pressing Submit, the new table might show:

| Scanner | Reported distance (after offset) | Target |
|---------|----------------------------------|--------|
| Kitchen ESP32 | 3.1 m | 3 m |
| Bedroom ESP32 | 3.3 m | 3 m |
| Living Room Shelly | 2.9 m | 3 m |

These are close enough. Check **"Save and Close"** and press **Submit** to save.

> **Tip:** You do not need perfect accuracy. Within 0.5 m of the actual distance is usually good enough for room detection. The goal is to eliminate large systematic errors, not achieve centimeter precision.

---

## Understanding the auto-calibration suggestions

### How confidence is calculated

The confidence score is based on three factors:

| Factor | Weight | What it measures |
|--------|--------|------------------|
| **Sample count** | 30% | How many signal samples have been collected between this scanner pair. Reaches full score at 100 samples. |
| **Scanner pair count** | 40% | How many other scanners can see this scanner. More pairs means better cross-validation. 1 pair = 33%, 2 pairs = 67%, 3+ pairs = 100%. |
| **Consistency** | 30% | How consistent the calculated offset is across different scanner pairs. Lower variation = higher confidence. |

Suggestions are only displayed when the combined confidence reaches **70% or higher**.

### What "below threshold" means

If a scanner shows confidence but says "below threshold", it means Bermuda has some data but not enough to be confident in the suggestion. This usually means:
- Not enough time has passed since the last restart (auto-calibration data is not persisted across restarts).
- Only one or two scanner pairs are available.
- The signal readings between the scanners are highly variable.

Give it more time. Suggestions typically become reliable after 2-4 hours of operation.

### Why suggestions are not saved across restarts

Auto-calibration data is recalculated fresh after every Home Assistant restart. This is by design:
- Scanner hardware or positions may have changed.
- Stale calibration data could be worse than no calibration.
- The system converges within a few hours of normal operation.

The offsets you manually enter and save **are** persisted. Only the auto-calculated suggestions reset.

---

## Frequently asked questions

### Do I need both Calibration 1 and Calibration 2?

Not necessarily.
- **Calibration 1** sets the global model. It is the most impactful step.
- **Calibration 2** fine-tunes per-scanner differences. It is most useful if you notice that one scanner consistently reports wrong distances while others are correct.

If your room detection works well, you can skip both.

### Does calibration affect fingerprint training?

No. Fingerprints use raw signal strength values, not calibrated distances. You can change calibration settings at any time without invalidating your fingerprint training data. See the [Fingerprint Training Guide](fingerprint-training.md) for details.

### Why do I need to select a device in Calibration 2 before seeing the table?

The distance table requires actual signal data from a specific device to calculate distances. Without selecting a device, there is no data to display. Once you select a device and press Submit, Bermuda retrieves that device's recent signal history from each scanner and shows the calculated distances.

This is a one-time step each time you open the dialog. After selecting a device, you can adjust offsets and press Submit multiple times to iterate.

### How often should I recalibrate?

Rarely. Recalibrate if:
- You replace scanner hardware (different ESP32 boards have different receiver sensitivities).
- You physically move a scanner to a new location.
- You add or remove scanners.
- Distance values seem consistently wrong across all devices.

### Can I undo calibration changes?

Yes, but there is no "Reset to defaults" button. Manually set the values back to their defaults:
- **Reference Power:** -55.0
- **Attenuation:** 3.0
- **All per-scanner offsets:** 0

Then check **"Save and Close"** and Submit.

### Do I need to recalibrate after changing fingerprint training?

No. Calibration and fingerprint training are completely independent. Fingerprints use raw signal values. Calibration affects only the calculated distances.

---

## Troubleshooting

### No auto-calibration suggestions appear

- **Scanners need to see each other.** This requires ESPHome-based scanners with Bluetooth proxy enabled, or Shelly devices that broadcast Bluetooth signals. The scanners must be close enough to detect each other's signal.
- **Give it time.** After a restart, it takes 2-4 hours for enough data to accumulate.
- **Check scanner count.** You need at least 2 scanners that can see each other for suggestions to appear.

### Distances are wildly wrong

- **Check reference power sign.** It must be negative (e.g., -55, not +55). A positive reference power causes extreme distance errors.
- **Check attenuation range.** Typical values are 2.0 to 4.0. Values outside this range produce unrealistic distances.
- **Check scanner health.** A rebooting or offline scanner will not provide reliable data.

### One scanner always reports wrong distances

This is exactly what Calibration 2 (per-scanner offsets) is for. Apply a positive or negative offset to that scanner until its distances match reality. Or wait for auto-calibration suggestions.

### Distances are correct at 1 m but wrong at 5 m (or vice versa)

This is an attenuation issue. Iterate between Calibration 1 steps 3 and 4, adjusting reference power and attenuation alternately until both distances are approximately correct.

### The Calibration 2 screen looks empty

This is expected if you have not selected a device yet. Select a device from the dropdown and press Submit. The distance table will then appear.

### After pressing Submit, my changes seem to disappear

If you did not check the **"Save and Close"** checkbox before pressing Submit, your changes were used to recalculate the preview but were not saved. To save permanently, check the **"Save and Close"** checkbox and press Submit again.

For additional troubleshooting, see the [Bermuda Troubleshooting Guide](troubleshooting.md).
