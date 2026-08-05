"""The AI feature: a natural-language dispatch briefing.

Where AI earns its keep here is at the *human interface*, not in the
localization. The localizer is a deterministic graph traversal — instant, free,
explainable, and testable — and an LLM has no business replacing it (see
ARCHITECTURE.md "The AI feature" and DECISIONS.md). What an LLM is genuinely
good at is turning the localizer's structured output plus the confidence
reasons into one calm, unambiguous sentence a tired operator at 2 a.m. can act
on without decoding jargon.

Design contract:
* Input is already-computed structured facts. The model never decides *where*
  the fault is; it only phrases what the algorithm found.
* It must degrade gracefully. If no API key is configured, or the call fails or
  times out, we fall back to a deterministic template that is always correct
  (just less fluent). The system never blocks on the model.
* Cost is one short completion per *new* ticket (not per telemetry message), so
  a few paise per fault at most; heartbeats never hit it.
"""
from __future__ import annotations

import os

_KIND_LABEL = {
    "span_point": "LT span fault",
    "span_range": "LT span fault (approx. range)",
    "dt_area": "LT fault under DT (span unknown)",
    "dt_equipment": "DT / HT-fuse failure",
    "feeder_area": "11 kV feeder failure",
    "sensor_point": "suspected sensor / lamp-point fault",
}

_ACTION = {
    "span": "Dispatch a line crew with LT conductor and a ladder to the span above.",
    "dt": "Dispatch to the transformer; check the HT fuse and DT before the LT line.",
    "feeder": "Escalate to the 11 kV feeder desk — the whole feeder is down, not a single line.",
    "sensor": "No dispatch: power is flowing downstream. Log for meter/sensor maintenance.",
}


def _template(inc: dict) -> str:
    kind = _KIND_LABEL.get(inc.get("localization_kind"), "Outage")
    band = str(inc.get("confidence_band", "")).upper()
    where = ""
    if inc.get("span_from_pole") and inc.get("span_to_pole"):
        where = f" between {inc['span_from_pole']} and {inc['span_to_pole']}"
    elif inc.get("dt_id"):
        where = f" under {inc['dt_id']}"
    loc = ""
    if inc.get("lat") is not None:
        loc = f" Navigate to {inc['lat']:.5f}, {inc['lon']:.5f}"
        if inc.get("pincode"):
            loc += f" (PIN {inc['pincode']})"
        loc += "."
    homes = inc.get("households_affected") or 0
    poles = inc.get("poles_affected") or 0
    feeder = inc.get("feeder_id") or "?"
    reason = (inc.get("reasons") or ["No corroborating detail."])[0]
    action = _ACTION.get(inc.get("fault_type"), "Review on the console.")
    return (
        f"{band} confidence — {kind}{where} on feeder {feeder}. "
        f"About {homes} households dark across {poles} poles.{loc} "
        f"Why: {reason} {action}"
    )


def generate_briefing(inc: dict) -> tuple[str, str]:
    """Return (briefing_text, source) where source is 'llm' or 'template'."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _template(inc), "template"
    try:
        import httpx

        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("AI_MODEL", "gpt-4o-mini")
        facts = {k: inc.get(k) for k in (
            "fault_type", "localization_kind", "span_from_pole", "span_to_pole",
            "dt_id", "feeder_id", "lat", "lon", "pincode", "ward",
            "poles_affected", "households_affected", "confidence_band", "reasons",
        )}
        prompt = (
            "You are the dispatch assistant for an electricity control room. "
            "Turn these already-computed fault facts into ONE calm, concrete "
            "sentence (max 45 words) for a non-engineer operator at 2 a.m.: what "
            "broke, where to drive (coords + PIN), how many homes, and the single "
            "next action. Do not invent facts or a location; use only what is "
            f"given.\n\nFACTS:\n{facts}"
        )
        r = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 90, "temperature": 0.2},
            timeout=6.0,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        return (text or _template(inc)), "llm"
    except Exception:
        # Any failure -> deterministic fallback. Never block dispatch on the LLM.
        return _template(inc), "template"
