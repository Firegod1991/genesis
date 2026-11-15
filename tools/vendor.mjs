// Rebuilds dashboard/static/vendor and the offline map styles from pinned npm packages.
// Run: node tools/vendor.mjs   (needs npm and a network connection; the dashboard itself needs neither)
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const PINS = {
  "maplibre-gl": "6.18.0",
  "pmtiles": "4.5.0",
  "@protomaps/basemaps": "5.7.2",
  "@fontsource-variable/overpass": "5.3.0",
};

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const out = path.join(root, "dashboard", "static");
const vendor = path.join(out, "vendor");
const work = fs.mkdtempSync(path.join(os.tmpdir(), "genesis-vendor-"));

fs.writeFileSync(path.join(work, "package.json"), JSON.stringify({ name: "genesis-vendor", private: true }));
execFileSync("npm", ["install", "--silent", "--no-audit", "--no-fund", ...Object.entries(PINS).map(([n, v]) => `${n}@${v}`)],
  { cwd: work, stdio: "inherit", shell: process.platform === "win32" });
const mod = (...p) => path.join(work, "node_modules", ...p);
const copy = (from, to) => { fs.mkdirSync(path.dirname(to), { recursive: true }); fs.copyFileSync(from, to); };

for (const f of ["maplibre-gl.mjs", "maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs", "maplibre-gl.css"]) {
  copy(mod("maplibre-gl", "dist", f), path.join(vendor, f));
}
copy(mod("maplibre-gl", "LICENSE.txt"), path.join(vendor, "maplibre-gl.LICENSE.txt"));
copy(mod("pmtiles", "dist", "pmtiles.js"), path.join(vendor, "pmtiles.js"));
for (const f of ["overpass-latin-wght-normal.woff2", "overpass-latin-ext-wght-normal.woff2"]) {
  copy(mod("@fontsource-variable", "overpass", "files", f), path.join(vendor, "fonts", f));
}
copy(mod("@fontsource-variable", "overpass", "LICENSE"), path.join(vendor, "fonts", "overpass.LICENSE.txt"));

// Neither package ships its license file on npm, so take them from the source repositories.
const licenses = {
  "pmtiles.LICENSE.txt": "https://raw.githubusercontent.com/protomaps/PMTiles/main/LICENSE",
  "protomaps-basemaps.LICENSE.md": "https://raw.githubusercontent.com/protomaps/basemaps/main/LICENSE.md",
};
for (const [name, url] of Object.entries(licenses)) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`could not fetch ${url}: ${res.status}`);
  fs.writeFileSync(path.join(vendor, name), await res.text());
}

// Map styles: the Protomaps basemap layers with addresses the dashboard serves from the vault.
const { layers, namedFlavor } = await import(pathToFileURL(mod("@protomaps", "basemaps", "dist", "esm", "index.js")).href)
  .catch(() => import(pathToFileURL(mod("@protomaps", "basemaps", "dist", "index.js")).href));
for (const flavor of ["light", "dark"]) {
  const style = {
    version: 8,
    name: "Genesis " + flavor,
    glyphs: "/maps/assets/fonts/{fontstack}/{range}.pbf",
    sprite: "/maps/assets/sprites/v4/" + flavor,
    sources: { protomaps: { type: "vector", url: "pmtiles://__TILES__", attribution: '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' } },
    layers: layers("protomaps", namedFlavor(flavor), { lang: "en" }),
  };
  fs.writeFileSync(path.join(out, `map-${flavor}.json`), JSON.stringify(style));
}

fs.rmSync(work, { recursive: true, force: true });
console.log("vendored", Object.entries(PINS).map(([n, v]) => `${n}@${v}`).join(", "));
