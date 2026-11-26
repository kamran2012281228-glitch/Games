# -*- coding: utf-8 -*-
"""
ПК Пульт — управление компьютером с iPhone (или любого телефона) по Wi-Fi.
Запуск: start.bat (сам установит Python и библиотеки при первом запуске).
"""
import io, json, os, secrets, socket, sys, threading, time, subprocess, math
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs

IS_WIN = os.name == "nt"

if IS_WIN:
    import ctypes
    from ctypes import wintypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

try:
    import mss
    from PIL import Image, ImageDraw
    MSS = getattr(mss, "MSS", None) or mss.mss
except ImportError:
    print("Не установлены библиотеки. Запустите start.bat")
    input("Enter для выхода...")
    sys.exit(1)

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0
    KEYS = set(pyautogui.KEYBOARD_KEYS)
except Exception:
    pyautogui = None
    KEYS = set()

BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE_DIR, "config.json")


# ---------------------------------------------------------------- настройки
def load_cfg():
    cfg = {}
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        pass
    if not (isinstance(cfg.get("pin"), str) and cfg["pin"].isdigit() and len(cfg["pin"]) >= 4):
        cfg["pin"] = "%06d" % secrets.randbelow(10 ** 6)
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(32)
    cfg.setdefault("port", 8765)
    save_cfg(cfg)
    return cfg


def save_cfg(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Не удалось сохранить config.json:", e)


CFG = load_cfg()

# ---------------------------------------------------------------- ввод
LOCK = threading.Lock()

if IS_WIN:
    user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    BTN = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}

    def _send(inputs):
        n = len(inputs)
        if not n:
            return
        arr = (INPUT * n)(*inputs)
        user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))

    def _mouse(flags, data=0):
        i = INPUT(type=0)
        i.u.mi = MOUSEINPUT(0, 0, int(data) & 0xFFFFFFFF, flags, 0, 0)
        _send([i])

    def cursor_pos():
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def set_pos(x, y):
        user32.SetCursorPos(int(round(x)), int(round(y)))

    def btn_down(b):
        _mouse(BTN[b][0])

    def btn_up(b):
        _mouse(BTN[b][1])

    def scroll(dy, dx):
        if dy:
            _mouse(0x0800, dy)
        if dx:
            _mouse(0x01000, dx)

    def _vk(vk, up=False):
        i = INPUT(type=1)
        i.u.ki = KEYBDINPUT(vk, 0, 2 if up else 0, 0, 0)
        return i

    def type_text(s):
        inputs = []
        for ch in s:
            if ch == "\n":
                inputs += [_vk(0x0D), _vk(0x0D, True)]
                continue
            if ch == "\t":
                inputs += [_vk(0x09), _vk(0x09, True)]
                continue
            data = ch.encode("utf-16-le")
            for j in range(0, len(data), 2):
                code = int.from_bytes(data[j:j + 2], "little")
                for fl in (0x0004, 0x0004 | 0x0002):  # KEYEVENTF_UNICODE, +KEYUP
                    i = INPUT(type=1)
                    i.u.ki = KEYBDINPUT(0, code, fl, 0, 0)
                    inputs.append(i)
        _send(inputs)
else:
    BTN = {"left": 1, "right": 1, "middle": 1}

    def cursor_pos():
        return tuple(pyautogui.position()) if pyautogui else (0, 0)

    def set_pos(x, y):
        pyautogui and pyautogui.moveTo(int(x), int(y))

    def btn_down(b):
        pyautogui and pyautogui.mouseDown(button=b)

    def btn_up(b):
        pyautogui and pyautogui.mouseUp(button=b)

    def scroll(dy, dx):
        if pyautogui and dy:
            pyautogui.scroll(int(dy / 40) or (1 if dy > 0 else -1))

    def type_text(s):
        pyautogui and pyautogui.write(s)


MONS = []


def refresh_monitors():
    global MONS
    try:
        with MSS() as s:
            MONS = [dict(m) for m in s.monitors]
    except Exception:
        MONS = []
    return MONS


def mon_geom(m):
    mons = MONS or [{"left": 0, "top": 0, "width": 1920, "height": 1080}] * 2
    if not (1 <= m < len(mons)):
        m = 1 if len(mons) > 1 else 0
    return mons[m]


def norm_pos(m):
    g = mon_geom(m)
    x, y = cursor_pos()
    return {"x": (x - g["left"]) / max(1, g["width"]), "y": (y - g["top"]) / max(1, g["height"])}


def num(v, lo, hi, d=0.0):
    try:
        v = float(v)
        if not math.isfinite(v):
            return d
        return max(lo, min(hi, v))
    except Exception:
        return d


def system_action(a):
    if not IS_WIN:
        return
    if a == "lock":
        user32.LockWorkStation()
    elif a == "sleep":
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    elif a == "shutdown":
        subprocess.Popen(["shutdown", "/s", "/t", "10"])
    elif a == "restart":
        subprocess.Popen(["shutdown", "/r", "/t", "10"])
    elif a == "cancel":
        subprocess.Popen(["shutdown", "/a"])


def do_events(m, evs):
    g = mon_geom(m)
    with LOCK:
        for ev in evs[:500]:
            if not isinstance(ev, dict):
                continue
            t = ev.get("t")
            try:
                if t == "move":
                    x, y = cursor_pos()
                    set_pos(x + num(ev.get("dx"), -5000, 5000), y + num(ev.get("dy"), -5000, 5000))
                elif t == "abs":
                    set_pos(g["left"] + num(ev.get("x"), 0, 1) * (g["width"] - 1),
                            g["top"] + num(ev.get("y"), 0, 1) * (g["height"] - 1))
                elif t in ("click", "down", "up"):
                    b = ev.get("b", "left")
                    if b not in BTN:
                        continue
                    if t == "down":
                        btn_down(b)
                    elif t == "up":
                        btn_up(b)
                    else:
                        for _ in range(int(num(ev.get("n", 1), 1, 3, 1))):
                            btn_down(b)
                            btn_up(b)
                elif t == "scroll":
                    scroll(int(num(ev.get("dy"), -12000, 12000)), int(num(ev.get("dx"), -12000, 12000)))
                elif t == "text":
                    s = str(ev.get("v", ""))[:4000]
                    if s:
                        type_text(s)
                elif t == "key" and pyautogui:
                    k = str(ev.get("k", "")).lower()
                    if k in KEYS:
                        pyautogui.press(k)
                elif t == "hotkey" and pyautogui:
                    ks = [str(k).lower() for k in (ev.get("k") or [])][:5]
                    if ks and all(k in KEYS for k in ks):
                        pyautogui.hotkey(*ks)
                elif t == "sys":
                    system_action(ev.get("a"))
            except Exception as e:
                print("Ошибка ввода:", e)


# ---------------------------------------------------------------- картинки
ARROW = [(0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12)]


def draw_cursor(img, mon, scale):
    try:
        x, y = cursor_pos()
    except Exception:
        return
    cx, cy = (x - mon["left"]) * scale, (y - mon["top"]) * scale
    if not (-30 < cx < img.width + 30 and -30 < cy < img.height + 30):
        return
    k = max(1.0, img.width / 800)
    pts = [(cx + px * k, cy + py * k) for px, py in ARROW]
    d = ImageDraw.Draw(img)
    d.polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))
    d.line(pts + [pts[0]], fill=(0, 0, 0), width=max(1, int(k)))


def make_icon(size=512):
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        f = y / size
        d.line([(0, y), (size, y)], fill=(int(18 + 20 * f), int(28 + 50 * f), int(60 + 110 * f)))
    s = size / 512
    W = (245, 247, 250)
    d.rounded_rectangle((96 * s, 130 * s, 416 * s, 330 * s), radius=int(28 * s), fill=W)
    d.rounded_rectangle((116 * s, 150 * s, 396 * s, 310 * s), radius=int(14 * s), fill=(47, 107, 255))
    d.rectangle((236 * s, 328 * s, 276 * s, 378 * s), fill=W)
    d.rounded_rectangle((176 * s, 370 * s, 336 * s, 396 * s), radius=int(12 * s), fill=W)
    ar = [(230 + px * 5.2, 172 + py * 5.2) for px, py in ARROW]
    d.polygon([(a * s, b * s) for a, b in ar], fill=W)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


ICON = make_icon()

# ---------------------------------------------------------------- HTML
HEAD = r"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ПК Пульт"><meta name="theme-color" content="#0b0d12">
<link rel="apple-touch-icon" href="/icon.png"><link rel="manifest" href="/manifest.json"><title>ПК Пульт</title>"""

LOGIN_HTML = HEAD + r"""<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#0b0d12;color:#e8ecf3;font:16px -apple-system,system-ui,sans-serif}
.c{max-width:340px;margin:0 auto;padding:calc(18vh + env(safe-area-inset-top)) 20px 20px;text-align:center}
img{width:84px;height:84px;border-radius:20px}h1{font-size:24px;margin:16px 0 6px}p{color:#8a93a6;margin:0 0 22px;font-size:14px}
input{width:100%;font-size:28px;letter-spacing:8px;text-align:center;padding:14px;border-radius:14px;border:1px solid #2a3040;background:#151922;color:#fff;outline:none}
button{width:100%;margin-top:12px;padding:15px;border:0;border-radius:14px;background:#2f6bff;color:#fff;font-size:17px;font-weight:600}
#e{color:#ff6b6b;min-height:22px;margin-top:12px;font-size:14px}
</style></head><body><div class="c"><img src="/icon.png" alt=""><h1>ПК Пульт</h1>
<p>Введите PIN, который показан в окне программы на компьютере</p>
<input id="p" type="tel" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="••••••">
<button id="b">Войти</button><div id="e"></div></div>
<script>
const p=document.getElementById('p'),e=document.getElementById('e');
function go(){fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:p.value.trim()})})
.then(r=>{if(r.ok)location.reload();else if(r.status===429)e.textContent='Слишком много попыток. Подождите минуту.';else{e.textContent='Неверный PIN';p.value=''}})
.catch(()=>e.textContent='Нет связи с компьютером')}
document.getElementById('b').onclick=go;p.addEventListener('keydown',ev=>{if(ev.key==='Enter')go()});
</script></body></html>"""

APP_HTML = HEAD + r"""<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
html,body{margin:0;height:100%;background:#000;color:#e8ecf3;font:15px -apple-system,system-ui,sans-serif;overflow:hidden;position:fixed;inset:0}
#app{position:fixed;left:0;right:0;top:0;height:var(--vh,100%);display:flex;flex-direction:column;padding:env(safe-area-inset-top) env(safe-area-inset-right) 0 env(safe-area-inset-left);background:#11141b}
.bar{display:flex;gap:6px;padding:6px;background:#11141b}
.bar button{flex:1}
button{background:#1f2430;color:#e8ecf3;border:0;border-radius:10px;padding:10px 4px;font:inherit;font-size:14px;touch-action:manipulation;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
button.on{background:#2f6bff}button:active{filter:brightness(1.5)}
#view{flex:1;position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center;touch-action:none;background:#000;min-height:80px}
#scr{max-width:100%;max-height:100%;display:block;transform-origin:center center;pointer-events:none;transition:transform .1s linear}
#status{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);background:rgba(20,24,34,.9);padding:10px 16px;border-radius:12px;font-size:14px;color:#cfd5e1;text-align:center;pointer-events:none}
#keys{display:grid;grid-template-columns:repeat(6,1fr);gap:5px;padding:6px;max-height:42%;overflow-y:auto;touch-action:pan-y;-webkit-overflow-scrolling:touch}
#keys button{padding:10px 2px;font-size:13px}#keys .w{grid-column:span 2}
#keys .sep{grid-column:1/-1;font-size:11px;color:#7d8597;padding:4px 2px 0;text-transform:uppercase;letter-spacing:.5px}
#bottom{padding-bottom:calc(6px + env(safe-area-inset-bottom))}#bottom button{padding:14px 4px}
.hidden{display:none!important}
#kb{position:fixed;left:0;bottom:0;width:2px;height:2px;opacity:0;font-size:16px;border:0;padding:0;resize:none;-webkit-user-select:text;user-select:text}
#settings{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:flex-end;z-index:10}
#settings .card{background:#151922;width:100%;border-radius:18px 18px 0 0;padding:16px 18px calc(16px + env(safe-area-inset-bottom));max-height:88%;overflow:auto;touch-action:pan-y}
#settings h3{margin:0 0 6px}label{display:flex;justify-content:space-between;margin:14px 0 4px;color:#aab2c0;font-size:13px}
input[type=range]{width:100%}select{width:100%;padding:10px;border-radius:10px;background:#1f2430;color:#fff;border:0;font-size:15px}
.help{font-size:13px;color:#8a93a6;line-height:1.5;margin-top:14px}.help b{color:#cfd5e1}
.row{display:flex;gap:8px;margin-top:16px}.row button{flex:1;padding:13px}.pri{background:#2f6bff}
</style></head><body><div id="app">
<div class="bar"><button id="bMode"></button><button id="bKb">⌨️ Текст</button><button id="bKeys">⌘ Клавиши</button><button id="bZoom">1×</button><button id="bSet">⚙️</button></div>
<div id="view"><img id="scr" alt=""><div id="status">Подключение…</div></div>
<div id="keys" class="hidden"></div>
<div class="bar" id="bottom"><button id="bL">Левая</button><button id="bM">Средняя</button><button id="bR">Правая</button></div>
</div>
<textarea id="kb" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"></textarea>
<div id="settings" class="hidden"><div class="card">
<h3>Настройки</h3>
<label>Качество картинки <span id="vq"></span></label><input id="sq" type="range" min="20" max="90" step="5">
<label>Разрешение <span id="vs"></span></label><input id="ss" type="range" min="0.3" max="1" step="0.05">
<label>Кадров в секунду <span id="vf"></span></label><input id="sf" type="range" min="3" max="30" step="1">
<label>Скорость курсора <span id="vn"></span></label><input id="sn" type="range" min="0.5" max="4" step="0.1">
<label>Монитор</label><select id="sm"></select>
<div class="help"><b>Тачпад:</b> водите пальцем — курсор двигается. Тап — клик, тап двумя пальцами — правый клик, тремя — средний. Двумя пальцами вверх/вниз — прокрутка. Двойной тап с удержанием и ведением — перетаскивание (или держите кнопку «Левая» внизу).<br>
<b>Касание:</b> тап по месту на экране — клик туда, долгое нажатие — правый клик, ведение пальцем — перетаскивание.<br>
<b>Ctrl/Alt/Shift/Win</b> во вкладке «Клавиши» залипают до следующей нажатой клавиши или буквы.</div>
<div class="row"><button id="bRe">⟳ Переподключить</button><button id="bOk" class="pri">Готово</button></div>
</div></div>
<script>
const $=id=>document.getElementById(id);
const LS={get(k,d){try{const v=localStorage.getItem(k);return v===null?d:JSON.parse(v)}catch(e){return d}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
const S=Object.assign({q:60,s:0.6,fps:15,sens:1.6,mode:'pad',mon:1},LS.get('rc',{}));
const img=$('scr'),view=$('view'),kb=$('kb');
let queue=[],busy=false,acc={dx:0,dy:0,sx:0,sy:0},cur={x:.5,y:.5},zoom=1,T=null,lastTap=0;

/* ---------- отправка событий ---------- */
function flushAcc(){const dx=Math.trunc(acc.dx),dy=Math.trunc(acc.dy);if(dx||dy){queue.push({t:'move',dx,dy});acc.dx-=dx;acc.dy-=dy}
 const sx=Math.trunc(acc.sx),sy=Math.trunc(acc.sy);if(sx||sy){queue.push({t:'scroll',dx:sx,dy:sy});acc.sx-=sx;acc.sy-=sy}}
function pending(){return queue.length||Math.abs(acc.dx)>=1||Math.abs(acc.dy)>=1||Math.abs(acc.sx)>=1||Math.abs(acc.sy)>=1}
function push(ev){flushAcc();const l=queue[queue.length-1];
 if(l&&ev.t==='abs'&&l.t==='abs'){l.x=ev.x;l.y=ev.y}else if(l&&ev.t==='text'&&l.t==='text'){l.v+=ev.v}else queue.push(ev);kick()}
function kick(){if(busy)return;flushAcc();if(!queue.length)return;busy=true;const batch=queue;queue=[];
 fetch('/api',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({m:S.mon,ev:batch})})
 .then(r=>{if(r.status===401){location.reload();return null}return r.json()})
 .then(j=>{if(j&&j.pos){cur=j.pos;if(S.mode==='pad'||!T)applyView()}})
 .catch(()=>setStatus('Нет связи с ПК'))
 .finally(()=>{busy=false;if(pending())kick()})}

/* ---------- экран ---------- */
let gotFrame=false,retryT=null;
function setStatus(t){const s=$('status');if(t){s.textContent=t;s.classList.remove('hidden')}else s.classList.add('hidden')}
function startStream(){clearTimeout(retryT);gotFrame=false;setStatus('Подключение…');
 img.src='/stream?m='+S.mon+'&q='+S.q+'&s='+S.s+'&f='+S.fps+'&_='+Date.now()}
img.onload=()=>{if(!gotFrame){gotFrame=true;setStatus('');applyView()}};
img.onerror=()=>{setStatus('Нет изображения, переподключение…');
 fetch('/api/pos').then(r=>{if(r.status===401)location.reload()}).catch(()=>{});
 clearTimeout(retryT);retryT=setTimeout(startStream,1500)};
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')startStream();else{clearTimeout(retryT);img.removeAttribute('src')}});

function applyView(){if(zoom===1){img.style.transform='';return}
 const W=img.offsetWidth,H=img.offsetHeight,CW=view.clientWidth,CH=view.clientHeight;if(!W||!H)return;
 const lx=Math.max(0,(W*zoom-CW)/2),ly=Math.max(0,(H*zoom-CH)/2);
 const tx=Math.max(-lx,Math.min(lx,(0.5-cur.x)*W*zoom)),ty=Math.max(-ly,Math.min(ly,(0.5-cur.y)*H*zoom));
 img.style.transform='translate('+tx+'px,'+ty+'px) scale('+zoom+')'}
function norm(x,y){const r=img.getBoundingClientRect();if(!r.width||!r.height)return null;
 return {x:Math.max(0,Math.min(1,(x-r.left)/r.width)),y:Math.max(0,Math.min(1,(y-r.top)/r.height))}}
setInterval(()=>{if(zoom>1&&!busy&&!T&&document.visibilityState==='visible')
 fetch('/api/pos').then(r=>r.json()).then(j=>{if(j.pos){cur=j.pos;applyView()}}).catch(()=>{})},600);

/* ---------- жесты ---------- */
const pts=tl=>Array.from(tl).map(t=>({x:t.clientX,y:t.clientY}));
const avg=(a,k)=>a.reduce((s,p)=>s+p[k],0)/a.length;
view.addEventListener('touchstart',e=>{e.preventDefault();const p=pts(e.touches);
 if(!T){T={start:Date.now(),x0:p[0].x,y0:p[0].y,last:p,max:p.length,moved:false,drag:false,handled:false,maybeDrag:p.length===1&&Date.now()-lastTap<350};
  if(S.mode==='direct'&&p.length===1){T.hold=setTimeout(()=>{if(T&&!T.moved&&T.max===1){const n=norm(T.x0,T.y0);if(n){push({t:'abs',x:n.x,y:n.y});push({t:'click',b:'right'});T.handled=true}}},550)}}
 else{T.max=Math.max(T.max,p.length);T.last=p;clearTimeout(T.hold)}},{passive:false});
view.addEventListener('touchmove',e=>{e.preventDefault();if(!T)return;const p=pts(e.touches);
 if(p.length===1&&T.max===1){const dx=p[0].x-T.last[0].x,dy=p[0].y-T.last[0].y;
  if(!T.moved&&Math.hypot(p[0].x-T.x0,p[0].y-T.y0)>7){T.moved=true;clearTimeout(T.hold);
   if(S.mode==='direct'){const n=norm(T.x0,T.y0);if(n){push({t:'abs',x:n.x,y:n.y});push({t:'down',b:'left'});T.drag=true}}
   else if(T.maybeDrag){push({t:'down',b:'left'});T.drag=true}}
  if(T.moved){if(S.mode==='direct'){const n=norm(p[0].x,p[0].y);if(n)push({t:'abs',x:n.x,y:n.y})}
   else{const d=Math.hypot(dx,dy),f=S.sens*Math.min(1+d*0.08,3.5);acc.dx+=dx*f;acc.dy+=dy*f;kick()}}}
 else if(p.length>=2&&T.last.length>=2){const dy=avg(p,'y')-avg(T.last,'y'),dx=avg(p,'x')-avg(T.last,'x');
  if(Math.hypot(avg(p,'x')-T.x0,avg(p,'y')-T.y0)>10||Math.abs(dy)>2)T.moved=true;acc.sy+=dy*4;acc.sx+=-dx*4;kick()}
 T.last=p},{passive:false});
function endTouch(e,cancel){e.preventDefault();if(!T)return;if(e.touches.length>0){T.last=pts(e.touches);return}
 clearTimeout(T.hold);const dt=Date.now()-T.start;
 if(T.drag)push({t:'up',b:'left'});
 else if(!cancel&&!T.moved&&!T.handled&&dt<500){
  if(T.max===1){if(S.mode==='direct'){const n=norm(T.x0,T.y0);if(n){push({t:'abs',x:n.x,y:n.y});push({t:'click',b:'left'})}}
   else push({t:'click',b:'left'});lastTap=T.maybeDrag?0:Date.now()}
  else if(T.max===2)push({t:'click',b:'right'});else push({t:'click',b:'middle'})}
 T=null;if(S.mode==='direct')applyView()}
view.addEventListener('touchend',e=>endTouch(e,false),{passive:false});
view.addEventListener('touchcancel',e=>endTouch(e,true),{passive:false});

/* ---------- кнопки мыши ---------- */
[['bL','left'],['bM','middle'],['bR','right']].forEach(([id,b])=>{const el=$(id);
 el.addEventListener('touchstart',e=>{e.preventDefault();el.classList.add('on');push({t:'down',b})},{passive:false});
 el.addEventListener('touchend',e=>{e.preventDefault();el.classList.remove('on');push({t:'up',b})},{passive:false});
 el.addEventListener('touchcancel',e=>{el.classList.remove('on');push({t:'up',b})});
 el.addEventListener('mousedown',()=>push({t:'down',b}));el.addEventListener('mouseup',()=>push({t:'up',b}))});

/* ---------- клавиатура ---------- */
const MODS={ctrl:false,alt:false,shift:false,win:false};
const RU='йцукенгшщзхъфывапролджэячсмитьбю',EN="qwertyuiop[]asdfghjkl;'zxcvbnm,.";
const latin=c=>{c=c.toLowerCase();const i=RU.indexOf(c);return i>=0?EN[i]:c};
const anyMod=()=>Object.values(MODS).some(Boolean);
function clearMods(){for(const k in MODS)MODS[k]=false;document.querySelectorAll('[data-mod]').forEach(b=>b.classList.remove('on'))}
function sendKey(k){if(anyMod()){push({t:'hotkey',k:Object.keys(MODS).filter(m=>MODS[m]).concat(k)});clearMods()}else push({t:'key',k})}
function sendCombo(c){const ks=Object.keys(MODS).filter(m=>MODS[m]);c.split('+').forEach(k=>{if(!ks.includes(k))ks.push(k)});push({t:'hotkey',k:ks});clearMods()}
function typeText(s){s.split('\n').forEach((p,i)=>{if(i>0)sendKey('enter');if(!p)return;
 if(anyMod()){const ch=Array.from(p);sendKey(latin(ch[0]));const rest=ch.slice(1).join('');if(rest)push({t:'text',v:rest})}else push({t:'text',v:p})})}
function resetKb(){kb.value=' ';try{kb.setSelectionRange(1,1)}catch(e){}}
kb.addEventListener('input',()=>{const v=kb.value;if(v.length<1)sendKey('backspace');else{const add=v.slice(1);if(add)typeText(add)}resetKb()});
kb.addEventListener('focus',()=>$('bKb').classList.add('on'));kb.addEventListener('blur',()=>$('bKb').classList.remove('on'));
$('bKb').onclick=()=>{if(document.activeElement===kb)kb.blur();else{resetKb();kb.focus()}};

const KEYS=[['sep','Модификаторы (залипают)'],['m','Ctrl','ctrl'],['m','Alt','alt'],['m','Shift','shift'],['m','Win','win'],['k','Esc','esc'],['k','Tab','tab'],
 ['sep','Клавиши'],['k','Enter','enter'],['k','⌫','backspace'],['k','Del','delete'],['k','Home','home'],['k','End','end'],['k','PrtSc','printscreen'],
 ['k','PgUp','pageup'],['k','PgDn','pagedown'],['k','←','left'],['k','↑','up'],['k','↓','down'],['k','→','right'],
 ...Array.from({length:12},(_,i)=>['k','F'+(i+1),'f'+(i+1)]),
 ['sep','Сочетания'],['h','Копир.','ctrl+c'],['h','Вставить','ctrl+v'],['h','Вырезать','ctrl+x'],['h','Отменить','ctrl+z'],['h','Выдел. всё','ctrl+a'],['h','Сохранить','ctrl+s'],
 ['h','Alt+Tab','alt+tab'],['h','Alt+F4','alt+f4'],['h','Win+Tab','win+tab'],['h','Пуск','win'],['h','Язык','alt+shift'],['h','Win+R','win+r'],
 ['h','Рабочий стол','win+d','w'],['h','Проводник','win+e','w'],['h','Диспетчер задач','ctrl+shift+esc','w'],
 ['sep','Медиа'],['k','🔉','volumedown'],['k','🔊','volumeup'],['k','🔇','volumemute'],['k','⏮','prevtrack'],['k','⏯','playpause'],['k','⏭','nexttrack'],
 ['sep','Система'],['s','🔒 Блокировать','lock','w'],['s','💤 Сон','sleep','w'],['s','✖ Отмена выкл.','cancel','w'],['s','⏻ Выключить','shutdown','w'],['s','↻ Перезагрузить','restart','w']];
const CONFIRM={shutdown:'Выключить компьютер через 10 секунд?',restart:'Перезагрузить компьютер через 10 секунд?',sleep:'Перевести компьютер в сон?'};
const kp=$('keys');
KEYS.forEach(([t,label,v,w])=>{if(t==='sep'){const d=document.createElement('div');d.className='sep';d.textContent=label;kp.appendChild(d);return}
 const b=document.createElement('button');b.textContent=label;if(w)b.className='w';
 if(t==='m'){b.dataset.mod=v;b.onclick=()=>{MODS[v]=!MODS[v];b.classList.toggle('on',MODS[v])}}
 else if(t==='k')b.onclick=()=>sendKey(v);
 else if(t==='h')b.onclick=()=>sendCombo(v);
 else if(t==='s')b.onclick=()=>{if(!CONFIRM[v]||confirm(CONFIRM[v]))push({t:'sys',a:v})};
 kp.appendChild(b)});
$('bKeys').onclick=()=>{kp.classList.toggle('hidden');$('bKeys').classList.toggle('on',!kp.classList.contains('hidden'));requestAnimationFrame(applyView)};

/* ---------- режимы, зум, настройки ---------- */
function modeLabel(){$('bMode').textContent=S.mode==='pad'?'🖱 Тачпад':'👆 Касание'}
$('bMode').onclick=()=>{S.mode=S.mode==='pad'?'direct':'pad';LS.set('rc',S);modeLabel()};modeLabel();
const ZL=[1,1.5,2,3];$('bZoom').onclick=()=>{zoom=ZL[(ZL.indexOf(zoom)+1)%ZL.length];$('bZoom').textContent=zoom+'×';$('bZoom').classList.toggle('on',zoom>1);
 fetch('/api/pos').then(r=>r.json()).then(j=>{if(j.pos)cur=j.pos;applyView()}).catch(()=>applyView())};
const F=[['sq','vq','q',v=>v],['ss','vs','s',v=>Math.round(v*100)+'%'],['sf','vf','fps',v=>v],['sn','vn','sens',v=>(+v).toFixed(1)]];
F.forEach(([i,o,k,fmt])=>{$(i).value=S[k];$(o).textContent=fmt(S[k]);$(i).oninput=()=>$(o).textContent=fmt($(i).value)});
function loadInfo(){fetch('/api/info').then(r=>r.json()).then(j=>{const sel=$('sm');sel.innerHTML='';
 (j.monitors||[]).forEach(m=>{const o=document.createElement('option');o.value=m.i;o.textContent='Монитор '+m.i+' — '+m.w+'×'+m.h;sel.appendChild(o)});
 if(!(j.monitors||[]).some(m=>m.i==S.mon)&&j.monitors&&j.monitors.length){S.mon=j.monitors[0].i}sel.value=S.mon}).catch(()=>{})}
$('bSet').onclick=()=>{loadInfo();$('settings').classList.remove('hidden')};
$('settings').addEventListener('click',e=>{if(e.target.id==='settings')$('settings').classList.add('hidden')});
$('bOk').onclick=()=>{const old=JSON.stringify([S.q,S.s,S.fps,S.mon]);
 S.q=+$('sq').value;S.s=+$('ss').value;S.fps=+$('sf').value;S.sens=+$('sn').value;if($('sm').value)S.mon=+$('sm').value;LS.set('rc',S);
 $('settings').classList.add('hidden');if(old!==JSON.stringify([S.q,S.s,S.fps,S.mon]))startStream()};
$('bRe').onclick=()=>{$('settings').classList.add('hidden');startStream()};

/* ---------- раскладка под экран и клавиатуру iOS ---------- */
function fit(){const vv=window.visualViewport;const h=vv?vv.height:innerHeight;document.documentElement.style.setProperty('--vh',h+'px');
 $('app').style.top=(vv?vv.offsetTop:0)+'px';requestAnimationFrame(applyView)}
if(window.visualViewport){visualViewport.addEventListener('resize',fit);visualViewport.addEventListener('scroll',fit)}
addEventListener('resize',fit);addEventListener('orientationchange',()=>setTimeout(fit,300));fit();
loadInfo();startStream();
</script></body></html>"""

MANIFEST = json.dumps({
    "name": "ПК Пульт", "short_name": "ПК Пульт", "display": "standalone", "start_url": "/",
    "background_color": "#0b0d12", "theme_color": "#0b0d12",
    "icons": [{"src": "/icon.png", "sizes": "512x512", "type": "image/png"}],
}, ensure_ascii=False).encode("utf-8")

# ---------------------------------------------------------------- сервер
FAILS = {}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PCRemote"

    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send(code, json.dumps(obj), "application/json")

    def authed(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return "rc" in c and secrets.compare_digest(c["rc"].value, CFG["token"])
        except Exception:
            return False

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p == "/":
            return self.send(200, APP_HTML if self.authed() else LOGIN_HTML, "text/html; charset=utf-8")
        if p == "/icon.png":
            return self.send(200, ICON, "image/png")
        if p == "/manifest.json":
            return self.send(200, MANIFEST, "application/manifest+json")
        if not self.authed():
            return self.send(401, "unauthorized")
        if p == "/stream":
            return self.stream(parse_qs(u.query))
        if p == "/api/info":
            mons = refresh_monitors()
            lst = [{"i": i, "w": m["width"], "h": m["height"]} for i, m in enumerate(mons) if i > 0]
            return self.send_json({"monitors": lst, "pos": norm_pos(1)})
        if p == "/api/pos":
            q = parse_qs(u.query)
            m = int(num((q.get("m") or [1])[0], 0, 16, 1))
            return self.send_json({"pos": norm_pos(m)})
        self.send(404, "not found")

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/login":
            ip = self.client_address[0]
            f = FAILS.get(ip, [0, 0.0])
            if time.time() < f[1]:
                return self.send(429, "wait")
            pin = str(self.body_json().get("pin", ""))
            if secrets.compare_digest(pin.encode(), CFG["pin"].encode()):
                FAILS.pop(ip, None)
                print("  ✔ Подключился телефон:", ip)
                return self.send(200, "ok", headers={
                    "Set-Cookie": "rc=%s; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict" % CFG["token"]})
            f[0] += 1
            if f[0] >= 5:
                f = [0, time.time() + 60]
                print("  ✖ Много неверных PIN с", ip, "— блок на минуту")
            FAILS[ip] = f
            return self.send(401, "bad pin")
        if not self.authed():
            return self.send(401, "unauthorized")
        if p == "/api":
            d = self.body_json()
            m = int(num(d.get("m", 1), 0, 16, 1))
            evs = d.get("ev") or []
            if isinstance(evs, list):
                do_events(m, evs)
            return self.send_json({"ok": True, "pos": norm_pos(m)})
        self.send(404, "not found")

    def stream(self, q):
        g = lambda k, d: (q.get(k) or [d])[0]
        m = int(num(g("m", 1), 0, 16, 1))
        quality = int(num(g("q", 60), 20, 95, 60))
        scale = num(g("s", 0.6), 0.2, 1.0, 0.6)
        fps = num(g("f", 15), 2, 30, 15)
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        interval = 1.0 / fps
        last, last_sent = None, 0.0
        try:
            with MSS() as sct:
                mons = sct.monitors
                mon = mons[m] if 1 <= m < len(mons) else mons[1 if len(mons) > 1 else 0]
                while True:
                    t0 = time.time()
                    shot = sct.grab(mon)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    if scale < 0.999:
                        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
                    draw_cursor(img, mon, img.width / max(1, mon["width"]))
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=quality)
                    data = buf.getvalue()
                    h = hash(data)
                    if h != last or t0 - last_sent > 2:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data))
                        self.wfile.write(data)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                        last, last_sent = h, t0
                    time.sleep(max(0.0, interval - (time.time() - t0)))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass
        except OSError:
            pass
        except Exception as e:
            print("Ошибка трансляции экрана:", e)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


def show_qr(url):
    try:
        import qrcode
    except ImportError:
        return
    try:
        qr = qrcode.QRCode(border=2)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        pass
    if CFG.get("last_url") != url:  # картинку с QR открываем только при первом запуске / смене адреса
        try:
            p = os.path.join(BASE_DIR, "qr.png")
            qrcode.make(url).save(p)
            if IS_WIN:
                os.startfile(p)
        except Exception:
            pass
        CFG["last_url"] = url
        save_cfg(CFG)


def main():
    port = int(CFG.get("port", 8765))
    refresh_monitors()
    try:
        srv = Server(("0.0.0.0", port), H)
    except OSError as e:
        print("Не удалось открыть порт %d: %s\nВозможно, программа уже запущена." % (port, e))
        input("Enter для выхода...")
        return
    ips = lan_ips()
    url = "http://%s:%d" % (ips[0], port)
    print("=" * 56)
    print("  ПК ПУЛЬТ запущен")
    print("=" * 56)
    print("  1) Телефон и компьютер — в одной сети Wi-Fi")
    print("  2) Откройте на iPhone в Safari:  " + url)
    for ip in ips[1:]:
        print("     (или http://%s:%d)" % (ip, port))
    print("  3) PIN:  " + CFG["pin"])
    print("  4) Safari → «Поделиться» → «На экран Домой»")
    print("-" * 56)
    show_qr(url)
    print("  Окно не закрывайте — пока оно открыто, пульт работает.")
    print("  Ctrl+C — остановить.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
