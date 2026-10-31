import io

P = r"D:\tg\site\cabinet.html"
t = io.open(P, encoding="utf-8").read()

old = """      <div class="fgroup">
        <div class="t">Маржа от</div>
        <input class="rng" id="margin" type="range" min="0" max="15000" step="500" value="0" aria-label="Минимальная маржа">
        <div class="fval"><span>0 ₽</span><span id="mval">0 ₽</span><span>15 000 ₽</span></div>
      </div>"""
new = """      <div class="fgroup">
        <div class="t">Маржа от <b id="mval">0 ₽</b></div>
        <input class="rng" id="margin" type="range" min="0" max="15000" step="500" value="0" aria-label="Минимальная маржа">
        <div class="fval"><span>0 ₽</span><span>15 000 ₽</span></div>
      </div>"""
assert old in t, "margin group"
t = t.replace(old, new, 1)

old = """.fgroup .t{font-family:var(--mono);font-size:8px;letter-spacing:.18em;text-transform:uppercase;color:var(--mut2);margin-bottom:9px}"""
new = """.fgroup .t{font-family:var(--mono);font-size:8px;letter-spacing:.18em;text-transform:uppercase;color:var(--mut2);margin-bottom:9px;display:flex;justify-content:space-between;align-items:baseline}
.fgroup .t b{color:#e8e8ef;font-weight:400;letter-spacing:.06em;font-size:10px}"""
assert old in t, "fgroup css"
t = t.replace(old, new, 1)

io.open(P, "w", encoding="utf-8").write(t)
print("ok")
