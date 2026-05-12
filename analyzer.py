"""Story analyzer: fetch a URL, extract the readable text, then ask Gemini
to split it into scenes with image + video prompts.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

from prompts import (
    DEFAULT_OUTPUT_LANGUAGE,
    SCENE_RESPONSE_SCHEMA,
    VIDEO_STYLES,
    render_scene_system_prompt,
)

log = logging.getLogger(__name__)


# Available Gemini text models for scene extraction. ``gemini-2.5-flash``
# is the default because it works on the free tier and handles
# Vietnamese well enough for short-to-medium stories. ``gemini-2.5-pro``
# gives better reasoning but is gated behind a paid plan on most newer
# accounts.
DEFAULT_TEXT_MODEL = "gemini-2.5-flash"
TEXT_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash",  # free tier, default
    "gemini-2.5-pro",    # paid tier, deeper reasoning
)
# Kept for backward compatibility with old imports.
FAST_TEXT_MODEL = DEFAULT_TEXT_MODEL

# Hard cap to keep token usage sane. ~30k characters ≈ ~10k tokens for
# Vietnamese which fits comfortably in Gemini's context window with
# room for the schema'd response.
MAX_INPUT_CHARS = 30_000


@dataclass
class Scene:
    index: int
    title: str
    summary: str
    image_prompt: str
    video_prompts: dict[str, str] = field(default_factory=dict)
    generated_image_path: Optional[str] = None


@dataclass
class StoryAnalysis:
    source_url: Optional[str]
    story_title: str
    art_style: str
    scenes: list[Scene]
    raw_text: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "source_url": self.source_url,
                "story_title": self.story_title,
                "art_style": self.art_style,
                "scenes": [
                    {
                        "index": s.index,
                        "title": s.title,
                        "summary": s.summary,
                        "image_prompt": s.image_prompt,
                        "video_prompts": s.video_prompts,
                        "generated_image_path": s.generated_image_path,
                    }
                    for s in self.scenes
                ],
            },
            ensure_ascii=False,
            indent=2,
        )


# ---------------------------------------------------------------------------
# URL fetching
# ---------------------------------------------------------------------------

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class FetchError(RuntimeError):
    """Raised when a URL cannot be fetched or contains no readable text."""


def fetch_story_text(url: str, *, timeout: int = 20) -> tuple[str, str]:
    """Fetch ``url`` and return ``(title, plain_text)``.

    Uses ``readability-lxml`` to strip nav/ads/comments and keep only the
    main article body. Falls back to a BeautifulSoup full-page extraction
    if readability fails.
    """
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "vi,en;q=0.7"}
    log.info("Fetching %s", url)
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    title = ""
    text = ""
    try:
        from readability import Document

        doc = Document(html)
        title = (doc.short_title() or "").strip()
        summary_html = doc.summary(html_partial=True)
        text = _html_to_text(summary_html)
    except Exception as exc:  # noqa: BLE001 — fall back to soup
        log.warning("readability failed (%s); falling back to BeautifulSoup", exc)

    if not text or len(text) < 200:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
            tag.decompose()
        body = soup.body or soup
        text = _normalize_whitespace(body.get_text("\n"))
        if not title:
            t = soup.find("title")
            title = (t.text.strip() if t else "")

    if not text.strip():
        raise FetchError(
            f"Không trích xuất được nội dung văn bản từ {url}. "
            "Có thể trang yêu cầu đăng nhập hoặc dùng JavaScript để render."
        )

    return title, text


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return _normalize_whitespace(soup.get_text("\n"))


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Gemini analysis
# ---------------------------------------------------------------------------


class StoryAnalyzer:
    """Wraps the google-genai client for scene extraction."""

    def __init__(self, api_key: str, *, model: str = DEFAULT_TEXT_MODEL) -> None:
        from google import genai

        if not api_key:
            raise ValueError("api_key is required")
        self._client = genai.Client(api_key=api_key)
        self.model = model

    def analyze_text(
        self,
        story_text: str,
        *,
        max_scenes: Optional[int] = None,
        extra_style_hint: str = "",
        source_url: Optional[str] = None,
        language: str = DEFAULT_OUTPUT_LANGUAGE,
    ) -> StoryAnalysis:
        from google.genai import types

        story_text = story_text.strip()
        if not story_text:
            raise ValueError("story_text is empty")
        if len(story_text) > MAX_INPUT_CHARS:
            log.warning(
                "Story text is %d chars; truncating to %d to fit context.",
                len(story_text),
                MAX_INPUT_CHARS,
            )
            story_text = story_text[:MAX_INPUT_CHARS]

        user_msg = _build_user_message(
            story_text=story_text,
            max_scenes=max_scenes,
            extra_style_hint=extra_style_hint,
            language=language,
        )

        log.info(
            "Calling Gemini %s for scene analysis (lang=%s)",
            self.model,
            language,
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=user_msg,
            config=types.GenerateContentConfig(
                system_instruction=render_scene_system_prompt(language),
                response_mime_type="application/json",
                response_schema=SCENE_RESPONSE_SCHEMA,
                temperature=0.7,
            ),
        )

        text = response.text or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Gemini trả về JSON không hợp lệ: {exc}\n\n{text[:500]}"
            ) from exc

        scenes_raw = data.get("scenes") or []
        scenes: list[Scene] = []
        for idx, item in enumerate(scenes_raw, start=1):
            video_prompts = {
                name: (item.get("video_prompts") or {}).get(name, "").strip()
                for name in VIDEO_STYLES
            }
            scenes.append(
                Scene(
                    index=idx,
                    title=(item.get("title") or f"Cảnh {idx}").strip(),
                    summary=(item.get("summary") or "").strip(),
                    image_prompt=(item.get("image_prompt") or "").strip(),
                    video_prompts=video_prompts,
                )
            )

        return StoryAnalysis(
            source_url=source_url,
            story_title=(data.get("story_title") or "").strip(),
            art_style=(data.get("art_style") or "").strip(),
            scenes=scenes,
            raw_text=story_text,
        )


def _build_user_message(
    *,
    story_text: str,
    max_scenes: Optional[int],
    extra_style_hint: str,
    language: str = DEFAULT_OUTPUT_LANGUAGE,
) -> str:
    parts: list[str] = []
    parts.append("Hãy phân tích CÂU CHUYỆN dưới đây thành các phân cảnh.")
    if max_scenes:
        parts.append(f"Số phân cảnh tối đa: {max_scenes}.")
    lang = (language or "auto").lower()
    if lang == "vi":
        parts.append(
            "Ngôn ngữ output: TIẾNG VIỆT cho tất cả các trường "
            "(title, summary, image_prompt, video_prompts)."
        )
    elif lang == "en":
        parts.append(
            "Output language: ENGLISH for every field (title, summary, "
            "image_prompt, video_prompts)."
        )
    else:
        parts.append(
            "Ngôn ngữ output: tự động theo ngôn ngữ của truyện cho "
            "title/summary; giữ nguyên TIẾNG ANH cho image_prompt và "
            "video_prompts."
        )
    if extra_style_hint.strip():
        parts.append(
            f"Phong cách hình ảnh mong muốn: {extra_style_hint.strip()}. "
            f"Hãy nhắc lại phong cách này trong mỗi image_prompt."
        )
    parts.append("=== NỘI DUNG TRUYỆN ===")
    parts.append(story_text)
    return "\n\n".join(parts)
