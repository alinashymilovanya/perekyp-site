"""Правки демо-логики кабинета: круглые цены, живые счётчики, порядок моделей."""

import io

P = r"D:\tg\site\cabinet.html"
t = io.open(P, encoding="utf-8").read()

# 1. круглые цены и маржа из них
old = """      var margin = ri(1200, 16500);
      var market = sp.market + ri(-2500, 2500);"""
new = """      var margin = ri(1200, 16500);
      var market = Math.round((sp.market + ri(-2500, 2500)) / 500) * 500;
      var price = Math.round((market - margin) / 500) * 500;"""
assert old in t
t = t.replace(old, new, 1)

old = """        price: market - margin,
        margin: margin,"""
new = """        price: price,
        margin: market - price,"""
assert old in t
t = t.replace(old, new, 1)

# 2. сигналы = отправленные (статус «Новый»)
old = """    var sig = rows.filter(function(r){ return r.margin >= 500; }).length;
    $("#kSignals").textContent = sig;
    $("#kSignalsD").textContent = "порог 500 ₽";"""
new = """    var sig = rows.filter(function(r){ return r.status === "Новый"; }).length;
    $("#kSignals").textContent = sig;
    $("#kSignalsD").textContent = "отправлено в чат";"""
assert old in t
t = t.replace(old, new, 1)

# 3. порядок моделей в чипах
old = """  function groups(){
    var seen = [], out = [];
    ROWS.forEach(function(r){ if (seen.indexOf(r.group) < 0) { seen.push(r.group); out.push(r.group); } });
    return out;
  }"""
new = """  var ORDER = ["12","13","14","14 Pro","15","15 Pro","15 Pro Max","16 Pro"];
  function groups(){
    var out = [];
    ROWS.forEach(function(r){ if (out.indexOf(r.group) < 0) out.push(r.group); });
    return out.sort(function(a, b){
      var ia = ORDER.indexOf(a), ib = ORDER.indexOf(b);
      if (ia < 0) ia = 99 + a.charCodeAt(0);
      if (ib < 0) ib = 99 + b.charCodeAt(0);
      return ia - ib;
    });
  }"""
assert old in t
t = t.replace(old, new, 1)

# 4. живые счётчики «Сегодня» в правой колонке
old = """      <div class="h" style="margin-top:26px"><span>Сегодня</span><b>37</b></div>
      <div class="rows">
        <div><span>Новых находок</span><b>37</b></div>
        <div><span>Лучшая маржа</span><b>+14 500 ₽</b></div>
        <div><span>Уведомлений</span><b>19</b></div>
      </div>"""
new = """      <div class="h" style="margin-top:26px"><span>Сегодня</span><b id="todayCount">0</b></div>
      <div class="rows">
        <div><span>Новых находок</span><b id="todayFinds">0</b></div>
        <div><span>Лучшая маржа</span><b id="todayBest">—</b></div>
        <div><span>Уведомлений</span><b id="todayNotif">0</b></div>
      </div>"""
assert old in t
t = t.replace(old, new, 1)

old = """    var names = { 24: "24 часа", 7: "7 дней", 30: "30 дней" };"""
new = """    var today = ROWS.filter(function(r){ return r.mins <= 1440; });
    var best = today.reduce(function(m, r){ return Math.max(m, r.margin); }, 0);
    $("#todayCount").textContent = today.length;
    $("#todayFinds").textContent = today.length;
    $("#todayBest").textContent = best ? "+" + fmt(best) + " ₽" : "—";
    $("#todayNotif").textContent = today.filter(function(r){ return r.status === "Новый"; }).length;

    var names = { 24: "24 часа", 7: "7 дней", 30: "30 дней" };"""
assert old in t
t = t.replace(old, new, 1)

# 5. при загрузке не прыгать к якорю
old = """  if (h.indexOf("panel-") === 0) switchTab(h.slice(6)); else switchTab("finds");"""
new = """  if (h.indexOf("panel-") === 0) switchTab(h.slice(6)); else switchTab("finds");
  window.scrollTo(0, 0);"""
assert old in t
t = t.replace(old, new, 1)

io.open(P, "w", encoding="utf-8").write(t)
print("ok", len(t))
