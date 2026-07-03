from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from shared.db import init_db, list_items

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="couple_app", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    items = list_items(limit=200)
    return templates.TemplateResponse(request, "index.html", {"items": items})
