# Catalog refresh 2026-11-25

| Pack | Items | New | Updated | Removed | Failed |
|---|---|---|---|---|---|
| ai | 20 | 2 | 0 | 0 | 0 |
| apps | 23 | 0 | 0 | 0 | 0 |
| code | 375 | 0 | 0 | 0 | 0 |
| isos | 24 | 0 | 0 | 0 | 1 |
| knowledge | 689 | 0 | 0 | 0 | 0 |
| library | 248 | 0 | 0 | 0 | 0 |
| maps | 6 | 0 | 0 | 0 | 0 |
| medical | 45 | 0 | 0 | 0 | 0 |
| radio | 33 | 0 | 0 | 0 | 0 |
| scripture | 1303 | 0 | 0 | 0 | 0 |
| services | 25 | 0 | 0 | 0 | 0 |
| survival | 77 | 0 | 0 | 0 | 1 |
| tools | 30 | 0 | 0 | 0 | 0 |

## New in ai

* `qwen35_4b_abliterated`: Qwen 3.5 4B abliterated (huihui_ai: Qwen 3.5 4B with its refusals removed)
* `qwen35_9b_abliterated`: Qwen 3.5 9B abliterated (huihui_ai: Qwen 3.5 9B with its refusals removed)

## Failed

These lines kept their last known good version where one existed.

* **isos** `collapseos`: FetchError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'collapseos.org'. (_ssl.c:1081)> for https://collapseos.org/files/collapseos-latest.tar.gz
* **survival** `plants_missouri_river_region`: FetchError: <urlopen error _ssl.c:1063: The handshake operation timed out> for https://archive.org/download/usesofplantsbyin00gilm/usesofplantsbyin00gilm.pdf

## Warnings

* **code** `llvm`: not in llvmorg-23.1.6, taken from an earlier release: macos-arm64 from llvmorg-23.1.5; assets there: ['clang+llvm-23.1.6-aarch64-pc-windows-msvc.tar.xz', 'clang+llvm-23.1.6-aarch64-pc-windows-msvc.tar.zst', 'clang+llvm-23.1.6-x86_64-pc-windows-msvc.tar.xz', 'clang+llvm-23.1.6-x86_64-pc-windows-msvc.tar.zst', 'clang-tools-extra_doxygen-23.1.6.tar.xz', 'clang_doxygen-23.1.6.tar.xz', 'flang_doxygen-23.1.6.tar.xz', 'LLVM-23.1.6-Linux-ARM64.tar.xz', 'LLVM-23.1.6-Linux-ARM64.tar.zst', 'LLVM-23.1.6-Linux-X64.tar.xz', 'LLVM-23.1.6-Linux-X64.tar.zst', 'LLVM-23.1.6-win64.msi', 'and 5 more']
* **isos** `home_assistant_os`: not in 18.5, taken from an earlier release: linux-x64 from 18.4; assets there: ['haos_rpi3-64-18.5.img.xz', 'haos_rpi3-64-18.5.raucb', 'haos_rpi4-64-18.5.img.xz', 'haos_rpi4-64-18.5.raucb', 'haos_rpi5-64-18.5.img.xz', 'haos_rpi5-64-18.5.raucb', 'haos_yellow-18.5.img.xz', 'haos_yellow-18.5.raucb']

