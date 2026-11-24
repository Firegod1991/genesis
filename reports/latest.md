# Catalog refresh 2026-11-24

| Pack | Items | New | Updated | Removed | Failed |
|---|---|---|---|---|---|
| ai | 17 | 0 | 0 | 0 | 0 |
| apps | 23 | 0 | 0 | 0 | 0 |
| code | 375 | 0 | 5 | 0 | 0 |
| isos | 24 | 0 | 1 | 0 | 1 |
| knowledge | 689 | 0 | 0 | 0 | 0 |
| library | 248 | 0 | 0 | 0 | 0 |
| maps | 6 | 0 | 1 | 0 | 0 |
| medical | 45 | 0 | 0 | 0 | 0 |
| radio | 33 | 0 | 0 | 0 | 0 |
| scripture | 1303 | 0 | 10 | 0 | 0 |
| services | 25 | 0 | 1 | 0 | 0 |
| survival | 77 | 1 | 1 | 0 | 1 |
| tools | 30 | 0 | 3 | 0 | 0 |

## Updated in code

* `devdocs_en_lua`: 2026-08 to 2026-11
* `devdocs_en_statsmodels`: 2026-05 to 2026-11
* `devdocs_en_zig`: 2026-08 to 2026-11
* `python`: 20261123 to 20261124
* `uv`: 0.13.9 to 0.13.10

## Updated in isos

* `tails`: 7.17.1 to 7.18

## Updated in maps

* `planet_basemap`: 20261123 to 20261124

## Updated in scripture

* `bible_englxxup`: 2026-11-20 to 2026-11-23
* `bible_gvs`: 2026-11-21 to 2026-11-24
* `bible_mkj`: 2026-11-20 to 2026-11-24
* `bible_nss`: 2026-11-20 to 2026-11-23
* `bible_rmyArli`: 2026-11-20 to 2026-11-24
* `bible_ronBayash`: 2026-11-20 to 2026-11-23
* `bible_tczchongthu`: 2026-11-13 to 2026-11-23
* `bible_tdt`: 2026-11-20 to 2026-11-24
* `bible_tsnsa`: 2026-11-23 to 2026-11-24
* `theographic`: b75c268 to c168673

## Updated in services

* `img_adsb`: 855a12d6681f to 6d77e136db65

## Updated in survival

* `100r.co_en_all`: 2026-08 to 2026-11

## New in survival

* `rifle_marksmanship_mcrp_3_01a`: Rifle Marksmanship (US Marine Corps MCRP 3-01A, 2001): safe handling, care and cleaning in every climate, and marksmanship

## Updated in tools

* `ffmpeg`: 2026-11-23 to 2026-11-24
* `llama_cpp`: b12674 to b12686
* `localsend`: 1.18.2 to 1.19.0

## Failed

These lines kept their last known good version where one existed.

* **isos** `collapseos`: FetchError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'collapseos.org'. (_ssl.c:1081)> for https://collapseos.org/files/collapseos-latest.tar.gz
* **survival** `rifle_marksmanship_mcrp_3_01a`: FetchError: <urlopen error _ssl.c:1063: The handshake operation timed out> for https://archive.org/download/MCRP3-01A/MCRP3-01A.pdf

## Warnings

* **code** `llvm`: not in llvmorg-23.1.6, taken from an earlier release: macos-arm64 from llvmorg-23.1.5; assets there: ['clang+llvm-23.1.6-aarch64-pc-windows-msvc.tar.xz', 'clang+llvm-23.1.6-aarch64-pc-windows-msvc.tar.zst', 'clang+llvm-23.1.6-x86_64-pc-windows-msvc.tar.xz', 'clang+llvm-23.1.6-x86_64-pc-windows-msvc.tar.zst', 'clang-tools-extra_doxygen-23.1.6.tar.xz', 'clang_doxygen-23.1.6.tar.xz', 'flang_doxygen-23.1.6.tar.xz', 'LLVM-23.1.6-Linux-ARM64.tar.xz', 'LLVM-23.1.6-Linux-ARM64.tar.zst', 'LLVM-23.1.6-Linux-X64.tar.xz', 'LLVM-23.1.6-Linux-X64.tar.zst', 'LLVM-23.1.6-win64.msi', 'and 5 more']
* **isos** `home_assistant_os`: not in 18.5, taken from an earlier release: linux-x64 from 18.4; assets there: ['haos_rpi3-64-18.5.img.xz', 'haos_rpi3-64-18.5.raucb', 'haos_rpi4-64-18.5.img.xz', 'haos_rpi4-64-18.5.raucb', 'haos_rpi5-64-18.5.img.xz', 'haos_rpi5-64-18.5.raucb', 'haos_yellow-18.5.img.xz', 'haos_yellow-18.5.raucb']

