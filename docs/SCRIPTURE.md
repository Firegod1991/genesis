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

## Rights

Genesis only lists translations that eBible.org marks as redistributable, and labels each one public domain or copyrighted in the catalog. If you hold rights to a translation and believe it is listed in error, open an issue and it will be removed from the catalog.
