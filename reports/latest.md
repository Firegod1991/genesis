# Catalog refresh 2026-11-15

| Pack | Items | New | Updated | Removed | Failed |
|---|---|---|---|---|---|
| ai | 15 | 0 | 0 | 0 | 0 |
| apps | 16 | 0 | 0 | 0 | 1 |
| code | 375 | 0 | 5 | 0 | 0 |
| isos | 24 | 0 | 0 | 0 | 0 |
| knowledge | 689 | 0 | 0 | 0 | 0 |
| library | 249 | 0 | 0 | 0 | 0 |
| maps | 6 | 0 | 0 | 0 | 2 |
| medical | 38 | 0 | 0 | 0 | 0 |
| radio | 28 | 0 | 3 | 0 | 1 |
| scripture | 1300 | 0 | 0 | 0 | 0 |
| services | 25 | 0 | 0 | 0 | 0 |
| survival | 44 | 0 | 0 | 0 | 0 |
| tools | 15 | 0 | 0 | 0 | 1 |

## Updated in code

* `bun`: bun-v1.4.4 to 1.4.4
* `duckdb`: ? to 1.5.6
* `jq`: jq-1.8.2 to 1.8.2
* `llvm`: llvmorg-23.1.5 to 23.1.5
* `odin`: dev-2026-11a to 2026-11a

## Updated in radio

* `meshcore_firmware`: companion-v1.18.0 to 1.18.0
* `rtl_sdr_blog_drivers`: V1.4.0 to 1.4.0
* `sdrpp`: 2022-11-22 to 2026-11-07

## Failed

These lines kept their last known good version where one existed.

* **apps** `aprsdroid`: GitHub release lookup failed for ge0rg/aprsdroid: HTTP 404 for https://api.github.com/repos/ge0rg/aprsdroid/releases/latest
* **maps** `world_overview`: GitHub release lookup failed for Firegod1991/genesis-vault: HTTP 404 for https://api.github.com/repos/Firegod1991/genesis-vault/releases/latest
* **maps** `survival_layers_world`: GitHub release lookup failed for Firegod1991/genesis-vault: HTTP 404 for https://api.github.com/repos/Firegod1991/genesis-vault/releases/latest
* **radio** `chirp`: FetchError: HTTP 403 for https://archive.chirpmyradio.com/chirp_next/
* **tools** `llama_cpp`: no asset in ggml-org/llama.cpp v1.0.0 matched ['bin-ubuntu-x64\\.(zip|tar\\.gz)$', 'bin-ubuntu-arm64\\.(zip|tar\\.gz)$', 'bin-win-cpu-x64\\.zip$', 'bin-macos-arm64\\.(zip|tar\\.gz)$']; assets: ['nightly-tag.txt']

## Warnings

* **code** `duckdb`: newest release v2.0.0 has none of the files, using v1.5.6; assets there: ['duckdb-cli-linux-amd64-musl.tar.gz', 'duckdb-cli-linux-amd64.tar.gz', 'duckdb-cli-linux-arm64-musl.tar.gz', 'duckdb-cli-linux-arm64.tar.gz', 'duckdb-cli-osx-amd64.tar.gz', 'duckdb-cli-osx-arm64.tar.gz', 'duckdb-cli-osx-universal.tar.gz', 'duckdb-cli-windows-amd64.tar.gz', 'duckdb-cli-windows-arm64.tar.gz', 'duckdb-shared-libs-linux-amd64-musl.tar.gz', 'duckdb-shared-libs-linux-amd64.tar.gz', 'duckdb-shared-libs-linux-arm64-musl.tar.gz', 'and 16 more']
* **code** `llvm`: not in llvmorg-23.1.5, taken from an earlier release: windows-x64 from llvmorg-22.1.8; assets there: ['clang+llvm-23.1.5-aarch64-pc-windows-msvc.tar.xz', 'clang+llvm-23.1.5-aarch64-pc-windows-msvc.tar.zst', 'clang+llvm-23.1.5-x86_64-pc-windows-msvc.tar.xz', 'clang+llvm-23.1.5-x86_64-pc-windows-msvc.tar.zst', 'LLVM-23.1.5-win64.msi', 'LLVM-23.1.5-woa64.msi', 'clang-tools-extra_doxygen-23.1.5.tar.xz', 'clang_doxygen-23.1.5.tar.xz', 'flang_doxygen-23.1.5.tar.xz', 'LLVM-23.1.5-Linux-ARM64.tar.xz', 'LLVM-23.1.5-Linux-ARM64.tar.zst', 'LLVM-23.1.5-Linux-X64.tar.xz', 'and 7 more']
* **isos** `home_assistant_os`: not in 18.5, taken from an earlier release: linux-x64 from 18.4; assets there: ['haos_rpi3-64-18.5.img.xz', 'haos_rpi3-64-18.5.raucb', 'haos_rpi4-64-18.5.img.xz', 'haos_rpi4-64-18.5.raucb', 'haos_rpi5-64-18.5.img.xz', 'haos_rpi5-64-18.5.raucb', 'haos_yellow-18.5.img.xz', 'haos_yellow-18.5.raucb']
* **radio** `js8call`: no asset for linux-arm64; assets: ['js8call-2.3.1-win64.exe', 'js8call-2.3.1.x86_64.rpm', 'js8call_2.3.1-x86_64.AppImage', 'JS8Call_2.3.1.dmg', 'js8call_2.3.1_amd64.deb']
* **tools** `keepassxc`: not in 2.8.0, taken from an earlier release: windows-x64 from 2.7.12; assets there: ['KeePassXC-2.8.0-ARM64.msi', 'KeePassXC-2.8.0-x64.msi', 'KeePassXC-2.8.0-1-aarch64.AppImage', 'KeePassXC-2.8.0-1-arm64.dmg', 'KeePassXC-2.8.0-1-x86_64.AppImage', 'KeePassXC-2.8.0-1-x86_64.dmg', 'KeePassXC-2.8.0-aarch64.AppImage', 'KeePassXC-2.8.0-arm64.dmg', 'KeePassXC-2.8.0-ARM64.zip', 'keepassxc-2.8.0-src.tar.xz', 'KeePassXC-2.8.0-x64.zip', 'KeePassXC-2.8.0-x86_64.AppImage', 'and 1 more']

