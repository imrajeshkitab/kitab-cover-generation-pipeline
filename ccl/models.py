"""Model calls: Gemini (text) and ElevenLabs /v1/flows/image (images)."""
import base64
import io
import sys
import time

import requests

from . import config


def gemini(prompt: str, model: str, thinking: str | None = "minimal", system: str | None = None) -> dict:
    """Returns {"text", "finish", "usage"}. Retries timeouts / 429 / 5xx up to 3 times."""
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {}}
    if thinking:
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": thinking}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    for attempt in range(1, 4):
        try:
            r = requests.post(url, headers={"x-goog-api-key": config.env("GEMINI_API_KEY")}, json=body, timeout=300)
        except requests.exceptions.RequestException as e:
            print(f"  gemini request error ({type(e).__name__}), attempt {attempt}/3", file=sys.stderr)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            print(f"  gemini {r.status_code}, attempt {attempt}/3", file=sys.stderr)
            time.sleep(5 * attempt)
            continue
        if not r.ok:
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:400]}")
        cand = r.json()["candidates"][0]
        parts = cand.get("content", {}).get("parts", [])
        return {"text": "".join(p.get("text", "") for p in parts).strip(),
                "finish": cand.get("finishReason"), "usage": r.json().get("usageMetadata", {})}
    raise RuntimeError("Gemini: 3 attempts failed")


def image(prompt: str, model: str, aspect_ratio: str, resolution: str, quality: str,
          references: list[bytes] | None = None, moderation_retries: int = 2) -> dict:
    """ElevenLabs image generation. references = images (any format PIL reads) sent inline.
    Moderation blocks are sporadic and not charged, so they are retried.
    Returns {"bytes", "generation_id", "mime", "seconds", "moderation_retries"}."""
    for attempt in range(moderation_retries + 1):
        try:
            out = _image_once(prompt, model, aspect_ratio, resolution, quality, references)
            out["moderation_retries"] = attempt
            return out
        except RuntimeError as e:
            if "moderated" not in str(e) or attempt == moderation_retries:
                raise
            print(f"  image blocked by moderation, retrying ({attempt + 1}/{moderation_retries})", file=sys.stderr)


def _image_once(prompt, model, aspect_ratio, resolution, quality, references):
    h = {"xi-api-key": config.env("ELEVEN_LABS_API_KEY_ADMIN")}
    body = {"model_id": model, "prompt": prompt, "aspect_ratio": aspect_ratio,
            "resolution": resolution, "quality": quality}
    if references:
        body["images"] = [{"type": "inline_base64", "content_base64": _jpeg_b64(b), "mime_type": "image/jpeg"}
                          for b in references]
    t0 = time.time()
    r = requests.post("https://api.elevenlabs.io/v1/flows/image", headers=h, json=body, timeout=120)
    if not r.ok:
        raise RuntimeError(f"ElevenLabs image {r.status_code}: {r.text[:400]}")
    gid = r.json()["id"]
    while True:
        g = requests.get(f"https://api.elevenlabs.io/v1/flows/image/{gid}", headers=h, timeout=60).json()
        status = g.get("status")
        if status == "completed":
            data = requests.get(g["content_url"], timeout=120).content
            return {"bytes": data, "generation_id": gid, "mime": g.get("content_mime_type", "image/png"),
                    "seconds": round(time.time() - t0)}
        if status == "failed":
            raise RuntimeError(f"ElevenLabs image {gid} failed: {g.get('failure_reason')} {g.get('error_message')}")
        if time.time() - t0 > 900:
            raise RuntimeError(f"ElevenLabs image {gid} timed out")
        time.sleep(5)


def elevenlabs_credits_used() -> int | None:
    try:
        r = requests.get("https://api.elevenlabs.io/v1/user/subscription",
                         headers={"xi-api-key": config.env("ELEVEN_LABS_API_KEY_ADMIN")}, timeout=30)
        return r.json().get("character_count")
    except Exception:
        return None


def _jpeg_b64(data: bytes) -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()
