# Scripture

The scripture pack follows the Bible from its source languages to the present, in every language that [eBible.org](https://ebible.org) is permitted to share. Genesis downloads each translation directly from eBible.org to your vault. Nothing is altered.

## Three tiers

**Seed: the line from the sources to today.** Always included, whatever languages you read:

| Language | Texts |
|---|---|
| Hebrew | Westminster Leningrad Codex, Hebrew Masoretic Old Testament |
| Greek | Septuagint (two editions), Textus Receptus, Byzantine, Majority Text, Tischendorf, Text Critical New Testament |
| Latin | Clementine Vulgate |
| Coptic | Bohairic New Testament, Coptic New Testament |

Then, for English readers: Wycliffe (portions), Tyndale New Testament, Geneva 1599, King James with Apocrypha, Douay Rheims, Webster, Young's Literal, Darby, Revised Version, American Standard, JPS Tanakh 1917, Brenton's English Septuagint, Bible in Basic English, Berean Standard, World English Bible.

And one established public domain translation each for Modern Hebrew, Arabic, Chinese (simplified and traditional), Spanish, French, German, Italian, Portuguese, Russian, Ukrainian, Dutch, Korean, Japanese, Vietnamese, Persian, Haitian Creole, Thai and Esperanto.

**Ark: every public domain translation.** About 110 translations in about 47 languages.

**Eden: every translation eBible.org may redistribute.** About 1,300 translations in more than 1,000 languages. Most of these are modern translations still under copyright, shared with permission. Each download carries its own copyright page.

## Commands

```sh
genesis fetch --pack scripture --tier seed
genesis fetch --pack scripture --tier ark  --lang eng,spa,fra
genesis fetch --pack scripture --tier eden --lang '*'      # every language
genesis fetch --id bible_engwebp --id bible_hboWLC          # exact translations
```

Language codes are ISO 639 3, as eBible uses them: `eng`, `spa`, `hbo` (Ancient Hebrew), `grc` (Ancient Greek), `lat`, `cop`.

## On disk

```
<vault>/scripture/<translation>/
    index.htm          start here: a complete readable copy
    copr.htm           copyright and permission for this translation
    <translation>_usfm.zip   the source text in USFM, the format translators exchange
    genesis.json       title, language, script, direction, counts
```

The dashboard lists every installed translation under Scripture, source languages first, and serves the pages to any device on the network. The folders are plain HTML and also open straight from the drive with no server at all.

## Study helps

The same pack carries the Biblical Hermeneutics, Christianity and Mi Yodeya (Judaism) Stack Exchange archives. These are commentary and discussion, not scripture.

## People, places and events

The seed tier also carries [Theographic Bible Metadata](https://github.com/robertrouse/theographic-bible-metadata) by Robert Rouse of Viz.Bible: a knowledge graph that ties every verse of the King James Version to the people, places and events it mentions.

| Table | What it holds |
|---|---|
| People | More than 3,000, each with the verses that name them and, where the text gives it, family and group |
| Places | About 1,300, nearly all with map coordinates |
| Events | A dated timeline with the people and places involved |
| Easton's Bible Dictionary | About 6,500 entries, linked to the people and places |

It comes with the seed tier. To fetch it alone:

```sh
genesis fetch --id theographic
```

It lands in `<vault>/study/theographic/` as JSON, CSV and GeoJSON: raw material for study tools, not pages to read. The dashboard does not browse it yet. `geo/bible-places.geojson` opens in any mapping program. The monthly refresh follows the newest commit of that repository.

The data is free to use and copy under CC BY-SA 4.0. If you build on it, credit Theographic and the sources it lists in `docs/data-source-bibliography.md`, some of which carry their own licenses. Its dates follow Dr. Floyd Nolen Jones, *The Chronology of the Old Testament*, which is one chronology among several.

## Rights

Genesis only lists translations that eBible.org marks as redistributable, and labels each one public domain or copyrighted in the catalog. If you hold rights to a translation and believe it is listed in error, open an issue and it will be removed from the catalog.
