"use strict";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "html") n.innerHTML = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) n.append(k.nodeType ? k : String(k));
  return n;
};
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const isoDay = (d) => d.toISOString().slice(0, 10);
const addDays = (s, n) => { const d = new Date(s + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return isoDay(d); };
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const DOW = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
function fmtDay(s, withDow = true) {
  const d = new Date(s.slice(0, 10) + "T12:00:00Z");
  return (withDow ? DOW[d.getUTCDay()] + " " : "") + MONTHS[d.getUTCMonth()] + " " + d.getUTCDate();
}
const hhmm = (dt) => dt.slice(11, 16);
const dayDiff = (a, b) => Math.round((new Date(b.slice(0, 10)) - new Date(a.slice(0, 10))) / 864e5);
const fmtDur = (m) => `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
let currency = "USD";
const money = (x) => new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(x);

// ---------------------------------------------------------------------------
// Airport inputs with autocomplete + nearby-airport chips
// ---------------------------------------------------------------------------
class AirportInput {
  constructor(field, { nearby = true, onChange = () => {} } = {}) {
    this.field = field;
    this.input = $("input", field);
    this.box = $(".suggest", field);
    this.nearbyBox = $(".nearby", field);
    this.useNearby = nearby;
    this.onChange = onChange;
    this.code = null;
    this.extra = new Map(); // nearby code -> selected?
    this.items = [];
    this.hl = -1;
    this.input.addEventListener("input", debounce(() => this.lookup(), 150));
    this.input.addEventListener("focus", () => this.input.select());
    this.input.addEventListener("keydown", (e) => this.key(e));
    this.input.addEventListener("blur", () => setTimeout(() => this.resolveTyped(), 150));
  }
  async lookup() {
    const q = this.input.value.trim();
    this.code = null;
    if (q.length < 2) { this.box.hidden = true; return; }
    const res = await fetch(`/api/airports?q=${encodeURIComponent(q)}`).then((r) => r.json()).catch(() => []);
    this.items = res; this.hl = res.length ? 0 : -1;
    this.box.replaceChildren(...res.map((a, i) =>
      el("button", { type: "button", class: i === 0 ? "hl" : "", onmousedown: (e) => { e.preventDefault(); this.pick(a); } },
        el("b", {}, a.code), el("div", {}, `${a.city}`, el("br"), el("span", {}, `${a.name}, ${a.country}`)))));
    this.box.hidden = !res.length;
  }
  key(e) {
    if (this.box.hidden) return;
    const btns = $$("button", this.box);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      this.hl = (this.hl + (e.key === "ArrowDown" ? 1 : -1) + btns.length) % btns.length;
      btns.forEach((b, i) => b.classList.toggle("hl", i === this.hl));
    } else if (e.key === "Enter" && this.hl >= 0) {
      e.preventDefault(); this.pick(this.items[this.hl]);
    } else if (e.key === "Escape") this.box.hidden = true;
  }
  async resolveTyped() {
    this.box.hidden = true;
    if (this.code) return;
    const q = this.input.value.trim();
    if (!q) return;
    const res = await fetch(`/api/airports?q=${encodeURIComponent(q)}`).then((r) => r.json()).catch(() => []);
    if (res.length && (res[0].code === q.toUpperCase() || res.length === 1)) this.pick(res[0]);
  }
  set(a, extras = null) {
    this.code = a.code;
    this.label = `${a.city} (${a.code})`;
    this.input.value = this.label;
    this.box.hidden = true;
    return this.loadNearby(extras);
  }
  pick(a) { this.set(a).then(() => this.onChange()); }
  clear() { this.code = null; this.input.value = ""; this.extra.clear(); this.nearbyBox.hidden = true; }
  async loadNearby(preset = null) {
    this.extra.clear();
    const radius = +$("#radius").value;
    if (!this.useNearby || !this.code || !radius) { this.nearbyBox.hidden = true; return; }
    const res = await fetch(`/api/airports/${this.code}/nearby?radius_km=${radius}`).then((r) => r.json()).catch(() => []);
    // Pre-tick the two nearest large airports; the rest are one tap away.
    const auto = new Set(res.filter((a) => a.major).sort((x, y) => x.distance_km - y.distance_km).slice(0, 2).map((a) => a.code));
    for (const a of res) this.extra.set(a.code, preset ? preset.includes(a.code) : auto.has(a.code));
    this.renderNearby(res);
  }
  renderNearby(list) {
    if (!list.length) { this.nearbyBox.hidden = true; return; }
    const chip = (a) => {
      const on = this.extra.get(a.code);
      const c = el("label", { class: "chip" + (on ? " on" : ""), title: `${a.name} · ${a.distance_km} km` },
        el("input", { type: "checkbox", checked: on, onchange: (e) => {
          this.extra.set(a.code, e.target.checked); c.classList.toggle("on", e.target.checked);
        } }), a.code, el("small", {}, ` ${a.distance_km}km`));
      return c;
    };
    this.nearbyBox.replaceChildren(el("span", { class: "chip main" }, this.code), ...list.map(chip));
    this.nearbyBox.hidden = false;
  }
  codes() {
    if (!this.code) return [];
    return [this.code, ...[...this.extra].filter(([, on]) => on).map(([c]) => c)];
  }
  state() { return this.code ? { code: this.code, label: this.label, extra: this.codes().slice(1) } : null; }
}

// ---------------------------------------------------------------------------
// Form
// ---------------------------------------------------------------------------
const form = $("#search");
const fromInput = new AirportInput($('[data-role="from"]'));
const toInput = new AirportInput($('[data-role="to"]'));
const pax = { adults: 1, children: 0, infants_in_seat: 0, infants_on_lap: 0 };
let multi = []; // [{from: AirportInput, to: AirportInput, date: input}]

const trip = () => $('input[name="trip"]:checked').value;

function updateTripUI() {
  const t = trip();
  $("#simple-legs").hidden = t === "multi-city";
  $("#multi-legs").hidden = t !== "multi-city";
  $("#return-field").style.visibility = t === "round-trip" ? "visible" : "hidden";
  $("#return").required = t === "round-trip";
  $("#radius").disabled = t === "multi-city";
  if (t === "multi-city" && !multi.length) { addLeg(); addLeg(); }
}
$$('input[name="trip"]').forEach((r) => r.addEventListener("change", updateTripUI));

function addLeg(preset) {
  if (multi.length >= 5) return;
  const mk = (role, label) => el("div", { class: "field airport-field" },
    el("label", {}, label), el("input", { placeholder: "City or airport", required: true }),
    el("div", { class: "suggest", hidden: true }), el("div", { class: "nearby", hidden: true }));
  const f = mk("from", "From"), t = mk("to", "To");
  const prev = multi[multi.length - 1];
  const date = el("input", { type: "date", required: true });
  const row = el("div", { class: "multi-leg" }, f, t,
    el("div", { class: "field" }, el("label", {}, "Date"), date),
    el("button", { type: "button", class: "rm", title: "Remove flight", onclick: () => {
      if (multi.length <= 2) return;
      multi = multi.filter((l) => l.row !== row); row.remove();
    } }, "✕"));
  $("#multi-list").append(row);
  const leg = { row, from: new AirportInput(f, { nearby: false }), to: new AirportInput(t, { nearby: false }), date };
  if (preset) {
    if (preset.from) leg.from.set(preset.from);
    if (preset.to) leg.to.set(preset.to);
    date.value = preset.date || "";
  } else if (prev) {
    if (prev.to.code) leg.from.set({ code: prev.to.code, city: prev.to.label.replace(/ \(.*\)$/, "") });
    date.value = prev.date.value ? addDays(prev.date.value, 3) : "";
  }
  leg.to.onChange = () => {};
  multi.push(leg);
}
$("#add-leg").addEventListener("click", () => addLeg());

$("#swap").addEventListener("click", async () => {
  const a = fromInput.state(), b = toInput.state();
  if (b) await fromInput.set({ code: b.code, city: b.label.replace(/ \(.*\)$/, "") }, b.extra); else fromInput.clear();
  if (a) await toInput.set({ code: a.code, city: a.label.replace(/ \(.*\)$/, "") }, a.extra); else toInput.clear();
});
$("#radius").addEventListener("change", () => { fromInput.loadNearby(); toInput.loadNearby(); });
$("#depart").addEventListener("change", () => {
  const d = $("#depart").value, r = $("#return");
  if (d && (!r.value || r.value < d)) r.value = addDays(d, 7);
  r.min = d;
});

// travellers popover
function renderPax() {
  $$(".stepper").forEach((s) => { $("b", s).textContent = pax[s.dataset.k]; });
  const n = pax.adults + pax.children + pax.infants_in_seat + pax.infants_on_lap;
  $("#pax-btn").textContent = n === 1 && pax.adults === 1 ? "1 adult" : `${n} travellers`;
}
$("#pax-btn").addEventListener("click", () => { $("#pax-pop").hidden = !$("#pax-pop").hidden; });
$("#pax-done").addEventListener("click", () => { $("#pax-pop").hidden = true; });
$$(".stepper").forEach((s) => {
  const [minus, plus] = $$("button", s);
  const k = s.dataset.k;
  minus.addEventListener("click", () => { pax[k] = Math.max(k === "adults" ? 1 : 0, pax[k] - 1); if (pax.infants_on_lap > pax.adults) pax.infants_on_lap = pax.adults; renderPax(); });
  plus.addEventListener("click", () => {
    const total = pax.adults + pax.children + pax.infants_in_seat + pax.infants_on_lap;
    if (total >= 9 || (k === "infants_on_lap" && pax.infants_on_lap >= pax.adults)) return;
    pax[k]++; renderPax();
  });
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#pax-pop, #pax-btn")) $("#pax-pop").hidden = true;
});
$("#hour").addEventListener("input", () => { $("#hour-out").textContent = "$" + $("#hour").value; });

function buildRequest() {
  const t = trip();
  let legs;
  if (t === "multi-city") {
    legs = multi.map((l, i) => {
      if (!l.from.code || !l.to.code) throw new Error(`Choose airports for flight ${i + 1}`);
      if (!l.date.value) throw new Error(`Choose a date for flight ${i + 1}`);
      return { origins: [l.from.code], destinations: [l.to.code], date: l.date.value };
    });
  } else {
    if (!fromInput.code) throw new Error("Choose where you're flying from");
    if (!toInput.code) throw new Error("Choose where you're flying to");
    if (fromInput.code === toInput.code) throw new Error("Origin and destination are the same");
    const o = fromInput.codes(), d = toInput.codes();
    const dep = $("#depart").value;
    if (!dep) throw new Error("Choose a departure date");
    legs = [{ origins: o, destinations: d, date: dep }];
    if (t === "round-trip") {
      const ret = $("#return").value;
      if (!ret) throw new Error("Choose a return date");
      if (ret < dep) throw new Error("Return date is before departure");
      legs.push({ origins: d, destinations: o, date: ret });
    }
  }
  const [carry, checked] = $("#bags").value.split(",").map(Number);
  const stops = $("#stops").value;
  return {
    trip_type: t,
    legs,
    passengers: { ...pax },
    cabin: $("#cabin").value,
    carry_on_bags: carry,
    checked_bags: checked,
    max_stops: stops === "" ? null : +stops,
    exclude_basic_economy: $("#no-basic").checked,
    flex_days: +$("#flex").value,
    depth: $("#depth").value,
    currency: $("#currency").value,
    provider: $("#provider").value,
    hour_value: +$("#hour").value,
  };
}

function saveForm() {
  store.set("fg-form", {
    trip: trip(), from: fromInput.state(), to: toInput.state(),
    depart: $("#depart").value, ret: $("#return").value, pax,
    multi: multi.map((l) => ({ from: l.from.state(), to: l.to.state(), date: l.date.value })),
    sel: Object.fromEntries(["cabin", "stops", "bags", "flex", "radius", "depth", "provider", "currency", "hour"].map((k) => [k, $("#" + k).value])),
    noBasic: $("#no-basic").checked,
  });
}

async function restoreForm() {
  const today = isoDay(new Date());
  $("#depart").min = today; $("#return").min = today;
  const s = store.get("fg-form", null);
  if (!s) {
    $("#depart").value = addDays(today, 21); $("#return").value = addDays(today, 28);
    updateTripUI(); return;
  }
  for (const [k, v] of Object.entries(s.sel || {})) if ($("#" + k)) $("#" + k).value = v;
  $("#hour-out").textContent = "$" + $("#hour").value;
  $("#no-basic").checked = !!s.noBasic;
  Object.assign(pax, s.pax || {}); renderPax();
  const r = $(`input[name="trip"][value="${s.trip}"]`); if (r) r.checked = true;
  $("#depart").value = s.depart && s.depart >= today ? s.depart : addDays(today, 21);
  $("#return").value = s.ret && s.ret >= $("#depart").value ? s.ret : addDays($("#depart").value, 7);
  const asAirport = (st) => ({ code: st.code, city: st.label.replace(/ \(.*\)$/, "") });
  if (s.from) await fromInput.set(asAirport(s.from), s.from.extra);
  if (s.to) await toInput.set(asAirport(s.to), s.to.extra);
  if (s.trip === "multi-city" && s.multi?.length >= 2) {
    for (const l of s.multi) addLeg({ from: l.from && asAirport(l.from), to: l.to && asAirport(l.to), date: l.date >= today ? l.date : "" });
  }
  updateTripUI();
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#form-error").textContent = "";
  let req;
  try { req = buildRequest(); } catch (err) { $("#form-error").textContent = err.message; return; }
  saveForm();
  startSearch(req);
});

// ---------------------------------------------------------------------------
// Search + polling
// ---------------------------------------------------------------------------
let data = null;
let pollTimer = null;
let searchToken = 0;
const view = { sort: "best", shown: 25, dateSel: null, filters: null, priceTouched: false, durTouched: false };

async function startSearch(req) {
  const token = ++searchToken;
  clearTimeout(pollTimer);
  $("#go").disabled = true;
  $("#go").textContent = "Searching…";
  $("#results").hidden = false;
  $("#progress-card").classList.remove("done");
  $("#bar-fill").style.width = "2%";
  $("#progress-msg").textContent = "Planning searches…";
  $("#progress-count").textContent = "";
  $("#errors").hidden = true;
  $("#list").replaceChildren();
  $("#insights").hidden = true;
  $("#grid-card").hidden = true;
  data = null;
  Object.assign(view, { shown: 25, dateSel: null, filters: null, priceTouched: false, durTouched: false });
  currency = req.currency;
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const r = await fetch("/api/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req) });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      const msg = Array.isArray(body.detail) ? body.detail.map((d) => d.msg.replace(/^Value error, /, "")).join("; ") : body.detail || r.statusText;
      throw new Error(msg);
    }
    const { id } = await r.json();
    poll(id, token);
  } catch (err) {
    finish();
    $("#progress-msg").textContent = "Search failed: " + err.message;
  }
}

async function poll(id, token) {
  if (token !== searchToken) return;
  try {
    const res = await fetch(`/api/search/${id}`).then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
    if (token !== searchToken) return;
    data = res;
    render();
    if (res.status === "running") pollTimer = setTimeout(() => poll(id, token), 700);
    else finish();
  } catch (err) {
    finish();
    $("#progress-msg").textContent = "Lost contact with the search: " + err.message;
  }
}

function finish() {
  $("#go").disabled = false;
  $("#go").textContent = "Search everything";
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
const STRATEGY = {
  "single": "1 ticket",
  "round-trip-fare": "Round-trip fare",
  "separate-tickets": "Separate tickets",
};

function estDuration(it) {
  const known = it.legs.filter(Boolean);
  return it.total_duration_min * it.legs.length / Math.max(1, known.length);
}
function legsKnown(it) { return it.legs.filter(Boolean); }
function maxStops(it) { return Math.max(0, ...legsKnown(it).map((l) => l.stops)); }
function firstDepartHour(it) { const l = it.legs[0]; return l ? +l.depart.slice(11, 13) : null; }
function airlinesOf(it) { return [...new Set(legsKnown(it).flatMap((l) => l.airlines))]; }
function airportsOf(it) { return [...new Set(it.routes.flatMap((r) => [r.origin, r.destination]))]; }

function render() {
  const p = data.progress;
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  $("#bar-fill").style.width = Math.max(2, pct) + "%";
  $("#progress-msg").textContent = p.message;
  $("#progress-count").textContent = p.total ? `${p.done}/${p.total}${p.failed ? ` · ${p.failed} failed` : ""}` : "";
  $("#progress-card").classList.toggle("done", data.status !== "running");
  if (data.request.provider === "demo") $("#progress-msg").textContent += " · DEMO DATA (made-up prices)";

  if (data.errors.length) {
    const hint = data.request.provider === "google"
      ? " If every search fails, Google may be blocking requests from your network for a while; try again later, lower the depth, or switch Data to Demo to try the app."
      : "";
    $("#errors").replaceChildren(el("b", {}, "Some searches failed. "), data.errors.slice(0, 3).join(" · "), hint);
    $("#errors").hidden = false;
  }

  renderInsights();
  renderGrid();
  buildFilters();
  renderList();
}

function renderInsights() {
  const box = $("#insights");
  box.replaceChildren(...data.insights.map((i) =>
    el("div", { class: "insight " + i.kind }, el("span", { class: "ico" }, i.kind === "time" ? "⏱" : "✓"), el("span", {}, i.text))));
  box.hidden = !data.insights.length;
}

function renderGrid() {
  const cells = data.date_grid;
  const card = $("#grid-card");
  if (cells.length < 2) { card.hidden = true; return; }
  card.hidden = false;
  const min = Math.min(...cells.map((c) => c.price));
  const req = data.request;
  const reqDep = req.legs[0].date, reqRet = req.legs[1]?.date;
  const isSel = (c) => view.dateSel && view.dateSel.depart === c.depart && (view.dateSel.ret ?? null) === (c.ret ?? null);
  const btn = (c, label) => el("button", {
    class: [c.price === min && "low", c.depart === reqDep && (c.ret ?? null) === (reqRet ?? null) && "req", isSel(c) && "sel"].filter(Boolean).join(" "),
    title: c.ret ? `${fmtDay(c.depart)} → ${fmtDay(c.ret)}` : fmtDay(c.depart),
    onclick: () => { view.dateSel = isSel(c) ? null : { depart: c.depart, ret: c.ret ?? null }; view.shown = 25; renderGrid(); renderList(); },
  }, label ?? money(c.price));

  const wrap = $("#grid");
  if (req.trip_type === "one-way") {
    wrap.replaceChildren(el("div", { class: "grid-row" }, cells.map((c) => btn(c, [el("small", {}, fmtDay(c.depart)), money(c.price)]))));
  } else {
    const deps = [...new Set(cells.map((c) => c.depart))].sort();
    const rets = [...new Set(cells.map((c) => c.ret))].sort();
    const map = new Map(cells.map((c) => [c.depart + c.ret, c]));
    wrap.replaceChildren(el("table", { class: "grid" },
      el("tr", {}, el("th", {}, "Depart ↓ / Return →"), rets.map((r) => el("th", {}, fmtDay(r)))),
      deps.map((d) => el("tr", {}, el("th", {}, fmtDay(d)), rets.map((r) => {
        const c = map.get(d + r);
        return el("td", {}, c ? btn(c) : "");
      })))));
  }
  $("#grid-clear").hidden = !view.dateSel;
}
$("#grid-clear").addEventListener("click", () => { view.dateSel = null; renderGrid(); renderList(); });

// ---- filters ----
const TIME_BUCKETS = [["early", "Early (00–06)", 0, 6], ["morning", "Morning (06–12)", 6, 12], ["afternoon", "Afternoon (12–18)", 12, 18], ["evening", "Evening (18–24)", 18, 24]];

function buildFilters() {
  const its = data.itineraries;
  if (!its.length) return;
  const f = view.filters || (view.filters = { stops: new Set(), time: new Set(), ticket: new Set(), airports: new Set(), airlines: new Set(), noWarn: false, price: null, dur: null });
  const prices = its.map((i) => i.price), durs = its.map((i) => Math.round(estDuration(i)));
  const pMax = Math.ceil(Math.max(...prices)), pMin = Math.floor(Math.min(...prices));
  const dMax = Math.max(...durs), dMin = Math.min(...durs);

  const pr = $("#f-price");
  pr.min = pMin; pr.max = pMax; pr.step = Math.max(1, Math.round((pMax - pMin) / 100));
  if (!view.priceTouched) { pr.value = pMax; f.price = null; }
  $("#f-price-out").textContent = money(+pr.value);
  const dr = $("#f-dur");
  dr.min = dMin; dr.max = dMax; dr.step = 15;
  if (!view.durTouched) { dr.value = dMax; f.dur = null; }
  $("#f-dur-out").textContent = fmtDur(+dr.value);

  const checks = (box, set, entries) => {
    box.replaceChildren(...entries.map(([val, label, count]) =>
      el("label", {}, el("input", { type: "checkbox", checked: set.has(val), onchange: (e) => {
        e.target.checked ? set.add(val) : set.delete(val); view.shown = 25; renderList();
      } }), label, el("small", {}, count))));
  };
  const count = (pred) => its.filter(pred).length;
  checks($("#f-stops"), f.stops, [[0, "Nonstop", count((i) => maxStops(i) === 0)], [1, "1 stop", count((i) => maxStops(i) === 1)], [2, "2+ stops", count((i) => maxStops(i) >= 2)]].filter((e) => e[2]));
  checks($("#f-time"), f.time, TIME_BUCKETS.map(([k, label, a, b]) => [k, label, count((i) => { const h = firstDepartHour(i); return h != null && h >= a && h < b; })]).filter((e) => e[2]));
  const strategies = [...new Set(its.map((i) => i.strategy))];
  $("#f-ticket-group").hidden = strategies.length < 2;
  checks($("#f-ticket"), f.ticket, strategies.map((s) => [s, STRATEGY[s], count((i) => i.strategy === s)]));
  const aps = [...new Set(its.flatMap(airportsOf))].sort();
  checks($("#f-airports"), f.airports, aps.map((a) => [a, a, count((i) => airportsOf(i).includes(a))]));
  const als = [...new Set(its.flatMap(airlinesOf))].sort();
  checks($("#f-airlines"), f.airlines, als.map((a) => [a, a, count((i) => airlinesOf(i).includes(a))]));
}
$("#f-price").addEventListener("input", (e) => { view.priceTouched = true; view.filters.price = +e.target.value; $("#f-price-out").textContent = money(+e.target.value); renderList(); });
$("#f-dur").addEventListener("input", (e) => { view.durTouched = true; view.filters.dur = +e.target.value; $("#f-dur-out").textContent = fmtDur(+e.target.value); renderList(); });
$("#f-nowarn").addEventListener("change", (e) => { view.filters.noWarn = e.target.checked; renderList(); });
$("#filters-reset").addEventListener("click", () => {
  view.filters = null; view.priceTouched = false; view.durTouched = false; view.dateSel = null; $("#f-nowarn").checked = false;
  buildFilters(); renderGrid(); renderList();
});
$("#toggle-filters").addEventListener("click", () => $("#filters").classList.toggle("open"));

function applyFilters(its) {
  const f = view.filters;
  return its.filter((i) => {
    if (view.dateSel) {
      if (i.routes[0].date !== view.dateSel.depart) return false;
      if (view.dateSel.ret && i.routes[1]?.date !== view.dateSel.ret) return false;
    }
    if (!f) return true;
    if (f.price != null && i.price > f.price) return false;
    if (f.dur != null && estDuration(i) > f.dur) return false;
    if (f.stops.size && ![...f.stops].some((s) => (s === 2 ? maxStops(i) >= 2 : maxStops(i) === s))) return false;
    if (f.time.size) {
      const h = firstDepartHour(i);
      if (!TIME_BUCKETS.some(([k, , a, b]) => f.time.has(k) && h >= a && h < b)) return false;
    }
    if (f.ticket.size && !f.ticket.has(i.strategy)) return false;
    if (f.airports.size && !airportsOf(i).every((a) => f.airports.has(a))) return false;
    if (f.airlines.size && !airlinesOf(i).every((a) => f.airlines.has(a))) return false;
    if (f.noWarn && i.warnings.length) return false;
    return true;
  });
}

const SORTS = {
  best: (a, b) => a.best_score - b.best_score,
  cheapest: (a, b) => a.price - b.price || a.best_score - b.best_score,
  fastest: (a, b) => estDuration(a) - estDuration(b) || a.price - b.price,
};
$$(".sort-tabs button").forEach((b) => b.addEventListener("click", () => {
  view.sort = b.dataset.sort; view.shown = 25;
  $$(".sort-tabs button").forEach((x) => x.classList.toggle("active", x === b));
  renderList();
}));
$("#more").addEventListener("click", () => { view.shown += 25; renderList(); });

function renderList() {
  if (!data) return;
  const all = applyFilters(data.itineraries);
  for (const b of $$(".sort-tabs button")) {
    const top = [...all].sort(SORTS[b.dataset.sort])[0];
    $("span", b).textContent = top ? `${money(top.price)} · ${fmtDur(Math.round(estDuration(top)))}` : "–";
  }
  const sorted = all.sort(SORTS[view.sort]);
  const list = $("#list");
  if (!data.itineraries.length) {
    list.replaceChildren(el("div", { class: "card empty" }, data.status === "running" ? "Searching… results appear here as they come in." : "No flights found. Try other dates, more flexibility, or nearby airports."));
  } else if (!sorted.length) {
    list.replaceChildren(el("div", { class: "card empty" }, "No results match your filters."));
  } else {
    const open = new Set($$(".result.open").map((n) => n.dataset.id));
    list.replaceChildren(...sorted.slice(0, view.shown).map((it) => card(it, open.has(it.id))));
  }
  $("#count").textContent = data.itineraries.length ? `${sorted.length} of ${data.itineraries.length} options` : "";
  $("#more").hidden = sorted.length <= view.shown;
}

function legSummary(leg) {
  const plus = dayDiff(leg.depart, leg.arrive);
  const stopsTxt = leg.stops === 0 ? el("span", { class: "nonstop" }, "Nonstop")
    : `${leg.stops} stop${leg.stops > 1 ? "s" : ""} (${leg.segments.slice(0, -1).map((s) => s.to_code).join(", ")})`;
  return el("div", { class: "leg" },
    el("div", { class: "leg-line" },
      el("span", { class: "times" }, `${hhmm(leg.depart)} – ${hhmm(leg.arrive)}`, plus ? el("span", { class: "plus" }, `+${plus}`) : null),
      el("span", { class: "airline" }, leg.airlines.join(", ") || "Multiple airlines")),
    el("div", { class: "leg-meta" }, `${fmtDay(leg.depart)} · ${leg.origin} → ${leg.destination} · ${fmtDur(leg.duration_min)} · `, stopsTxt));
}

function card(it, isOpen) {
  const pax = data.request.passengers;
  const nPax = pax.adults + pax.children + pax.infants_in_seat + pax.infants_on_lap;
  const legs = it.routes.map((r, i) => it.legs[i] ? legSummary(it.legs[i])
    : el("div", { class: "leg" }, el("div", { class: "open-leg" },
        `${fmtDay(r.date)} · ${r.origin} → ${r.destination}: pick your return flight on the booking site (included in the price)`)));
  const notes = [STRATEGY[it.strategy]];
  if (it.strategy === "separate-tickets") notes.push(`${it.legs.length} one-way tickets`);
  if (nPax > 1) notes.push(`total for ${nPax} travellers`);

  const details = el("div", { class: "details", hidden: !isOpen }, detailBody(it));
  const node = el("article", { class: "card result" + (isOpen ? " open" : ""), "data-id": it.id },
    el("div", { class: "result-main", onclick: () => {
      details.hidden = !details.hidden; node.classList.toggle("open", !details.hidden);
    } },
      el("div", { class: "legs" }, legs,
        it.warnings.length ? el("div", { class: "warn-line" }, "⚠ " + it.warnings.slice(0, 2).join(" · ") + (it.warnings.length > 2 ? ` · +${it.warnings.length - 2} more` : "")) : null),
      el("div", { class: "price-col" },
        el("div", { class: "tags" }, it.tags.map((t) => el("span", { class: "tag " + t }, t))),
        el("div", { class: "price" }, money(it.price)),
        el("div", { class: "price-note" }, notes.join(" · ")))),
    details);
  return node;
}

function detailBody(it) {
  const parts = [];
  it.routes.forEach((r, i) => {
    const leg = it.legs[i];
    const box = el("div", {}, el("div", { class: "leg-title" }, `${i === 0 ? "Outbound" : it.routes.length === 2 && data.request.trip_type === "round-trip" ? "Return" : `Flight ${i + 1}`} · ${r.origin} → ${r.destination} · ${fmtDay(r.date)}`));
    if (!leg) {
      box.append(el("div", { class: "open-leg" }, "Google shows return flights for this fare after you choose the outbound. Open Google Flights below, choose the outbound shown above, then pick a return."));
    } else {
      leg.segments.forEach((s, j) => {
        box.append(el("div", { class: "seg" },
          el("span", { class: "t" }, hhmm(s.depart)), el("span", {}, `${s.from_code} ${s.from_name}`),
          el("span", { class: "sub" }, `${fmtDur(s.duration_min)} · ${s.airline || leg.airlines.join(", ")}${s.aircraft ? " · " + s.aircraft : ""}`),
          el("span", { class: "t" }, hhmm(s.arrive), dayDiff(s.depart, s.arrive) ? el("span", { class: "plus" }, "+" + dayDiff(s.depart, s.arrive)) : null),
          el("span", {}, `${s.to_code} ${s.to_name}`)));
        if (j < leg.segments.length - 1) {
          const lay = leg.layovers_min[j];
          box.append(el("div", { class: "layover" + (lay < 50 || lay >= 360 ? " bad" : "") }, `${fmtDur(lay)} layover in ${s.to_code}`));
        }
      });
      if (leg.price != null && it.strategy === "separate-tickets") box.append(el("div", { class: "hint" }, `This ticket: ${money(leg.price)}`));
    }
    parts.push(box);
  });
  if (it.warnings.length) parts.push(el("div", { class: "warn-line" }, it.warnings.map((w) => "⚠ " + w).join("  ")));

  const groups = new Map();
  for (const l of it.booking_links) {
    if (!groups.has(l.label)) groups.set(l.label, []);
    groups.get(l.label).push(l);
  }
  const lookFor = legsKnown(it).map((l) => `${l.airlines.join("/")} ${hhmm(l.depart)} ${l.origin}→${l.destination}`).join(", ");
  parts.push(el("div", { class: "book" },
    el("div", { class: "hint" }, `Book it: each button opens the same search on that site. Look for ${lookFor}.`),
    [...groups].map(([label, ls]) => el("div", { class: "book-group" },
      label ? el("span", { class: "lbl" }, `Ticket: ${label}`) : null,
      ls.map((l) => el("a", { href: l.url, target: "_blank", rel: "noopener", class: l.site === "Google Flights" ? "g" : "" }, l.site))))));
  return parts;
}

// ---------------------------------------------------------------------------
renderPax();
restoreForm();
