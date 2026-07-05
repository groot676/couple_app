"""Process launcher for Railway.

The web and bot services deploy the *same* codebase. Railway's builder needs a
start command baked in at build time, and the CLI can't set a per-service start
command — so both services share one start command (`python start.py`) and this
launcher picks the actual process from the SERVICE_ROLE env var set per service.

  SERVICE_ROLE=web  -> uvicorn web.main:app   (default)
  SERVICE_ROLE=bot  -> python -m bot.main
"""
import os
import sys

role = os.getenv("SERVICE_ROLE", "web").strip().lower()

if role == "bot":
    args = [sys.executable, "-m", "bot.main"]
else:
    port = os.getenv("PORT", "8000")
    args = [
        sys.executable, "-m", "uvicorn", "web.main:app",
        "--host", "0.0.0.0", "--port", port,
    ]

# Replace this process so signals (SIGTERM on redeploy) reach the real server.
os.execvp(args[0], args)
