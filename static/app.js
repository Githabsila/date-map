const $ = (id) => document.getElementById(id);
const state = { category: "any", budget: 0, coords: null, markers: [], places: [], center: null, me: null, routeFor: -1 };
const PROVIDER_LABEL = { yandex: "Яндекс", "2gis": "2ГИС", google: "Google", osm: "OSM", demo: "демо" };
const PRICE = { 1: "до 700 ₽", 2: "700–1500 ₽", 3: "1500–3000 ₽", 4: "от 3000 ₽" };

const MATCH = {
  exact: ["ok", "✓ Точно то, что искали"],
  type: ["ok", "✓ Подходит"],
  maybe: ["maybe", "⚠ Уточните перед поездкой"],
};

function load(key) {
  try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* хранилище недоступно */ }
}

// Карта открывается там, где искали в прошлый раз, а не всегда в Москве
const last = load("lastView");
const map = L.map("map", { zoomControl: true }).setView(last ? [last.lat, last.lon] : [55.7558, 37.6173], last ? 12 : 11);
if (load("city")) $("city").value = load("city");
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const layer = L.layerGroup().addTo(map);
const meLayer = L.layerGroup().addTo(map);
const routeLayer = L.layerGroup().addTo(map);
let meWatch = null;

/* ---------- Моё местоположение на карте ---------- */

const LocateControl = L.Control.extend({
  options: { position: "topleft" },
  onAdd() {
    const btn = L.DomUtil.create("button", "locate-btn");
    btn.type = "button";
    btn.title = "Показать, где я";
    btn.setAttribute("aria-label", "Показать, где я");
    btn.innerHTML = "◎";
    L.DomEvent.disableClickPropagation(btn);
    L.DomEvent.on(btn, "click", () => locate({ center: true }));
    return btn;
  },
});
map.addControl(new LocateControl());

function drawMe() {
  meLayer.clearLayers();
  if (!state.me) return;
  const { lat, lon, accuracy } = state.me;
  if (accuracy < 3000) {
    L.circle([lat, lon], { radius: accuracy, color: "#3b82f6", weight: 1, fillOpacity: 0.1, interactive: false }).addTo(meLayer);
  }
  L.marker([lat, lon], {
    icon: L.divIcon({ className: "", html: '<div class="me-dot"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }),
    zIndexOffset: 1000,
  }).bindTooltip("Вы здесь").addTo(meLayer);
}

// Следим за местоположением, пока страница открыта: точка двигается вместе с человеком
function locate({ center = false } = {}) {
  return new Promise((resolve) => {
    if (!navigator.geolocation || !window.isSecureContext) {
      showGeoNote("Браузер не даёт определить местоположение (нужен https).", true);
      return resolve(null);
    }
    if (state.me) {
      if (center) map.setView([state.me.lat, state.me.lon], Math.max(map.getZoom(), 14));
      return resolve(state.me);
    }
    let first = true;
    const onPos = (pos) => {
      const { latitude: lat, longitude: lon, accuracy } = pos.coords;
      state.me = { lat, lon, accuracy };
      drawMe();
      document.querySelector(".locate-btn")?.classList.add("on");
      if (first) {
        first = false;
        if (center) map.setView([lat, lon], Math.max(map.getZoom(), 14));
        resolve(state.me);
      }
    };
    const onErr = (err) => {
      if (meWatch !== null) navigator.geolocation.clearWatch(meWatch);
      meWatch = null;
      if (first) {
        first = false;
        const why = err.code === 1 ? "доступ к местоположению запрещён в настройках браузера" : "не получилось определить местоположение";
        showGeoNote(`Не вышло: ${why}.`, true);
        resolve(null);
      }
    };
    if (meWatch !== null) navigator.geolocation.clearWatch(meWatch);
    meWatch = navigator.geolocation.watchPosition(onPos, onErr, { timeout: 15000, enableHighAccuracy: true, maximumAge: 30000 });
  });
}

// Если человек уже разрешал геолокацию раньше — сразу показываем его на карте, без вопросов
navigator.permissions?.query({ name: "geolocation" }).then((p) => {
  if (p.state === "granted") locate();
}).catch(() => {});

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function chipGroup(id, key, cast = (v) => v) {
  $(id).addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    $(id).querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === btn));
    state[key] = cast(btn.dataset.v);
    $("foodField").hidden = !["any", "food"].includes(state.category);
  });
}
chipGroup("category", "category");
chipGroup("budget", "budget", Number);

$("examples").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  $("text").value = btn.textContent;
  search();
});

function resetNearby() {
  state.coords = null;
  $("nearby").classList.remove("on");
  $("nearby").textContent = "Рядом со мной";
  $("city").disabled = false;
  $("geoNote").hidden = true;
}

$("nearby").addEventListener("click", async () => {
  if (state.coords) return resetNearby();
  $("nearby").textContent = "Определяю…";
  const me = await locate();
  if (!me) {
    resetNearby();
    return;
  }
  const { lat, lon, accuracy } = me;
  state.coords = { lat, lon };
  $("nearby").classList.add("on");
  $("nearby").textContent = "Рядом ✓";
  $("city").disabled = true;
  map.setView([lat, lon], 13);
  let city = "";
  try {
    city = (await (await fetch(`/api/nearest?lat=${lat}&lon=${lon}`)).json()).city || "";
  } catch { /* без названия города тоже работаем */ }
  if (city) $("city").value = city;
  let note = city ? `📍 Ищу рядом с вами: ${esc(city)}` : "📍 Ищу рядом с вами";
  if (accuracy > 20000) {
    note += `<br>Браузер определил место очень примерно (±${Math.round(accuracy / 1000)} км). Если город не твой — нажми «Рядом ✓», чтобы выключить, и выбери город вручную.`;
  }
  showGeoNote(note, accuracy > 20000);
  search();
});

function showGeoNote(html, warn = false) {
  const el = $("geoNote");
  el.hidden = false;
  el.className = warn ? "geo warn" : "geo";
  el.innerHTML = html;
}

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  search();
});

async function loadStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    $("cities").innerHTML = s.cities.map((c) => `<option value="${esc(c)}">`).join("");
    const on = Object.entries(s.providers).filter(([, v]) => v).map(([k]) => PROVIDER_LABEL[k]);
    const st = $("status");
    if (s.demo) {
      st.textContent = "Демо-режим";
      st.classList.add("demo");
      st.title = "Ключей карт нет, показываю выдуманные места. Добавь ключи в .env";
    } else {
      st.textContent = on.join(" · ") || "Нет источников";
    }
  } catch {
    /* статус не критичен */
  }
}

async function search() {
  const btn = $("go");
  btn.disabled = true;
  btn.textContent = "Ищу в картах…";
  const body = {
    text: $("text").value,
    food: $("foodField").hidden ? "" : $("food").value,
    city: $("city").value || "Москва",
    category: state.category,
    max_price_level: state.budget,
    ...(state.coords || {}),
  };
  try {
    const r = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Ошибка поиска");
    if (!state.coords) save("city", body.city);
    save("lastView", { lat: data.center.lat, lon: data.center.lon });
    render(data);
  } catch (err) {
    $("summary").hidden = false;
    $("summary").innerHTML = `<div class="warn">${esc(err.message)}</div>`;
    $("results").innerHTML = "";
  } finally {
    btn.disabled = false;
    btn.textContent = "Найти места";
  }
}

function priceText(p) {
  if (p.avg_check) return `~${p.avg_check.toLocaleString("ru-RU")} ₽`;
  return p.price_level ? PRICE[p.price_level] : "";
}

function linksHtml(p) {
  const links = p.sources.map((s) => (s.url
    ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${PROVIDER_LABEL[s.provider]}${s.rating ? ` ★${s.rating}` : ""}</a>`
    : `<span>${PROVIDER_LABEL[s.provider]}</span>`));
  // Отзывов в OpenStreetMap нет — даём ссылку на поиск этого места в Яндекс Картах
  if (!p.sources.some((s) => ["yandex", "demo"].includes(s.provider))) {
    const url = `https://yandex.ru/maps/?mode=search&text=${encodeURIComponent(p.name)}&ll=${p.lon},${p.lat}&z=17`;
    links.push(`<a href="${esc(url)}" target="_blank" rel="noopener">Отзывы в Яндексе</a>`);
  }
  return links.join("");
}

function render(data) {
  const { plan, places, errors, demo, check, center } = data;
  const sum = $("summary");
  sum.hidden = false;
  let html = `<b>${esc(plan.summary)}</b> · ${esc(center.name || "")}<br>`;
  html += plan.parser === "rules" ? "" : `Запрос поняла нейросеть. `;
  html += `Проверил ${check.checked} ${plural(check.checked, "место", "места", "мест")}, советую ${places.length}`;
  const removed = Object.entries(check.removed);
  if (removed.length) html += `. Отсеял: ${removed.map(([why, n]) => `${esc(why)} — ${n}`).join(", ")}`;
  if (check.widened_km) html += `<div class="warn">Рядом подходящих мест почти нет — расширил поиск до ${check.widened_km} км.</div>`;
  if (demo) html += `<div class="warn">Демо-режим: места выдуманы. Добавь API-ключи карт в файл .env, и появятся настоящие.</div>`;
  for (const [name, msg] of Object.entries(errors)) html += `<div class="warn">${esc(name)}: ${esc(msg)}</div>`;
  sum.innerHTML = html;

  layer.clearLayers();
  routeLayer.clearLayers();
  state.markers = [];
  state.places = places;
  state.center = center;
  state.routeFor = -1;
  const list = $("results");
  list.innerHTML = places.length ? "" : `<li class="empty">Ничего не нашлось. Попробуй описать иначе или убрать фильтр бюджета.</li>`;

  places.forEach((p, i) => {
    const n = i + 1;
    const meta = [
      p.kind && esc(p.kind),
      p.rating && `<span class="rating">★ ${p.rating}</span>${p.reviews ? ` (${p.reviews.toLocaleString("ru-RU")})` : ""}`,
      priceText(p),
      p.distance_km != null && `${p.distance_km} км`,
    ].filter(Boolean).join("</span><span>");

    const li = document.createElement("li");
    li.className = "card";
    const [cls, label] = MATCH[p.match] || MATCH.type;
    li.innerHTML = `
      <div class="top"><span class="num">${n}</span><span class="name">${esc(p.name)}</span></div>
      <div class="meta"><span>${meta}</span></div>
      ${p.address ? `<div class="meta">${esc(p.address)}</div>` : ""}
      <div class="check ${cls}">${label}${p.why ? `: ${esc(p.why)}` : ""}</div>
      ${p.warnings.map((w) => `<div class="check maybe">${esc(w)}</div>`).join("")}
      <div class="links">${linksHtml(p)}</div>
      <div class="routes" hidden></div>`;
    li.addEventListener("click", (e) => {
      if (e.target.closest("a, .route-mode")) return;
      focusPlace(i);
    });
    list.appendChild(li);

    const icon = L.divIcon({ className: "", html: `<div class="pin"><span>${n}</span></div>`, iconSize: [26, 26], iconAnchor: [13, 26], popupAnchor: [0, -24] });
    const marker = L.marker([p.lat, p.lon], { icon }).addTo(layer).bindPopup(`
      <b>${esc(p.name)}</b><br>
      <span style="color:#7b6d70">${[esc(p.kind), p.rating ? `★ ${p.rating}` : "", priceText(p)].filter(Boolean).join(" · ")}</span><br>
      ${esc(p.address)}
      <div class="links">${linksHtml(p)}</div>`);
    marker.on("click", () => { highlight(i); showRoutes(i); });
    state.markers.push(marker);
  });

  if (places.length) {
    map.fitBounds(L.latLngBounds(places.map((p) => [p.lat, p.lon])).pad(0.15), { maxZoom: 15 });
  } else {
    map.setView([data.center.lat, data.center.lon], 12);
  }
}

function highlight(i) {
  document.querySelectorAll(".card").forEach((c, j) => c.classList.toggle("active", i === j));
  document.querySelectorAll(".card")[i]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function focusPlace(i) {
  const m = state.markers[i];
  map.setView(m.getLatLng(), Math.max(map.getZoom(), 15));
  m.openPopup();
  highlight(i);
  showRoutes(i);
}

/* ---------- Маршруты до выбранного места ---------- */

const MODE_ICON = { foot: "🚶", bus: "🚌", car: "🚗", bike: "🚲", scooter: "🛴" };
const MODE_COLOR = { foot: "#16a34a", bus: "#7c3aed", car: "#2563eb", bike: "#ea580c", scooter: "#db2777" };

function fmtTime(s) {
  const m = Math.max(1, Math.round(s / 60));
  return m < 60 ? `${m} мин` : `${Math.floor(m / 60)} ч ${m % 60 ? `${m % 60} мин` : ""}`.trim();
}
function fmtDist(m) {
  return m < 1000 ? `${Math.round(m / 10) * 10} м` : `${(m / 1000).toFixed(m < 10000 ? 1 : 0)} км`;
}

async function showRoutes(i) {
  if (state.routeFor === i) return;
  state.routeFor = i;
  routeLayer.clearLayers();
  document.querySelectorAll(".routes").forEach((r) => { r.hidden = true; r.innerHTML = ""; });
  const box = document.querySelectorAll(".card")[i]?.querySelector(".routes");
  const p = state.places[i];
  if (!box || !p) return;
  box.hidden = false;
  box.innerHTML = `<div class="route-note">Строю маршруты…</div>`;

  let from = state.me || await locate();
  let fromNote = "от вас";
  if (!from) {
    from = state.center;
    fromNote = `от центра (${esc(state.center?.name || "города")}) — включите геолокацию, чтобы считать от вас`;
  }
  if (state.routeFor !== i) return; // пока ждали, выбрали другое место

  let routes;
  try {
    const q = new URLSearchParams({ from_lat: from.lat, from_lon: from.lon, to_lat: p.lat, to_lon: p.lon });
    const r = await fetch(`/api/route?${q}`);
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Не получилось построить маршрут");
    routes = data.routes;
  } catch (err) {
    box.innerHTML = `<div class="route-note warn">${esc(err.message)}</div>`;
    return;
  }
  if (state.routeFor !== i) return;

  box.innerHTML = `
    <div class="route-modes">${routes.map((r, k) => `
      <button type="button" class="route-mode" data-k="${k}" title="${esc(r.label)}">
        <span class="ico">${MODE_ICON[r.mode]}</span>
        <span>${r.exact ? "" : "≈"}${fmtTime(r.duration_s)}</span>
      </button>`).join("")}
    </div>
    <div class="route-info"></div>
    <div class="route-note">Маршрут ${fromNote}</div>`;
  const pick = (k) => {
    const r = routes[k];
    box.querySelectorAll(".route-mode").forEach((b) => b.classList.toggle("on", +b.dataset.k === k));
    routeLayer.clearLayers();
    const line = r.geometry || [[from.lat, from.lon], [p.lat, p.lon]];
    const poly = L.polyline(line, {
      color: MODE_COLOR[r.mode], weight: 5, opacity: 0.85,
      dashArray: r.geometry ? (r.mode === "foot" ? "2 8" : null) : "6 10",
    }).addTo(routeLayer);
    // Всплывающее окно закрываем: всё нужное есть в карточке, а маршрут должен быть виден целиком
    state.markers[i]?.closePopup();
    map.fitBounds(poly.getBounds().pad(0.2), { maxZoom: 16 });
    let info = `<b>${esc(r.label)}:</b> ${r.exact ? "" : "примерно "}${fmtTime(r.duration_s)}, ${fmtDist(r.distance_m)}`;
    if (r.mode === "bus") info += "<br>Точный маршрут на транспорте с пересадками — в Яндекс Картах";
    else if (!r.exact) info += "<br>Сервер маршрутов не ответил — время оценено по расстоянию";
    if (r.far) info += "<br>Пешком далековато — может, лучше на транспорте?";
    info += `<br><a href="${esc(r.yandex)}" target="_blank" rel="noopener">Открыть маршрут в Яндекс Картах →</a>`;
    box.querySelector(".route-info").innerHTML = info;
  };
  box.querySelectorAll(".route-mode").forEach((b) => b.addEventListener("click", () => pick(+b.dataset.k)));
  // Сразу показываем самый разумный вариант: пешком, если близко, иначе на машине
  const near = routes.find((r) => r.mode === "foot").distance_m <= 2500;
  pick(routes.findIndex((r) => r.mode === (near ? "foot" : "car")));
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

loadStatus();
