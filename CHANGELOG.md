## Unreleased

- Add configurable FMDN modes and EID parsing, improving manual selection and avoiding duplicate devices through canonical address normalization.
- Harden FMDN EID candidate extraction and deduplicate shared tracker identities to prevent ghost devices and capture variable-length EIDs.
- Align MAC normalization with Home Assistant formatting, separate pseudo-identifier handling, and stabilize FMDN metadevice keys to avoid address collisions.
