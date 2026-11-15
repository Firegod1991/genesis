# Catalog refresh 2026-11-15

| Pack | Items | New | Updated | Removed | Failed |
|---|---|---|---|---|---|
| ai | 15 | 0 | 0 | 0 | 0 |
| apps | 16 | 0 | 0 | 0 | 0 |
| code | 375 | 0 | 0 | 0 | 0 |
| isos | 24 | 0 | 0 | 0 | 0 |
| knowledge | 689 | 0 | 0 | 0 | 0 |
| library | 249 | 0 | 0 | 0 | 0 |
| maps | 6 | 0 | 0 | 0 | 2 |
| medical | 38 | 0 | 0 | 0 | 0 |
| radio | 28 | 0 | 2 | 0 | 0 |
| scripture | 1300 | 0 | 0 | 0 | 0 |
| services | 25 | 0 | 0 | 0 | 0 |
| survival | 44 | 0 | 0 | 0 | 0 |
| tools | 15 | 0 | 1 | 0 | 0 |

## Updated in radio

* `chirp`: ? to 20261113
* `wsjtx`: 3.0.0 to 3.0.0-rc1

## Updated in tools

* `llama_cpp`: ? to b12396

## Failed

These lines kept their last known good version where one existed.

* **maps** `world_overview`: GitHub release lookup failed for Firegod1991/genesis-vault: HTTP 404 for https://api.github.com/repos/Firegod1991/genesis-vault/releases/latest
* **maps** `survival_layers_world`: GitHub release lookup failed for Firegod1991/genesis-vault: HTTP 404 for https://api.github.com/repos/Firegod1991/genesis-vault/releases/latest

## Warnings

* **code** `duckdb`: newest release v2.0.0 has none of the files, using v1.5.6; assets there: ['duckdb-cli-linux-amd64-musl.tar.gz', 'duckdb-cli-linux-amd64.tar.gz', 'duckdb-cli-linux-arm64-musl.tar.gz', 'duckdb-cli-linux-arm64.tar.gz', 'duckdb-cli-osx-amd64.tar.gz', 'duckdb-cli-osx-arm64.tar.gz', 'duckdb-cli-osx-universal.tar.gz', 'duckdb-cli-windows-amd64.tar.gz', 'duckdb-cli-windows-arm64.tar.gz', 'duckdb-shared-libs-linux-amd64-musl.tar.gz', 'duckdb-shared-libs-linux-amd64.tar.gz', 'duckdb-shared-libs-linux-arm64-musl.tar.gz', 'and 16 more']
* **code** `llvm`: not in llvmorg-23.1.5, taken from an earlier release: windows-x64 from llvmorg-22.1.8; assets there: ['clang+llvm-23.1.5-aarch64-pc-windows-msvc.tar.xz', 'clang+llvm-23.1.5-aarch64-pc-windows-msvc.tar.zst', 'clang+llvm-23.1.5-x86_64-pc-windows-msvc.tar.xz', 'clang+llvm-23.1.5-x86_64-pc-windows-msvc.tar.zst', 'LLVM-23.1.5-win64.msi', 'LLVM-23.1.5-woa64.msi', 'clang-tools-extra_doxygen-23.1.5.tar.xz', 'clang_doxygen-23.1.5.tar.xz', 'flang_doxygen-23.1.5.tar.xz', 'LLVM-23.1.5-Linux-ARM64.tar.xz', 'LLVM-23.1.5-Linux-ARM64.tar.zst', 'LLVM-23.1.5-Linux-X64.tar.xz', 'and 7 more']
* **isos** `home_assistant_os`: not in 18.5, taken from an earlier release: linux-x64 from 18.4; assets there: ['haos_rpi3-64-18.5.img.xz', 'haos_rpi3-64-18.5.raucb', 'haos_rpi4-64-18.5.img.xz', 'haos_rpi4-64-18.5.raucb', 'haos_rpi5-64-18.5.img.xz', 'haos_rpi5-64-18.5.raucb', 'haos_yellow-18.5.img.xz', 'haos_yellow-18.5.raucb']
* **radio** `chirp`: the server would not confirm chirp-next-20261113-installer.exe, chirp-20261113-py3-none-any.whl, chirp-next-20261113.app.zip

