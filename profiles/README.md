MCU Update Manager hardware profiles
====================================

Profiles are grouped by device role:

- `mainboard/` - printer controller boards, including USB-CAN bridge boards.
- `toolhead/` - CAN or USB toolhead boards such as EBB, SB, SHT, H36.
- `cartographer/` - Cartographer probe firmware profiles, managed from the
  Cartographer firmware repository instead of Klipper/Kalico builds.
- `beacon/` - reserved for Beacon vendor-managed firmware profiles.

Profile sections
----------------

- `match` identifies an already configured MCU from Klipper runtime, transport,
  MCU section name, and optional printer.cfg pin fingerprints.
- `build` describes the Klipper/Kalico firmware menuconfig target.
- `bootloader` describes the Katapult target to build before initial flashing.
- `initial_flash` describes first-time bootloader installation, normally DFU for
  STM32 boards or BOOTSEL for RP2040 boards.
- `update` describes the regular update path after Katapult or a vendor
  bootloader is already installed.
- `flash` is the current action selector used by the backend.

The loader reads YAML files recursively, so keeping profiles in these
subdirectories does not change existing profile IDs or saved confirmations.
