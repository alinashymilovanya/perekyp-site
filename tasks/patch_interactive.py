"""Делает все кнопки и ссылки сайта рабочими: фильтры, вкладки, пагинация, документы."""

import io
import re

CAB = r"D:\tg\site\cabinet.html"
IDX = r"D:\tg\site\index.html"

CSS_ADD = r"""
/* interactive */
.panel{display:none}
.panel.on{display:block}
.rng{-webkit-appearance:none;appearance:none;width:100%;height:2px;margin:14px 0 8px;background:rgba(255,255,255,.1);outline:none}
.rng::-webkit-slider-thumb{-webkit-appearance:none;width:9px;height:9px;border-radius:50%;background:#fff;cursor:pointer}
.rng::-moz-range-thumb{width:9px;height:9px;border:0;border-radius:50%;background:#fff;cursor:pointer}
.chip{cursor:pointer;user-select:none}
.tabs a{cursor:pointer}
.avito{color:#ff6d8f;font-family:var(--mono);font-size:8px;letter-spacing:.14em}
.empty{padding:26px 8px;color:var(--mut2);font-size:12px;white-space:normal}
.slist,.mlist{display:flex;flex-direction:column}
.srow{display:flex;align-items:center;gap:14px;padding:12px 16px;border-bottom:1px solid var(--line2);font-size:12px;color:#c9c9d2}
.srow:last-child,.mrow:last-child{border-bottom:0}
.srow .t{font-family:var(--mono);font-size:8.5px;color:var(--mut2);width:44px;flex:0 0 auto}
.srow .m{color:#f0f0f4;font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.srow .v{font-family:var(--mono);font-size:12px;color:#ff4a76;flex:0 0 auto}
.mrow{display:flex;align-items:center;gap:14px;padding:12px 16px;border-bottom:1px solid var(--line2);font-size:12px}
.mrow .m{flex:1;color:#f0f0f4}
.mrow .c{font-family:var(--mono);font-size:9px;color:var(--mut2)}
.switch{width:34px;height:18px;border-radius:9px;border:1px solid var(--line);background:rgba(255,255,255,.04);position:relative;cursor:pointer;flex:0 0 auto}
.switch i{position:absolute;left:2px;top:50%;width:12px;height:12px;border-radius:50%;background:#6a6a74;transform:translateY(-50%);transition:.15s}
.switch.on{border-color:rgba(196,18,69,.55);background:rgba(123,4,42,.35)}
.switch.on i{left:18px;background:#ff4a76}
.addrow{display:flex;gap:8px;padding:14px 16px}
.addrow input{flex:1;min-width:0;background:transparent;border:1px solid var(--line);color:var(--txt);padding:10px 12px;font-size:12px;font-family:inherit}
.addrow input:focus{outline:none;border-color:rgba(196,18,69,.55)}
.frm{display:flex;flex-direction:column;gap:16px;padding:16px}
.frm label{display:flex;align-items:center;justify-content:space-between;gap:16px;font-size:12px;color:#c9c9d2}
.frm label>span{max-width:60%}
.frm input,.frm select{background:transparent;border:1px solid var(--line);color:var(--txt);padding:9px 11px;font-size:12px;font-family:var(--mono);min-width:96px}
.frm input:focus,.frm select:focus{outline:none;border-color:rgba(196,18,69,.55)}
.toast{position:fixed;right:22px;bottom:22px;z-index:80;border:1px solid rgba(196,18,69,.5);background:rgba(11,11,14,.94);
  padding:12px 16px;font-family:var(--mono);font-size:9px;letter-spacing:.14em;text-transform:uppercase;color:#f0f0f4;
  opacity:0;transform:translateY(8px);transition:.2s;pointer-events:none;backdrop-filter:blur(8px)}
.toast.on{opacity:1;transform:none}
@media(max-width:1180px){
  .top{flex-wrap:wrap}
  .tabs{display:flex;order:10;width:100%;gap:20px;margin:2px 0 0;overflow-x:auto}
  .tabs a{white-space:nowrap;font-size:9px}
  .tabs a.on:after{bottom:-7px}
}
"""

TABS_OLD = """      <a class="on" href="#">Находки</a>
      <a href="#">Сигналы</a>
      <a href="#">Модели</a>
      <a href="#">Настройки</a>"""

TABS_NEW = """      <a class="on" href="#panel-finds" data-tab="finds">Находки</a>
      <a href="#panel-signals" data-tab="signals">Сигналы</a>
      <a href="#panel-models" data-tab="models">Модели</a>
      <a href="#panel-settings" data-tab="settings">Настройки</a>"""

ASIDE_NEW = """    <aside class="col side-l">
      <div class="h"><span>Фильтры</span><b id="fcount">4</b></div>

      <div class="fgroup">
        <div class="t">Авито Доставка</div>
        <div class="chips" data-f="del">
          <span class="chip on" data-v="all">Все</span><span class="chip" data-v="yes">Есть</span><span class="chip" data-v="no">Нет</span>
        </div>
      </div>

      <div class="fgroup">
        <div class="t">Модель</div>
        <div class="chips" data-f="model" id="modelChips"></div>
      </div>

      <div class="fgroup">
        <div class="t">Память</div>
        <div class="chips" data-f="mem" id="memChips"></div>
      </div>

      <div class="fgroup">
        <div class="t">Маржа от</div>
        <input class="rng" id="margin" type="range" min="0" max="15000" step="500" value="0" aria-label="Минимальная маржа">
        <div class="fval"><span>0 ₽</span><span id="mval">0 ₽</span><span>15 000 ₽</span></div>
      </div>

      <div class="fgroup">
        <div class="t">Период</div>
        <div class="chips" data-f="period">
          <span class="chip" data-v="24">24 ч</span><span class="chip on" data-v="7">7 дней</span><span class="chip" data-v="30">30 дней</span>
        </div>
      </div>

      <div class="btns"><button class="btn p full" id="apply">Показать</button></div>
      <div class="btns"><button class="btn full" id="reset">Сбросить</button></div>
    </aside>"""

MAIN_NEW = """    <main class="mid">
      <section class="panel on" id="panel-finds">
        <div class="h"><span id="scope">Находки · вся Россия · 7 дней</span><b>обновлено <span id="updated">00:41</span></b></div>

        <div class="connect">
          <s></s>
          <div class="tx">
            <b>Мониторинг в Telegram</b>
            <span>Сигналы приходят в чат с ботом — подключение занимает минуту</span>
          </div>
          <a class="btn p sm" href="https://t.me/perekypibot" target="_blank" rel="noopener">Подключить в боте →</a>
        </div>

        <div class="kpis">
          <div class="kpi"><div class="l">Найдено</div><div class="v" id="kFound">214</div><div class="d" id="kFoundD">+38 за сутки</div></div>
          <div class="kpi"><div class="l">Сигналов</div><div class="v" id="kSignals">87</div><div class="d" id="kSignalsD">порог 500 ₽</div></div>
          <div class="kpi"><div class="l">Средняя маржа</div><div class="v" id="kMargin">6 800<small>₽</small></div><div class="d">по выбранным находкам</div></div>
          <div class="kpi"><div class="l">Куплено</div><div class="v" id="kBought">12</div><div class="d warn" id="kBoughtD">3 в работе</div></div>
        </div>

        <div class="card">
          <div class="ch"><span>Маржа по дням</span><b>профит 81 600 ₽</b></div>
          <div class="spark">
            <svg viewBox="0 0 900 150" preserveAspectRatio="none">
              <defs>
                <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="#c41245" stop-opacity=".45"/>
                  <stop offset="100%" stop-color="#c41245" stop-opacity="0"/>
                </linearGradient>
              </defs>
              <path d="M0 118 L128 96 L257 104 L385 72 L514 84 L642 52 L771 60 L900 28 L900 150 L0 150 Z" fill="url(#g1)"/>
              <path d="M0 118 L128 96 L257 104 L385 72 L514 84 L642 52 L771 60 L900 28" fill="none" stroke="#ff4a76" stroke-width="1.6"/>
              <g fill="rgba(255,255,255,.14)">
                <rect x="0" y="0" width="900" height="1"/>
                <rect x="0" y="50" width="900" height="1"/>
                <rect x="0" y="100" width="900" height="1"/>
              </g>
            </svg>
            <div class="axis"><span>25.10</span><span>27.10</span><span>29.10</span><span>31.10</span></div>
          </div>
        </div>

        <div class="card">
          <div class="ch"><span>Все находки</span><b id="shown">показано 6 из 214</b></div>
          <table class="tbl">
            <thead><tr>
              <th>Время</th><th>Модель</th><th>Цена</th><th class="hidem">Рынок</th><th>Маржа</th><th>Доставка</th><th>Статус</th><th class="hidem">Ссылка</th>
            </tr></thead>
            <tbody id="rows"></tbody>
          </table>
          <div class="tfoot"><span id="count">показано 6 из 214</span>
            <div class="pages" id="pages"></div>
          </div>
        </div>
      </section>

      <section class="panel" id="panel-signals">
        <div class="h"><span>Сигналы · вся Россия</span><b id="sigCount">—</b></div>
        <div class="connect">
          <s></s>
          <div class="tx">
            <b>Так выглядит сигнал в чате</b>
            <span>Бот присылает карточку с ценой, рынком, маржой и кнопкой «беру»</span>
          </div>
          <a class="btn p sm" href="https://t.me/perekypibot" target="_blank" rel="noopener">Открыть бота →</a>
        </div>
        <div class="card">
          <div class="ch"><span>Лента сигналов</span><b>сортировка по марже</b></div>
          <div class="slist" id="signals"></div>
        </div>
      </section>

      <section class="panel" id="panel-models">
        <div class="h"><span>Модели под наблюдением</span><b id="modelCount">—</b></div>
        <div class="card">
          <div class="ch"><span>Список моделей</span><b>переключатель включает модель</b></div>
          <div class="mlist" id="models"></div>
        </div>
        <div class="card">
          <div class="ch"><span>Добавить модель</span><b>iPhone</b></div>
          <div class="addrow">
            <input id="newModel" placeholder="Например, iPhone 16 Pro 256">
            <button class="btn p sm" id="addModel">Добавить</button>
          </div>
        </div>
      </section>

      <section class="panel" id="panel-settings">
        <div class="h"><span>Настройки мониторинга</span><b id="settingsState">черновик</b></div>
        <div class="card">
          <div class="ch"><span>Порог и частота</span><b>применяются к боту</b></div>
          <div class="frm">
            <label><span>Маржа, ниже которой не присылать</span><input id="setMargin" type="number" min="0" step="500" value="3000"></label>
            <label><span>Интервал сканирования</span><select id="setEvery"><option value="60">60 сек</option><option value="120" selected>120 сек</option><option value="300">300 сек</option></select></label>
            <label><span>Уведомления в Telegram</span><button class="switch on" id="setNotify" type="button" aria-label="Уведомления"><i></i></button></label>
            <button class="btn p full" id="saveSettings">Сохранить</button>
          </div>
        </div>
      </section>
    </main>"""

QUICK_NEW = """      <div class="quick">
        <button class="btn sm full" id="addModelQuick">Добавить модель</button>
        <button class="btn sm full" id="pauseBtn">Пауза на час</button>
        <button class="btn sm full" id="historyBtn">История сигналов</button>
      </div>"""

JS = r"""
<div class="toast" id="toast"></div>
<script>
(function(){
  var $ = function(s,r){ return (r||document).querySelector(s); };
  var $$ = function(s,r){ return [].slice.call((r||document).querySelectorAll(s)); };

  var seed = 20261031;
  function rnd(){ seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; }
  function ri(a,b){ return a + Math.floor(rnd() * (b - a + 1)); }
  function pick(a){ return a[Math.floor(rnd() * a.length)]; }
  function fmt(n){ return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " "); }

  var SPECS = [
    {n:"15 Pro",    mem:[128,256,512], market:78500},
    {n:"15 Pro Max",mem:[256,512],     market:86000},
    {n:"15",        mem:[128,256],     market:55400},
    {n:"14 Pro",    mem:[128,256],     market:52000},
    {n:"14",        mem:[128,256],     market:42000},
    {n:"13",        mem:[128,256],     market:34800},
    {n:"12",        mem:[64,128],      market:22400},
    {n:"16 Pro",    mem:[256,512],     market:99000}
  ];

  var now = new Date();
  var ROWS = [];
  SPECS.forEach(function(sp){
    var lots = 4 + Math.floor(rnd() * 3);
    for (var i = 0; i < lots; i++) {
      var mem = pick(sp.mem);
      var margin = ri(1200, 16500);
      var market = sp.market + ri(-2500, 2500);
      var mins = i === 0 ? ri(2, 200) : ri(5, 41000);
      var t = new Date(now.getTime() - mins * 60000);
      ROWS.push({
        model: sp.n,
        group: sp.n,
        mem: mem,
        market: market,
        price: market - margin,
        margin: margin,
        mins: mins,
        time: ("0" + t.getHours()).slice(-2) + ":" + ("0" + t.getMinutes()).slice(-2),
        delivery: rnd() < 0.6,
        status: pick(["Новый","Новый","Новый","В работе","В работе","Куплено","Мимо"]),
        url: "https://www.avito.ru/rossiya/telefony?q=" + encodeURIComponent("iphone " + sp.n + " " + mem)
      });
    }
  });
  ROWS.sort(function(a,b){ return a.mins - b.mins; });

  var state = {
    tab: "finds",
    del: "all",
    model: "all",
    mem: {},
    period: 7,
    minMargin: 0,
    page: 1,
    off: {},
    paused: false,
    notify: true
  };
  ROWS.forEach(function(r){ state.mem[r.mem] = true; });

  function toast(msg){
    var el = $("#toast");
    el.textContent = msg;
    el.classList.add("on");
    clearTimeout(el._t);
    el._t = setTimeout(function(){ el.classList.remove("on"); }, 2200);
  }

  function groups(){
    var seen = [], out = [];
    ROWS.forEach(function(r){ if (seen.indexOf(r.group) < 0) { seen.push(r.group); out.push(r.group); } });
    return out;
  }
  function mems(){
    var seen = [], out = [];
    ROWS.forEach(function(r){ if (seen.indexOf(r.mem) < 0) { seen.push(r.mem); out.push(r.mem); } });
    return out.sort(function(a,b){ return a - b; });
  }

  function buildChips(){
    var m = $("#modelChips");
    m.innerHTML = '<span class="chip on" data-v="all">Все</span>' + groups().map(function(g){
      return '<span class="chip" data-v="' + g + '">' + g + '</span>';
    }).join("");
    var mm = $("#memChips");
    mm.innerHTML = mems().map(function(v){
      return '<span class="chip on" data-v="' + v + '">' + v + '</span>';
    }).join("");
  }

  function filtered(){
    var days = state.period * 1440;
    return ROWS.filter(function(r){
      if (state.off[r.group]) return false;
      if (r.mins > days) return false;
      if (state.del === "yes" && !r.delivery) return false;
      if (state.del === "no" && r.delivery) return false;
      if (state.model !== "all" && r.group !== state.model) return false;
      if (!state.mem[r.mem]) return false;
      if (r.margin < state.minMargin) return false;
      return true;
    });
  }

  function avg(a){
    if (!a.length) return 0;
    var s = 0;
    a.forEach(function(x){ s += x.margin; });
    return Math.round(s / a.length);
  }

  function renderTable(){
    var rows = filtered();
    var per = 6;
    var total = Math.max(1, Math.ceil(rows.length / per));
    if (state.page > total) state.page = total;
    var start = (state.page - 1) * per;
    var slice = rows.slice(start, start + per);

    var html = slice.map(function(r){
      var st = { "Новый":"", "В работе":" work", "Куплено":" done", "Мимо":" no" }[r.status];
      var del = r.delivery ? '<span class="badge del">Есть</span>' : '<span class="badge del no">Нет</span>';
      return '<tr><td class="time">' + r.time + '</td><td class="mod">iPhone ' + r.model + ' ' + r.mem +
        '</td><td class="num">' + fmt(r.price) + ' ₽</td><td class="num hidem">' + fmt(r.market) +
        ' ₽</td><td class="mar">+' + fmt(r.margin) + ' ₽</td><td>' + del +
        '</td><td><span class="badge' + st + '">' + r.status + '</span></td><td class="hidem"><a class="avito" href="' +
        r.url + '" target="_blank" rel="noopener">AVITO ↗</a></td></tr>';
    }).join("");

    $("#rows").innerHTML = html || '<tr><td colspan="8" class="empty">Под эти фильтры ничего не подошло — ослабьте условия или нажмите «Сбросить».</td></tr>';

    var shown = slice.length;
    var label = "показано " + shown + " из " + rows.length;
    $("#shown").textContent = label;
    $("#count").textContent = label;

    var pages = "";
    var count = Math.min(total, 7);
    for (var i = 1; i <= count; i++) pages += '<a href="#" data-p="' + i + '"' + (i === state.page ? ' class="on"' : '') + '>' + i + '</a>';
    if (total > 7) pages += '<a href="#" data-p="' + total + '">' + total + '</a>';
    $("#pages").innerHTML = pages;

    $("#kFound").textContent = rows.length;
    $("#kFoundD").textContent = "в выборке";
    var sig = rows.filter(function(r){ return r.margin >= 500; }).length;
    $("#kSignals").textContent = sig;
    $("#kSignalsD").textContent = "порог 500 ₽";
    $("#kMargin").innerHTML = fmt(avg(rows)) + "<small>₽</small>";
    var bought = rows.filter(function(r){ return r.status === "Куплено"; }).length;
    var work = rows.filter(function(r){ return r.status === "В работе"; }).length;
    $("#kBought").textContent = bought;
    $("#kBoughtD").textContent = work + " в работе";

    var names = { 24: "24 часа", 7: "7 дней", 30: "30 дней" };
    $("#scope").textContent = "Находки · вся Россия · " + names[state.period] +
      (state.model !== "all" ? " · " + state.model : "");
  }

  function renderSignals(){
    var rows = filtered().slice().sort(function(a,b){ return b.margin - a.margin; }).slice(0, 12);
    $("#sigCount").textContent = rows.length + " сигналов";
    $("#signals").innerHTML = rows.map(function(r){
      return '<div class="srow"><span class="t">' + r.time + '</span><span class="m">iPhone ' + r.model + ' ' + r.mem +
        '</span><span class="c" style="color:#8b8b95;font-family:var(--mono);font-size:9px">' + fmt(r.price) +
        ' ₽</span><span class="v">+' + fmt(r.margin) + ' ₽</span></div>';
    }).join("") || '<div class="srow"><span class="m">Пока пусто — ослабьте фильтры.</span></div>';
  }

  function renderModels(){
    var gs = groups();
    $("#modelCount").textContent = gs.filter(function(g){ return !state.off[g]; }).length + " из " + gs.length + " включены";
    $("#models").innerHTML = gs.map(function(g){
      var n = ROWS.filter(function(r){ return r.group === g; }).length;
      var on = !state.off[g];
      return '<div class="mrow"><span class="m">iPhone ' + g + '</span><span class="c">' + n +
        ' объявл.</span><button class="switch' + (on ? " on" : "") + '" data-g="' + g + '" type="button" aria-label="' +
        g + '"><i></i></button></div>';
    }).join("");
  }

  function renderAll(){ renderTable(); renderSignals(); renderModels(); }

  function switchTab(name){
    state.tab = name;
    $$(".tabs a").forEach(function(a){ a.classList.toggle("on", a.getAttribute("data-tab") === name); });
    $$(".panel").forEach(function(p){ p.classList.toggle("on", p.id === "panel-" + name); });
    if (location.hash !== "#panel-" + name) history.replaceState(null, "", "#panel-" + name);
  }

  /* чипы фильтров */
  $$(".chips").forEach(function(box){
    box.addEventListener("click", function(e){
      var chip = e.target.closest(".chip");
      if (!chip || !box.contains(chip)) return;
      var f = box.getAttribute("data-f");
      var v = chip.getAttribute("data-v");
      if (f === "mem") {
        var on = !chip.classList.contains("on");
        state.mem[v] = on;
        chip.classList.toggle("on", on);
      } else {
        $$(".chip", box).forEach(function(c){ c.classList.toggle("on", c === chip); });
        if (f === "del") state.del = v;
        if (f === "model") state.model = v;
        if (f === "period") state.period = parseInt(v, 10);
      }
      state.page = 1;
      renderAll();
    });
  });

  /* ползунок маржи */
  var rng = $("#margin");
  rng.addEventListener("input", function(){
    state.minMargin = parseInt(rng.value, 10);
    $("#mval").textContent = fmt(state.minMargin) + " ₽";
    state.page = 1;
    renderAll();
  });

  $("#apply").addEventListener("click", function(){
    state.page = 1;
    renderAll();
    toast("Показано " + filtered().length + " находок");
  });

  $("#reset").addEventListener("click", function(){
    state.del = "all"; state.model = "all"; state.period = 7; state.minMargin = 0; state.page = 1;
    mems().forEach(function(v){ state.mem[v] = true; });
    state.off = {};
    $$('[data-f="del"] .chip').forEach(function(c){ c.classList.toggle("on", c.getAttribute("data-v") === "all"); });
    $$('[data-f="model"] .chip').forEach(function(c){ c.classList.toggle("on", c.getAttribute("data-v") === "all"); });
    $$('[data-f="mem"] .chip').forEach(function(c){ c.classList.add("on"); });
    $$('[data-f="period"] .chip').forEach(function(c){ c.classList.toggle("on", c.getAttribute("data-v") === "7"); });
    rng.value = 0; $("#mval").textContent = "0 ₽";
    renderAll();
    toast("Фильтры сброшены");
  });

  /* пагинация */
  $("#pages").addEventListener("click", function(e){
    var a = e.target.closest("a");
    if (!a) return;
    e.preventDefault();
    state.page = parseInt(a.getAttribute("data-p"), 10);
    renderTable();
    document.getElementById("panel-finds").scrollIntoView({ block: "start", behavior: "smooth" });
  });

  /* вкладки */
  $$(".tabs a").forEach(function(a){
    a.addEventListener("click", function(e){
      e.preventDefault();
      switchTab(a.getAttribute("data-tab"));
    });
  });

  /* переключатели моделей */
  $("#models").addEventListener("click", function(e){
    var b = e.target.closest(".switch");
    if (!b) return;
    var g = b.getAttribute("data-g");
    state.off[g] = !state.off[g];
    state.page = 1;
    renderAll();
    toast("iPhone " + g + (state.off[g] ? " снят с наблюдения" : " снова в наблюдении"));
  });

  /* добавить модель */
  function addModel(){
    var input = $("#newModel");
    var raw = (input.value || "").trim();
    if (!raw) { input.focus(); toast("Введите модель, например iPhone 16 Pro 256"); return; }
    var mem = (raw.match(/\b(64|128|256|512)\b/) || [])[1];
    var name = raw.replace(/^iphone\s*/i, "").replace(/\b(64|128|256|512)\b/, "").trim();
    if (!name) { toast("Не понял модель — напишите, например: 16 Pro 256"); return; }
    var g = name.charAt(0).toUpperCase() + name.slice(1);
    if (groups().indexOf(g) < 0) {
      SPECS.push({ n: g, mem: [parseInt(mem || 256, 10)], market: 90000 });
      for (var i = 0; i < 4; i++) {
        var market = 90000 + ri(-3000, 3000);
        var margin = ri(2000, 12000);
        ROWS.push({
          model: g, group: g, mem: parseInt(mem || 256, 10), market: market,
          price: market - margin, margin: margin, mins: ri(5, 2000),
          time: "12:00", delivery: rnd() < 0.5, status: "Новый",
          url: "https://www.avito.ru/rossiya/telefony?q=" + encodeURIComponent("iphone " + g)
        });
      }
      buildChips();
      $$('[data-f="model"] .chip').forEach(function(c){ c.classList.toggle("on", c.getAttribute("data-v") === "all"); });
      state.model = "all";
      input.value = "";
      renderAll();
      toast("iPhone " + g + " добавлен в наблюдение");
    } else {
      toast("iPhone " + g + " уже в списке");
    }
  }
  $("#addModel").addEventListener("click", addModel);
  $("#newModel").addEventListener("keydown", function(e){ if (e.key === "Enter") addModel(); });

  /* быстрые кнопки */
  $("#addModelQuick").addEventListener("click", function(){ switchTab("models"); setTimeout(function(){ $("#newModel").focus(); }, 60); });
  $("#historyBtn").addEventListener("click", function(){ switchTab("signals"); });
  var pauseBtn = $("#pauseBtn");
  var pauseTimer = null;
  pauseBtn.addEventListener("click", function(){
    state.paused = !state.paused;
    var st = $(".status");
    if (state.paused) {
      var left = 3600;
      st.innerHTML = "<s style='background:#ffb020;box-shadow:0 0 12px #ffb020'></s><span id='pauseLeft'>Пауза · 60:00</span>";
      pauseBtn.textContent = "Продолжить";
      pauseTimer = setInterval(function(){
        left--;
        if (left <= 0) { clearInterval(pauseTimer); state.paused = false; pauseBtn.textContent = "Пауза на час"; renderBot(); return; }
        var m = Math.floor(left / 60), s = left % 60;
        var el = $("#pauseLeft");
        if (el) el.textContent = "Пауза · " + m + ":" + ("0" + s).slice(-2);
      }, 1000);
      toast("Мониторинг на паузе — 1 час");
    } else {
      clearInterval(pauseTimer);
      renderBot();
      pauseBtn.textContent = "Пауза на час";
      toast("Мониторинг возобновлён");
    }
  });
  function renderBot(){
    var st = $(".status");
    if (!state.paused) st.innerHTML = "<s></s>Сканирует";
  }

  /* настройки */
  $("#setNotify").addEventListener("click", function(){
    state.notify = !state.notify;
    this.classList.toggle("on", state.notify);
  });
  $("#saveSettings").addEventListener("click", function(){
    var v = parseInt($("#setMargin").value, 10) || 0;
    var every = $("#setEvery").value;
    $("#settingsState").textContent = "сохранено";
    toast("Сохранено: маржа от " + fmt(v) + " ₽, скан каждые " + every + " с");
  });

  /* старт */
  buildChips();
  renderAll();
  var h = location.hash.replace("#", "");
  if (h.indexOf("panel-") === 0) switchTab(h.slice(6)); else switchTab("finds");
})();
</script>
"""


def patch_cabinet():
    t = io.open(CAB, encoding="utf-8").read()

    t = t.replace("</style>", CSS_ADD + "</style>", 1)
    assert TABS_OLD in t, "tabs anchor"
    t = t.replace(TABS_OLD, TABS_NEW, 1)

    t, n1 = re.subn(r'    <aside class="col side-l">.*?\n    </aside>', ASIDE_NEW, t, count=1, flags=re.S)
    assert n1 == 1, "aside"
    t, n2 = re.subn(r'    <main class="mid">.*?\n    </main>', MAIN_NEW, t, count=1, flags=re.S)
    assert n2 == 1, "main"
    t, n3 = re.subn(r'      <div class="quick">.*?\n      </div>', QUICK_NEW, t, count=1, flags=re.S)
    assert n3 == 1, "quick"

    t = t.replace('      <div class="status"><s></s>Сканирует</div>',
                  '      <div class="status"><s></s>Сканирует</div>', 1)
    t = t.replace("</body>", JS + "</body>", 1)
    io.open(CAB, "w", encoding="utf-8").write(t)
    print("cabinet patched", len(t))


def patch_index():
    t = io.open(IDX, encoding="utf-8").read()

    t = t.replace('<a class="sec-link" href="#">Все находки →</a>',
                  '<a class="sec-link" href="cabinet.html">Все находки →</a>', 1)

    t = t.replace('<span class="chip on">Все</span><span class="chip">Есть</span><span class="chip">Нет</span>',
                  '<span class="chip on" data-v="all">Все</span><span class="chip" data-v="yes">Есть</span><span class="chip" data-v="no">Нет</span>', 1)
    t = t.replace('<span class="df-note">фильтр заработает после запуска</span>',
                  '<span class="df-note">фильтр по доставке Авито</span>', 1)

    old_contacts = '<ul><li><a href="https://t.me/perekypibot" target="_blank" rel="noopener">@perekypibot — бот</a></li><li><a href="https://t.me/Aalishhechka" target="_blank" rel="noopener">@Aalishhechka — связь</a></li><li><a href="#">Вся Россия</a></li><li><a href="#">Демо по запросу</a></li></ul>'
    new_contacts = '<ul><li><a href="https://t.me/perekypibot" target="_blank" rel="noopener">@perekypibot — бот</a></li><li><a href="https://t.me/Aalishhechka" target="_blank" rel="noopener">@Aalishhechka — связь</a></li><li>Вся Россия, без выбора города</li></ul>'
    assert old_contacts in t, "contacts"
    t = t.replace(old_contacts, new_contacts, 1)

    old_docs = '<ul><li><a href="#">Политика данных</a></li><li><a href="#">Условия сервиса</a></li><li><a href="#">Оферта</a></li></ul>'
    new_docs = '<ul><li><a href="legal.html#privacy">Политика данных</a></li><li><a href="legal.html#terms">Условия сервиса</a></li><li><a href="legal.html#offer">Оферта</a></li></ul>'
    assert old_docs in t, "docs"
    t = t.replace(old_docs, new_docs, 1)

    filter_js = """
/* фильтр «Авито Доставка» на главной */
(function(){
  var chips = [].slice.call(document.querySelectorAll('.dfilter .chip'));
  var rows = [].slice.call(document.querySelectorAll('.finds .row')).filter(function(r){ return !r.classList.contains('hd'); });
  chips.forEach(function(chip){
    chip.addEventListener('click', function(){
      chips.forEach(function(c){ c.classList.toggle('on', c === chip); });
      var v = chip.getAttribute('data-v');
      rows.forEach(function(r){
        var cell = r.querySelector('.deliv');
        var has = cell ? cell.textContent.trim() === 'Есть' : false;
        var show = v === 'all' || (v === 'yes' && has) || (v === 'no' && !has);
        r.style.display = show ? '' : 'none';
      });
    });
  });
})();
"""
    t = t.replace("</script>\n</body>", filter_js + "</script>\n</body>", 1)
    io.open(IDX, "w", encoding="utf-8").write(t)
    print("index patched", len(t))


patch_cabinet()
patch_index()
