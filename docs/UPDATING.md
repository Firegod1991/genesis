# How Genesis stays current

Genesis separates two things: knowing what the newest version of something is, and having it on your drive.

## The catalog

`catalog/packs/*.json` is written by people. Each line names a thing to track and where its newest version can be discovered. It never contains a version number.

`catalog/lock/*.json` is written by the machine. `genesis refresh` visits every source and records the exact newest download: URL, size, checksum, version and date.

| Source type | How the newest version is found |
|---|---|
| `kiwix`, `kiwix_rule` | The Kiwix library catalog. Rules pull in whole families, such as every DevDocs book or Wikipedia in every language, so new books appear with no edit here |
| `ebible` | The eBible.org translation list, limited to translations it is allowed to share |
| `github` | The newest release of a repository, matched to each platform |
| `checksum_file`, `ubuntu`, `openwrt` | The checksum list a distribution publishes beside its images |
| `toolchain` | The official release feed of each language |
| `tails`, `alpine`, `fedora`, `rpi_imager`, `sourceforge`, `fdroid` | That project's own release feed |
| `protomaps` | The newest daily planet map build |
| `ollama`, `docker` | The model and image registries, which give a digest that proves the item exists |
| `static`, `http_index` | A fixed address or a plain file listing |

## Every month on GitHub

`.github/workflows/refresh.yml` runs at 06:17 UTC on the first of each month, on demand, and whenever a pack changes. It:

1. runs `genesis refresh` against the live sources
2. commits the new lock files, `docs/CATALOG.md` and `reports/latest.md`
3. publishes a release named for the month, with the list of what changed

A source that is down or has changed shape does not break the run. Its line keeps the last known good version, is marked stale, and is listed under Failed in the report so someone can fix the pattern.

GitHub pauses scheduled workflows in repositories with no activity for 60 days. The monthly commit normally counts as activity. If the schedule ever stops, open the Actions tab and enable it again.

## Every month on your box

```sh
genesis timer        # installs a systemd timer: monthly, with a random delay, catches up after downtime
genesis update       # the same thing, right now
```

`genesis update` pulls the newest catalog with git, fetches whatever changed for the tier, languages and packs you chose, verifies it, removes the superseded files, pulls newer service images and restarts the library so new books show up. With no connection it leaves everything exactly as it is.

## Checking and sealing

```sh
genesis status       # current, outdated and missing, per pack
genesis verify       # re hash installed files against the catalog
genesis seal         # write an offline restore kit into the vault
```

A sealed vault contains the service images (`docker save`), a git bundle of this repository and a manifest with the SHA 256 of every file. `restore/RESTORE.txt` on the drive explains how to bring up a new machine from it.

## Adding or fixing a line

1. Edit a file in `catalog/packs/`.
2. Run `genesis lint`, then `genesis refresh --pack <name>` and read the output.
3. Open a pull request. The refresh workflow validates it against the live source.
