import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")
DATA = ROOT / "backend" / "data"
RUNTIME = ROOT / "runtime"
RUNTIME.mkdir(exist_ok=True)
AGENT_MODE = os.getenv("AGENT_MODE", "deterministic")
MEMORY_MODE = os.getenv("MEMORY_MODE", "local")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
