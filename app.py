"""
StoryViz — Story → Image / Video prompt generator
=================================================

Desktop app powered by Google Gemini.

Workflow:
    1. Paste a URL to a Vietnamese / English novel / blog / story.
    2. The app downloads the article, extracts the main body, and asks
       Gemini to split it into scenes.
    3. Each scene gets:
        - A detailed English image prompt (used directly with Imagen)
        - English video prompts tuned for Grok, Veo 3, Seedance, Runway,
          Kling, and Pika.
    4. Click "Tạo ảnh" on any scene to generate the actual image via
       Imagen (uses the same Google AI API key).

Just run ``python app.py`` (or ``run.sh`` / ``run.bat``).
"""

from __future__ import annotations

import logging
import os
import queue
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk
from PIL import Image

from analyzer import (
    DEFAULT_TEXT_MODEL,
    TEXT_MODELS,
    FetchError,
    Scene,
    StoryAnalysis,
    StoryAnalyzer,
    fetch_story_text,
)
from image_gen import (
    ASPECT_RATIOS,
    DEFAULT_ASPECT_RATIO,
    IMAGE_MODELS,
    ImageGenError,
    ImageGenerator,
)
from prompts import VIDEO_STYLES


APP_TITLE = "StoryViz"
APP_SUBTITLE = "Phân tích truyện → prompt ảnh & video AI"

PRIMARY = ("#7c3aed", "#a78bfa")  # violet
PRIMARY_HOVER = ("#6d28d9", "#c4b5fd")
ACCENT = ("#0891b2", "#22d3ee")
DANGER = ("#dc2626", "#ef4444")
MUTED = ("#64748b", "#94a3b8")
CARD_BG = ("#ffffff", "#1f2937")
CARD_BORDER = ("#e2e8f0", "#334155")
APP_BG = ("#f5f3ff", "#0f172a")  # subtle violet wash on light mode


# ---------------------------------------------------------------------------
# UI building blocks
# ---------------------------------------------------------------------------


class Card(ctk.CTkFrame):
    """A bordered, rounded section with a small header label.

    If ``collapsible=True`` a small chevron button is shown on the right
    of the header. Clicking it toggles the body visibility, freeing
    vertical space for the scene list below.
    """

    def __init__(
        self,
        master,
        *,
        title: str,
        step: Optional[str] = None,
        subtitle: Optional[str] = None,
        collapsible: bool = False,
        start_collapsed: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            corner_radius=14,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
            border_width=1,
            **kwargs,
        )
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 6))

        if step:
            ctk.CTkLabel(
                header,
                text=step,
                width=26,
                height=26,
                corner_radius=13,
                fg_color=PRIMARY,
                text_color="#ffffff",
                font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(side="left", padx=(0, 10))

        text_box = ctk.CTkFrame(header, fg_color="transparent")
        text_box.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            text_box,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(anchor="w", fill="x")
        if subtitle:
            self._subtitle_label = ctk.CTkLabel(
                text_box,
                text=subtitle,
                font=ctk.CTkFont(size=11),
                text_color=MUTED,
                anchor="w",
            )
            self._subtitle_label.pack(anchor="w", fill="x")
        else:
            self._subtitle_label = None

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        self._expanded = True
        self._toggle_btn: Optional[ctk.CTkButton] = None
        if collapsible:
            self._toggle_btn = ctk.CTkButton(
                header,
                text="⌃",
                width=32,
                height=32,
                corner_radius=8,
                fg_color="transparent",
                hover_color=CARD_BORDER,
                text_color=("#1e293b", "#e2e8f0"),
                font=ctk.CTkFont(size=18, weight="bold"),
                command=self.toggle,
            )
            self._toggle_btn.pack(side="right", padx=(8, 0))
            if start_collapsed:
                # Defer the collapse so the body has been laid out first
                # (otherwise CTk can complain about unmanaged widgets).
                self.after(0, self.collapse)

    def toggle(self) -> None:
        if self._expanded:
            self.collapse()
        else:
            self.expand()

    def collapse(self) -> None:
        if not self._expanded:
            return
        self.body.pack_forget()
        if self._subtitle_label is not None:
            self._subtitle_label.pack_forget()
        if self._toggle_btn is not None:
            self._toggle_btn.configure(text="⌄")
        self._expanded = False

    def expand(self) -> None:
        if self._expanded:
            return
        if self._subtitle_label is not None:
            self._subtitle_label.pack(anchor="w", fill="x")
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        if self._toggle_btn is not None:
            self._toggle_btn.configure(text="⌃")
        self._expanded = True


class SceneCard(ctk.CTkFrame):
    """One scrollable card per scene with image+video prompt tabs."""

    def __init__(
        self,
        master,
        *,
        scene: Scene,
        on_generate_image,
        on_open_image,
    ) -> None:
        super().__init__(
            master,
            corner_radius=14,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
            border_width=1,
        )
        self.scene = scene
        self._on_generate_image = on_generate_image
        self._on_open_image = on_open_image
        self._image_label: Optional[ctk.CTkLabel] = None
        self._pil_preview: Optional[Image.Image] = None
        self._ctk_image: Optional[ctk.CTkImage] = None

        # ----- Header -----
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 4))

        ctk.CTkLabel(
            header,
            text=f"#{scene.index:02d}",
            width=46,
            height=28,
            corner_radius=14,
            fg_color=PRIMARY,
            text_color="#ffffff",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(
            header,
            text=scene.title or f"Cảnh {scene.index}",
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # ----- Summary -----
        ctk.CTkLabel(
            self,
            text=scene.summary or "(không có tóm tắt)",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
            anchor="w",
            justify="left",
            wraplength=820,
        ).pack(fill="x", padx=16, pady=(0, 10))

        # ----- Two-column body: tabs on the left, preview on the right -----
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        body.grid_columnconfigure(0, weight=3, uniform="scene")
        body.grid_columnconfigure(1, weight=2, uniform="scene")
        body.grid_rowconfigure(0, weight=1)

        self._build_tabs(body).grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._build_preview(body).grid(row=0, column=1, sticky="nsew")

    # ------------------------------------------------------------------

    def _build_tabs(self, master) -> ctk.CTkTabview:
        tabs = ctk.CTkTabview(
            master,
            corner_radius=10,
            segmented_button_selected_color=PRIMARY,
            segmented_button_selected_hover_color=PRIMARY_HOVER,
        )
        tabs.add("🖼 Ảnh (Imagen)")
        self._add_prompt_pane(
            tabs.tab("🖼 Ảnh (Imagen)"),
            self.scene.image_prompt,
            kind="image",
            engine="Imagen",
        )
        for engine in VIDEO_STYLES:
            label = f"🎬 {engine}"
            tabs.add(label)
            self._add_prompt_pane(
                tabs.tab(label),
                self.scene.video_prompts.get(engine, ""),
                kind="video",
                engine=engine,
            )
        tabs.set("🖼 Ảnh (Imagen)")
        return tabs

    def _add_prompt_pane(
        self, parent, text: str, *, kind: str, engine: str
    ) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)

        textbox = ctk.CTkTextbox(
            parent,
            wrap="word",
            font=ctk.CTkFont(size=12),
            corner_radius=8,
            height=140,
        )
        textbox.grid(row=0, column=0, sticky="nsew", pady=(8, 6))
        textbox.insert("1.0", text or "(prompt rỗng)")

        button_row = ctk.CTkFrame(parent, fg_color="transparent")
        button_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        def copy_to_clipboard() -> None:
            payload = textbox.get("1.0", "end").strip()
            try:
                self.clipboard_clear()
                self.clipboard_append(payload)
                self.update()
            except Exception:  # noqa: BLE001
                pass

        ctk.CTkButton(
            button_row,
            text="📋 Copy prompt",
            command=copy_to_clipboard,
            height=32,
            corner_radius=8,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            button_row,
            text=f"Engine: {engine}",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).pack(side="right")

        # Persist edits made by the user back into the Scene object.
        def on_change(_event=None) -> None:
            value = textbox.get("1.0", "end").rstrip()
            if kind == "image":
                self.scene.image_prompt = value
            else:
                self.scene.video_prompts[engine] = value

        textbox.bind("<FocusOut>", on_change)
        textbox.bind("<KeyRelease>", on_change)

    # ------------------------------------------------------------------

    def _build_preview(self, master) -> ctk.CTkFrame:
        wrapper = ctk.CTkFrame(
            master,
            corner_radius=10,
            fg_color=APP_BG,
            border_color=CARD_BORDER,
            border_width=1,
        )

        self._image_label = ctk.CTkLabel(
            wrapper,
            text="Chưa có ảnh.\n\nBấm 'Tạo ảnh' để render bằng Imagen.",
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
            height=200,
        )
        self._image_label.pack(fill="both", expand=True, padx=10, pady=10)

        row = ctk.CTkFrame(wrapper, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(0, 10))

        self._gen_btn = ctk.CTkButton(
            row,
            text="🎨 Tạo ảnh",
            command=lambda: self._on_generate_image(self),
            height=36,
            corner_radius=10,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._gen_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self._open_btn = ctk.CTkButton(
            row,
            text="📂 Mở file",
            command=lambda: self._on_open_image(self),
            height=36,
            corner_radius=10,
            fg_color="transparent",
            hover_color=CARD_BORDER,
            border_color=PRIMARY,
            border_width=1,
            text_color=PRIMARY,
            state="disabled",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._open_btn.pack(side="left")

        return wrapper

    # ------------------------------------------------------------------

    def set_generating(self, generating: bool) -> None:
        if generating:
            self._gen_btn.configure(text="⏳ Đang tạo...", state="disabled")
            if self._image_label:
                self._image_label.configure(
                    text="Đang gọi Imagen, vui lòng đợi...",
                    image="",
                )
        else:
            self._gen_btn.configure(text="🎨 Tạo lại ảnh", state="normal")

    def set_image(self, path: Path) -> None:
        self.scene.generated_image_path = str(path)
        try:
            img = Image.open(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Lỗi", f"Không mở được ảnh đã tạo: {exc}")
            return
        self._pil_preview = img
        # Fit into a max 380x280 preview while keeping aspect ratio.
        max_w, max_h = 380, 280
        w, h = img.size
        scale = min(max_w / w, max_h / h, 1.0)
        size = (max(1, int(w * scale)), max(1, int(h * scale)))
        self._ctk_image = ctk.CTkImage(light_image=img, dark_image=img, size=size)
        if self._image_label:
            self._image_label.configure(image=self._ctk_image, text="")
        self._open_btn.configure(state="normal")


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------


class StoryVizApp:
    def __init__(self) -> None:
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title(f"{APP_TITLE} — {APP_SUBTITLE}")
        self.root.geometry("1100x780")
        self.root.minsize(960, 640)
        self.root.configure(fg_color=APP_BG)

        # -------- State --------
        self.url_var = ctk.StringVar()
        self.api_key_var = ctk.StringVar(value=os.environ.get("GEMINI_API_KEY", ""))
        self.style_hint_var = ctk.StringVar(value="cinematic photo")
        self.aspect_var = ctk.StringVar(value=DEFAULT_ASPECT_RATIO)
        self.model_var = ctk.StringVar(value=DEFAULT_TEXT_MODEL)
        self.image_model_var = ctk.StringVar(value=IMAGE_MODELS[0])
        self.max_scenes_var = ctk.StringVar(value="8")
        self.status_var = ctk.StringVar(value="● Sẵn sàng")
        self.appearance_var = ctk.StringVar(value="System")

        self.analysis: Optional[StoryAnalysis] = None
        self.scene_cards: list[SceneCard] = []
        self._worker: Optional[threading.Thread] = None
        self._image_workers: dict[int, threading.Thread] = {}
        self._log_queue: "queue.Queue[str]" = queue.Queue()

        self._build_ui()
        self.root.after(120, self._drain_log_queue)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_header()

        body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=(0, 12))

        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        self._build_input_card(body).grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        self._build_options_card(body).grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        self._build_scene_area(body).grid(row=2, column=0, sticky="nsew")

        self._build_status_bar()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self.root, fg_color="transparent", height=72)
        header.pack(fill="x", padx=24, pady=(18, 12))

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left")

        ctk.CTkLabel(
            title_box,
            text="📖",
            font=ctk.CTkFont(size=30),
        ).pack(side="left", padx=(0, 12))

        text_box = ctk.CTkFrame(title_box, fg_color="transparent")
        text_box.pack(side="left")
        ctk.CTkLabel(
            text_box,
            text=APP_TITLE,
            font=ctk.CTkFont(size=26, weight="bold"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            text_box,
            text=APP_SUBTITLE,
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
            anchor="w",
        ).pack(anchor="w")

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right")

        ctk.CTkLabel(
            right, text="Giao diện:", font=ctk.CTkFont(size=12), text_color=MUTED
        ).pack(side="left", padx=(0, 8))
        ctk.CTkSegmentedButton(
            right,
            values=["Light", "Dark", "System"],
            variable=self.appearance_var,
            command=lambda v: ctk.set_appearance_mode(v),
            corner_radius=8,
        ).pack(side="left")

    def _build_input_card(self, master) -> ctk.CTkFrame:
        card = Card(
            master,
            title="Link truyện / blog / web novel",
            subtitle="Dán link bài đăng có nội dung văn bản. App sẽ tự bóc lấy phần nội dung chính.",
            step="1",
            collapsible=True,
        )
        body = card.body
        body.grid_columnconfigure(0, weight=1)

        ctk.CTkEntry(
            body,
            textvariable=self.url_var,
            placeholder_text="https://...",
            height=42,
            font=ctk.CTkFont(size=13),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self._analyze_btn = ctk.CTkButton(
            body,
            text="🔍 Phân tích",
            command=self._on_analyze_clicked,
            height=42,
            width=160,
            corner_radius=10,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
        )
        self._analyze_btn.grid(row=0, column=1)

        ctk.CTkLabel(
            body,
            text=(
                "Mẹo: nếu trang yêu cầu đăng nhập, hãy dán trực tiếp văn bản vào ô "
                "“dán văn bản” bên dưới thay vì link."
            ),
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
            anchor="w",
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 8))

        self._raw_textbox = ctk.CTkTextbox(
            body,
            height=90,
            wrap="word",
            corner_radius=8,
            font=ctk.CTkFont(size=12),
        )
        self._raw_textbox.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self._raw_textbox.insert(
            "1.0",
            "(Tùy chọn) Dán văn bản truyện trực tiếp vào đây nếu không muốn dùng link.",
        )
        self._raw_textbox.bind("<FocusIn>", self._clear_raw_placeholder_once)
        self._raw_textbox._placeholder_cleared = False  # type: ignore[attr-defined]

        return card

    def _clear_raw_placeholder_once(self, _event=None) -> None:
        if getattr(self._raw_textbox, "_placeholder_cleared", False):
            return
        self._raw_textbox.delete("1.0", "end")
        self._raw_textbox._placeholder_cleared = True  # type: ignore[attr-defined]

    def _build_options_card(self, master) -> ctk.CTkFrame:
        card = Card(
            master,
            title="API key + tuỳ chọn",
            subtitle="Cần Google AI API key. Lấy miễn phí tại aistudio.google.com/app/apikey.",
            step="2",
            collapsible=True,
        )
        body = card.body
        body.grid_columnconfigure(0, weight=2)
        body.grid_columnconfigure(1, weight=1)
        body.grid_columnconfigure(2, weight=1)
        body.grid_columnconfigure(3, weight=1)

        # Row 0: API key
        ctk.CTkLabel(
            body, text="Google AI API key", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkEntry(
            body,
            textvariable=self.api_key_var,
            placeholder_text="Bắt đầu bằng AIza...",
            height=36,
            show="•",
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(2, 12))

        # Row 0/1: model + image model + aspect + scenes
        ctk.CTkLabel(
            body, text="Gemini model", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkOptionMenu(
            body,
            variable=self.model_var,
            values=list(TEXT_MODELS),
            height=36,
            corner_radius=10,
            fg_color=CARD_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(2, 12))

        ctk.CTkLabel(
            body, text="Image model", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=2, sticky="w")
        ctk.CTkOptionMenu(
            body,
            variable=self.image_model_var,
            values=list(IMAGE_MODELS),
            height=36,
            corner_radius=10,
            fg_color=CARD_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
        ).grid(row=1, column=2, sticky="ew", padx=(0, 10), pady=(2, 12))

        ctk.CTkLabel(
            body, text="Tỉ lệ ảnh", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=3, sticky="w")
        ctk.CTkOptionMenu(
            body,
            variable=self.aspect_var,
            values=list(ASPECT_RATIOS),
            height=36,
            corner_radius=10,
            fg_color=CARD_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
        ).grid(row=1, column=3, sticky="ew", pady=(2, 12))

        # Row 2/3: style hint + max scenes
        ctk.CTkLabel(
            body, text="Phong cách hình ảnh (tiếng Anh)", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=2, column=0, sticky="w")
        ctk.CTkEntry(
            body,
            textvariable=self.style_hint_var,
            placeholder_text="ví dụ: cinematic photo, anime, watercolor, pixar 3d, ...",
            height=36,
            font=ctk.CTkFont(size=12),
        ).grid(row=3, column=0, sticky="ew", padx=(0, 10))

        ctk.CTkLabel(
            body, text="Số phân cảnh tối đa", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=2, column=1, sticky="w")
        ctk.CTkOptionMenu(
            body,
            variable=self.max_scenes_var,
            values=["4", "6", "8", "10", "12", "16", "20"],
            height=36,
            corner_radius=10,
            fg_color=CARD_BG,
            button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER,
        ).grid(row=3, column=1, sticky="ew", padx=(0, 10))

        # Row 2/3: output dir
        ctk.CTkLabel(
            body, text="Thư mục lưu ảnh", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=2, column=2, columnspan=2, sticky="w")

        out_row = ctk.CTkFrame(body, fg_color="transparent")
        out_row.grid(row=3, column=2, columnspan=2, sticky="ew")
        out_row.grid_columnconfigure(0, weight=1)

        self.output_dir_var = ctk.StringVar(value=str(Path.cwd() / "generated"))
        ctk.CTkEntry(
            out_row,
            textvariable=self.output_dir_var,
            height=36,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            out_row,
            text="…",
            command=self._pick_output_dir,
            width=36,
            height=36,
            corner_radius=8,
            fg_color="transparent",
            hover_color=CARD_BORDER,
            border_color=PRIMARY,
            border_width=1,
            text_color=PRIMARY,
        ).grid(row=0, column=1)

        return card

    def _build_scene_area(self, master) -> ctk.CTkFrame:
        wrapper = ctk.CTkFrame(
            master,
            corner_radius=14,
            fg_color=CARD_BG,
            border_color=CARD_BORDER,
            border_width=1,
        )

        header = ctk.CTkFrame(wrapper, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 6))

        ctk.CTkLabel(
            header,
            text="3",
            width=26,
            height=26,
            corner_radius=13,
            fg_color=PRIMARY,
            text_color="#ffffff",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=(0, 10))

        self._scene_title = ctk.CTkLabel(
            header,
            text="Phân cảnh sẽ hiển thị ở đây sau khi phân tích",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        self._scene_title.pack(side="left", fill="x", expand=True)

        self._generate_all_btn = ctk.CTkButton(
            header,
            text="🎨 Tạo ảnh tất cả",
            command=self._on_generate_all_clicked,
            height=32,
            corner_radius=10,
            fg_color=ACCENT,
            hover_color=PRIMARY_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            state="disabled",
        )
        self._generate_all_btn.pack(side="right")

        self._scene_scroll = ctk.CTkScrollableFrame(
            wrapper,
            fg_color="transparent",
        )
        self._scene_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        self._scene_placeholder = ctk.CTkLabel(
            self._scene_scroll,
            text=(
                "📭 Chưa có dữ liệu.\n\n"
                "1. Dán link ở ô trên (hoặc dán văn bản trực tiếp).\n"
                "2. Nhập API key Google AI.\n"
                "3. Bấm 'Phân tích'."
            ),
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
            justify="center",
        )
        self._scene_placeholder.pack(pady=60)

        return wrapper

    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self.root, fg_color="transparent", height=28)
        bar.pack(fill="x", padx=24, pady=(0, 12))

        ctk.CTkLabel(
            bar,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        ).pack(side="left")

        ctk.CTkLabel(
            bar,
            text="Powered by Gemini · Imagen · Nano Banana",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        ).pack(side="right")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _pick_output_dir(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self.output_dir_var.get() or os.getcwd(),
            title="Chọn thư mục lưu ảnh",
        )
        if chosen:
            self.output_dir_var.set(chosen)

    def _on_analyze_clicked(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Đang xử lý", "Đang phân tích, vui lòng đợi.")
            return

        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning(
                "Thiếu API key",
                "Hãy nhập Google AI API key (lấy ở aistudio.google.com/app/apikey).",
            )
            return

        url = self.url_var.get().strip()
        pasted = self._get_pasted_text()
        if not url and not pasted:
            messagebox.showwarning(
                "Thiếu nội dung",
                "Hãy dán link truyện hoặc dán văn bản trực tiếp vào ô bên dưới.",
            )
            return

        try:
            max_scenes = int(self.max_scenes_var.get())
        except ValueError:
            max_scenes = 8

        self._analyze_btn.configure(text="⏳ Đang phân tích...", state="disabled")
        self._set_status("● Đang tải nội dung và gọi Gemini...")

        self._worker = threading.Thread(
            target=self._run_analyze,
            args=(api_key, url, pasted, max_scenes),
            daemon=True,
        )
        self._worker.start()

    def _get_pasted_text(self) -> str:
        if not getattr(self._raw_textbox, "_placeholder_cleared", False):
            return ""
        return self._raw_textbox.get("1.0", "end").strip()

    def _run_analyze(
        self, api_key: str, url: str, pasted: str, max_scenes: int
    ) -> None:
        try:
            text = pasted
            source_url: Optional[str] = None
            if url and not pasted:
                source_url = url
                self._log_status("● Đang tải URL...")
                title, text = fetch_story_text(url)
                if title:
                    self._log_status(f"● Đã lấy nội dung: {title}")

            if not text or len(text) < 50:
                raise FetchError("Nội dung quá ngắn để phân tích (< 50 ký tự).")

            self._log_status(
                f"● Đang gọi {self.model_var.get()} để chia phân cảnh..."
            )
            analyzer = StoryAnalyzer(api_key=api_key, model=self.model_var.get())
            analysis = analyzer.analyze_text(
                text,
                max_scenes=max_scenes,
                extra_style_hint=self.style_hint_var.get(),
                source_url=source_url,
            )
            self.root.after(0, lambda: self._render_analysis(analysis))
        except Exception as exc:  # noqa: BLE001
            err_msg = str(exc)
            tb = traceback.format_exc()
            logging.error("analyze failed:\n%s", tb)
            self.root.after(
                0,
                lambda: self._on_analyze_failed(err_msg),
            )

    def _on_analyze_failed(self, msg: str) -> None:
        self._analyze_btn.configure(text="🔍 Phân tích", state="normal")
        self._set_status("● Lỗi: " + msg[:80])
        messagebox.showerror("Phân tích thất bại", msg)

    def _render_analysis(self, analysis: StoryAnalysis) -> None:
        self.analysis = analysis
        for child in self._scene_scroll.winfo_children():
            child.destroy()
        self.scene_cards.clear()

        if not analysis.scenes:
            ctk.CTkLabel(
                self._scene_scroll,
                text="Gemini không tách được phân cảnh nào.",
                text_color=MUTED,
                font=ctk.CTkFont(size=13),
            ).pack(pady=40)
            self._analyze_btn.configure(text="🔍 Phân tích lại", state="normal")
            return

        self._scene_title.configure(
            text=(
                f"{analysis.story_title or 'Câu chuyện'}"
                f" — {len(analysis.scenes)} phân cảnh"
            )
        )

        for scene in analysis.scenes:
            card = SceneCard(
                self._scene_scroll,
                scene=scene,
                on_generate_image=self._on_generate_image,
                on_open_image=self._on_open_image,
            )
            card.pack(fill="x", expand=True, pady=(0, 12))
            self.scene_cards.append(card)

        self._analyze_btn.configure(text="🔍 Phân tích lại", state="normal")
        self._generate_all_btn.configure(state="normal")
        self._set_status(f"● Đã tách {len(analysis.scenes)} phân cảnh. Sẵn sàng tạo ảnh.")

    # ------------------------------------------------------------------
    # Image generation
    # ------------------------------------------------------------------

    def _on_generate_image(self, card: SceneCard) -> None:
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("Thiếu API key", "Hãy nhập Google AI API key.")
            return

        worker = self._image_workers.get(card.scene.index)
        if worker and worker.is_alive():
            messagebox.showinfo("Đang xử lý", f"Cảnh {card.scene.index} đang được tạo ảnh.")
            return

        prompt = card.scene.image_prompt.strip()
        if not prompt:
            messagebox.showwarning("Thiếu prompt", "Image prompt đang rỗng.")
            return

        card.set_generating(True)
        self._set_status(f"● Đang tạo ảnh cho cảnh #{card.scene.index}...")

        worker = threading.Thread(
            target=self._run_image_gen,
            args=(card, api_key, prompt),
            daemon=True,
        )
        self._image_workers[card.scene.index] = worker
        worker.start()

    def _run_image_gen(self, card: SceneCard, api_key: str, prompt: str) -> None:
        try:
            generator = ImageGenerator(
                api_key=api_key,
                model=self.image_model_var.get(),
                output_dir=Path(self.output_dir_var.get() or "generated"),
            )
            slug = _slugify(self.analysis.story_title if self.analysis else "story")
            path = generator.generate(
                prompt,
                aspect_ratio=self.aspect_var.get(),
                scene_index=card.scene.index,
                story_slug=slug,
            )
            self.root.after(0, lambda: self._on_image_done(card, path))
        except (ImageGenError, Exception) as exc:  # noqa: BLE001
            err_msg = str(exc)
            tb = traceback.format_exc()
            logging.error("image gen failed:\n%s", tb)
            self.root.after(0, lambda: self._on_image_failed(card, err_msg))

    def _on_image_done(self, card: SceneCard, path: Path) -> None:
        card.set_generating(False)
        card.set_image(path)
        self._set_status(f"● Đã lưu ảnh cảnh #{card.scene.index} → {path.name}")

    def _on_image_failed(self, card: SceneCard, msg: str) -> None:
        card.set_generating(False)
        self._set_status(f"● Tạo ảnh cảnh #{card.scene.index} thất bại")
        messagebox.showerror(
            "Tạo ảnh thất bại",
            f"Cảnh #{card.scene.index}:\n\n{msg}",
        )

    def _on_open_image(self, card: SceneCard) -> None:
        if not card.scene.generated_image_path:
            return
        path = card.scene.generated_image_path
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}" >/dev/null 2>&1 &')
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Lỗi", f"Không mở được ảnh: {exc}")

    def _on_generate_all_clicked(self) -> None:
        if not self.scene_cards:
            return
        for card in self.scene_cards:
            if card.scene.generated_image_path:
                continue  # skip already-generated
            # Throttle: only schedule if no worker for this scene
            worker = self._image_workers.get(card.scene.index)
            if worker and worker.is_alive():
                continue
            self._on_generate_image(card)
            # Slight stagger to avoid hammering the API.
            time.sleep(0.25)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _log_status(self, text: str) -> None:
        self._log_queue.put(text)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self.status_var.set(line)
        except queue.Empty:
            pass
        self.root.after(150, self._drain_log_queue)

    # ------------------------------------------------------------------

    def run(self) -> None:
        self.root.mainloop()


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9-_ ]", "", name).strip().replace(" ", "-").lower()
    return base[:40] or "story"


def _log_dir() -> Path:
    """Pick a writable directory for log files. When frozen by PyInstaller
    we sit next to the .exe (which the user can usually find easily);
    otherwise we just use the current working directory."""
    if getattr(sys, "frozen", False):
        # sys.executable is the .exe path when frozen
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def main() -> None:
    log_path = _log_dir() / "storyviz-error.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    try:
        StoryVizApp().run()
    except Exception:  # noqa: BLE001
        # In --windowed PyInstaller builds there is no console, so any
        # startup crash is invisible to the user. Write the traceback to
        # a file next to the .exe AND surface it via a Tk error popup so
        # the user can copy-paste it back to us for debugging.
        tb = traceback.format_exc()
        try:
            log_path.write_text(tb, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        # Try to show a Tk dialog. If even that fails we re-raise so the
        # console (if any) prints it.
        try:
            from tkinter import Tk, messagebox as mb

            r = Tk()
            r.withdraw()
            mb.showerror(
                "StoryViz — startup crash",
                f"StoryViz could not start.\n\n"
                f"Traceback was written to:\n{log_path}\n\n"
                f"{tb}",
            )
            r.destroy()
        except Exception:  # noqa: BLE001
            pass
        raise


if __name__ == "__main__":
    main()
