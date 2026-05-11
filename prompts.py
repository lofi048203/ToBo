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


SCENE_SYSTEM_PROMPT = """\
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
   - `title`: tiêu đề ngắn gọn bằng tiếng Việt (≤ 10 chữ)
   - `summary`: tóm tắt 1-2 câu bằng tiếng Việt về diễn biến chính của
     phân cảnh đó
   - `image_prompt`: prompt TIẾNG ANH cực kỳ chi tiết để đưa vào Imagen.
     Mô tả rõ chủ thể, hành động, biểu cảm, trang phục, bối cảnh, ánh
     sáng, góc máy, phong cách (cinematic photo / anime / oil painting /
     ...). Khoảng 60-120 từ. KHÔNG để trống.
   - `video_prompts`: một object có khóa là tên engine, giá trị là prompt
     TIẾNG ANH được tối ưu cho engine đó. Mỗi prompt 30-80 từ, mô tả
     hành động / chuyển động camera / âm thanh / nhịp 5-10 giây.
     Các engine bắt buộc phải có (đúng tên này, đúng chính tả):
     {video_styles}

QUY TẮC
=======
- Image prompt và video prompt phải BẰNG TIẾNG ANH vì các engine chỉ hỗ
  trợ tiếng Anh. Tuyệt đối không xen tiếng Việt vào.
- Giữ nguyên TÊN RIÊNG (nhân vật, địa danh) trong prompt tiếng Anh.
- Mô tả nhân vật phải NHẤT QUÁN giữa các phân cảnh — cùng một nhân vật
  thì cùng trang phục, cùng tuổi, cùng đặc điểm. Lặp lại các đặc điểm
  này ở mọi phân cảnh có nhân vật đó xuất hiện.
- KHÔNG tự ý thêm cảnh không có trong truyện. Bám sát nội dung gốc.
- Số lượng phân cảnh: thường 4-12 cho 1 truyện ngắn, tối đa 20.
- Trả lời PHẢI là JSON hợp lệ theo đúng schema được chỉ định, không kèm
  text giải thích nào khác.
"""


def render_scene_system_prompt() -> str:
    """Embed the runtime list of video styles into the system prompt."""
    bullet = "\n     ".join(f"* {name}" for name in VIDEO_STYLES)
    return SCENE_SYSTEM_PROMPT.format(video_styles=bullet)


SCENE_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "story_title": {
            "type": "string",
            "description": "Tiêu đề ngắn cho toàn bộ câu chuyện (tiếng Việt).",
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
