"""Image generation via Google's image models.

Two families of models are supported, picked automatically based on the
model name:

- ``imagen-*`` models use the dedicated Imagen Predict API
  (``client.models.generate_images``). Higher fidelity but currently
  requires a **paid** Google AI plan.
- ``gemini-*-image*`` models (a.k.a. "Nano Banana") use the regular
  ``generate_content`` API with ``response_modalities=['IMAGE','TEXT']``.
  These work on the **free tier** (subject to daily quota).

The public ``IMAGE_MODELS`` tuple lists every model the UI should expose,
in the order they appear in the dropdown. The first one is the default.
"""

from __future__ import annotations

import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

log = logging.getLogger(__name__)

# Default model order. "Nano Banana" first because it works on the free
# tier — most users will start there. Imagen models are kept in the list
# for users on a paid plan who want higher fidelity.
IMAGE_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash-image",          # Nano Banana — free tier
    "gemini-3.1-flash-image-preview",  # Nano Banana 2 — free tier preview
    "gemini-3-pro-image-preview",      # Nano Banana Pro — free tier preview
    "imagen-4.0-fast-generate-001",    # Imagen 4 Fast — paid tier
    "imagen-4.0-generate-001",         # Imagen 4 — paid tier
    "imagen-4.0-ultra-generate-001",   # Imagen 4 Ultra — paid tier
)

# Kept for backward compatibility with code that imported this name.
PREFERRED_IMAGE_MODELS = IMAGE_MODELS

ASPECT_RATIOS: tuple[str, ...] = ("1:1", "16:9", "9:16", "4:3", "3:4")
DEFAULT_ASPECT_RATIO = "16:9"


class ImageGenError(RuntimeError):
    """Raised when image generation fails for all candidate models."""


class ImageGenerator:
    def __init__(
        self,
        api_key: str,
        *,
        model: Optional[str] = None,
        output_dir: Path = Path("generated"),
        fallback: bool = True,
    ) -> None:
        from google import genai

        if not api_key:
            raise ValueError("api_key is required")
        self._client = genai.Client(api_key=api_key)
        self._preferred_model = model or IMAGE_MODELS[0]
        self._fallback = fallback
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        scene_index: Optional[int] = None,
        story_slug: str = "story",
    ) -> Path:
        """Generate one image and save it to ``output_dir``.

        Returns the path to the saved PNG. Raises ``ImageGenError`` if
        every candidate model fails.
        """
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is empty")

        candidates: list[str] = [self._preferred_model]
        if self._fallback:
            # If the chosen model fails, try other models in the SAME family
            # first (Nano Banana ↔ Imagen are treated as separate families
            # because they need different billing tiers — silently falling
            # back across families would surprise the user).
            family = _model_family(self._preferred_model)
            for m in IMAGE_MODELS:
                if m == self._preferred_model:
                    continue
                if _model_family(m) == family:
                    candidates.append(m)

        last_err: Optional[Exception] = None
        for model in candidates:
            try:
                log.info("Generating image with %s", model)
                image_bytes = self._call(model, prompt, aspect_ratio)
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("Model %s failed: %s", model, exc)
                last_err = exc
        else:
            raise ImageGenError(
                f"Tạo ảnh thất bại với {len(candidates)} model: {last_err}"
            ) from last_err

        path = self._save(image_bytes, scene_index=scene_index, story_slug=story_slug)
        return path

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _call(self, model: str, prompt: str, aspect_ratio: str) -> bytes:
        family = _model_family(model)
        if family == "imagen":
            return self._call_imagen(model, prompt, aspect_ratio)
        return self._call_gemini_image(model, prompt, aspect_ratio)

    def _call_imagen(self, model: str, prompt: str, aspect_ratio: str) -> bytes:
        from google.genai import types

        config = types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio=aspect_ratio if aspect_ratio in ASPECT_RATIOS else DEFAULT_ASPECT_RATIO,
        )
        response = self._client.models.generate_images(
            model=model,
            prompt=prompt,
            config=config,
        )
        if not response.generated_images:
            raise ImageGenError(
                "Imagen không trả về ảnh nào (có thể prompt bị safety filter chặn)."
            )
        generated = response.generated_images[0]
        image = generated.image
        # The SDK exposes raw bytes either via .image_bytes or by saving
        # to disk; we want bytes so we can let Pillow handle the format
        # conversion. Both paths work; prefer .image_bytes when present.
        data = getattr(image, "image_bytes", None)
        if data is None:
            # Older SDKs may need .save / .show — fall back to PIL.
            buf = BytesIO()
            image.save(buf)
            data = buf.getvalue()
        if not data:
            raise ImageGenError("Imagen trả về ảnh rỗng.")
        return data

    def _call_gemini_image(
        self, model: str, prompt: str, aspect_ratio: str
    ) -> bytes:
        from google.genai import types

        # Nano Banana doesn't have a structured aspect_ratio parameter;
        # we nudge it via the prompt instead.
        ar = aspect_ratio if aspect_ratio in ASPECT_RATIOS else DEFAULT_ASPECT_RATIO
        full_prompt = f"{prompt}\n\n(Aspect ratio: {ar})"
        response = self._client.models.generate_content(
            model=model,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise ImageGenError(
                "Gemini image model không trả về candidate nào (có thể bị safety chặn)."
            )
        parts = candidates[0].content.parts or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data
        raise ImageGenError(
            "Gemini image model không trả về phần ảnh nào (có thể bị safety chặn)."
        )

    def _save(
        self,
        data: bytes,
        *,
        scene_index: Optional[int],
        story_slug: str,
    ) -> Path:
        # Normalize to PNG via Pillow so we know the on-disk format.
        img = Image.open(BytesIO(data))
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        ts = time.strftime("%Y%m%d-%H%M%S")
        idx = f"scene{scene_index:02d}" if scene_index is not None else "img"
        safe_slug = "".join(
            c if c.isalnum() or c in ("-", "_") else "-" for c in story_slug
        ).strip("-")[:40] or "story"
        out_path = self.output_dir / f"{safe_slug}-{idx}-{ts}.png"
        img.save(out_path, format="PNG")
        log.info("Saved image → %s", out_path)
        return out_path


def _model_family(model: str) -> str:
    """Return ``"imagen"`` for Imagen Predict-API models and ``"gemini"``
    for Gemini image-output models (Nano Banana family)."""
    return "imagen" if model.lower().startswith("imagen") else "gemini"
