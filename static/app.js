const $ = (id) => document.getElementById(id);
const state = { category: "any", budget: 0, coords: null, markers: [] };
const PROVIDER_LABEL = { yandex: "Яндекс", "2gis": "2ГИС", google: "Google", osm: "OSM", demo: "демо" };
const PRICE = { 1: "до 700 ₽", 2: "700–1500 ₽", 3: "1500–3000 ₽", 4: "от 3000 ₽" };

const map = L.map("map", { zoomControl: true }).setView([55.7558, 37.6173], 11);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const layer = L.layerGroup().addTo(map);

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

$("nearby").addEventListener("click", () => {
  if (state.coords) {
    state.coords = null;
    $("nearby").classList.remove("on");
    $("nearby").textContent = "Рядом со мной";
    $("city").disabled = false;
    return;
  }
  if (!navigator.geolocation) return alert("Браузер не умеет определять местоположение");
  $("nearby").textContent = "Определяю…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      state.coords = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      $("nearby").classList.add("on");
      $("nearby").textContent = "Рядом ✓";
      $("city").disabled = true;
    },
    () => {
      $("nearby").textContent = "Рядом со мной";
      alert("Не получилось определить местоположение. Выбери город вручную.");
    },
    { timeout: 10000 },
  );
});

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
  const { plan, places, errors, demo } = data;
  const sum = $("summary");
  sum.hidden = false;
  let html = `<b>${esc(plan.summary)}</b><br>Нашёл ${places.length} ${plural(places.length, "место", "места", "мест")}`;
  html += ` · ${plan.parser === "claude" ? "запрос понял Claude" : "разбор по ключевым словам"}`;
  if (demo) html += `<div class="warn">Демо-режим: места выдуманы. Добавь API-ключи карт в файл .env, и появятся настоящие.</div>`;
  for (const [name, msg] of Object.entries(errors)) html += `<div class="warn">${esc(name)}: ${esc(msg)}</div>`;
  sum.innerHTML = html;

  layer.clearLayers();
  state.markers = [];
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
    li.innerHTML = `
      <div class="top"><span class="num">${n}</span><span class="name">${esc(p.name)}</span></div>
      <div class="meta"><span>${meta}</span></div>
      ${p.address ? `<div class="meta">${esc(p.address)}</div>` : ""}
      <div class="links">${linksHtml(p)}</div>`;
    li.addEventListener("click", (e) => {
      if (e.target.closest("a")) return;
      focusPlace(i);
    });
    list.appendChild(li);

    const icon = L.divIcon({ className: "", html: `<div class="pin"><span>${n}</span></div>`, iconSize: [26, 26], iconAnchor: [13, 26], popupAnchor: [0, -24] });
    const marker = L.marker([p.lat, p.lon], { icon }).addTo(layer).bindPopup(`
      <b>${esc(p.name)}</b><br>
      <span style="color:#7b6d70">${[esc(p.kind), p.rating ? `★ ${p.rating}` : "", priceText(p)].filter(Boolean).join(" · ")}</span><br>
      ${esc(p.address)}
      <div class="links">${linksHtml(p)}</div>`);
    marker.on("click", () => highlight(i));
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
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

loadStatus();
