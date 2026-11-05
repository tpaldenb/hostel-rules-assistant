"""Runtime settings, read from the environment or a local .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DOCUMENTS_DIR = ROOT / "data" / "documents"
STATIC_DIR = ROOT / "static"

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
MODE = os.getenv("ASSISTANT_MODE", "auto").lower()  # auto | offline | claude | gemini | groq | ollama
LLM_MODEL = os.getenv("LLM_MODEL", "")  # overrides the default model of gemini, groq or ollama
MAX_TOKENS = 16000
EFFORT = "medium"
HISTORY_MESSAGES = 6  # previous chat messages sent to the model


def has_credentials() -> bool:
    """True if the Anthropic SDK can plausibly authenticate."""
    if any(os.getenv(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


# Providers with free tiers, reached through their OpenAI-compatible endpoints.
OPENAI_COMPATIBLE = {
    "gemini": {
        "label": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        "model": "gemini-flash-lite-latest",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
    },
    "ollama": {
        "label": "Ollama",
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        "key_env": None,  # runs on this computer, no key
        "model": "llama3.1:8b",
    },
}


def active_provider() -> str:
    """The provider answering questions. In auto mode, the first one with a key."""
    if MODE != "auto":
        return MODE
    if has_credentials():
        return "claude"
    for name, preset in OPENAI_COMPATIBLE.items():
        if preset["key_env"] and os.getenv(preset["key_env"]):
            return name
    return "offline"


def provider_label(provider: str) -> str:
    return {"claude": "Claude", "offline": "Offline"}.get(provider) or OPENAI_COMPATIBLE[provider]["label"]


def model_for(provider: str) -> str | None:
    if provider == "claude":
        return MODEL
    if provider == "offline":
        return None
    return LLM_MODEL or OPENAI_COMPATIBLE[provider]["model"]
