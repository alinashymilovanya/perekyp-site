import io

P = r"D:\tg\site\cabinet.html"
t = io.open(P, encoding="utf-8").read()

old = """    if (location.hash !== "#panel-" + name) history.replaceState(null, "", "#panel-" + name);"""
assert old in t, "replaceState"
t = t.replace(old, "", 1)

old = """  var h = location.hash.replace("#", "");
  if (h.indexOf("panel-") === 0) switchTab(h.slice(6)); else switchTab("finds");
  window.scrollTo(0, 0);
  window.addEventListener("hashchange", function(){
    var hh = location.hash.replace("#", "");
    if (hh.indexOf("panel-") === 0) switchTab(hh.slice(6));
  });"""
new = """  var tab = new URLSearchParams(location.search).get("tab");
  switchTab(tab && document.getElementById("panel-" + tab) ? tab : "finds");
  window.scrollTo(0, 0);"""
assert old in t, "init block"
t = t.replace(old, new, 1)

io.open(P, "w", encoding="utf-8").write(t)
print("ok")
