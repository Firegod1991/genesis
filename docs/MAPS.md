# Maps and survival layers

## The basemap

Genesis uses [Protomaps](https://protomaps.com) vector maps built from OpenStreetMap. A map is one `.pmtiles` file in `<vault>/maps/`. The dashboard draws every file it finds there, and serves them with the range requests the format needs.

| Command | Result |
|---|---|
| `genesis fetch --id world_overview` | Every country at low zoom. Built monthly by genesis-vault |
| `genesis fetch --id map_assets` | Fonts and icons so labels render |
| `genesis maps region --region europe/monaco` | Full detail for a [Geofabrik](https://download.geofabrik.de) region |
| `genesis maps region --name camp --bbox W,S,E,N` | Full detail for any box |
| `genesis maps region --region us/texas --maxzoom 12` | Less detail, much smaller |
| `genesis fetch --id planet_basemap` | The entire planet, about 140 GB |
| `genesis maps list` | What is installed |

Region cuts read only the needed byte ranges from the newest remote planet build, or from your local `planet.pmtiles` if you have one. With a local planet file you can cut regions for other people with no internet.

Region names are Geofabrik ids: `north-america`, `us/california`, `europe/germany/bayern`, `asia/japan`.

## Survival layers

One SQLite file, `<vault>/poi/survival.sqlite`, answers "what is nearest" offline.

| Layer | What is in it (OpenStreetMap tags) |
|---|---|
| Water | drinking water points, water points, wells, taps, springs, water towers and works |
| Medical | hospitals, clinics, doctors, pharmacies, dentists, veterinarians, ambulance stations, defibrillators |
| Fuel | fuel stations, vehicle charging, bottled gas, marine fuel |
| Power | power plants, substations, generators (rooftop solar is left out) |
| Shelter and safety | shelters, fire stations, police, community centres, assembly points, huts, camp sites, bunkers |
| Supply | supermarkets, convenience, hardware, outdoor and farm supply stores, marketplaces |
| Communications | communications towers, emergency phones |
| Transport | airfields, helipads, ferry terminals, rail stations, marinas |

The list lives in `catalog/poi_categories.json`. Change it and rebuild to track anything else OpenStreetMap maps.

### Get them

```sh
genesis fetch --id survival_layers_world     # prebuilt for the whole world, refreshed monthly
```

### Or build them

```sh
sudo apt install osmium-tool
genesis poi build --region us/california                 # downloads the extract, verifies it, builds
genesis poi build --region us/nevada                     # adds to the same database
genesis poi build --pbf planet-latest.osm.pbf --replace  # the whole world from the planet file
```

If the vault holds the World Resources Institute power plant database (`genesis fetch --id global_power_plants`), the build adds about 35,000 plants with fuel type and capacity.

### Ask them

```sh
genesis poi nearest --lat 43.7384 --lon 7.4246 -n 10
genesis poi nearest --lat 43.7384 --lon 7.4246 --cat medical
genesis poi stats
genesis poi export --format geojson --cat water --out water.geojson
```

The dashboard does the same on the map: set your position by typing coordinates or a Maidenhead grid locator, or by clicking. Browsers only share GPS position with secure pages, so on a plain local address you enter the position yourself.

## Limits to know

* OpenStreetMap is made by volunteers. Coverage of wells, springs and small clinics varies by country. A missing point does not mean nothing is there, and a mapped point may be dry, closed or gone.
* Treat every water source as unsafe until treated. The EPA guide in the survival pack explains how.
* Routing is not in the dashboard yet. The `routing` profile starts a Valhalla engine that builds from the extracts in `<vault>/osm/`.

Map data © OpenStreetMap contributors, available under the Open Database License.
