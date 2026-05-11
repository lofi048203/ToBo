# StoryViz — Phân tích truyện → prompt ảnh & video AI

Ứng dụng desktop dùng **Google Gemini + Imagen** để biến một link truyện chữ
/ blog / web novel thành:

- Một danh sách các **phân cảnh** liên tục (4 → 20 cảnh)
- Với mỗi cảnh: **prompt ảnh** (tiếng Anh, dùng trực tiếp với Imagen) và
  **prompt video** đã được tinh chỉnh cho từng engine
  (Grok, Veo 3, Seedance, Runway Gen-3, Kling, Pika)
- Nút **Tạo ảnh** gọi thẳng Imagen từ cùng một API key Google AI

> Mặc định: `gemini-2.5-flash` (free tier) để phân cảnh + `gemini-2.5-flash-image` ("Nano Banana", free tier) để tạo ảnh.
> Có thể chuyển sang `gemini-2.5-pro` để phân cảnh sâu hơn hoặc sang `imagen-4.0-*` để ảnh chất lượng cao hơn — cả hai đều yêu cầu **paid plan** trên Google AI.

---

## 1. Cài đặt nhanh (1 click)

| Hệ điều hành | Bước duy nhất |
|---|---|
| **Windows** | Cài [Python 3.10+](https://www.python.org/downloads/) (nếu chưa có), rồi double-click `run.bat` |
| **macOS / Linux** | Cài Python 3.10+, mở Terminal và chạy `./run.sh` (lần đầu chạy `chmod +x run.sh`) |

Lần đầu tiên script sẽ tự tạo `.venv` và cài thư viện. Những lần sau bấm là chạy luôn.

---

## 2. Lấy Google AI API key

1. Vào https://aistudio.google.com/app/apikey
2. Bấm **Create API key** (miễn phí — bạn dùng quota cá nhân)
3. Copy key (`AIza...`) → dán vào ô **Google AI API key** trong app

> App **không gửi key đi đâu khác** ngoài chính API Google.

### Free tier vs Paid tier

- **Free tier** (mặc định): có quota hàng ngày cho `gemini-2.5-flash` (text)
  và `gemini-2.5-flash-image` / Nano Banana (image). Đủ để test và dùng cá
  nhân, nhưng nếu phân tích nhiều truyện trong ngày sẽ gặp lỗi
  `429 RESOURCE_EXHAUSTED` → chờ 24h hoặc upgrade.
- **Paid plan** (https://ai.dev/projects): mở khoá `gemini-2.5-pro`,
  `imagen-4.0-*` và quota cao hơn. Imagen 4 Fast chỉ ~$0.02/ảnh.

---

## 3. Sử dụng

1. Dán **link** truyện chữ / blog / web novel vào ô link.
   - Hoặc dán **văn bản trực tiếp** vào ô bên dưới nếu trang yêu cầu đăng nhập.
2. Nhập **API key** Google AI.
3. (Tuỳ chọn) Chỉnh **phong cách hình ảnh** (cinematic photo, anime,
   watercolor, pixar 3d, …) và **số phân cảnh** muốn tách.
4. Bấm **"🔍 Phân tích"** — Gemini sẽ đọc truyện và tách thành các phân cảnh.
5. Mỗi cảnh có các tab:
   - **🖼 Ảnh (Imagen)** — prompt tiếng Anh để tạo ảnh
   - **🎬 Grok / Veo 3 / Seedance / Runway / Kling / Pika** — prompt video
     đã tinh chỉnh cho từng engine
   Tất cả prompt đều có thể chỉnh tay, có nút **📋 Copy prompt**.
6. Bấm **"🎨 Tạo ảnh"** trên một cảnh để Imagen render → ảnh hiện ngay trong
   app và lưu vào thư mục `generated/`.
7. Bấm **"🎨 Tạo ảnh tất cả"** ở đầu danh sách nếu muốn render hàng loạt.

---

## 4. Chạy bằng dòng lệnh (tuỳ chọn)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export GEMINI_API_KEY="AIza..."    # tuỳ chọn — app sẽ auto fill
python app.py
```

---

## 5. Cấu trúc thư mục

```
storyviz/
├── app.py            # CustomTkinter UI
├── analyzer.py       # fetch URL + gọi Gemini tách phân cảnh
├── image_gen.py      # gọi Imagen để render ảnh
├── prompts.py        # system prompt + JSON schema + danh sách engine video
├── requirements.txt
├── run.sh / run.bat
└── README.md
```

---

## 6. FAQ

**Q: Prompt video có dùng được ngay không?**
A: Có — chỉ cần copy và paste vào Grok Imagine / Google Vertex Veo 3 /
Seedance / Runway / Kling / Pika. App chỉ sinh **text prompt** chứ không gọi
trực tiếp các engine đó vì API của họ chưa mở public.

**Q: Imagen có vẽ được nhân vật theo mô tả tiếng Việt không?**
A: Imagen chỉ hỗ trợ prompt **tiếng Anh**. App tự sinh image prompt tiếng Anh
chi tiết để bạn không phải dịch tay.

**Q: Ảnh tạo ra ở đâu?**
A: Mặc định lưu trong thư mục `generated/` cùng cấp với `app.py`. Có thể
đổi trong UI.

**Q: Có dùng được offline không?**
A: Không — cả Gemini và Imagen đều là API trên cloud của Google.

---

## 7. Giấy phép

MIT.
