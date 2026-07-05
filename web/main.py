from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from shared import db, money
from shared.config import (
    APP_NAME,
    DEFAULT_CURRENCY_CODE,
    DEFAULT_CURRENCY_SYMBOL,
    DEFAULT_TIMEZONE,
)
from .auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    NotAuthed,
    check_passcode,
    make_token,
    require_auth,
    verify_token,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

CURRENCIES = {"CZK": "Kč", "EUR": "€", "USD": "$", "GBP": "£"}
STARTERS = {
    "a bed you'll both love": 70,
    "the couch where Sundays happen": 65,
    "the table you'll gather around": 60,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.exception_handler(NotAuthed)
async def notauthed_handler(request: Request, exc: NotAuthed):
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": "/passcode"})
    return RedirectResponse("/passcode", status_code=303)


# --- context -------------------------------------------------------------------

def _surface_context() -> dict:
    settings = db.get_settings() or {}
    complete = db.setup_complete(settings)
    dreams = db.list_dreams()
    everyday = db.list_everyday()
    unsorted_items = db.list_unsorted()
    made_real = db.list_made_real()

    code = settings.get("currency_code") or DEFAULT_CURRENCY_CODE
    symbol = settings.get("currency_symbol") or DEFAULT_CURRENCY_SYMBOL
    tz_name = settings.get("timezone") or DEFAULT_TIMEZONE

    pot = money.pot_value(settings, made_real) if complete else None
    ready_ids = {
        d["id"] for d in dreams
        if pot is not None and d["estimated_price"] and d["estimated_price"] <= pot
    }
    next_days = None
    pot_pending_days = None
    if complete:
        next_days = money.days_until(money.next_payday(settings), settings)
        if pot is None:
            pot_pending_days = money.days_until(money.first_pot_date(settings), settings)

    p1_initial = (settings.get("p1_name") or "")[:1].upper()
    p2_initial = (settings.get("p2_name") or "")[:1].upper()

    def initial_for(item: dict) -> str:
        sid = item.get("sender_id")
        if not sid:
            return ""  # starter dreams carry no presence
        if sid == settings.get("p1_sender_id"):
            return p1_initial or (item.get("sender_name") or "")[:1].upper()
        if sid == settings.get("p2_sender_id"):
            return p2_initial or (item.get("sender_name") or "")[:1].upper()
        return (item.get("sender_name") or "")[:1].upper()

    def fmt(value: int) -> str:
        return money.fmt_money(value, symbol, code)

    def made_date(item: dict) -> str:
        ts = db.parse_ts(item.get("done_at"))
        if ts is None:
            return ""
        try:
            local = ts.astimezone(ZoneInfo(tz_name))
        except Exception:
            local = ts
        return f"{local.day} {local.strftime('%b')}"

    return {
        "app_name": APP_NAME,
        "settings": settings,
        "complete": complete,
        "dreams": dreams,
        "everyday": everyday,
        "unsorted": unsorted_items,
        "made_real": made_real,
        "made_real_count": len(made_real),
        "pot": pot,
        "next_days": next_days,
        "pot_pending_days": pot_pending_days,
        "ready_ids": ready_ids,
        "currency_code": code,
        "currency_symbol": symbol,
        "money": fmt,
        "made_date": made_date,
        "initial_for": initial_for,
        "p1_initial": p1_initial,
        "p2_initial": p2_initial,
        "starters": list(STARTERS),
    }


def _surface(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/surface.html", _surface_context())


# Served from the root (not /static/) so its scope can cover the whole app —
# a service worker can never control paths above its own URL.
@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


# Root route with no-cache: a stale cached manifest silently breaks Android's
# install (the app-minting step reads it) — never let a browser hold an old copy.
@app.get("/manifest.json", include_in_schema=False)
def manifest():
    return FileResponse(
        BASE_DIR / "static" / "manifest.json",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


# Unauthenticated on purpose: reveals nothing private, and install problems
# usually mean the phone can't get past the door anyway.
_PWA_CHECK = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pwa check</title>
<link rel="manifest" href="/manifest.json">
<style>body{font:16px/1.6 monospace;background:#F6F1E8;color:#2A2521;padding:1.2rem}</style>
</head><body><h3>install diagnostics</h3><pre id="o">running…</pre>
<script>
var out = [];
function line(k, v) { out.push(k + ": " + v); document.getElementById("o").textContent = out.join("\\n"); }
line("browser", navigator.userAgent.match(/Chrome\\/[\\d.]+|Safari\\/[\\d.]+|wv/g) || navigator.userAgent);
line("in-app webview", /\\bwv\\b/.test(navigator.userAgent) ? "YES — install cannot work here" : "no");
line("secure context", window.isSecureContext);
line("display mode", ["standalone","minimal-ui","fullscreen","browser"].find(function(m){return matchMedia("(display-mode: "+m+")").matches}));
if (!("serviceWorker" in navigator)) { line("service worker", "UNSUPPORTED — likely an in-app browser"); }
else {
  navigator.serviceWorker.register("/sw.js").then(function(r){ line("service worker", "registered, scope " + r.scope); return navigator.serviceWorker.ready; })
    .then(function(r){ line("sw active", !!r.active); })
    .catch(function(e){ line("sw ERROR", e); });
}
fetch("/manifest.json", {cache: "no-store"}).then(function(r){ return r.json(); })
  .then(function(m){ line("manifest", "ok — id " + m.id + ", icons " + m.icons.length); })
  .catch(function(e){ line("manifest ERROR", e); });
var fired = false;
addEventListener("beforeinstallprompt", function(){ fired = true; line("installable", "YES — Chrome says criteria met"); });
setTimeout(function(){ if (!fired) line("installable", "no signal after 6s (already installed? in-app browser? criteria unmet?)"); }, 6000);
if (navigator.getInstalledRelatedApps) navigator.getInstalledRelatedApps().then(function(a){ line("already installed here", a.length ? "YES" : "not detected"); });
</script></body></html>"""


@app.get("/pwa-check", response_class=HTMLResponse, include_in_schema=False)
def pwa_check():
    return HTMLResponse(_PWA_CHECK)


# --- the door -------------------------------------------------------------------

@app.get("/passcode", response_class=HTMLResponse)
def passcode_page(request: Request):
    if verify_token(request.cookies.get(COOKIE_NAME, "")):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "passcode.html", {"app_name": APP_NAME, "error": None}
    )


@app.post("/passcode")
def passcode_submit(request: Request, passcode: str = Form("")):
    if not check_passcode(passcode):
        return templates.TemplateResponse(
            request, "passcode.html", {"app_name": APP_NAME, "error": "that's not it"}
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        make_token(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


# --- the surface -----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", _surface_context())


# The same surface, served without a manifest link. On Android Chrome an
# installable page hides the plain "Add to Home screen" shortcut option;
# this address brings it back for phones whose WebAPK minting is broken.
@app.get("/go", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def index_shortcut(request: Request):
    ctx = _surface_context()
    ctx["hide_manifest"] = True
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/surface", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def surface(request: Request):
    return _surface(request)


# --- item actions ----------------------------------------------------------------

@app.post("/item/{item_id}/done", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def item_done(request: Request, item_id: int, by: str = Form("")):
    item = db.get_item(item_id)
    if item and item["status"] == "active":
        db.mark_done(item_id, by if by in ("p1", "p2") else None)
    return _surface(request)


@app.post("/item/{item_id}/remove", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def item_remove(request: Request, item_id: int):
    db.mark_removed(item_id)
    return _surface(request)


@app.post("/item/{item_id}/price", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def item_price(request: Request, item_id: int, price: str = Form("")):
    try:
        value = int(price)
    except ValueError:
        value = None
    if value is not None and value >= 0:
        db.set_price(item_id, value)
    return _surface(request)


@app.post("/item/{item_id}/lane", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def item_lane(request: Request, item_id: int, lane: str = Form(...)):
    item = db.get_item(item_id)
    if item and item["status"] == "active" and lane in ("dream", "everyday"):
        db.set_lane(item_id, lane)
        # A human placing what the LLM couldn't is itself a learning signal.
        db.add_overrule(item_id, "lane", item["lane"], lane, "web")
    return _surface(request)


@app.post("/starter", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def adopt_starter(request: Request, text: str = Form(...)):
    if text in STARTERS:
        db.add_item(
            text, "starter", 0, 0,
            lane="dream", display_text=text, priority=STARTERS[text],
        )
    return _surface(request)


# --- setup ------------------------------------------------------------------------

@app.get("/setup", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def setup_page(request: Request):
    settings = db.get_settings() or {}
    senders = db.distinct_senders()
    prefill = dict(settings)
    if not prefill.get("p1_name") and len(senders) > 0:
        prefill["p1_name"] = senders[0][1]
        prefill.setdefault("p1_sender_id", senders[0][0])
    if not prefill.get("p2_name") and len(senders) > 1:
        prefill["p2_name"] = senders[1][1]
        prefill.setdefault("p2_sender_id", senders[1][0])
    return templates.TemplateResponse(
        request, "setup.html",
        {
            "app_name": APP_NAME,
            "s": prefill,
            "currencies": CURRENCIES,
            "default_tz": prefill.get("timezone") or DEFAULT_TIMEZONE,
        },
    )


def _int_or_none(v: str | None, lo: int | None = None, hi: int | None = None) -> int | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    if lo is not None and n < lo:
        return None
    if hi is not None and n > hi:
        return None
    return n


@app.post("/setup", dependencies=[Depends(require_auth)])
def setup_save(
    request: Request,
    p1_name: str = Form(""),
    p1_income: str = Form(""),
    p1_payday: str = Form(""),
    p1_sender_id: str = Form(""),
    p2_name: str = Form(""),
    p2_income: str = Form(""),
    p2_payday: str = Form(""),
    p2_sender_id: str = Form(""),
    baseline: str = Form(""),
    currency: str = Form("CZK"),
    timezone: str = Form(DEFAULT_TIMEZONE),
):
    tz = timezone.strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(tz)
    except Exception:
        tz = DEFAULT_TIMEZONE
    code = currency if currency in CURRENCIES else DEFAULT_CURRENCY_CODE
    db.save_settings(
        {
            "p1_name": p1_name.strip() or None,
            "p1_income": _int_or_none(p1_income, 0),
            "p1_payday": _int_or_none(p1_payday, 1, 31),
            "p1_sender_id": _int_or_none(p1_sender_id),
            "p2_name": p2_name.strip() or None,
            "p2_income": _int_or_none(p2_income, 0),
            "p2_payday": _int_or_none(p2_payday, 1, 31),
            "p2_sender_id": _int_or_none(p2_sender_id),
            "baseline": _int_or_none(baseline, 0),
            "currency_code": code,
            "currency_symbol": CURRENCIES[code],
            "timezone": tz,
        }
    )
    return RedirectResponse("/", status_code=303)
