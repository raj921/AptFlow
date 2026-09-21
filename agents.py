"""LLM agents. Real calls go through the Requesty gateway (OpenAI-compatible,
DeepSeek). MOCK_LLM=1 swaps in deterministic rules so evals/demos run offline.
Every decision returns {.., "confidence", "reasoning"} — the engine acts on
confidence, not vibes."""
import json, os, re, time, urllib.request

LLM_URL = os.getenv("LLM_BASE_URL", "https://router.requesty.ai/v1/chat/completions")
LLM_KEY = os.getenv("REQUESTY_API_KEY", "")
MODEL = os.getenv("MODEL", "deepseek/deepseek-v3.1")
MOCK = not LLM_KEY or os.getenv("MOCK_LLM") == "1"


def llm_json(system: str, user: str, mock) -> dict:
    if MOCK:
        return mock()
    body = json.dumps({
        "model": MODEL, "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(LLM_URL, data=body, headers={
                "Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                text = json.load(r)["choices"][0]["message"]["content"]
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start:end + 1])
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def triage(message: str) -> dict:
    return llm_json(
        "You triage apartment ops issues. Return JSON: "
        '{"category": "maintenance|housekeeping|guest_support", '
        '"severity": "critical|high|medium|low", "confidence": 0-1, "reasoning": str}',
        message,
        lambda: _mock_triage(message),
    )


def _mock_triage(msg: str) -> dict:
    m = msg.lower()
    rules = [
        (("leak", "flood", "water", "burst", "no heat", "gas"), "maintenance", "critical", 0.95),
        (("broken", "ac", "toilet", "fridge", "outlet", "door"), "maintenance", "high", 0.9),
        (("clean", "dirty", "towel", "trash", "linen"), "housekeeping", "medium", 0.9),
        (("wifi", "password", "check-in", "lock code", "parking"), "guest_support", "low", 0.9),
    ]
    for keys, cat, sev, conf in rules:
        # word-boundary match: "place" must not match "ac" (learned the hard way)
        if any(re.search(rf"\b{re.escape(k)}\b", m) for k in keys):
            return {"category": cat, "severity": sev, "confidence": conf,
                    "reasoning": f"keyword match in {keys}"}
    return {"category": "guest_support", "severity": "low", "confidence": 0.35,
            "reasoning": "no clear signal — uncertain"}


def dispatch(incident: dict, vendors: list[dict]) -> dict:
    return llm_json(
        "Pick the best vendor for this incident. Return JSON: "
        '{"vendor_id": str, "confidence": 0-1, "reasoning": str}',
        json.dumps({"incident": incident, "vendors": vendors}),
        lambda: _mock_dispatch(incident, vendors),
    )


def _mock_dispatch(incident: dict, vendors: list[dict]) -> dict:
    trade = {"maintenance": "plumbing", "housekeeping": "cleaning"}.get(
        incident.get("category"), "general")
    for v in vendors:
        if v["trade"] == trade:
            return {"vendor_id": v["id"], "confidence": 0.9,
                    "reasoning": f"trade match: {trade}"}
    v = vendors[0]
    return {"vendor_id": v["id"], "confidence": 0.5, "reasoning": "fallback to generalist"}


def verify(incident: dict, evidence: str) -> dict:
    return llm_json(
        "Decide if vendor evidence proves the incident is fixed. Return JSON: "
        '{"complete": bool, "confidence": 0-1, "reasoning": str}',
        json.dumps({"incident": incident, "evidence": evidence}),
        lambda: _mock_verify(evidence),
    )


def _mock_verify(evidence: str) -> dict:
    e = evidence.lower()
    strong = any(k in e for k in ("replaced", "fixed", "tested", "photo attached")) and len(e) > 25
    if strong:
        return {"complete": True, "confidence": 0.9, "reasoning": "evidence specific and verifiable"}
    return {"complete": False, "confidence": 0.4,
            "reasoning": "evidence vague — no proof of resolution"}


def guest_reply(incident: dict, outcome: str) -> str:
    if MOCK:
        return (f"Hi! Your {incident['category']} issue has been {outcome}. "
                "Reply here if anything still seems off.")
    out = llm_json(
        "Write a short guest update. Return JSON: {\"reply\": str}",
        json.dumps({"incident": incident, "outcome": outcome}),
        lambda: {"reply": ""},
    )
    return out["reply"]
