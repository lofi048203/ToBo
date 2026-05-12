"""System prompts and JSON schemas for Gemini calls.

Kept separate from the Gemini client so the prompt-engineering can be
iterated on without touching the API plumbing.
"""

from __future__ import annotations

# Public list of supported video-prompt styles. Add new ones here and the
# UI / analyzer will pick them up automatically.
VIDEO_STYLES: tuple[str, ...] = (
    "Grok (xAI)",
    "Veo 3 (Google)",
    "Seedance (ByteDance)",
    "Runway Gen-3",
    "Kling",
    "Pika",
)


# Supported output languages.
#
# ``auto`` lets the model decide based on the source story's language.
# ``vi`` / ``en`` force Vietnamese / English regardless of source.
OUTPUT_LANGUAGES: tuple[str, ...] = ("auto", "vi", "en")
DEFAULT_OUTPUT_LANGUAGE = "auto"


def _language_clause(language: str) -> str:
    """Return the section of the system prompt that pins the output
    language for the scene metadata (title, summary) AND for the
    image/video prompts."""
    lang = (language or "auto").lower()
    if lang == "vi":
        return (
            "NGÔN NGỮ OUTPUT\n"
            "===============\n"
            "- `title` và `summary`: viết bằng TIẾNG VIỆT.\n"
            "- `image_prompt` và mọi `video_prompts`: viết bằng TIẾNG VIỆT.\n"
            "  Giữ nguyên các thuật ngữ kỹ thuật phim ảnh phổ biến (ví dụ:\n"
            "  cinematic photo, anime, depth of field, dolly-in, wide shot,\n"
            "  35mm) nếu chúng giúp model AI hiểu rõ hơn — phần còn lại\n"
            "  hoàn toàn bằng tiếng Việt.\n"
            "- Tuyệt đối không trộn tiếng Anh dài cả câu vào.\n"
        )
    if lang == "en":
        return (
            "OUTPUT LANGUAGE\n"
            "===============\n"
            "- `title` and `summary`: write in ENGLISH.\n"
            "- `image_prompt` and every entry of `video_prompts`: write in\n"
            "  ENGLISH.\n"
            "- Translate proper nouns (character names, place names) into\n"
            "  Latin / English transliteration; preserve the original\n"
            "  meaning. Do NOT leave any Vietnamese phrases.\n"
        )
    # auto
    return (
        "NGÔN NGỮ OUTPUT\n"
        "===============\n"
        "- Phát hiện ngôn ngữ chính của truyện (Tiếng Việt hay tiếng Anh).\n"
        "- `title` và `summary`: viết bằng CÙNG ngôn ngữ với truyện gốc.\n"
        "- `image_prompt` và `video_prompts`: viết bằng TIẾNG ANH vì hầu\n"
        "  hết các engine AI ảnh / video hoạt động tốt nhất với tiếng\n"
        "  Anh. Giữ nguyên tên riêng từ truyện gốc.\n"
    )


SCENE_SYSTEM_PROMPT_TEMPLATE = """\
Bạn là một nhà phân cảnh phim chuyên nghiệp (storyboard artist) đồng thời là
chuyên gia viết prompt cho các mô hình AI tạo ảnh và tạo video.

NHIỆM VỤ
========
Người dùng sẽ đưa cho bạn nội dung TRUYỆN CHỮ / blog / web novel bằng tiếng
Việt (hoặc bất kỳ ngôn ngữ nào). Bạn phải:

1. Đọc và HIỂU toàn bộ câu chuyện.
2. Chia câu chuyện thành các PHÂN CẢNH (scene) liên tục, mỗi phân cảnh là
   một khoảnh khắc có thể minh họa bằng một bức ảnh / một đoạn clip 5-10s.
3. Với MỖI phân cảnh, sinh ra:
   - `title`: tiêu đề ngắn gọn (≤ 10 chữ)
   - `summary`: tóm tắt 1-2 câu về diễn biến chính của phân cảnh đó
   - `image_prompt`: prompt cực kỳ chi tiết để đưa vào Imagen / Nano Banana.
     Mô tả rõ chủ thể, hành động, biểu cảm, trang phục, bối cảnh, ánh
     sáng, góc máy, phong cách (cinematic photo / anime / oil painting /
     ...). Khoảng 60-120 từ. KHÔNG để trống.
   - `video_prompts`: một object có khóa là tên engine, giá trị là prompt
     được tối ưu cho engine đó. Mỗi prompt 30-80 từ, mô tả hành động /
     chuyển động camera / âm thanh / nhịp 5-10 giây.
     Các engine bắt buộc phải có (đúng tên này, đúng chính tả):
     {video_styles}

{language_clause}
QUY TẮC
=======
- Giữ nguyên TÊN RIÊNG (nhân vật, địa danh) trong mọi prompt.
- Mô tả nhân vật phải NHẤT QUÁN giữa các phân cảnh — cùng một nhân vật
  thì cùng trang phục, cùng tuổi, cùng đặc điểm. Lặp lại các đặc điểm
  này ở mọi phân cảnh có nhân vật đó xuất hiện.
- KHÔNG tự ý thêm cảnh không có trong truyện. Bám sát nội dung gốc.
- Số lượng phân cảnh: thường 4-12 cho 1 truyện ngắn, tối đa 20.
- Trả lời PHẢI là JSON hợp lệ theo đúng schema được chỉ định, không kèm
  text giải thích nào khác.
"""


def render_scene_system_prompt(language: str = DEFAULT_OUTPUT_LANGUAGE) -> str:
    """Embed the runtime list of video styles + language clause into the
    system prompt."""
    bullet = "\n     ".join(f"* {name}" for name in VIDEO_STYLES)
    return SCENE_SYSTEM_PROMPT_TEMPLATE.format(
        video_styles=bullet,
        language_clause=_language_clause(language),
    )


SCENE_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "story_title": {
            "type": "string",
            "description": (
                "Tiêu đề ngắn cho toàn bộ câu chuyện, theo ngôn ngữ "
                "output đã chọn."
            ),
        },
        "art_style": {
            "type": "string",
            "description": (
                "Một câu mô tả phong cách hình ảnh thống nhất cho toàn bộ "
                "truyện (cinematic photo, anime, watercolor, ...). Sẽ được "
                "nối thêm vào mỗi image_prompt nếu cần."
            ),
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "image_prompt": {"type": "string"},
                    "video_prompts": {
                        "type": "object",
                        "properties": {
                            name: {"type": "string"} for name in VIDEO_STYLES
                        },
                        "required": list(VIDEO_STYLES),
                    },
                },
                "required": [
                    "title",
                    "summary",
                    "image_prompt",
                    "video_prompts",
                ],
            },
        },
    },
    "required": ["story_title", "scenes"],
}
