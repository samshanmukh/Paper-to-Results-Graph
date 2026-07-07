"""LLM access via the Butterbase AI gateway (OpenAI-compatible).

Uses the same credentials the RocketRide pipeline uses (ROCKETRIDE_GATEWAY_*),
so extraction/codegen and the agent all share one LLM route.
"""

import json
import os
import urllib.error
import urllib.request

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))


def chat(prompt: str, system: str | None = None, max_tokens: int = 4000,
         temperature: float = 0.0, timeout: int = 180) -> str:
    base = os.environ["ROCKETRIDE_GATEWAY_BASE_URL"].rstrip("/")
    key = os.environ["ROCKETRIDE_GATEWAY_KEY"]
    model = os.environ.get("ROCKETRIDE_GATEWAY_MODEL", "x-ai/grok-4.3")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps({"model": model, "max_tokens": max_tokens,
                         "temperature": temperature, "messages": messages}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"gateway HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    return out["choices"][0]["message"]["content"]


def extract_json_obj(text: str) -> dict:
    """First JSON object in an LLM reply (fences/prose tolerated)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in reply: {text[:150]!r}")
    return json.loads(text[start:end + 1])


def extract_code_block(text: str) -> str:
    """Python code from a ```python fenced block (or the whole reply)."""
    if "```" in text:
        for chunk in text.split("```")[1::2]:
            body = chunk[6:] if chunk.startswith("python") else chunk
            if "def main" in body or "import" in body:
                return body.strip() + "\n"
    return text.strip() + "\n"
