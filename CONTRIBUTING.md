# Contributing

The most useful contribution is a catalog line: one more thing worth keeping, tracked at its newest version.

## Three ways to help

1. **Suggest something.** No code needed. [Open a suggestion](https://github.com/Firegod1991/genesis/issues/new/choose) with the link and a line on why it matters. A maintainer adds it.
2. **Add a catalog line yourself.** The steps are below. Never used git? Open the pack file on GitHub, press the pencil icon, add your line and follow the prompts. GitHub makes your own copy of the project and turns your edit into a pull request.
3. **Report from real hardware.** See the end of this page.

Nobody needs an invitation. Every pull request is tested automatically and read by a maintainer, who checks the source link before merging it.

## Add something to the catalog

1. Pick the pack in `catalog/packs/` and add a line. Three common shapes:

   ```json
   {"zim": "ham.stackexchange.com_en_all", "tier": "seed", "tags": ["ham"]}
   ```
   A Kiwix book. Find names in the [Kiwix library](https://library.kiwix.org).

   ```json
   {"id": "ripgrep", "title": "ripgrep", "tier": "ark", "license": "MIT",
    "source": {"type": "github", "repo": "BurntSushi/ripgrep",
               "assets": {"linux-x64": "x86_64-unknown-linux-musl\\.tar\\.gz$"}}}
   ```
   A GitHub release. Each platform maps to a pattern that matches one release file.

   ```json
   {"id": "debian_netinst", "title": "Debian stable (network installer)", "tier": "ark",
    "source": {"type": "checksum_file", "url": "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/SHA256SUMS",
               "pattern": "^debian-[\\d.]+-amd64-netinst\\.iso$"}}
   ```
   Anything that publishes a checksum list.

   All source types are described in [docs/UPDATING.md](docs/UPDATING.md) and implemented in `genesis/resolvers.py`.

2. Check it:

   ```sh
   python3 -m genesis lint
   python3 -m genesis refresh --pack <pack>
   python3 -m unittest discover -s tests
   ```

3. Open a pull request. Say what the item is for and what license it carries.

## What belongs

* Free to download and legal to keep. Public domain, open licenses, or free distribution by the publisher.
* Useful with no internet.
* From the original publisher wherever possible.

## What does not

* Pirated books, films, software or operating systems.
* Weapons manufacturing instructions.
* Anything that needs an account or a payment to download.

## Code

Standard library Python only, so Genesis runs on a bare machine. Keep functions small and say why in comments, not what. Every resolver needs a test with a fixture in `tests/`.

## Reports from real hardware

Genesis needs people to try it on real radios, GPUs and Raspberry Pis. Open an issue with what you ran, on what, and what happened, including when it simply worked.
