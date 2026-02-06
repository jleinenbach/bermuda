# Fingerprint Training Guide

Bermuda can determine which room a device is in by comparing the pattern of signal strengths that your Bluetooth scanners report. This pattern is called a **fingerprint**. The fingerprint training feature lets you teach Bermuda what a specific room "looks like" from the perspective of all nearby scanners.

> **Key terms used in this guide:**
> - **Scanner** -- An ESP32, Shelly, or similar device that listens for Bluetooth signals and reports them to Home Assistant.
> - **Signal strength** -- How strong a Bluetooth signal is when it reaches a scanner. Measured in dBm (decibels relative to one milliwatt). The values are always negative: **-50 dBm is a strong signal** (device is close), **-90 dBm is a weak signal** (device is far away).
> - **Area** -- What Home Assistant calls a "room." Each scanner and each tracked device is assigned to an area.
> - **Floor** -- A group of areas in Home Assistant. Used to organize rooms by level (Ground Floor, First Floor, Basement, etc.).

> **When do you need this?** Fingerprint training is most useful when:
> - A device keeps flickering between two adjacent rooms.
> - You want to track a device in a room that has **no scanner of its own** (a "scannerless room" -- for example, a basement, hallway, or storage closet).
> - The standard distance-based detection picks the wrong room because of walls, reflections, or scanner placement.
>
> If your devices are already tracking correctly, you do not need to train fingerprints.

---

## Table of Contents

- [Concepts](#concepts)
  - [How fingerprints work](#how-fingerprints-work)
  - [Two types of learned data](#two-types-of-learned-data)
  - [What training does for other devices](#what-training-does-for-other-devices)
  - [Scannerless rooms](#scannerless-rooms)
- [Prerequisites](#prerequisites)
- [Step-by-step: Training a room](#step-by-step-training-a-room)
- [Step-by-step: Training a scannerless room](#step-by-step-training-a-scannerless-room)
- [Step-by-step: Training from multiple positions](#step-by-step-training-from-multiple-positions)
- [When to reset training data](#when-to-reset-training-data)
- [Step-by-step: Resetting training data](#step-by-step-resetting-training-data)
- [Frequently asked questions](#frequently-asked-questions)
- [Troubleshooting](#troubleshooting)

---

## Concepts

### How fingerprints work

Every Bluetooth scanner in your home sees your device at a different signal strength. A device standing in the kitchen might be seen as a strong signal by the kitchen scanner, a medium signal by the living room scanner, and a weak signal by the bedroom scanner. This unique combination is the "fingerprint" for the kitchen.

When you train a room, Bermuda records this pattern. Later, when the device reports similar signal strengths, Bermuda recognizes the pattern and assigns the device to that room -- even if the raw distances are ambiguous.

```
                    Kitchen scanner:     strong signal  (-55 dBm)
Device in           Living room scanner: medium signal  (-72 dBm)
the kitchen  --->   Bedroom scanner:     weak signal    (-85 dBm)
                    = "Kitchen fingerprint"
```

Think of it like a unique "signal signature" for each room. The kitchen has a signature, the bedroom has a different one, and Bermuda learns to tell them apart.

### Two types of learned data

Bermuda maintains two layers of fingerprint data:

**1. Auto-learned data (shared across all devices)**
- Created **automatically** while Bermuda is running. No action needed from you.
- Stores a general fingerprint for each room, shared by all devices.
- Updated continuously in the background.
- Provides a baseline so that even devices you have never trained benefit from the general knowledge of your home's layout.

**2. Button-trained data (specific to one device)**
- Created **manually** when you press the "Learn Fingerprint" button.
- Stores a fingerprint specific to one device in one room.
- **Your training always takes priority.** Over time, the system may adjust the fingerprint slightly to adapt to small environmental changes (moved furniture, seasonal changes), but it can never change more than 30% from what you trained. You stay in control.

### What training does for other devices

Imagine you train your iPhone for the kitchen. Two things happen:

1. **Your iPhone** gets a strong, personal fingerprint for the kitchen. This directly and significantly improves kitchen detection for your iPhone.
2. **The shared "Kitchen" profile** is also updated. This is the general fingerprint used by all devices. So your partner's phone, your smartwatch, or a Bluetooth tag -- any device that has never been specifically trained -- will also benefit because Bermuda now has a better understanding of what the kitchen "looks like."

Training one device improves detection for all devices to some degree, but the biggest improvement is always for the device you trained.

### Scannerless rooms

A scannerless room is a room that has no Bluetooth scanner of its own. Examples: a basement, a storage closet, a hallway, or any room where you have not (or cannot) place an ESP32 or Shelly device.

Without fingerprint training, Bermuda cannot detect a device in a scannerless room. The distance-based algorithm only knows about rooms that have a physical scanner. **Training is the only way to make a scannerless room detectable.**

After training, Bermuda calculates a "virtual distance" for the scannerless room based on how well the current signal pattern matches the trained fingerprint. A strong match means a short virtual distance, allowing the scannerless room to compete against -- and potentially beat -- rooms with actual scanners that are farther away.

**Important:** Scannerless room detection requires an advanced room detection mode to be enabled. Go to **Settings > Integrations > Bermuda > Configure > Global Options** and enable **"Use UKF area selection"**. This mode compares signal patterns against learned fingerprints, which is necessary because there is no physical scanner to measure distance. It uses slightly more CPU but falls back to standard distance-based detection when it cannot make a confident decision.

---

## Prerequisites

Before you start training, make sure:

1. **All your scanners are assigned to rooms.** Go to **Settings > Devices & Services**, find each ESP32/Shelly scanner device, click on it, and verify that it has a Home Assistant **Area** (room) assigned. Bermuda cannot use a scanner that has no area.

2. **Your rooms are assigned to floors.** Go to **Settings > Areas, Labels & Zones**. Make sure every area (room) has a **Floor** assigned. The training dropdowns use the floor to filter the list of available rooms.

3. **The device you want to train is selected in Bermuda.** Go to **Settings > Integrations > Bermuda > Configure > Select Devices** and make sure the device is in the list of tracked devices.

4. **The device is currently in range.** At least one scanner must be able to see the device. If no scanner can see the device, training will fail because there is no signal data to collect.

5. **(For scannerless rooms only) Advanced mode is enabled.** Go to **Settings > Integrations > Bermuda > Configure > Global Options** and enable **"Use UKF area selection"**.

---

## Step-by-step: Training a room

This example trains a phone for the kitchen.

### 1. Go to the device page

1. Open **Settings > Devices & Services**.
2. Find the **Bermuda BLE Trilateration** integration card.
3. Click on the device count (e.g., "5 devices") to see the list of tracked devices.
4. Click on the device you want to train.

You will see the device's entities, including:

- **Training Floor** (a dropdown selector)
- **Training Room** (a dropdown selector)
- **Learn Fingerprint** (a button -- initially grayed out)
- **Reset Training** (a button)

### 2. Select the floor

Click the **Training Floor** dropdown and select the floor where the room is located (e.g., "Ground Floor").

After selecting a floor, the **Training Room** dropdown will update to show only rooms on that floor.

### 3. Select the room

Click the **Training Room** dropdown and select the target room (e.g., "Kitchen").

After selecting the room:
- The **Learn Fingerprint** button becomes active (no longer grayed out).
- The device's detected area is temporarily locked to the selected room. This prevents automatic detection from moving the device to a different room while you are training.

### 4. Go to the room and press "Learn Fingerprint"

**Physically go to the room** with the device, then press the **Learn Fingerprint** button.

- The button icon changes from a brain icon to a timer/hourglass icon.
- A notification appears: *"Training started. Collecting 60 samples..."*
- Bermuda now collects signal strength samples from all visible scanners for up to 5 minutes.
- Each sample must come from a genuinely new Bluetooth advertisement (no duplicates).
- There is a minimum 5-second gap between samples to ensure good data quality.

**While training is running:**
- **Stay in the room.** Do not leave.
- You can move around a little within the room -- this is actually helpful because it captures the signal variation at different positions.
- The button is disabled to prevent accidental double-clicks.

### 5. Wait for completion

Training finishes when one of these happens:
- **60 samples collected** (best case, typically takes 3-5 minutes).
- **5-minute timeout** reached (any data collected so far is still saved).

A notification appears with the result:
- **Success:** *"Collected 60/60 samples (100% quality). Device placed in Kitchen."*
- **Partial success:** *"Collected 35/60 samples (96% quality). Device placed in Kitchen."* -- still usable, but consider retraining if quality is below 70%.
- **Failure:** *"No scanner data available."* -- the device is out of range or offline.

> **What does "quality" mean?** The quality percentage tells you how statistically reliable the collected samples are. It accounts for the fact that samples taken close together in time are partially redundant. Above 80% is good. Below 50%, consider retraining when more scanners are online or the device has a stronger signal.

> **What if training is interrupted?** If Home Assistant restarts or the network drops during training, any data collected before the interruption is still saved. You can run training again to collect more samples and improve the fingerprint.

### 6. Verify

After training completes:
- The **Training Floor** and **Training Room** dropdowns automatically reset to empty (no selection).
- The **Learn Fingerprint** button goes back to its grayed-out state. This is normal.
- The area lock is released, and normal detection resumes.

The device should now show the trained room as its current area. Walk to a different room and verify that the device eventually switches. Then walk back and verify it returns to the trained room.

---

## Step-by-step: Training a scannerless room

This example trains a device for a basement storage room that has no scanner.

### 1. Enable the advanced detection mode

Go to **Settings > Integrations > Bermuda > Configure > Global Options** and enable **"Use UKF area selection"**. Press Submit. This mode is required because scannerless rooms can only be detected through fingerprint pattern matching, not physical distance to a scanner.

### 2. Create the room in Home Assistant (if it does not exist)

Go to **Settings > Areas, Labels & Zones**. If the scannerless room does not exist yet, create it and assign it to the correct floor.

### 3. Train the device

Follow the same steps as [Training a room](#step-by-step-training-a-room) above. Select the scannerless room as the target. Bermuda handles the rest automatically -- it knows the room has no scanner and uses fingerprint matching instead.

**Important notes for scannerless rooms:**
- The device must still be visible to **at least one scanner somewhere** in your home. For example, a scanner on the floor above might still pick up the device's signal faintly through the ceiling. If no scanner anywhere can see the device, training cannot work.
- After training, the "Distance" sensor will show a calculated distance based on the fingerprint match quality, not a real measured distance. This is expected.
- **Floor requirement:** At least one scanner on the same floor as the scannerless room must be able to see the device. If no scanner on that floor picks up any signal from the device, Bermuda will not place the device in the scannerless room. This prevents impossible assignments (e.g., placing a device in a basement room when all evidence says it is on the second floor).

### 4. Verify

Walk to a room with a scanner. The device should switch away from the scannerless room. Walk back to the scannerless room -- the device should switch back.

If the scannerless room is not detected, check:
- Is the advanced detection mode enabled? (**Global Options > "Use UKF area selection"**)
- Is the room assigned to a floor?
- Is at least one scanner on the same floor able to see the device (even faintly)?
- Did training collect enough samples (ideally 60, but at least 15 may work for basic detection)?
- Try pressing "Reset Training" and then retraining from scratch.

---

## Step-by-step: Training from multiple positions

Large rooms (living rooms, open-plan kitchens) have significant signal variation depending on where the device is. Training from a single position only captures one "corner" of the room. Multi-position training averages multiple positions into one broader fingerprint.

### 1. Train the first position

Follow the normal [training steps](#step-by-step-training-a-room). Stand in one area of the room (e.g., near the sofa).

### 2. Move to a different position

After the first training completes, physically move to a different part of the room (e.g., near the dining table).

### 3. Train again for the same room

Select the same floor and same room again, and press "Learn Fingerprint" again.

Bermuda automatically detects that training data already exists for this room. Instead of overwriting the previous training, it **averages both positions** into the fingerprint. The new training session has strong initial influence (~50%) to ensure it meaningfully shifts the fingerprint toward the new position.

### 4. Repeat for additional positions (optional)

For very large rooms, you can train from 3-4 different positions. Each training session further broadens the fingerprint to cover more of the room.

---

## When to reset training data

Reset training data when any of these situations apply:

| Situation | Why reset helps |
|-----------|-----------------|
| **You trained the wrong room by accident.** | The incorrect fingerprint will make the device stick to the wrong room. Resetting removes it. |
| **You moved a scanner to a different location.** | The old fingerprint was learned with the scanner in its previous position. It no longer matches reality. |
| **A device is stuck in a room and will not leave.** | Incorrect or outdated training data can lock a device into a room. Resetting falls back to auto-learned data or distance-based detection. |
| **You added or removed a scanner.** | The fingerprint was learned with a different set of scanners. The pattern may no longer be valid. |
| **Detection got worse after training.** | If you trained under bad conditions (device was moving, a scanner was rebooting, unusual interference), the fingerprint may be noisy. |

### What reset affects

When you press **"Reset Training"**, the following data is cleared **for this device only**:

| Data cleared | Effect |
|--------------|--------|
| **Your manual training** (all rooms) | The fingerprints you created by pressing "Learn Fingerprint" are removed. |
| **Auto-learned data** (all rooms) | The automatically collected patterns for this device are also removed, because they may have been influenced by incorrect training. (The auto-learner learns based on which room the device was assigned to -- if the assignment was wrong due to bad training, the auto-learned data may also be wrong.) |

**What is NOT affected:**
- Training data for other devices is untouched.
- Shared room profiles (the general fingerprints used by all devices) are untouched.
- Scanner calibration settings are untouched.
- Global configuration is untouched.

After resetting, the device falls back to:
1. Shared room profiles (if they exist from other devices' training or auto-learning).
2. Pure distance-based detection.

---

## Step-by-step: Resetting training data

### 1. Go to the device page

1. Open **Settings > Devices & Services**.
2. Find the **Bermuda BLE Trilateration** integration card.
3. Click on the device count, then click the device you want to reset.

### 2. Press "Reset Training"

Click the **Reset Training** button. A confirmation notification appears: *"Training data cleared for [device name]."*

### 3. Verify

The device should now use distance-based detection or shared room profiles. If the device was stuck in a wrong room, it should start tracking correctly again.

If the device still behaves incorrectly after resetting, the issue is likely not related to training data. Check the [Bermuda Troubleshooting Guide](troubleshooting.md) for other possible causes.

---

## Frequently asked questions

### Do I need to train every device?

No. Training is optional. Many devices work well with just the distance-based algorithm. Train only when you notice a problem (flickering, wrong room) or when you need scannerless room detection.

### Do I need to train every room?

No. Train only the rooms where detection is problematic. Rooms with a scanner close to where the device usually sits typically work fine without training.

### Can I train a device that uses a rotating MAC address (iPhone, Android)?

Yes. Bermuda automatically identifies your device even when its Bluetooth address changes (which iPhones and Android phones do for privacy). The training is stored against the device's stable identity, not its temporary address. No special steps are needed.

### How long does training last?

Indefinitely. The trained fingerprint does not expire. The system may adjust it slightly over time (up to 30% influence from auto-learning), but your training remains dominant. If you move furniture or scanners, consider retraining.

### What happens if a scanner goes offline during training?

Training will still collect samples from the remaining online scanners. The quality may be lower because fewer scanners contribute data. If possible, make sure all scanners are online before training.

### Can two people train the same device at the same time?

No. The "Learn Fingerprint" button is disabled while training is in progress. Wait for the current session to finish.

### Does scanner calibration affect fingerprints?

No. Fingerprints are based on raw signal strength values, not calibrated distances. You can change calibration settings at any time without invalidating your fingerprint training. See the [Scanner Calibration Guide](scanner-calibration.md) for details on calibration.

---

## Troubleshooting

### Training button is grayed out

- You must select **both** a floor **and** a room before the button becomes active.
- If a training session is already running, the button is disabled until it finishes.

### Training completed but quality is low (below 50%)

- The device may be at the edge of scanner range. Move closer to the center of the room.
- A scanner may have been offline during training. Check the "Scanner Online" sensor for each scanner.
- The Bluetooth advertisement interval of the device may be very long. Some battery-powered devices only advertise every 10+ seconds, which means fewer samples per minute.

### Device still shows the wrong room after training

- If you trained a scannerless room, verify the advanced detection mode is enabled (**Global Options > "Use UKF area selection"**).
- Check that the device is still in range of at least one scanner.
- Walk out of and back into the trained room. The system needs to see a room transition to apply the new fingerprint.
- If the device was previously trained for a different room, that old training may conflict. Press "Reset Training" first, then retrain.

### "No scanner data available" error during training

- The device is not currently visible to any scanner.
- Make sure the device is powered on and its Bluetooth is active.
- Check that at least one scanner is online (look for the "Scanner Online" binary sensor on each scanner's device page).

### Scannerless room is not detected

- Is the advanced detection mode enabled? (**Global Options > "Use UKF area selection"**)
- Is the room assigned to a floor in Home Assistant?
- Is at least one scanner on the same floor as the scannerless room picking up the device's signal?
- Did training collect enough samples? Ideally 60, but at least 15 may work for basic detection.
- Try pressing "Reset Training" and retraining from scratch.
