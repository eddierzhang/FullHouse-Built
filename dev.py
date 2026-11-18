#!/usr/bin/env python3
"""FullHouse developer tasks, one command each, on Windows, macOS and Linux.

    python dev.py setup      install everything and create a seeded database
    python dev.py dev        run the API (:8000) and the frontend (:5173) together
    python dev.py demo       run the public demo locally (:8080), no Docker or model needed
    python dev.py margin-labor   the Margin & Labor Engine on sample data (:8090)
    python dev.py test       backend tests and a frontend type-check/build
    python dev.py e2e        Playwright end-to-end tests on an isolated stack
    python dev.py eval ...   agent evaluation; extra arguments go to evals.run

A plain script rather than a Makefile: `make` is not installed on Windows by
default, and this needs nothing beyond the Python that runs the backend.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
WINDOWS = os.name == "nt"
VENV_PYTHON = BACKEND / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
NPM = shutil.which("npm") or "npm"


def step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def run(args: list, cwd: Path, env: dict | None = None) -> None:
    result = subprocess.run([str(a) for a in args], cwd=cwd, env={**os.environ, **(env or {})})
    if result.returncode != 0:
        sys.exit(result.returncode)


def python() -> Path:
    if not VENV_PYTHON.exists():
        sys.exit("The backend virtualenv is missing. Run `python dev.py setup` first.")
    return VENV_PYTHON


def setup(_args) -> None:
    if not VENV_PYTHON.exists():
        step("Creating backend/.venv")
        venv.create(BACKEND / ".venv", with_pip=True)
    step("Installing backend dependencies")
    run([VENV_PYTHON, "-m", "pip", "install", "-q", "-r", "requirements-dev.txt"], BACKEND)

    env_file = BACKEND / ".env"
    if not env_file.exists():
        step("Creating backend/.env from .env.example (agents default to the scripted provider)")
        text = (BACKEND / ".env.example").read_text(encoding="utf-8")
        env_file.write_text(text.replace("LLM_PROVIDER=\n", "LLM_PROVIDER=scripted\n", 1), encoding="utf-8")

    step("Migrating and seeding the database")
    run([VENV_PYTHON, "-m", "alembic", "upgrade", "head"], BACKEND)
    run([VENV_PYTHON, "scripts/seed_data.py"], BACKEND)

    step("Installing frontend dependencies")
    run([NPM, "install"], FRONTEND)
    print("\nReady. Start it with: python dev.py dev")


def _serve(processes: list[subprocess.Popen]) -> None:
    """Wait on long-running processes; Ctrl+C stops them all."""
    try:
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
        for p in processes:
            p.wait()


def dev(_args) -> None:
    step("API on http://localhost:8000, frontend on http://localhost:5173 (Ctrl+C stops both)")
    _serve([
        subprocess.Popen([str(python()), "-m", "uvicorn", "app.main:app", "--port", "8000"], cwd=BACKEND),
        subprocess.Popen([NPM, "run", "dev"], cwd=FRONTEND),
    ])


def demo(_args) -> None:
    """The single-container demo, without the container."""
    step("Building the frontend")
    run([NPM, "run", "build"], FRONTEND, env={"VITE_API_BASE": "same-origin"})

    database = Path(tempfile.mkdtemp(prefix="fullhouse-demo-")) / "demo.db"
    env = {
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "DEMO_MODE": "true",
        "LLM_PROVIDER": "scripted",
        "SCRIPTED_STEP_DELAY_SECONDS": "0.8",
        "STATIC_DIR": str(FRONTEND / "dist"),
    }
    step("Loading the demo restaurant")
    run([python(), "-m", "alembic", "upgrade", "head"], BACKEND, env)

    step("Demo on http://localhost:8080 (Ctrl+C to stop)")
    _serve([subprocess.Popen(
        [str(python()), "-m", "uvicorn", "app.main:app", "--port", "8080"], cwd=BACKEND,
        env={**os.environ, **env},
    )])


def margin_labor(_args) -> None:
    """The Margin & Labor Engine on sample data, on its own port and its own throwaway database.

    Two sample restaurants with every module switched on: sign in as ml@example.com (Menu costs, Usage, Ordering and
    prep, Channels) or labor@example.com (Labor), password ml-password. Nothing here touches the demo or your data.
    """
    step("Building the frontend")
    run([NPM, "run", "build"], FRONTEND, env={"VITE_API_BASE": "same-origin"})

    database = Path(tempfile.mkdtemp(prefix="fullhouse-margin-labor-")) / "preview.db"
    env = {
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "LLM_PROVIDER": "scripted",
        "REQUIRE_MFA": "false",
        # Lets the Labor Today page be read as of a time of day, to see a service in progress at any hour.
        "ALLOW_TIME_TRAVEL": "true",
        "STATIC_DIR": str(FRONTEND / "dist"),
    }
    step("Loading the sample restaurants")
    run([python(), "-m", "alembic", "upgrade", "head"], BACKEND, env)
    for script in ("scripts/seed_data.py", "scripts/seed_e2e.py", "scripts/seed_e2e_margin_labor.py"):
        run([python(), script], BACKEND, env)

    step("Margin & Labor on http://localhost:8090 (Ctrl+C to stop). Sign in: ml@example.com or labor@example.com, password ml-password")
    _serve([subprocess.Popen(
        [str(python()), "-m", "uvicorn", "app.main:app", "--port", "8090"], cwd=BACKEND,
        env={**os.environ, **env},
    )])


def test(_args) -> None:
    step("Backend tests")
    run([python(), "-m", "pytest", "-q"], BACKEND)
    step("Frontend type-check and build")
    run([NPM, "run", "build"], FRONTEND)


def e2e(_args) -> None:
    step("Playwright end-to-end tests")
    run([NPM, "run", "test:e2e"], FRONTEND)


def evaluate(args) -> None:
    step("Agent evaluation")
    run([python(), "-m", "evals.run", *(args or ["--targets", "scripted"])], BACKEND)


COMMANDS = {"setup": setup, "dev": dev, "demo": demo, "margin-labor": margin_labor, "test": test, "e2e": e2e, "eval": evaluate}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 2)
    COMMANDS[sys.argv[1]](sys.argv[2:])
