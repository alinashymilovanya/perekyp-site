const state = {
  status: null,
  listings: [],
  sort: "margin",
  query: "",
};

export function calculateMargin({ marketPrice, price, delivery, commission, reserve }) {
  return marketPrice - price - delivery - commission - reserve;
}

export function formatRubles(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toLocaleString("ru-RU").replaceAll("\u00a0", " ")} ₽`;
}

export function sortListings(listings) {
  return [...listings].sort((left, right) => {
    if (left.margin_rub === null && right.margin_rub === null) return 0;
    if (left.margin_rub === null) return 1;
    if (right.margin_rub === null) return -1;
    return right.margin_rub - left.margin_rub;
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function byId(id) {
  return document.getElementById(id);
}

function formatDate(value) {
  if (!value) return "Дата неизвестна";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Дата неизвестна"
    : new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function renderStatus() {
  const status = state.status ?? {};
  const banner = byId("status-banner");
  const badge = byId("status-badge");
  const text = byId("status-text");
  const dot = byId("status-dot");
  const isHealthy = status.healthy === true;

  banner.classList.toggle("is-warning", !isHealthy);
  dot.classList.toggle("is-warning", !isHealthy);
  badge.textContent = isHealthy ? "Онлайн" : "Нет связи";
  badge.className = `badge ${isHealthy ? "badge-success" : "badge-warning"}`;
  text.textContent = isHealthy
    ? status.monitoring_enabled
      ? "Бот отвечает, мониторинг активен"
      : "Бот отвечает, источник объявлений отключён"
    : status.error || "Сигнал от бота не получен";

  byId("source-state").textContent = status.monitoring_enabled ? "Активен" : "Отключён";
  byId("source-state").className = status.monitoring_enabled ? "value value-success" : "value value-muted";
  byId("failure-count").textContent = String(status.source_failure_count ?? 0);
  byId("heartbeat-time").textContent = formatDate(status.heartbeat_iso);
}

function renderMetrics() {
  const listings = state.listings;
  const margins = listings.map((listing) => listing.margin_rub).filter((value) => value !== null);
  const totalMargin = margins.reduce((sum, value) => sum + value, 0);
  const averageMargin = margins.length ? Math.round(totalMargin / margins.length) : null;

  byId("stat-listings").textContent = String(listings.length);
  byId("stat-promising").textContent = String(listings.filter((listing) => (listing.margin_rub ?? -1) >= 500).length);
  byId("stat-average").textContent = formatRubles(averageMargin);
  byId("stat-best").textContent = formatRubles(margins.length ? Math.max(...margins) : null);
}

function listingMarkup(listing) {
  const marginClass = listing.margin_rub !== null && listing.margin_rub >= 500 ? "margin-positive" : "margin-neutral";
  return `<article class="listing-row">
    <div class="listing-index">${escapeHtml(listing.item_id.slice(-2).padStart(2, "0"))}</div>
    <div class="listing-main">
      <h3>${escapeHtml(listing.title)}</h3>
      <p>${escapeHtml(formatDate(listing.notified_at))} · Цена ${escapeHtml(formatRubles(listing.price_rub))}</p>
    </div>
    <div class="listing-market"><span>Рынок</span><strong>${escapeHtml(formatRubles(listing.market_price_rub))}</strong></div>
    <div class="listing-margin ${marginClass}"><span>Маржа</span><strong>${escapeHtml(formatRubles(listing.margin_rub))}</strong></div>
    <a class="icon-link" href="${escapeHtml(listing.url)}" target="_blank" rel="noreferrer" aria-label="Открыть объявление">↗</a>
  </article>`;
}

function visibleListings() {
  const filtered = state.listings.filter((listing) => listing.title.toLowerCase().includes(state.query.toLowerCase()));
  return state.sort === "margin" ? sortListings(filtered) : [...filtered].sort((a, b) => String(b.notified_at).localeCompare(String(a.notified_at)));
}

function renderListings() {
  const listings = visibleListings();
  const markup = listings.length
    ? listings.map(listingMarkup).join("")
    : `<div class="empty-state"><div class="empty-icon">⌁</div><h3>Новых уведомлений пока нет</h3><p>Когда бот найдёт подходящее объявление, оно появится здесь автоматически.</p></div>`;
  byId("listing-count").textContent = `${listings.length} ${listings.length === 1 ? "запись" : "записей"}`;
  byId("listings-container").innerHTML = markup;
  byId("preview-list").innerHTML = state.listings.length
    ? sortListings(state.listings).slice(0, 3).map(listingMarkup).join("")
    : `<div class="empty-state compact"><div class="empty-icon">⌁</div><p>Список находок появится после первого уведомления.</p></div>`;
}

function renderError(error) {
  byId("load-error").hidden = false;
  byId("load-error").textContent = `Не удалось загрузить данные: ${error.message}. Проверьте, что сайт запущен через server.py.`;
}

async function loadDashboard() {
  byId("load-error").hidden = true;
  byId("refresh-button").classList.add("is-loading");
  try {
    const [status, listingsResponse] = await Promise.all([getJson("/api/status"), getJson("/api/listings?limit=100")]);
    state.status = status;
    state.listings = listingsResponse.items ?? [];
    renderStatus();
    renderMetrics();
    renderListings();
    byId("last-updated").textContent = `Обновлено ${new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(new Date())}`;
  } catch (error) {
    renderError(error);
  } finally {
    byId("refresh-button").classList.remove("is-loading");
  }
}

function setupNavigation() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.dataset.view;
      document.querySelectorAll("[data-view]").forEach((item) => item.classList.toggle("is-active", item === button));
      document.querySelectorAll(".page-view").forEach((section) => section.classList.toggle("is-visible", section.id === `view-${view}`));
      byId("page-title").textContent = { overview: "Обзор", listings: "Все объявления", calculator: "Калькулятор" }[view];
    });
  });
}

function setupCalculator() {
  const fields = ["calc-market", "calc-price", "calc-delivery", "calc-commission", "calc-reserve"];
  const update = () => {
    const values = Object.fromEntries(fields.map((id) => [id, Number(byId(id).value) || 0]));
    const margin = calculateMargin({ marketPrice: values["calc-market"], price: values["calc-price"], delivery: values["calc-delivery"], commission: values["calc-commission"], reserve: values["calc-reserve"] });
    byId("calc-result").textContent = formatRubles(margin);
    byId("calc-result").className = margin >= 500 ? "calculator-result is-positive" : "calculator-result is-negative";
    byId("calc-verdict").textContent = margin >= 500 ? "Сделка выглядит перспективно" : "Запас маржи ниже порога 500 ₽";
  };
  fields.forEach((id) => byId(id).addEventListener("input", update));
  update();
}

function setupFilters() {
  byId("search-input").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderListings();
  });
  byId("sort-select").addEventListener("change", (event) => {
    state.sort = event.target.value;
    renderListings();
  });
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => {
    setupNavigation();
    setupCalculator();
    setupFilters();
    byId("refresh-button").addEventListener("click", loadDashboard);
    loadDashboard();
  });
}
