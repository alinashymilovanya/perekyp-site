import io

P = r"D:\tg\site\cabinet.html"
t = io.open(P, encoding="utf-8").read()

old = """      return '<div class="srow"><span class="t">' + r.time + '</span><span class="m">iPhone ' + r.model + ' ' + r.mem +
        '</span><span class="c" style="color:#8b8b95;font-family:var(--mono);font-size:9px">' + fmt(r.price) +
        ' ₽</span><span class="v">+' + fmt(r.margin) + ' ₽</span></div>';"""

new = """      return '<div class="srow"><span class="t">' + r.time + '</span><span class="m">iPhone ' + r.model + ' ' + r.mem +
        '</span><span class="c">' + fmt(r.price) + ' ₽</span><span class="v">+' + fmt(r.margin) +
        ' ₽</span><a class="avito" href="' + r.url + '" target="_blank" rel="noopener">AVITO ↗</a></div>';"""

assert old in t, "signals block not found"
t = t.replace(old, new, 1)
io.open(P, "w", encoding="utf-8").write(t)
print("ok")
