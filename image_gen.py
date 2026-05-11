"""Image generation via Gemini's Imagen models."""

from __future__ import annotations

import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

log = logging.getLogger(__name__)

# Tested Imagen model names ordered by preference. The first one that
# accepts the request is used. We try the standard model first, then
# fall back to the fast/cheap variant if the account does not have
# access to the standard one.
PREFERRED_IMAGE_MODELS: tuple[str, ...] = (
    "imagen-4.0-generate-001",
    "imagen-3.0-generate-002",
    "imagen-3.0-fast-generate-001",
)

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
    ) -> None:
        from google import genai

        if not api_key:
            raise ValueError("api_key is required")
        self._client = genai.Client(api_key=api_key)
        self._preferred_model = model
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

        candidates: list[str] = []
        if self._preferred_model:
            candidates.append(self._preferred_model)
        for m in PREFERRED_IMAGE_MODELS:
            if m not in candidates:
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
                f"Tạo ảnh thất bại với tất cả model: {last_err}"
            ) from last_err

        path = self._save(image_bytes, scene_index=scene_index, story_slug=story_slug)
        return path

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _call(self, model: str, prompt: str, aspect_ratio: str) -> bytes:
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
