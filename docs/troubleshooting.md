# Bermuda Troubleshooting Guide

This document covers common issues, warnings, and their solutions when using the Bermuda BLE Trilateration integration.

---

## Table of Contents

- [Scanner Configuration Issues](#scanner-configuration-issues)
  - [Invalid ref_power Warning](#invalid-ref_power-warning)
  - [Scanner Not Detected](#scanner-not-detected)
- [Distance and Location Issues](#distance-and-location-issues)
  - [Inaccurate Distance Readings](#inaccurate-distance-readings)
  - [Room Flickering](#room-flickering)
- [Metadevice Issues](#metadevice-issues)
  - [FMDN Device Not Visible](#fmdn-device-not-visible)
  - [IRK Device Not Tracking](#irk-device-not-tracking)
- [Performance Issues](#performance-issues)
  - [High CPU Usage](#high-cpu-usage)
  - [Slow Updates](#slow-updates)

---

## Scanner Configuration Issues

### Invalid ref_power Warning

**Symptom:**
```
WARNING [custom_components.bermuda.filters.kalman] Invalid ref_power X.X dBm (expected -100 to 0)
WARNING [custom_components.bermuda.bermuda_advert] Device XXX has invalid ref_power X.X dBm (from YYY)
```

**Understanding ref_power vs TX Power:**

These are two **different** values that are often confused:

| Term | Description | Typical Range | Example |
|------|-------------|---------------|---------|
| **ref_power** | Calibrated RSSI at 1 meter distance | -40 to -70 dBm | -55 dBm |
| **TX Power** | Transmit power (how loud the device broadcasts) | -20 to +20 dBm | +3 dBm |

- **ref_power** is always **negative** because it's a received signal strength
- **TX Power** can be **positive** for powerful transmitters (ESP32, Class 1 Bluetooth)

**Cause:**
A positive `ref_power` value (e.g., +3 dBm) indicates that somewhere TX power is being incorrectly used as the calibrated RSSI-at-1m value. This can happen with:
- iBeacon devices reporting incorrect `beacon_power` (measured power field)
- Misconfigured device firmware
- Custom integrations passing wrong values

**Solution:**

1. **Identify the device** - Check the log message for the device name
2. **Check beacon_power** - If it's an iBeacon, verify the manufacturer data is correct
3. **Manually calibrate** - Set a proper `ref_power` value in device settings:
   - Place the device exactly 1 meter from a scanner
   - Note the RSSI value shown
   - Use that value (e.g., -55 dBm) as ref_power

**Technical Background:**
The `ref_power` is used by the adaptive Kalman filter to determine signal quality:
- Signals close to ref_power = strong signal = low noise = high trust
- Signals much weaker than ref_power = weak signal = high noise = low trust

If ref_power is positive (+3 dBm) and actual RSSI is -60 dBm, the filter incorrectly thinks the signal is 63 dB below threshold, causing extreme noise inflation and frozen estimates.

---

### Scanner Not Detected

**Symptom:**
Scanner doesn't appear in Bermuda's device list despite being online.

**Possible Causes:**
1. **ESPHome Scanner**: Ensure `bluetooth_proxy` is enabled with `active: true`
2. **Missing Area Assignment**: Scanner must be assigned to a Home Assistant area
3. **Bluetooth Integration**: The HA Bluetooth integration must be loaded

**Solution:**
1. Verify scanner is sending BLE advertisements to Home Assistant
2. Assign the scanner device to an area in Home Assistant
3. Check Home Assistant logs for Bluetooth-related errors

---

## Distance and Location Issues

### Inaccurate Distance Readings

**Symptom:**
Reported distances don't match physical distances.

**Possible Causes:**
1. **Uncalibrated Scanner**: Default calibration may not match your hardware
2. **Environmental Interference**: Walls, metal objects, water (humans!) affect signals
3. **TX Power Mismatch**: Device's actual TX power differs from reported value

**Solution:**
1. **Calibrate Reference Power:**
   - Place a device at exactly 1 meter from the scanner
   - Note the RSSI value
   - Adjust the scanner's `ref_power` setting to match

2. **Calibrate Attenuation:**
   - Higher values = faster signal decay = shorter calculated distances
   - Typical indoor values: 2.5 to 4.0
   - Start with 3.0 and adjust based on observations

3. **Use Scanner RSSI Offset:**
   - If a scanner consistently reads high/low, apply an offset
   - Check the auto-calibration suggestions in Bermuda's diagnostics

---

### Room Flickering

**Symptom:**
Device constantly switches between rooms despite being stationary.

**Possible Causes:**
1. **Weak Signal**: Device at the edge of scanner range
2. **Similar Distances**: Multiple scanners report similar distances
3. **Insufficient Training**: Fingerprint profiles not yet learned

**Solution:**
1. **Train the Room:**
   - Use the fingerprint training feature
   - Select floor → room → press "Learn Fingerprint"
   - Stay in the room for up to 5 minutes while training

2. **Adjust Max Velocity:**
   - Lower values increase stability but reduce responsiveness
   - Default: 3.0 m/s (walking speed)

3. **Check Scanner Placement:**
   - Ensure each room has adequate scanner coverage
   - Avoid placing scanners near room boundaries

---

## Metadevice Issues

### FMDN Device Not Visible

**Symptom:**
Google Find My device (Moto Tag, Chipolo, etc.) doesn't appear in Bermuda's device list.

**Requirements:**
1. **GoogleFindMy-HA** integration must be installed (v1.7.0-3 or later)
2. Device must be set up in your Google Find My account
3. Device must not be disabled/ignored in Home Assistant

**Solution:**
1. Verify GoogleFindMy-HA is working (check for device_tracker entities)
2. Wait for the device to broadcast an EID (can take up to 15 minutes)
3. Check Bermuda logs for EID resolution messages
4. Restart Home Assistant if the device was recently added

**Technical Note:**
FMDN devices use rotating MAC addresses. Bermuda creates a "metadevice" that aggregates all the rotating addresses into a single stable device.

---

### IRK Device Not Tracking

**Symptom:**
Private BLE Device (Apple device, etc.) with IRK doesn't track properly.

**Requirements:**
1. **Private BLE Device** integration must be installed
2. IRK (Identity Resolving Key) must be correctly configured
3. Device must use Resolvable Private Addresses (RPAs)

**Solution:**
1. Verify the IRK is correct (32 hex characters)
2. Check that the device is using RPA (first character of MAC in 4-7 range)
3. Look for IRK resolution messages in debug logs

---

## Performance Issues

### High CPU Usage

**Symptom:**
Home Assistant CPU usage increases significantly with Bermuda.

**Possible Causes:**
1. **Too Many Devices**: Large number of tracked devices
2. **Fast Update Interval**: Processing updates too frequently
3. **UKF Enabled**: Experimental UKF uses more CPU

**Solution:**
1. Reduce the number of actively tracked devices
2. Increase the update interval in Bermuda settings
3. Disable UKF if not needed (it's experimental)

---

### Slow Updates

**Symptom:**
Device locations update slowly or with significant delay.

**Possible Causes:**
1. **Scanner Advertisement Interval**: Some scanners send data less frequently
2. **Device Sleep Mode**: BLE devices may sleep between advertisements
3. **Network Latency**: ESPHome scanners communicate over network

**Solution:**
1. Check scanner's advertisement interval (target: 1-2 seconds)
2. For battery devices, slower updates are normal (power saving)
3. Ensure network connectivity is stable for remote scanners

---

## Getting Help

If your issue isn't covered here:

1. **Enable Debug Logging:**
   ```yaml
   logger:
     logs:
       custom_components.bermuda: debug
   ```

2. **Check Diagnostics:**
   - Go to Settings → Integrations → Bermuda → 3-dot menu → Download Diagnostics
   - Review the diagnostics file for clues

3. **Report Issues:**
   - GitHub: https://github.com/jleinenbach/bermuda/issues
   - Include diagnostics (with sensitive data redacted)
   - Describe expected vs. actual behavior

---

## Glossary

| Term | Description |
|------|-------------|
| **ref_power** | Reference/TX power at 1 meter distance (dBm) |
| **Attenuation** | Signal decay rate with distance |
| **RSSI** | Received Signal Strength Indicator (dBm) |
| **EID** | Ephemeral Identifier (rotating ID for FMDN) |
| **IRK** | Identity Resolving Key (for Apple/Private BLE devices) |
| **RPA** | Resolvable Private Address (rotating MAC) |
| **Metadevice** | Virtual device aggregating multiple rotating addresses |
| **UKF** | Unscented Kalman Filter (experimental area selection) |
