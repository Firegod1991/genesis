# Catalog refresh 2026-11-17

| Pack | Items | New | Updated | Removed | Failed |
|---|---|---|---|---|---|
| ai | 17 | 2 | 0 | 0 | 0 |
| apps | 23 | 6 | 0 | 0 | 0 |
| code | 375 | 0 | 0 | 0 | 0 |
| isos | 24 | 0 | 0 | 0 | 0 |
| knowledge | 689 | 0 | 0 | 0 | 0 |
| library | 252 | 2 | 0 | 0 | 0 |
| maps | 6 | 0 | 0 | 0 | 0 |
| medical | 42 | 0 | 0 | 0 | 0 |
| radio | 28 | 0 | 0 | 0 | 0 |
| scripture | 1301 | 0 | 0 | 0 | 0 |
| services | 25 | 0 | 1 | 0 | 0 |
| survival | 76 | 0 | 0 | 0 | 1 |
| tools | 29 | 13 | 1 | 0 | 0 |

## New in ai

* `deepseek_r1_8b`: DeepSeek R1 8B (step by step reasoning)
* `glm_ocr`: GLM-OCR (reads printed pages, tables and formulas from images)

## New in apps

* `ankidroid`: AnkiDroid (flashcards)
* `columba`: Columba (Reticulum messenger over Bluetooth, WiFi and LoRa)
* `koreader`: KOReader (reads PDF, EPUB and DjVu)
* `localsend_android`: LocalSend (send files between nearby devices)
* `meshenger`: Meshenger (voice and video calls on a local network, no server)
* `wifi_analyzer`: WiFi Analyzer (pick a clear channel for the hotspot)

## New in library

* `chinese_poetry`: 中华古诗词数据库 (classical Chinese poetry: 55,000 Tang and 260,000 Song poems)
* `howtocook_zh`: 程序员做饭指南 HowToCook (a precise home cooking guide, in Chinese)

## Updated in services

* `img_adsb`: ed8a1395ad66 to a0a6c252492e

## Updated in tools

* `llama_cpp`: b12455 to b12458

## New in tools

* `anki`: Anki (flashcards that schedule what to review)
* `copyparty`: copyparty (turn any computer into a file server with one file)
* `freecad`: FreeCAD (design parts in 3D)
* `kicad`: KiCad (design circuit boards)
* `localsend`: LocalSend (send files between nearby devices, no internet)
* `openscad`: OpenSCAD (design parts by writing code)
* `orcaslicer`: OrcaSlicer (prepare 3D prints for almost any printer)
* `radpro`: Rad Pro (open firmware for common handheld Geiger counters)
* `rustdesk`: RustDesk (remote desktop that works on a local network)
* `umi_ocr`: Umi-OCR (read text out of photos and scans, offline)
* `universal_gcode_sender`: Universal G-code Sender (run CNC mills, routers and lasers)
* `veracrypt`: VeraCrypt (encrypted drives and containers)
* `whisper_cpp`: whisper.cpp (speech to text engine, source)

## Failed

These lines kept their last known good version where one existed.

* **survival** `iroquois_food_plants`: FetchError: <urlopen error _ssl.c:1063: The handshake operation timed out> for https://archive.org/download/iroquoisusesofma00parkrich/iroquoisusesofma00parkrich.pdf

## Warnings

* **isos** `home_assistant_os`: not in 18.5, taken from an earlier release: linux-x64 from 18.4; assets there: ['haos_rpi3-64-18.5.img.xz', 'haos_rpi3-64-18.5.raucb', 'haos_rpi4-64-18.5.img.xz', 'haos_rpi4-64-18.5.raucb', 'haos_rpi5-64-18.5.img.xz', 'haos_rpi5-64-18.5.raucb', 'haos_yellow-18.5.img.xz', 'haos_yellow-18.5.raucb']
* **radio** `chirp`: the server would not confirm chirp-next-20261113-installer.exe, chirp-20261113-py3-none-any.whl, chirp-next-20261113.app.zip

