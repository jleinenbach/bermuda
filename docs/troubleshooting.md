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
WARNING [custom_components.bermuda.filters.kalman] Invalid ref_power X.X dBm (expected -100 to +20), using default -55
```

**Cause:**
The scanner is reporting a TX power (reference power) value outside the expected BLE range. This can happen with:
- Misconfigured scanner firmware
- Custom TX power settings
- Hardware reporting errors

**Valid TX Power Ranges:**

| Device Type | Typical TX Power Range |
|-------------|----------------------|
| ESP32 (AtomS3, M5Stack, etc.) | -12 dBm to +9 dBm |
| ESP32-S3/C3 variants | -12 dBm to +21 dBm |
| Bluetooth Class 1 | up to +20 dBm |
| Bluetooth Class 2 | up to +4 dBm |
| Bluetooth Class 3 | up to 0 dBm |
| Most BLE trackers | -20 dBm to -4 dBm |
| iBeacons | -30 dBm to 0 dBm |

**Note:** Positive TX power values (e.g., +3 dBm) are **completely valid** for ESP32-based scanners like AtomS3. These are high-power transmitters commonly used in ESPHome scanner setups.

**Solution:**
1. If the reported value is within the valid BLE range (-100 to +20 dBm), this warning indicates a bug that has been fixed in recent versions. Update Bermuda.
2. If the value is truly invalid (e.g., +50 dBm or -150 dBm), check your scanner's firmware configuration.

**Technical Background:**
The `ref_power` (reference power / TX power) is used by the adaptive Kalman filter to adjust measurement noise based on signal strength. Invalid values can cause incorrect distance calculations.

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
   - GitHub: https://github.com/agittins/bermuda/issues
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
