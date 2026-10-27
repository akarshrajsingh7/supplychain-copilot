"""Unit tests must not spend credits or upload traces from a developer's .env."""
import os

os.environ["AGENT_MODE"] = "deterministic"
os.environ["LANGSMITH_TRACING"] = "false"
