// Genesis dashboard. No build step, no network beyond this box.
import * as maplibregl from "./vendor/maplibre-gl.mjs";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const k of kids.flat()) if (k != null) node.append(k);
  return node;
};
const api = (path) => fetch("api/" + path).then((r) => r.json());
const bytes = (n) => {
  if (!n && n !== 0) return "?";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return (i < 2 ? Math.round(n) : n.toFixed(1)) + " " + units[i];
};
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: fine */ } },
};
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
const serviceUrl = (s) => `${location.protocol}//${location.hostname}:${s.public}/`;

// ---------------------------------------------------------------- position helpers
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWX";
function maidenhead(lat, lon) {
  const x = lon + 180, y = lat + 90;
  return LETTERS[Math.floor(x / 20)] + LETTERS[Math.floor(y / 10)] + Math.floor((x % 20) / 2) + Math.floor(y % 10) +
    LETTERS[Math.floor((x % 2) * 12)].toLowerCase() + LETTERS[Math.floor((y % 1) * 24)].toLowerCase();
}
function parsePosition(text) {
  const t = text.trim();
  const grid = t.match(/^([A-R])([A-R])(\d)(\d)(?:([A-X])([A-X]))?$/i);
  if (grid) {
    const [, a, b, c, d, e, f] = grid.map((s) => (s || "").toUpperCase());
    let lon = LETTERS.indexOf(a) * 20 - 180 + Number(c) * 2, lat = LETTERS.indexOf(b) * 10 - 90 + Number(d);
    if (e) { lon += LETTERS.indexOf(e) / 12 + 1 / 24; lat += LETTERS.indexOf(f) / 24 + 1 / 48; } else { lon += 1; lat += 0.5; }
    return { lat, lon };
  }
  const nums = t.match(/-?\d+(?:\.\d+)?/g);
  if (!nums || nums.length < 2) return null;
  const lat = Number(nums[0]), lon = Number(nums[1]);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon } : null;
}

// ---------------------------------------------------------------- map
let map, marker, categories = {}, position = null, poiData = { type: "FeatureCollection", features: [] };
const hidden = new Set(JSON.parse(store.get("genesis.hidden") || "[]"));

function hint(text) {
  const p = $("#maphint");
  p.hidden = !text;
  p.textContent = text || "";
}

async function buildStyle(info) {
  const night = document.documentElement.classList.contains("night");
  const base = await fetch(night ? "map-dark.json" : "map-light.json").then((r) => r.json());
  const files = info.maps;
  const planet = files.find((m) => m.name === "planet");
  const overview = files.find((m) => m.name === "world_overview");
  const regions = files.filter((m) => m.name !== "planet" && m.name !== "world_overview");
  const stack = planet ? [planet] : [overview, ...regions].filter(Boolean);
  const style = { version: 8, name: "Genesis", sources: {}, layers: [] };
  if (info.assets) { style.glyphs = location.origin + "/maps/assets/fonts/{fontstack}/{range}.pbf"; style.sprite = location.origin + base.sprite; }
  style.layers.push(...base.layers.filter((l) => l.type === "background"));
  for (const m of stack) {
    const src = "m_" + m.name;
    style.sources[src] = { type: "vector", url: `pmtiles://${location.origin}/maps/${m.file}`, attribution: "© OpenStreetMap contributors, Protomaps" };
    for (const layer of base.layers) {
      if (layer.type === "background" || (layer.type === "symbol" && !info.assets)) continue;
      const copy = { ...layer, id: `${layer.id}__${m.name}`, source: src };
      // The overview carries the world to zoom 6. Regions take over above that so labels are not doubled.
      if (m === overview && regions.length && layer.type === "symbol") copy.maxzoom = 7;
      if (m !== overview && overview && !planet) copy.minzoom = Math.max(layer.minzoom || 0, 7);
      style.layers.push(copy);
    }
  }
  const colors = ["match", ["get", "cat"]];
  for (const [id, c] of Object.entries(categories)) colors.push(id, c.color);
  colors.push("#888888");
  style.sources.poi = { type: "geojson", data: poiData };
  style.layers.push({
    id: "poi", type: "circle", source: "poi",
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 3, 14, 7], "circle-color": colors,
             "circle-stroke-color": "#16201b", "circle-stroke-width": 1.2 },
  });
  return style;
}

async function loadPlaces() {
  if (!map || map.getZoom() < 8) { poiData = { type: "FeatureCollection", features: [] }; map?.getSource("poi")?.setData(poiData); return; }
  const b = map.getBounds();
  const cats = Object.keys(categories).filter((c) => !hidden.has(c));
  if (!cats.length) { poiData.features = []; map.getSource("poi")?.setData(poiData); return; }
  const q = `west=${b.getWest()}&south=${b.getSouth()}&east=${b.getEast()}&north=${b.getNorth()}&cat=${cats.join(",")}`;
  const res = await api("poi/bbox?" + q);
  poiData = { type: "FeatureCollection", features: (res.places || []).map((p) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
    properties: { cat: p.cat, sub: p.sub, name: p.name || "", tags: JSON.stringify(p.tags || {}) } })) };
  map.getSource("poi")?.setData(poiData);
}

function placeCard(p) {
  const tags = typeof p.tags === "string" ? JSON.parse(p.tags) : (p.tags || {});
  const card = el("div", {}, el("strong", { textContent: p.name || p.sub.replaceAll("_", " ") }),
    el("div", { textContent: `${categories[p.cat]?.label || p.cat}: ${p.sub.replaceAll("_", " ")}` }));
  for (const [k, v] of Object.entries(tags).slice(0, 6)) card.append(el("div", { textContent: `${k.replaceAll("_", " ")}: ${v}` }));
  return card;
}

async function setPosition(lat, lon, fly = true) {
  position = { lat, lon };
  $("#pos").value = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  $("#grid").textContent = maidenhead(lat, lon);
  store.set("genesis.position", JSON.stringify(position));
  if (map) {
    if (!marker) marker = new maplibregl.Marker({ color: "#6a2880" });
    marker.setLngLat([lon, lat]).addTo(map);
    if (fly) map[calm ? "jumpTo" : "flyTo"]({ center: [lon, lat], zoom: Math.max(map.getZoom(), 12) });
  }
  const body = $("#bearings tbody");
  const res = await api(`poi/nearest?lat=${lat}&lon=${lon}&n=2&each=1`);
  body.replaceChildren();
  if (res.error) {
    body.append(el("tr", {}, el("td", { colSpan: 4, className: "empty",
      textContent: "No survival layers installed yet. Run: genesis fetch --id survival_layers_world, or build your region with genesis poi build." })));
    return;
  }
  for (const [cat, places] of Object.entries(res.by_category || {})) {
    const c = categories[cat] || { label: cat, color: "#888" };
    if (!places.length) continue;
    places.forEach((p, i) => {
      const dot = el("span", { className: "dot" });
      dot.style.background = c.color;
      const row = el("tr", {},
        el("td", { className: "need" }, i === 0 ? [dot, c.label] : ""),
        el("td", {}, p.name || "Unnamed", " ", el("span", { className: "kind", textContent: p.sub.replaceAll("_", " ") })),
        el("td", { className: "num" }, el("span", { className: "km", textContent: p.km < 10 ? p.km.toFixed(2) : p.km.toFixed(0) }), " km"),
        el("td", { className: "num", textContent: `${p.compass} ${String(p.bearing).padStart(3, "0")}°` }));
      row.dataset.lat = p.lat;
      row.tabIndex = 0;
      const go = () => { map?.[calm ? "jumpTo" : "flyTo"]({ center: [p.lon, p.lat], zoom: 15 });
        if (map) new maplibregl.Popup().setLngLat([p.lon, p.lat]).setDOMContent(placeCard(p)).addTo(map); };
      row.addEventListener("click", go);
      row.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      body.append(row);
    });
  }
  if (!body.children.length) body.append(el("tr", {}, el("td", { colSpan: 4, className: "empty", textContent: "Nothing mapped within 2000 km of this position." })));
}

async function initMap() {
  categories = await api("poi/categories");
  const box = $("#layers");
  for (const [id, c] of Object.entries(categories)) {
    const input = el("input", { type: "checkbox", checked: !hidden.has(id) });
    input.addEventListener("change", () => { input.checked ? hidden.delete(id) : hidden.add(id);
      store.set("genesis.hidden", JSON.stringify([...hidden])); loadPlaces(); });
    const dot = el("span", { className: "dot" });
    dot.style.background = c.color;
    box.append(el("label", {}, input, dot, c.label));
  }
  const info = await api("maps");
  const saved = JSON.parse(store.get("genesis.position") || "null");
  if (!info.maps.length) {
    hint("No map installed yet. Run: genesis fetch --id world_overview --id map_assets, then genesis maps region --region <your region>.");
    $("#map").hidden = true;
    if (saved) setPosition(saved.lat, saved.lon, false);
    return;
  }
  if (!info.assets) hint("Map labels need fonts. Run: genesis fetch --id map_assets");
  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  const detail = info.maps.filter((m) => m.name !== "world_overview").sort((a, b) => b.bytes - a.bytes)[0] || info.maps[0];
  const header = await new pmtiles.PMTiles(`${location.origin}/maps/${detail.file}`).getHeader();
  map = new maplibregl.Map({ container: "map", style: await buildStyle(info), attributionControl: { compact: true },
    bounds: detail.name === "planet" ? undefined : [[header.minLon, header.minLat], [header.maxLon, header.maxLat]],
    center: [0, 20], zoom: 1.5, maxZoom: 19 });
  map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  map.on("click", (e) => {
    const hit = map.queryRenderedFeatures(e.point, { layers: ["poi"] })[0];
    if (hit) { new maplibregl.Popup().setLngLat(hit.geometry.coordinates).setDOMContent(placeCard(hit.properties)).addTo(map); return; }
    setPosition(e.lngLat.lat, e.lngLat.lng, false);
  });
  map.on("mouseenter", "poi", () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", "poi", () => { map.getCanvas().style.cursor = ""; });
  map.on("moveend", loadPlaces);
  map.on("load", () => { loadPlaces(); if (saved) setPosition(saved.lat, saved.lon, false); });
  window.genesisMap = map;
  $("#night").addEventListener("click", async () => map.setStyle(await buildStyle(info)));
}

$("#where").addEventListener("submit", (e) => {
  e.preventDefault();
  const p = parsePosition($("#pos").value);
  if (!p) { hint("Enter a position as latitude, longitude (for example 43.7384, 7.4246) or a grid locator such as JN33."); return; }
  hint("");
  setPosition(p.lat, p.lon);
});

// ---------------------------------------------------------------- the other views
const loaders = {
  async library() {
    const [v, services] = await Promise.all([api("vault"), api("services")]);
    const total = Object.values(v.packs).reduce((n, p) => n + p.items, 0);
    $("#library-lede").textContent = total
      ? `${total} items in the vault. Catalog refreshed ${v.catalog_refreshed || "never"}.`
      : "The vault is empty. Run genesis fetch --tier seed to fill it.";
    const open = $("#library-open");
    open.replaceChildren();
    for (const id of ["kiwix", "files", "calibre", "kolibri", "jellyfin"]) {
      const s = services.find((x) => x.id === id);
      if (s?.up) open.append(el("a", { href: serviceUrl(s), textContent: "Open " + s.name.toLowerCase() }));
    }
    const packs = $("#library-packs");
    packs.replaceChildren();
    for (const [name, p] of Object.entries(v.packs).sort()) {
      const list = el("ul", { className: "plain" }, p.list.map((i) => el("li", { textContent: `${i.title} (${i.version || "installed"})${i.outdated ? ", update available" : ""}` })));
      packs.append(el("details", { className: "pack" }, el("summary", {}, name, el("span", { textContent: `${p.items} items${p.outdated ? `, ${p.outdated} with updates` : ""}` })), list));
    }
    $("#library-docs").replaceChildren(...(v.documents.length ? v.documents.map((d) => el("li", {}, el("a", { href: "/" + d, textContent: d.replace(/^documents\//, "") }))) : [el("li", { textContent: "None yet." })]));
    $("#library-apps").replaceChildren(...(v.apps.length ? v.apps.map((a) => el("li", {}, el("a", { href: "/apps/" + encodeURIComponent(a), textContent: a }))) : [el("li", { textContent: "None yet. Run genesis fetch --pack apps." })]));
  },

  async scripture() {
    const all = await api("scripture");
    const langs = new Set(all.map((t) => t.language));
    $("#scripture-lede").textContent = all.length
      ? `${all.length} translations in ${langs.size} languages, from the Hebrew, Greek and Latin sources to today.`
      : "No scripture installed yet. Run genesis fetch --pack scripture.";
    const render = (needle) => {
      const groups = new Map();
      for (const t of all) {
        if (needle && !`${t.language} ${t.native} ${t.label} ${t.title}`.toLowerCase().includes(needle)) continue;
        if (!groups.has(t.language)) groups.set(t.language, []);
        groups.get(t.language).push(t);
      }
      $("#scripture-list").replaceChildren(...[...groups].map(([language, list]) => el("div", { className: "lang" },
        el("h3", {}, language, list[0].native && list[0].native !== language ? el("small", { textContent: list[0].native, dir: "auto" }) : null),
        el("ul", {}, list.map((t) => el("li", {}, el("a", { href: t.href, textContent: (t.label || t.title || t.id).replace(/^[^:]+:\s*/, "") }),
          el("small", { textContent: [t.ot && "Old Testament", t.nt && "New Testament", t.dc && "Deuterocanon"].filter(Boolean).join(", ") })))))));
    };
    render("");
    $("#scripture-filter").oninput = (e) => render(e.target.value.trim().toLowerCase());
  },

  async radio() {
    const [ref, services] = await Promise.all([fetch("reference.json").then((r) => r.json()), api("services")]);
    $("#radio-note").textContent = ref.note;
    const open = $("#radio-open");
    open.replaceChildren();
    for (const id of ["openwebrx", "meshtastic", "adsb", "ais", "chat"]) {
      const s = services.find((x) => x.id === id);
      if (s?.up) open.append(el("a", { href: serviceUrl(s), textContent: "Open " + s.name.toLowerCase() }));
    }
    $("#radio-tables").replaceChildren(...ref.tables.map((t) => el("div", {},
      el("table", {}, el("caption", { textContent: t.title }),
        el("thead", {}, el("tr", {}, t.columns.map((c) => el("th", { scope: "col", textContent: c })))),
        el("tbody", {}, t.rows.map((r) => el("tr", {}, r.map((c) => el("td", { textContent: c })))))),
      t.note ? el("p", { className: "hint", textContent: t.note }) : null)));
    $("#radio-alpha").replaceChildren(...Object.keys(ref.morse).map((ch) => el("div", {},
      el("b", { textContent: ch }), el("span", { textContent: ref.phonetic[ch] || "" }), el("i", { textContent: ref.morse[ch].replaceAll("-", "–").replaceAll(".", "·") }))));
    $("#radio-signals tbody").replaceChildren(...ref.signals.map(([a, b]) => el("tr", {}, el("td", { className: "need", textContent: a }), el("td", { textContent: b }))));
  },

  async services() {
    const list = await api("services");
    $("#services-table tbody").replaceChildren(...list.map((s) => el("tr", {},
      el("td", { className: "need" }, s.up ? el("a", { href: serviceUrl(s), textContent: s.name }) : s.name),
      el("td", { textContent: s.about }), el("td", {}, el("code", { textContent: s.profile })),
      el("td", { className: s.up ? "state-up" : "state-down", textContent: s.up ? "Running" : "Not started" }))));
  },

  async system() {
    const [s, p] = await Promise.all([api("system"), api("poi/stats")]);
    const up = s.uptime ? `${Math.floor(s.uptime / 86400)} d ${Math.floor((s.uptime % 86400) / 3600)} h` : "?";
    const facts = [["Genesis", s.genesis], ["Up for", up], ["Load", `${s.load ?? "?"} on ${s.cpus} CPUs`],
      ["Memory free", `${bytes(s.ram_free)} of ${bytes(s.ram_total)}`], ["Vault drive free", `${bytes(s.disk_free)} of ${bytes(s.disk_total)}`]];
    if (s.temp_c != null) facts.push(["Temperature", `${s.temp_c} °C`]);
    if (s.battery != null) facts.push(["Battery", `${s.battery}% (${s.battery_status || "unknown"})`]);
    $("#system-list").replaceChildren(...facts.flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })]));
    const rows = p.error ? [["Installed", "None yet"]] : [["Places", p.total.toLocaleString()],
      ...Object.entries(p.categories).map(([c, v]) => [categories[c]?.label || c, v.total.toLocaleString()])];
    $("#poi-stats").replaceChildren(...rows.flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })]));
  },
};

function show() {
  const view = (location.hash || "#map").slice(1);
  const name = document.getElementById("view-" + view) ? view : "map";
  for (const s of document.querySelectorAll(".view")) s.hidden = s.id !== "view-" + name;
  for (const a of document.querySelectorAll(".rail a[data-view]")) a.dataset.view === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  loaders[name]?.().catch((e) => console.error(e));
  if (name === "map") map?.resize();
}

const nightButton = $("#night");
function setNight(on) {
  document.documentElement.classList.toggle("night", on);
  nightButton.setAttribute("aria-pressed", String(on));
  store.set("genesis.night", on ? "1" : "");
}
setNight(store.get("genesis.night") === "1");
nightButton.addEventListener("click", () => setNight(!document.documentElement.classList.contains("night")));
addEventListener("hashchange", show);
initMap().catch((e) => { console.error(e); hint("The map could not start: " + e.message); }).finally(show);
