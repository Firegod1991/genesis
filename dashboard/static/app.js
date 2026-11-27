// Genesis dashboard. No build step, no network beyond this box.
import * as maplibregl from "./vendor/maplibre-gl.mjs";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const k of kids.flat()) if (k != null) node.append(k);
  return node;
};
const api = (path) => fetch("api/" + path).then((r) => r.json());
// One part of the box failing must not blank a page that shows many: fall back to nothing for that part.
const count = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const tryApi = (path, none) => api(path).then((v) => (v && !v.error ? v : none), () => none);
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
const icon = (name) => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "ic");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `icons/${name}.svg#g`);
  svg.append(use);
  return svg;
};
const post = (path, data, signal) => fetch("api/" + path, { method: "POST", signal,
  headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

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
const nothing = () => ({ type: "FeatureCollection", features: [] });
let routeData = nothing();
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
  style.sources.route = { type: "geojson", data: routeData };
  style.layers.push({ id: "route-casing", type: "line", source: "route", layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": night ? "#000000" : "#ffffff", "line-width": 9 } });
  style.layers.push({ id: "route", type: "line", source: "route", layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": night ? "#ff5a4d" : "#6a2880", "line-width": 5 } });
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

function placeCard(p, at) {
  const tags = typeof p.tags === "string" ? JSON.parse(p.tags) : (p.tags || {});
  const card = el("div", {}, el("strong", { textContent: p.name || p.sub.replaceAll("_", " ") }),
    el("div", { textContent: `${categories[p.cat]?.label || p.cat}: ${p.sub.replaceAll("_", " ")}` }));
  for (const [k, v] of Object.entries(tags).slice(0, 6)) card.append(el("div", { textContent: `${k.replaceAll("_", " ")}: ${v}` }));
  if (position && at) {
    const place = { ...p, lon: at[0], lat: at[1] };
    card.append(el("div", { className: "way" }, wayButton(place, "walk"), wayButton(place, "drive")));
  }
  return card;
}

// ---------------------------------------------------------------- a way there, along the roads of the map
const metresText = (m) => m < 1000 ? `${Math.max(10, Math.round(m / 10) * 10)} m` : `${(m / 1000).toFixed(m < 10000 ? 1 : 0)} km`;
const timeText = (s) => s < 3600 ? `${Math.max(1, Math.round(s / 60))} min` : `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
const POINTS = ["north", "north east", "east", "south east", "south", "south west", "west", "north west"];
function heading([lon1, lat1], [lon2, lat2]) {
  const r = Math.PI / 180, dl = (lon2 - lon1) * r;
  const y = Math.sin(dl) * Math.cos(lat2 * r);
  const x = Math.cos(lat1 * r) * Math.sin(lat2 * r) - Math.sin(lat1 * r) * Math.cos(lat2 * r) * Math.cos(dl);
  return (Math.atan2(y, x) / r + 360) % 360;
}
function wayButton(p, mode) {
  const b = el("button", { type: "button", className: "quiet", textContent: mode === "walk" ? "Walk" : "Drive" });
  b.addEventListener("click", (e) => { e.stopPropagation(); findWay(p, mode); });
  return b;
}
function clearWay() {
  routeData = nothing();
  map?.getSource("route")?.setData(routeData);
  $("#route").hidden = true;
}
async function findWay(p, mode) {
  if (!position) return;
  const box = $("#route"), name = p.name || (p.sub || "the place").replaceAll("_", " ");
  const clear = el("button", { type: "button", className: "quiet", textContent: "Clear the way" });
  clear.addEventListener("click", clearWay);
  box.hidden = false;
  box.replaceChildren(el("p", { className: "hint", textContent: `Finding the way ${mode === "walk" ? "on foot" : "by car"} to ${name} along the roads and paths of the map.` }));
  const r = await tryApi(`route?from=${position.lat},${position.lon}&to=${p.lat},${p.lon}&mode=${mode}`, { found: false, why: "The box did not answer." });
  if (!r.found) {
    routeData = nothing();
    map?.getSource("route")?.setData(routeData);
    box.replaceChildren(el("p", { className: "hint", textContent: r.why }), clear);
    return;
  }
  routeData = { type: "FeatureCollection", features: [{ type: "Feature", geometry: { type: "LineString", coordinates: r.line }, properties: {} }] };
  if (map) {
    map.getSource("route")?.setData(routeData);
    const lons = r.line.map((c) => c[0]), lats = r.line.map((c) => c[1]);
    map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 48, duration: calm ? 0 : 800 });
  }
  const notes = [];
  if (r.off_road[0] > 30) {
    const deg = heading(r.line[0], r.line[1]);
    notes.push(`The nearest road or path is ${metresText(r.off_road[0])} away, ${POINTS[Math.round(deg / 45) % 8]} of you.`);
  }
  if (r.off_road[1] > 30) {
    const deg = heading(r.line[r.line.length - 2], r.line[r.line.length - 1]);
    notes.push(`The last ${metresText(r.off_road[1])} leave the road: from where it ends, go ${POINTS[Math.round(deg / 45) % 8]}, bearing ${String(Math.round(deg) % 360).padStart(3, "0")}°.`);
  }
  box.replaceChildren(
    el("h2", {}, icon("route"), `${mode === "walk" ? "On foot" : "By car"} to ${name}`),
    el("p", { className: "route-sum" }, el("strong", { textContent: metresText(r.metres) }), `, about ${timeText(r.seconds)}. ${metresText(r.straight)} in a straight line.`),
    ...notes.map((t) => el("p", { textContent: t })),
    el("ol", {}, r.steps.map((st) => el("li", {}, st.text, " ", el("span", { className: "kind", textContent: metresText(st.metres) })))),
    el("p", { className: "hint", textContent: "Worked out from the roads and paths on the map, which knows nothing of closures, damage, gates or flooding"
      + (mode === "drive" ? ", and not every one way street" : "") + ". Look before you go." }),
    clear);
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
    body.append(el("tr", {}, el("td", { colSpan: 5, className: "empty",
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
        el("td", { className: "num", textContent: `${p.compass} ${String(p.bearing).padStart(3, "0")}°` }),
        el("td", { className: "way" }, wayButton(p, "walk"), wayButton(p, "drive")));
      row.dataset.lat = p.lat;
      row.tabIndex = 0;
      const go = () => { map?.[calm ? "jumpTo" : "flyTo"]({ center: [p.lon, p.lat], zoom: 15 });
        if (map) new maplibregl.Popup().setLngLat([p.lon, p.lat]).setDOMContent(placeCard(p, [p.lon, p.lat])).addTo(map); };
      row.addEventListener("click", go);
      row.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      body.append(row);
    });
  }
  if (!body.children.length) body.append(el("tr", {}, el("td", { colSpan: 5, className: "empty", textContent: "Nothing mapped within 2000 km of this position." })));
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
    if (hit) { new maplibregl.Popup().setLngLat(hit.geometry.coordinates).setDOMContent(placeCard(hit.properties, hit.geometry.coordinates)).addTo(map); return; }
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

// ---------------------------------------------------------------- shelves, cards and the reader
let shelfData, readerBase = null;
const shelfInfo = () => shelfData || (shelfData = fetch("shelves.json").then((r) => r.json()));
const plain = (title) => title.replace(/\s*\(([^()]*)\)\s*$/, "");          // the title without its publisher
const maker = (title) => (title.match(/\(([^()]*)\)\s*$/) || [])[1] || "";   // the publisher, for the cover
const read = (url, title) => { location.hash = "read=" + encodeURIComponent(url) + "|" + encodeURIComponent(title || ""); };

async function reader() {
  if (readerBase === null) {
    const kiwix = (await api("services")).find((s) => s.id === "kiwix");
    readerBase = kiwix ? serviceUrl(kiwix).replace(/\/$/, "") : "";
  }
  return readerBase;
}

function cover(doc) {
  if (doc.cover) return el("img", { className: "cover", src: doc.cover, alt: "", loading: "lazy" });
  return el("span", { className: "cover drawn" }, el("b", { textContent: plain(doc.title) }), el("i", { textContent: maker(doc.title) }));
}

function manualCard(doc) {
  const a = el("a", { className: "card", href: doc.href },
    cover(doc),
    el("span", { className: "card-text" },
      el("strong", { textContent: plain(doc.title) }),
      maker(doc.title) ? el("small", { textContent: maker(doc.title) }) : null,
      doc.note ? el("span", { textContent: doc.note }) : null));
  a.addEventListener("click", (e) => { e.preventDefault(); read(doc.href, plain(doc.title)); });
  return a;
}

function shelf(name, about, iconName, docs, danger) {
  return el("section", { className: "shelf" + (danger ? " danger" : "") },
    el("h2", {}, icon(iconName), name),
    about ? el("p", { className: "hint", textContent: about }) : null,
    el("div", { className: "cards" }, docs.map(manualCard)));
}

function hitRow(h, base) {
  const kind = { manual: ["manual", "Manual"], book: ["book", "Book"], page: ["search", h.book || "Page"] }[h.kind];
  const url = h.href || (h.path && base ? base + h.path : null);
  const row = el(url ? "a" : "div", { className: "hit" }, icon(kind[0]),
    el("span", {}, el("strong", { textContent: h.kind === "manual" ? plain(h.title) : h.title }),
      el("small", { textContent: kind[1] }), h.text ? el("span", { textContent: h.text }) : null));
  if (url) { row.href = url; row.addEventListener("click", (e) => { e.preventDefault(); read(url, h.title); }); }
  return row;
}

async function findIt(q) {
  const box = $("#found");
  box.hidden = false;
  box.replaceChildren(el("p", { className: "hint", textContent: "Looking through the manuals and the books…" }));
  const [res, base] = await Promise.all([api("find?q=" + encodeURIComponent(q)), reader()]);
  const askIt = el("button", { type: "button", className: "act", textContent: "Ask the assistant about this" });
  askIt.addEventListener("click", () => { store.set("genesis.ask", q); location.hash = "ask"; });
  box.replaceChildren(...[
    el("h2", { textContent: res.hits.length ? `Found for “${q}”` : `Nothing is called “${q}”` }),
    ...res.hits.map((h) => hitRow(h, base)),
    res.hits.some((h) => h.kind === "page") || !base ? null
      : el("p", { className: "hint", textContent: "No page inside the books matched. Try one or two plain words." }),
    base ? null : el("p", { className: "hint", textContent: "The book reader is not running, so only titles were searched. Start Genesis with the Start Genesis icon on the drive to search inside the books." }),
    el("div", { className: "actions" }, askIt)].filter(Boolean));
}

// ---------------------------------------------------------------- the assistant
const chat = { messages: [], stop: null };
const unthink = (text) => text.replace(/<think>[\s\S]*?(<\/think>|$)/g, "").trimStart();

function bubble(role, text) {
  const node = el("div", { className: "say " + role }, el("b", { textContent: role === "user" ? "You" : "Genesis" }), el("div", { className: "words", textContent: text }));
  $("#ask-log").append(node);
  node.scrollIntoView({ block: "end" });
  return node;
}

async function ask(text) {
  if (chat.stop) return;
  const model = $("#ask-model").value, go = $("#ask-go");
  if (!model) { bubble("genesis", "No model is running on this box. Start Genesis with the Start Genesis icon on the drive, or run: genesis start"); return; }
  chat.messages.push({ role: "user", content: text });
  bubble("user", text);
  const mine = bubble("genesis", "Reading…"), words = $(".words", mine);
  const control = new AbortController();
  chat.stop = () => control.abort();
  go.textContent = "Stop";
  let answer = "", excerpts = [], waiting = "Reading the library", began = Date.now();
  // A model loads from a hard disk before its first word: say so, and count, so the wait is not taken for a fault.
  const tick = setInterval(() => { if (!unthink(answer)) words.textContent = `${waiting}… ${Math.round((Date.now() - began) / 1000)} s`; }, 1000);
  try {
    if ($("#ask-library").checked) {
      const [res, base] = await Promise.all([api("find?q=" + encodeURIComponent(text)), reader()]);
      excerpts = res.hits.filter((h) => h.text).slice(0, 6);
      if (excerpts.length) mine.append(el("div", { className: "sources" }, el("small", { textContent: "From the library" }), excerpts.map((h) => hitRow(h, base))));
    }
    waiting = "Waking the model. Its first answer can take a minute or two";
    began = Date.now();
    const res = await post("ai/chat", { model, messages: chat.messages, excerpts }, control.signal);
    if (!res.ok) throw new Error((await res.json()).error || "The assistant could not answer.");
    const stream = res.body.getReader(), decode = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await stream.read();
      if (done) break;
      buffer += decode.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const part = JSON.parse(line);
        if (part.error) throw new Error(part.error);
        if (part.message?.thinking) waiting = "Thinking it through";
        answer += part.message?.content || "";
        if (unthink(answer)) words.textContent = unthink(answer);
      }
      mine.scrollIntoView({ block: "end" });
    }
  } catch (e) {
    if (e.name !== "AbortError") words.textContent = (unthink(answer) ? unthink(answer) + "\n\n" : "") + e.message;
  }
  clearInterval(tick);
  chat.stop = null;
  go.textContent = "Ask";
  answer = unthink(answer);
  if (!answer) { chat.messages.pop(); return; }
  chat.messages.push({ role: "assistant", content: answer });
  const keep = el("button", { type: "button", className: "quiet", textContent: "Keep as a note" });
  keep.addEventListener("click", async () => {
    const name = text.replace(/[^\w ]+/g, " ").trim().slice(0, 50).trim() || "note";
    const saved = await post(`notes/${encodeURIComponent(name + ".md")}`, { text: `# ${text}\n\n${answer}\n` });
    keep.textContent = saved.ok ? "Kept in Notes" : "Could not keep it";
    keep.disabled = true;
  });
  mine.append(keep);
}

$("#ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (chat.stop) { chat.stop(); return; }
  const text = $("#ask-q").value.trim();
  if (!text) return;
  $("#ask-q").value = "";
  ask(text);
});
$("#ask-q").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); } });
$("#ask-clear").addEventListener("click", () => { chat.stop?.(); chat.messages = []; $("#ask-log").replaceChildren(); });
$("#need").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#need-q").value.trim(); if (q) findIt(q); });

// ---------------------------------------------------------------- notes
let noteOpen = null;
async function listNotes() {
  const all = await api("notes");
  $("#note-names").replaceChildren(...(all.length ? all.map((n) => {
    const a = el("a", { href: "#notes", textContent: n.name.replace(/\.(md|txt)$/, "") });
    a.addEventListener("click", (e) => { e.preventDefault(); openNote(n.name); });
    return el("li", {}, a);
  }) : [el("li", { className: "hint", textContent: "No notes yet." })]));
}
async function openNote(name) {
  const form = $("#note-form");
  form.hidden = false;
  noteOpen = name;
  $("#note-name").value = (name || "").replace(/\.(md|txt)$/, "");
  $("#note-text").value = name ? (await api("notes/" + encodeURIComponent(name))).text || "" : "";
  $("#note-state").textContent = "";
  $(name ? "#note-text" : "#note-name").focus();
}
$("#note-new").addEventListener("click", () => openNote(null));
$("#note-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const typed = $("#note-name").value.replace(/[^\w .()-]+/g, " ").trim().slice(0, 70);
  if (!typed) { $("#note-state").textContent = "Give the note a name."; return; }
  const name = noteOpen && noteOpen.replace(/\.(md|txt)$/, "") === typed ? noteOpen : typed + ".md";
  const res = await post("notes/" + encodeURIComponent(name), { text: $("#note-text").value });
  $("#note-state").textContent = res.ok ? "Saved on the drive." : "Could not save: " + ((await res.json()).error || res.status);
  if (res.ok) { noteOpen = name; listNotes(); }
});
$("#note-ask").addEventListener("click", async () => {
  const model = (await api("ai/models")).models[0]?.name, state = $("#note-state"), text = $("#note-text").value;
  if (!model) { state.textContent = "The assistant is not running."; return; }
  if (!text.trim()) return;
  state.textContent = "The assistant is reading the note…";
  const res = await post("ai/chat", { model, messages: [{ role: "user",
    content: "Tidy this note. Fix the spelling and make it clear. Keep every fact and add none. Answer with the note only.\n\n" + text }] });
  if (!res.ok) { state.textContent = (await res.json()).error || "The assistant could not answer."; return; }
  let out = "";
  for (const line of (await res.text()).split("\n")) if (line.trim()) out += JSON.parse(line).message?.content || "";
  if (unthink(out).trim()) { $("#note-text").value = unthink(out).trim() + "\n"; state.textContent = "Tidied. Save to keep it, or open the note again to go back."; }
});

// ---------------------------------------------------------------- phones
// On the box itself: the code a phone's camera opens Genesis with. On a phone: how to keep it on the home screen.
const onThisBox = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(location.hostname);
const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
function phoneCard(ph) {
  const card = $("#phone");
  const picture = (text) => { const svg = new DOMParser().parseFromString(text, "image/svg+xml").documentElement; svg.setAttribute("class", "qr"); return svg; };
  if (onThisBox && ph.links.length && ph.qr && ph.wifi) {
    card.replaceChildren(picture(ph.wifi.qr), el("div", {},
      el("h2", {}, icon("wifi"), `1. Join the ${ph.wifi.name} WiFi`),
      el("p", { textContent: "This box is a WiFi network of its own. Point the phone's camera at this code to join it, or pick it in the phone's WiFi settings." }),
      el("p", {}, "Name ", el("code", { textContent: ph.wifi.name }), ", password ", el("code", { textContent: ph.wifi.password }))),
    picture(ph.qr), el("div", {},
      el("h2", {}, icon("phone"), "2. Open Genesis"),
      el("p", { textContent: "Most phones open Genesis by themselves once they join. If one does not, point its camera at this code." }),
      el("p", {}, "Or type ", el("code", { textContent: ph.links[0] }))));
    card.hidden = false;
  } else if (onThisBox && ph.links.length && ph.qr) {
    const svg = picture(ph.qr);
    card.replaceChildren(svg, el("div", {},
      el("h2", {}, icon("phone"), "On your phone"),
      el("p", { textContent: "Join the same WiFi as this computer, or its hotspot, then point the phone's camera at this code. Every page works there, the assistant too." }),
      el("p", {}, "Or type ", el("code", { textContent: ph.links[0] }), ph.links.length > 1 ? ` (or ${ph.links.slice(1, 3).join(", ")})` : ""),
      el("p", { className: "hint", textContent: "On Windows, say yes when it asks to let Python through the firewall, or phones cannot reach it." })));
    card.hidden = false;
  } else if (!onThisBox && !standalone) {
    const iphone = /iPhone|iPad|iPod/.test(navigator.userAgent);
    card.replaceChildren(el("div", {},
      el("h2", {}, icon("phone"), "Keep Genesis on this phone"),
      el("p", { textContent: iphone
        ? "In Safari tap Share, then Add to Home Screen. Genesis opens from its own icon whenever this phone is on the box's network."
        : "Open the browser menu and choose Add to Home screen or Install app. Genesis opens from its own icon whenever this phone is on the box's network." })));
    card.hidden = false;
  } else {
    card.hidden = true;
  }
}

// ---------------------------------------------------------------- get more: tick shelves, watch them come down
let getTimer = null, getDrawn = false, getFree = 0;
const sizeText = (c) => c.items
  ? `${bytes(c.to_get)}, ${count(c.items, "item")}${c.unsized ? ` (${c.unsized} of no published size)` : ""}`
  : "all on the drive";
function getChoices() {
  const out = [];
  for (const box of document.querySelectorAll("#get-sets input:checked")) out.push({ set: box.value });
  for (const pick of document.querySelectorAll("#get-shelves select")) if (pick.value) out.push({ pack: pick.name, tier: pick.value });
  return out;
}
let costing = 0;
async function getCost() {
  const choices = getChoices(), mine = ++costing, total = $("#get-total"), go = $("#get-go");
  if (!choices.length) { total.textContent = "Nothing ticked yet."; go.disabled = true; return; }
  total.textContent = "Adding it up";
  const c = await tryApi("get/cost?choices=" + encodeURIComponent(JSON.stringify(choices)), null);
  if (mine !== costing) return;
  if (!c) { total.textContent = "The box did not answer."; go.disabled = true; return; }
  const fits = c.to_get <= getFree * 0.95;
  total.textContent = c.items ? `To download: ${sizeText(c)}. ${fits ? `${bytes(getFree)} free on this drive.` : `Only ${bytes(getFree)} free: tick less, or use a bigger drive.`}`
    : "All of that is on the drive already.";
  go.disabled = !c.items || !fits || go.dataset.locked === "1";
}
function getProgress(st, can) {
  const now = $("#get-now");
  if (!st.job) { now.hidden = true; return false; }
  const pct = st.bytes ? Math.min(100, (st.so_far / st.bytes) * 100) : (st.items ? (st.done / st.items) * 100 : 0);
  const failed = st.result && st.result.code !== 0;
  const head = st.running ? "Downloading" : st.result ? (failed ? "Done, with some that did not come down" : "Done") : "Stopped";
  const kids = [el("h2", {}, icon("get"), head), el("progress", { max: 100, value: st.running || st.result ? pct : 0 }),
    el("p", { textContent: `${st.done} of ${count(st.items, "item")}, ${bytes(st.so_far)} of ${bytes(st.bytes)}` })];
  if (st.running && st.now.length) kids.push(el("p", { className: "hint", textContent: "Coming down now: " + st.now.join(", ") }));
  if (st.result) kids.push(el("p", { textContent: "New books open in the library now. A new model shows on the Ask page; if one does not, stop Genesis and start it again." }));
  if (failed) kids.push(el("p", { className: "hint", textContent: "What did not come down stays ticked: download again to try those once more." }));
  if (!st.running && !st.result) kids.push(el("p", { className: "hint", textContent: "Stopped part way. What came down stays, and a download of the same picks up where this one stopped." }));
  kids.push(el("details", {}, el("summary", { textContent: "What it is doing" }), el("pre", { textContent: st.log.join("\n") })));
  if (st.running && can) {
    const stop = el("button", { type: "button", className: "act", textContent: "Stop" });
    stop.addEventListener("click", async () => { stop.disabled = true; await post("get/stop", {}); loaders.get(); });
    kids.push(el("div", { className: "actions" }, stop));
  }
  now.replaceChildren(...kids);
  now.hidden = false;
  return st.running;
}
function getForm(o) {
  const sets = [];
  if (o.starter.of) sets.push(["starter", "The starter", `Every manual and plant guide, ten books, a map of the world and the Bible. ${sizeText(o.starter)}.`, o.starter]);
  if (o.assistant.of) sets.push(["assistant", "The assistant", `${o.assistant.name} and the Ollama that runs it: answers questions with no internet. ${sizeText(o.assistant)}.`, o.assistant]);
  $("#get-sets").replaceChildren(...sets.map(([value, name, about, c]) => {
    const box = el("input", { type: "checkbox", name: "set", value, disabled: !c.items });
    box.dataset.none = c.items ? "" : "1";
    return el("label", { className: "get-row" }, box, el("span", {}, el("strong", { textContent: name }), el("span", { textContent: about })));
  }));
  $("#get-shelves").replaceChildren(...o.shelves.map((sh) => {
    const pick = el("select", { name: sh.pack, ariaLabel: sh.title },
      el("option", { value: "", textContent: "Nothing more" }),
      ...["seed", "ark", "eden"].map((t) => el("option", { value: t, disabled: !sh.tiers[t].items,
        textContent: `${t[0].toUpperCase() + t.slice(1)}: ${sizeText(sh.tiers[t])}` })));
    return el("div", { className: "get-row" }, el("span", {}, el("strong", { textContent: sh.title }), el("span", { textContent: sh.about.replaceAll("`", "") })), pick);
  }));
}
$("#get-form").addEventListener("change", getCost);
$("#get-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const go = $("#get-go"), why = $("#get-why");
  go.disabled = true;
  const r = await post("get/start", { choices: getChoices() }).then((x) => x.json(), () => ({ error: "The box did not answer." }));
  if (r.error) { why.hidden = false; why.textContent = r.error; getCost(); return; }
  why.hidden = true;
  getDrawn = false;
  loaders.get();
});

// ---------------------------------------------------------------- the views
const loaders = {
  async home() {
    const [info, shelves, books, bible, ai, soft, maps, sys, safe] = await Promise.all([shelfInfo(), tryApi("shelves", {}),
      tryApi("books", { books: [] }), tryApi("scripture", []), tryApi("ai/models", { models: [] }),
      tryApi("software", { tools: [], apps: [], isos: [] }), tryApi("maps", { maps: [] }), tryApi("system", {}), tryApi("safe", {})]);
    const have = new Set(Object.values(shelves).flat().map((d) => d.href));
    $("#first").replaceChildren(...info.first.filter((f) => f.view || have.has(f.file)).map((f) => {
      const a = el("a", { className: "tile strong", href: f.view ? "#" + f.view : f.file }, icon(f.icon), el("strong", { textContent: f.name }));
      if (f.file) a.addEventListener("click", (e) => { e.preventDefault(); read(f.file, f.name); });
      return a;
    }));
    const manuals = Object.entries(shelves).filter(([k]) => k !== "plants").reduce((n, [, v]) => n + v.length, 0);
    const langs = new Set(bible.map((t) => t.language)).size;
    const planet = maps.maps.some((m) => m.name === "planet");
    const tiles = [
      ["ask", "assistant", "Ask", ai.models.length ? `${count(ai.models.length, "model")} that run on this box` : "The assistant is not running"],
      ["map", "map", "Map", planet ? "The whole planet, down to the street" : maps.maps.length ? `${count(maps.maps.length, "map")} on the drive` : "No map on the drive yet"],
      ["manuals", "manual", "Manuals", `${count(manuals, "manual")}, with their pictures`],
      ["plants", "leaf", "Plants", `${count((shelves.plants || []).length, "guide")}, region by region`],
      ["library", "book", "Library", `${count(books.books.length, "book")}: encyclopedias, medicine, repair, classics`],
      ["scripture", "scripture", "Scripture", bible.length ? `${count(bible.length, "translation")} in ${count(langs, "language")}` : "None on the drive yet"],
      ["radio", "radio", "Radio", "Frequencies, Morse and calling for help"],
      ["notes", "note", "Notes", "Your own pages, kept on the drive"],
      ["safe", "lock", "Safe", safe.open ? "Open now. Lock it when you are done" : safe.exists ? "Your papers, locked behind a passphrase" : "A locked place for your own papers"],
      ["programs", "tools", "Programs", `${soft.tools.length + soft.apps.length + soft.isos.length} programs, apps and systems for other machines`],
      ["get", "get", "Get more", "Choose more for this drive and watch it come down"],
      ["system", "gear", "System", `${bytes(sys.disk_free)} free of ${bytes(sys.disk_total)}`],
    ];
    $("#tiles").replaceChildren(...tiles.map(([view, pic, name, line]) =>
      el("a", { className: "tile", href: "#" + view }, icon(pic), el("strong", { textContent: name }), el("span", { textContent: line }))));
    phoneCard(await tryApi("phone", { links: [] }));
    $("#home-foot").textContent = "Everything here is on the drive. Nothing on this screen needs the internet.";
    if ($("#found").hidden && matchMedia("(hover: hover)").matches) $("#need-q").focus({ preventScroll: true });
  },

  async ask() {
    const ai = await api("ai/models"), pick = $("#ask-model"), last = store.get("genesis.model");
    pick.replaceChildren(...ai.models.map((m) => el("option", { value: m.name, textContent: `${m.name}: ${m.use} (${bytes(m.bytes)})`, selected: m.name === last })));
    pick.onchange = () => store.set("genesis.model", pick.value);
    if (!ai.models.length) $("#ask-lede").textContent = ai.running
      ? "The model server is running and has no models. Fetch them with: genesis fetch --pack ai"
      : "The assistant is not running. Start Genesis with the Start Genesis icon on the drive, or run: genesis start";
    const waiting = store.get("genesis.ask");
    if (waiting) { store.set("genesis.ask", ""); ask(waiting); }
    $("#ask-q").focus();
  },

  async manuals() {
    const [info, shelves] = await Promise.all([shelfInfo(), api("shelves")]);
    const named = new Set(info.shelves.map((s) => s.id).concat("plants"));
    const rest = Object.keys(shelves).filter((k) => !named.has(k)).map((id) => ({ id, name: id.replaceAll("_", " "), icon: "manual", about: "" }));
    const out = info.shelves.concat(rest).filter((s) => shelves[s.id]?.length).map((s) => shelf(s.name, s.about, s.icon, shelves[s.id]));
    $("#manual-shelves").replaceChildren(...(out.length ? out : [el("p", { className: "hint", textContent: "No manuals on the drive yet. Run: genesis fetch --tag fieldcraft" })]));
  },

  async plants() {
    const [info, shelves] = await Promise.all([shelfInfo(), api("shelves")]);
    const byFile = new Map((shelves.plants || []).map((d) => [d.file, d]));
    $("#plant-rules").replaceChildren(el("h2", {}, icon("hazard"), "Before you eat anything"), el("ol", {}, info.plants.rules.map((r) => el("li", { textContent: r }))));
    const used = new Set(), out = [];
    for (const region of info.plants.regions) {
      const docs = region.files.map((f) => byFile.get(f)).filter(Boolean);
      docs.forEach((d) => used.add(d.file));
      if (docs.length) out.push(shelf(region.name, "", region.danger ? "hazard" : "leaf", docs, region.danger));
    }
    const others = [...byFile.values()].filter((d) => !used.has(d.file));
    if (others.length) out.push(shelf("More guides", "", "leaf", others));
    if (shelves.medical?.length) out.push(shelf("Plant medicine", "What each medicinal plant is used for, the evidence, the dose and the danger.", "medical", shelves.medical));
    $("#plant-shelves").replaceChildren(...(out.length ? out : [el("p", { className: "hint", textContent: "No plant guides on the drive yet. Run: genesis fetch --tag plants" })]));
  },

  async library() {
    const [info, data, base] = await Promise.all([shelfInfo(), api("books"), reader()]);
    const total = data.books.reduce((n, b) => n + b.bytes, 0);
    $("#library-lede").textContent = !data.books.length ? "No books on the drive yet. Run: genesis fetch --tier seed"
      : data.reader ? `${data.books.length} books, ${bytes(total)}. Each opens here, with its pictures, and can be searched.`
        : `${data.books.length} books, ${bytes(total)}, are on the drive, and the reader that opens them is not running. Start Genesis with the Start Genesis icon on the drive, or run: genesis start`;
    const render = (needle) => {
      const out = [];
      for (const pack of info.packs) {
        const list = data.books.filter((b) => (info.packs.some((p) => p.id === b.pack) ? b.pack : "other") === pack.id
          && (!needle || `${b.title} ${b.about} ${b.name}`.toLowerCase().includes(needle)));
        if (!list.length) continue;
        const cards = el("div", { className: "books" }, list.map((b) => {
          const url = b.path && base ? base + b.path : null;
          const card = el(url ? "a" : "div", { className: "bookcard" + (url ? "" : " shut") },
            b.icon && base ? el("img", { src: base + b.icon, alt: "", loading: "lazy", width: 48, height: 48 }) : icon("book"),
            el("span", {}, el("strong", { textContent: b.title }), b.about ? el("span", { textContent: b.about }) : null, el("small", { textContent: bytes(b.bytes) })));
          if (url) { card.href = url; card.addEventListener("click", (e) => { e.preventDefault(); read(url, b.title); }); }
          return card;
        }));
        out.push(pack.id === "code" && !needle
          ? el("details", { className: "pack" }, el("summary", {}, pack.name, el("span", { textContent: count(list.length, "book") })), cards)
          : el("section", { className: "shelf" }, el("h2", {}, icon("book"), pack.name, el("small", { textContent: ` ${list.length}` })), cards));
      }
      $("#library-books").replaceChildren(...out);
    };
    render("");
    $("#library-filter").oninput = (e) => render(e.target.value.trim().toLowerCase());
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

  async notes() { await listNotes(); },

  async safe() {
    const s = await tryApi("safe", {});
    const when = s.locked ? new Date(s.locked * 1000).toLocaleString() : "";
    $("#safe-state").textContent = s.open ? "The safe is open on this box right now. Lock it when you are done."
      : s.exists ? `The safe is locked: ${bytes(s.bytes)}, last locked ${when}.`
        : s.program === false ? "No safe yet, and 7-Zip is not unpacked on this drive. Run: genesis fetch --id sevenzip, then genesis ready"
          : "No safe has been made on this drive yet.";
    const steps = s.exists
      ? ["Double click Genesis Safe at the top of the drive. In a terminal that is: genesis safe",
        "Type your passphrase. Your papers open as a folder.",
        "Add, change or remove what you need.",
        "Double click Genesis Safe again and answer yes to lock it."]
      : ["Double click Genesis Safe at the top of the drive. In a terminal that is: genesis safe",
        "A folder opens. Put your papers in it.",
        "Double click Genesis Safe again to lock it. You choose the passphrase then: a few words you will not forget.",
        "Write the passphrase down and keep it somewhere that is not this drive."];
    $("#safe-steps").replaceChildren(...steps.map((t) => el("li", { textContent: t })));
  },

  async programs() {
    const soft = await api("software");
    const rows = (list, pic, empty) => list.length ? list.map((p) => el("a", { className: "hit", href: p.href, download: p.file }, icon(pic),
      el("span", {}, el("strong", { textContent: p.title }), el("small", { textContent: `${p.file}, ${bytes(p.bytes)}` }), p.note ? el("span", { textContent: p.note }) : null)))
      : [el("p", { className: "hint", textContent: empty })];
    $("#programs-apps").replaceChildren(...rows(soft.apps, "phone", "None on the drive yet. Run: genesis fetch --pack apps"));
    $("#programs-tools").replaceChildren(...rows(soft.tools, "tools", "None on the drive yet. Run: genesis fetch --pack tools"));
    $("#programs-isos").replaceChildren(...rows(soft.isos, "disc", "None on the drive yet. Run: genesis fetch --pack isos"));
  },

  async get() {
    clearTimeout(getTimer);
    const g = await tryApi("get", null), why = $("#get-why"), go = $("#get-go");
    if (!g) { why.hidden = false; why.textContent = "The box did not answer."; return; }
    getFree = g.options.free;
    const running = getProgress(g.status, g.can);
    if (!getDrawn || (!running && go.dataset.locked === "1")) { getForm(g.options); getDrawn = true; }
    go.dataset.locked = !g.can || running ? "1" : "";
    for (const field of document.querySelectorAll("#get-form input, #get-form select")) field.disabled = !g.can || running || field.dataset.none === "1";
    why.hidden = g.can;
    why.textContent = g.why;
    if (running) getTimer = setTimeout(() => { if (location.hash === "#get") loaders.get(); }, 2000);
    getCost();
  },

  async system() {
    const [s, p, v, list] = await Promise.all([api("system"), api("poi/stats"), api("vault"), api("services")]);
    const up = s.uptime ? `${Math.floor(s.uptime / 86400)} d ${Math.floor((s.uptime % 86400) / 3600)} h` : "?";
    const facts = [["Genesis", s.genesis], ["Up for", up], ["Load", `${s.load ?? "?"} on ${s.cpus} CPUs`],
      ["Memory free", `${bytes(s.ram_free)} of ${bytes(s.ram_total)}`], ["Vault drive free", `${bytes(s.disk_free)} of ${bytes(s.disk_total)}`],
      ["Catalog refreshed", v.catalog_refreshed || "never"]];
    if (s.temp_c != null) facts.push(["Temperature", `${s.temp_c} °C`]);
    if (s.battery != null) facts.push(["Battery", `${s.battery}% (${s.battery_status || "unknown"})`]);
    $("#system-list").replaceChildren(...facts.flatMap(([k, val]) => [el("dt", { textContent: k }), el("dd", { textContent: val })]));
    const rows = p.error ? [["Installed", "None yet"]] : [["Places", p.total.toLocaleString()],
      ...Object.entries(p.categories).map(([c, val]) => [categories[c]?.label || c, val.total.toLocaleString()])];
    $("#poi-stats").replaceChildren(...rows.flatMap(([k, val]) => [el("dt", { textContent: k }), el("dd", { textContent: val })]));
    $("#library-packs").replaceChildren(...Object.entries(v.packs).sort().map(([name, pack]) => el("details", { className: "pack" },
      el("summary", {}, name, el("span", { textContent: `${pack.items} items${pack.outdated ? `, ${pack.outdated} with updates` : ""}` })),
      el("ul", { className: "plain" }, pack.list.map((i) => el("li", { textContent: `${i.title} (${i.version || "installed"})${i.outdated ? ", update available" : ""}` }))))));
    $("#services-table tbody").replaceChildren(...list.map((svc) => el("tr", {},
      el("td", { className: "need" }, svc.up ? el("a", { href: serviceUrl(svc), textContent: svc.name }) : svc.name),
      el("td", { textContent: svc.about }), el("td", {}, el("code", { textContent: svc.profile })),
      el("td", { className: svc.up ? "state-up" : "state-down", textContent: svc.up ? "Running" : "Not started" }))));
  },

  async read() {
    const [url, title] = location.hash.slice(6).split("|").map(decodeURIComponent);
    // Only this box: its own pages, and its book reader on the next port.
    const base = await reader();
    if (!url || !(/^\/[^/\\]/.test(url) || (base && url.startsWith(base + "/")))) { location.hash = "home"; return; }
    $("#read-title").textContent = title || "";
    $("#read-out").href = url;
    if ($("#read-frame").getAttribute("src") !== url) $("#read-frame").src = url;
  },
};

let before = "home";
$("#read-back").addEventListener("click", (e) => { e.preventDefault(); location.hash = before; });

function show() {
  const view = (location.hash || "#home").slice(1).split("=")[0];
  const name = document.getElementById("view-" + view) ? view : "home";
  if (name !== "read") before = name;
  else if (!$("#read-frame").getAttribute("src")) before = before || "home";
  document.body.classList.toggle("reading", name === "read");
  for (const s of document.querySelectorAll(".view")) s.hidden = s.id !== "view-" + name;
  for (const a of document.querySelectorAll(".rail a[data-view]")) a.dataset.view === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  loaders[name]?.().catch((e) => console.error(e));
  if (name === "map") map?.resize();
  if (name !== "read") $("#read-frame").removeAttribute("src");
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
show();  // the home screen first: the map loads behind it
initMap().catch((e) => { console.error(e); hint("The map could not start: " + e.message); })
  .finally(() => { if (location.hash.startsWith("#map")) map?.resize(); });
