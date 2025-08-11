GeoChange — Unified App (Emulator + Location Spoofer)
=====================================================

Run (dev):
  python -m pip install PySide6
  python geochange_app.py

Build (onedir recommended):
  pyinstaller --noconsole --name GeoChange geochange_app.py

Usage:
- Emulator Manager tab:
  1) Install SDK — downloads commandlinetools, installs platform-tools, emulator, Google Play image,
     accepts licenses automatically.
  2) Create AVD (Play) — creates GeoChangePlay.
  3) Start Emulator / Stop Emulator / Open Play Store.
  4) List ADB Devices — populates device list; first emulator is used by the Spoofer.

- Location Spoofer tab:
  - Search or click the map, then "Teleport here" to send `adb emu geo fix <lon> <lat>`.
  - Saved locations JSON sits next to geochange_app.py.
  - Map tiles cache at C:\geochange\map_cache.

Defaults:
- SDK root: C:\geochange\sdk
- AVD home: C:\geochange\avd
- AVD name: GeoChangePlay
