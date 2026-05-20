import os
import logging
import io
import re
import base64
import textwrap
import subprocess
import requests
from fastapi import FastAPI, Request
from huggingface_hub import InferenceClient, HfApi
from ddgs import DDGS
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from groq import Groq
from PIL import Image, ImageDraw, ImageFont
import uvicorn

logging.basicConfig(level=logging.INFO)

BOT_TOKEN    = os.environ["BOT_TOKEN"]
HF_TOKEN     = os.environ["HF_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

hf_client   = InferenceClient(token=HF_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ────────────────────────────────────────────
# Доступні стилі для переробки фото
# ────────────────────────────────────────────
STYLES = [
    {"label": "🎨 Олійний живопис",   "prompt": "oil painting style, thick brushstrokes, artistic, museum quality"},
    {"label": "✏️ Олівцевий ескіз",   "prompt": "pencil sketch, hand drawn, graphite, detailed line art"},
    {"label": "🌸 Аніме / манга",      "prompt": "anime style, manga illustration, vibrant colors, japanese animation"},
    {"label": "🤖 Кіберпанк",          "prompt": "cyberpunk style, neon lights, futuristic, dark atmosphere, sci-fi"},
    {"label": "🖼️ Акварель",           "prompt": "watercolor painting, soft colors, artistic watercolor style"},
    {"label": "📸 Вінтаж / ретро",     "prompt": "vintage photo style, retro 1970s, film grain, faded colors"},
    {"label": "🏙️ Комікс / pop-art",   "prompt": "comic book style, pop art, bold outlines, halftone dots, vibrant"},
    {"label": "🌊 Імпресіонізм",        "prompt": "impressionist painting style, monet style, soft brushwork, dreamy"},
    {"label": "🎭 Готичний стиль",      "prompt": "gothic dark art style, dark fantasy, dramatic lighting, mysterious"},
    {"label": "🌅 Фентезі / казка",    "prompt": "fantasy art style, magical, fairytale illustration, enchanted"},
]

# ────────────────────────────────────────────
# Відправка повідомлень
# ────────────────────────────────────────────
def send_message(chat_id: int, text: str, reply_markup=None) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_dict()
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            logging.info("send_message OK")
            return True
        logging.error(f"send_message статус: {r.status_code} {r.text}")
        return False
    except Exception as e:
        logging.error(f"send_message помилка: {e}")
        return False

def send_photo(chat_id: int, photo: io.BytesIO, caption: str = ""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        requests.post(url, data={"chat_id": chat_id, "caption": caption},
                      files={"photo": photo}, timeout=30)
    except Exception as e:
        logging.error(f"send_photo помилка: {e}")

def answer_callback(callback_id: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_id}, timeout=10)
    except Exception as e:
        logging.error(f"answer_callback помилка: {e}")

# ────────────────────────────────────────────
# Робота з фото Telegram
# ────────────────────────────────────────────
def download_tg_photo(file_id: str) -> bytes:
    r = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
        params={"file_id": file_id}, timeout=10
    )
    file_path = r.json()["result"]["file_path"]
    r2 = requests.get(
        f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}",
        timeout=30
    )
    return r2.content

def photo_to_base64(image_bytes: bytes) -> str:
    return base64.standard_b64encode(image_bytes).decode("utf-8")

# ────────────────────────────────────────────
# Мем: генерація тексту через Groq vision
# ────────────────────────────────────────────
def generate_meme_text(image_bytes: bytes) -> tuple[str, str]:
    b64 = photo_to_base64(image_bytes)
    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": (
                        "Ти генератор мемів. Подивись на фото і придумай смішний мем-текст українською.\n"
                        "Відповідай СТРОГО у такому форматі, без зайвого тексту:\n"
                        "ВЕРХ: тут короткий текст\n"
                        "НИЗ: тут короткий текст"
                    )}
                ]
            }],
            max_tokens=80
        )
        raw = response.choices[0].message.content.strip()
        logging.info(f"Groq meme raw: {repr(raw)}")

        top, bottom = "", ""
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("ВЕРХ:"):
                top = line.split(":", 1)[1].strip()
            elif line.upper().startswith("НИЗ:"):
                bottom = line.split(":", 1)[1].strip()

        if not top and not bottom:
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            top    = lines[0] if len(lines) > 0 else "Коли просиш зробити мем"
            bottom = lines[1] if len(lines) > 1 else "А він просто повертає фото 😅"

        logging.info(f"Мем: ВЕРХ='{top}' | НИЗ='{bottom}'")
        return top or "Коли...", bottom or "...ось так 😅"

    except Exception as e:
        logging.error(f"generate_meme_text помилка: {e}")
        return "Коли просиш бота", "зробити мем 😅"

# ────────────────────────────────────────────
# Мем: накладання тексту через Pillow
# ────────────────────────────────────────────
def add_meme_text(image_bytes: bytes, top_text: str, bottom_text: str) -> io.BytesIO:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    font_size = max(32, int(h * 0.07))
    font = None

    # Список шрифтів — перший що знайдеться
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    for path in font_paths:
        try:
            font = ImageFont.truetype(path, font_size)
            logging.info(f"Шрифт: {path}")
            break
        except Exception:
            continue

    # Якщо нічого не знайшли — шукаємо через fc-list
    if font is None:
        try:
            result = subprocess.run(
                ["fc-list", ":style=Bold", "--format=%{file}\n"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.endswith(".ttf") or line.endswith(".otf"):
                    try:
                        font = ImageFont.truetype(line, font_size)
                        logging.info(f"Шрифт fc-list: {line}")
                        break
                    except Exception:
                        continue
        except Exception:
            pass

    if font is None:
        logging.warning("Жодного шрифту не знайдено, використовую load_default")
        font = ImageFont.load_default()

    def draw_outlined_text(text: str, y_start: int):
        text = text.upper()
        max_chars = max(10, int(w / (font_size * 0.55)))
        lines = textwrap.wrap(text, width=max_chars) or [text]
        line_height = font_size + 8
        outline = max(2, font_size // 14)

        for i, line in enumerate(lines):
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                text_w = bbox[2] - bbox[0]
            except Exception:
                text_w = font_size * len(line) // 2

            x = max(0, (w - text_w) // 2)
            y = y_start + i * line_height

            # Чорна обводка
            for dx in range(-outline, outline + 1):
                for dy in range(-outline, outline + 1):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
            # Білий текст
            draw.text((x, y), line, font=font, fill=(255, 255, 255))

    # Верх
    draw_outlined_text(top_text, int(h * 0.02))

    # Низ — рахуємо скільки рядків щоб не вилізти за межі
    max_chars = max(10, int(w / (font_size * 0.55)))
    bottom_lines = len(textwrap.wrap(bottom_text.upper(), width=max_chars) or [bottom_text])
    line_height = font_size + 8
    bottom_y = int(h * 0.97) - bottom_lines * line_height
    draw_outlined_text(bottom_text, bottom_y)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf

# ────────────────────────────────────────────
# Переробка стилю через HuggingFace img2img
# ────────────────────────────────────────────
def restyle_image(image_bytes: bytes, style_prompt: str) -> io.BytesIO | None:
    try:
        result = hf_client.image_to_image(
            image=io.BytesIO(image_bytes),
            prompt=style_prompt,
            model="timbrooks/instruct-pix2pix",
            strength=0.75,
        )
        buf = io.BytesIO()
        result.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception as e:
        logging.error(f"restyle_image помилка: {e}")
        return None

# ────────────────────────────────────────────
# Опис фото через Groq vision
# ────────────────────────────────────────────
def describe_image(image_bytes: bytes) -> str:
    b64 = photo_to_base64(image_bytes)
    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": "Опиши детально що зображено на цьому фото. Відповідай українською мовою."}
                ]
            }],
            max_tokens=300
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"describe_image помилка: {e}")
        return "Не вдалося розпізнати фото."

# ────────────────────────────────────────────
# База знань — локальний парсинг і пошук
# ────────────────────────────────────────────
def load_knowledge() -> list:
    try:
        api = HfApi(token=HF_TOKEN)
        files = api.list_repo_files(
            repo_id="Tiny89/tiny-bot-knowledge",
            repo_type="dataset"
        )
        all_entries = []
        for file in files:
            if file.endswith(".md"):
                url = f"https://huggingface.co/datasets/Tiny89/tiny-bot-knowledge/resolve/main/{file}"
                headers = {"Authorization": f"Bearer {HF_TOKEN}"}
                response = requests.get(url, headers=headers)
                if response.status_code == 200:
                    entries = parse_knowledge(response.text)
                    all_entries.extend(entries)
                    logging.info(f"Завантажено: {file} ({len(entries)} записів)")
        logging.info(f"База знань: {len(all_entries)} записів")
        return all_entries
    except Exception as e:
        logging.error(f"Помилка завантаження бази: {e}")
        return []

def parse_knowledge(text: str) -> list:
    entries = []
    sections = re.split(r"\n?###KEYWORDS###\n?", text)
    for i, section in enumerate(sections):
        if "###ANSWER###" not in section:
            continue
        parts = section.split("###ANSWER###", 1)
        if len(parts) < 2:
            continue
        keywords_raw = parts[0].strip()
        answer_raw   = parts[1].strip()
        answer = re.split(r"\n---\n", answer_raw)[0].strip()

        prev = sections[i - 1] if i > 0 else ""
        if "###ANSWER###" in prev:
            after_answer = prev.split("###ANSWER###")[-1]
        else:
            after_answer = prev
        h_match = re.search(r"^#\s+(.+)$", after_answer, re.MULTILINE)

        if h_match:
            title = h_match.group(1).strip()
        else:
            b_match = re.search(r"<b>(.*?)</b>", answer)
            if b_match:
                title = b_match.group(1).strip()
            else:
                for line in keywords_raw.split("\n"):
                    line = line.strip()
                    if line:
                        title = line.split(",")[0].strip()
                        break
                else:
                    title = "Без назви"

        kw_list = [k.strip().lower() for k in keywords_raw.split(",") if k.strip()]
        if kw_list and answer:
            entries.append({"title": title, "keywords": kw_list, "answer": answer})
    return entries

def search_knowledge(query: str) -> list:
    q = query.lower().strip()
    matches = []
    seen = set()
    for entry in KNOWLEDGE:
        for kw in entry["keywords"]:
            kw = kw.strip()
            if q == kw or q.startswith(kw + " ") or q.endswith(" " + kw) or (" " + kw + " ") in q:
                if entry["title"] not in seen:
                    seen.add(entry["title"])
                    matches.append(entry)
                break
            if kw.startswith(q + " ") or kw.endswith(" " + q) or (" " + q + " ") in kw:
                if entry["title"] not in seen:
                    seen.add(entry["title"])
                    matches.append(entry)
                break
    logging.info(f"Пошук '{query}': {len(matches)} збігів")
    return matches

KNOWLEDGE = load_knowledge()

# ────────────────────────────────────────────
# Пам'ять
# ────────────────────────────────────────────
chat_history:    dict[int, list] = {}
pending_answers: dict[int, list] = {}
pending_photos:  dict[int, str]  = {}   # user_id -> file_id

def get_history(user_id: int) -> list:
    return chat_history.setdefault(user_id, [])

def add_to_history(user_id: int, role: str, content: str):
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > 20:
        history.pop(0)

# ────────────────────────────────────────────
# Пошук в інтернеті
# ────────────────────────────────────────────
def search_web(query: str) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3, region="ua-uk"))
        if not results:
            return ""
        text = "🔍 Знайдено в інтернеті:\n\n"
        for r in results:
            text += f"• {r['title']}\n{r['body']}\n\n"
        return text
    except Exception as e:
        logging.error(f"Пошук помилка: {e}")
        return ""

# ────────────────────────────────────────────
# Groq — для інтернет відповідей
# ────────────────────────────────────────────
def ask_llm(system_prompt: str, user_id: int, user_text: str) -> str:
    messages = [{"role": "system", "content": system_prompt}]
    messages += get_history(user_id)
    messages.append({"role": "user", "content": user_text})
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=500
    )
    return response.choices[0].message.content

def groq_web_answer(user_id: int, text: str) -> str:
    web = search_web(text)
    if not web:
        return "Вибач, не знайшов відповіді ні в базі знань, ні в інтернеті."
    try:
        answer = ask_llm(
            "Ти корисний асистент служби підтримки. Відповідай ВИКЛЮЧНО українською мовою. "
            "Жодного слова іншою мовою.\n\n"
            "Правила відповіді:\n"
            "1. Дай коротку чітку відповідь на запит користувача\n"
            "2. Використовуй тільки релевантну інформацію з джерел\n"
            "3. Структуруй відповідь: спочатку суть, потім деталі\n"
            "4. Максимум 5 речень\n"
            "5. Не копіюй зайвий текст з джерел\n\n"
            f"Джерела:\n{web}",
            user_id, text
        )
        return "🌐 (з інтернету)\n\n" + answer
    except Exception as e:
        logging.error(f"Groq помилка: {e}")
        return web

# ────────────────────────────────────────────
# Генерація картинки (text-to-image)
# ────────────────────────────────────────────
def generate_image(prompt: str) -> io.BytesIO:
    image = hf_client.text_to_image(
        prompt,
        model="black-forest-labs/FLUX.1-schnell"
    )
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ────────────────────────────────────────────
# Клавіатури
# ────────────────────────────────────────────
def photo_action_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Головне меню дій з фото."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("😂 Зробити мем",     callback_data=f"photo_meme_{user_id}")],
        [InlineKeyboardButton("🎨 Змінити стиль",   callback_data=f"photo_styles_{user_id}")],
        [InlineKeyboardButton("🔍 Описати фото",    callback_data=f"photo_describe_{user_id}")],
    ])

def styles_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Меню вибору стилю переробки."""
    rows = []
    for i, style in enumerate(STYLES):
        rows.append([InlineKeyboardButton(
            style["label"],
            callback_data=f"style_{user_id}_{i}"
        )])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"photo_back_{user_id}")])
    return InlineKeyboardMarkup(rows)

# ────────────────────────────────────────────
# FastAPI
# ────────────────────────────────────────────
fastapi_app = FastAPI()

@fastapi_app.get("/")
async def root():
    return {"status": "running", "knowledge_entries": len(KNOWLEDGE)}

@fastapi_app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()

        # ════════════════════════════════════════
        # CALLBACK QUERY (кнопки)
        # ════════════════════════════════════════
        if "callback_query" in data:
            callback    = data["callback_query"]
            callback_id = callback["id"]
            chat_id     = callback["message"]["chat"]["id"]
            message_id  = callback["message"]["message_id"]
            cb_data     = callback["data"]

            # ── Кнопки стилів ──────────────────
            if cb_data.startswith("style_"):
                parts   = cb_data.split("_")          # style_{user_id}_{idx}
                user_id = int(parts[1])
                idx     = int(parts[2])
                file_id = pending_photos.get(user_id)

                if not file_id:
                    send_message(chat_id, "Фото не знайдено, надішли ще раз.")
                    answer_callback(callback_id)
                    return {"ok": True}

                style = STYLES[idx]
                send_message(chat_id, f"⏳ Переробляю у стилі «{style['label']}»...")
                try:
                    image_bytes = download_tg_photo(file_id)
                    buf = restyle_image(image_bytes, style["prompt"])
                    if buf:
                        send_photo(chat_id, buf, caption=f"{style['label']} ✅")
                    else:
                        send_message(chat_id, "Не вдалося переробити. Спробуй інший стиль.")
                except Exception as e:
                    logging.error(f"Style помилка: {e}")
                    send_message(chat_id, f"❌ Помилка: {e}")
                pending_photos.pop(user_id, None)
                answer_callback(callback_id)
                return {"ok": True}

            # ── Головне меню фото ───────────────
            if cb_data.startswith("photo_"):
                parts   = cb_data.split("_")          # photo_{action}_{user_id}
                action  = parts[1]
                user_id = int(parts[2])
                file_id = pending_photos.get(user_id)

                # Назад до головного меню
                if action == "back":
                    requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageReplyMarkup",
                        json={
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "reply_markup": photo_action_keyboard(user_id).to_dict()
                        }, timeout=10
                    )
                    answer_callback(callback_id)
                    return {"ok": True}

                # Показати список стилів
                if action == "styles":
                    requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                        json={
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "text": "🎨 Обери стиль переробки:",
                            "reply_markup": styles_keyboard(user_id).to_dict()
                        }, timeout=10
                    )
                    answer_callback(callback_id)
                    return {"ok": True}

                if not file_id:
                    send_message(chat_id, "Фото не знайдено, надішли ще раз.")
                    answer_callback(callback_id)
                    return {"ok": True}

                if action == "meme":
                    send_message(chat_id, "⏳ Генерую мем...")
                    try:
                        image_bytes = download_tg_photo(file_id)
                        top, bottom = generate_meme_text(image_bytes)
                        buf = add_meme_text(image_bytes, top, bottom)
                        send_photo(chat_id, buf, caption="😂 Твій мем готовий!")
                    except Exception as e:
                        logging.error(f"Мем помилка: {e}")
                        send_message(chat_id, f"❌ Помилка при створенні мему: {e}")
                    pending_photos.pop(user_id, None)

                elif action == "describe":
                    send_message(chat_id, "⏳ Аналізую фото...")
                    try:
                        image_bytes = download_tg_photo(file_id)
                        description = describe_image(image_bytes)
                        send_message(chat_id, f"🔍 <b>Що на фото:</b>\n\n{description}")
                    except Exception as e:
                        logging.error(f"Describe помилка: {e}")
                        send_message(chat_id, f"❌ Помилка: {e}")
                    pending_photos.pop(user_id, None)

                answer_callback(callback_id)
                return {"ok": True}

            # ── База знань ──────────────────────
            if cb_data.startswith("notfound_"):
                user_id = int(cb_data.split("_")[1])
                last    = get_history(user_id)
                query   = last[-1]["content"] if last else ""
                answer  = groq_web_answer(user_id, query)
                add_to_history(user_id, "assistant", answer)
                send_message(chat_id, answer)
                answer_callback(callback_id)
                return {"ok": True}

            try:
                _, uid_str, idx_str = cb_data.split("_")
                user_id = int(uid_str)
                idx     = int(idx_str)
                matches = pending_answers.get(user_id, [])
                if matches and idx < len(matches):
                    answer = matches[idx]["answer"]
                    send_message(chat_id, answer)
                    add_to_history(user_id, "assistant", answer)
            except Exception as e:
                logging.error(f"Callback помилка: {e}")
            answer_callback(callback_id)
            return {"ok": True}

        # ════════════════════════════════════════
        # ПОВІДОМЛЕННЯ
        # ════════════════════════════════════════
        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        user_id = message.get("from", {}).get("id")
        text    = message.get("text", "")

        if not chat_id:
            return {"ok": True}

        # ── Отримали фото ───────────────────────
        if "photo" in message:
            file_id = message["photo"][-1]["file_id"]
            caption = message.get("caption", "").strip().lower()
            pending_photos[user_id] = file_id

            # Якщо є підпис "мем" — одразу мем
            if caption in ["мем", "meme"]:
                send_message(chat_id, "⏳ Генерую мем...")
                try:
                    image_bytes = download_tg_photo(file_id)
                    top, bottom = generate_meme_text(image_bytes)
                    buf = add_meme_text(image_bytes, top, bottom)
                    send_photo(chat_id, buf, caption="😂 Твій мем готовий!")
                except Exception as e:
                    logging.error(f"Мем помилка: {e}")
                    send_message(chat_id, f"❌ Помилка: {e}")
                pending_photos.pop(user_id, None)

            # Якщо є підпис "опис" — одразу опис
            elif caption in ["опис", "describe", "що це"]:
                send_message(chat_id, "⏳ Аналізую фото...")
                try:
                    image_bytes = download_tg_photo(file_id)
                    description = describe_image(image_bytes)
                    send_message(chat_id, f"🔍 <b>Що на фото:</b>\n\n{description}")
                except Exception as e:
                    send_message(chat_id, f"❌ Помилка: {e}")
                pending_photos.pop(user_id, None)

            else:
                # Без підпису — показуємо головне меню
                send_message(chat_id, "📸 Що зробити з цим фото?",
                             reply_markup=photo_action_keyboard(user_id))
            return {"ok": True}

        if not text:
            return {"ok": True}

        # ── Стандартні команди ──────────────────
        if text == "/start":
            send_message(chat_id,
                "👋 Привіт! Я розумний бот.\n\n"
                "Що я вмію:\n"
                "• Відповідати з бази знань\n"
                "• Шукати в інтернеті якщо не знаю\n"
                "• Генерувати картинки — /img опис\n"
                "• 😂 Робити меми з твоїх фото\n"
                "• 🎨 Переробляти фото у 10 стилях\n"
                "• 🔍 Описувати що на фото\n"
                "• Пам'ятаю розмову\n\n"
                "Надішли фото — і побачиш варіанти!")
            return {"ok": True}

        if text == "/clear":
            chat_history[user_id]  = []
            pending_answers.pop(user_id, None)
            pending_photos.pop(user_id, None)
            send_message(chat_id, "🗑 Пам'ять очищена!")
            return {"ok": True}

        if text.startswith("/img"):
            prompt = text.replace("/img", "").strip()
            if not prompt:
                send_message(chat_id, "Вкажи опис: /img sunset over mountains")
                return {"ok": True}
            try:
                buf = generate_image(prompt)
                send_photo(chat_id, buf, caption=f"🖼 {prompt}")
            except Exception as e:
                logging.error(f"Генерація помилка: {e}")
                send_message(chat_id, "Не вдалося згенерувати, спробуй пізніше.")
            return {"ok": True}

        # ── База знань + інтернет ───────────────
        add_to_history(user_id, "user", text)
        matches = search_knowledge(text)

        if len(matches) == 1:
            pending_answers[user_id] = matches
            keyboard = [
                [InlineKeyboardButton(text=matches[0]["title"], callback_data=f"ans_{user_id}_0")],
                [InlineKeyboardButton(text="❌ Не те, що шукав", callback_data=f"notfound_{user_id}")]
            ]
            send_message(chat_id, "🔍 Знайшов варіант — обери потрібний:",
                         reply_markup=InlineKeyboardMarkup(keyboard))

        elif len(matches) > 1:
            pending_answers[user_id] = matches
            keyboard = [[InlineKeyboardButton(
                text=m["title"],
                callback_data=f"ans_{user_id}_{i}"
            )] for i, m in enumerate(matches)]
            keyboard.append([InlineKeyboardButton(
                text="❌ Не те, що шукав",
                callback_data=f"notfound_{user_id}"
            )])
            send_message(chat_id, "🔍 Знайшов кілька варіантів — обери потрібний:",
                         reply_markup=InlineKeyboardMarkup(keyboard))

        else:
            answer = groq_web_answer(user_id, text)
            add_to_history(user_id, "assistant", answer)
            send_message(chat_id, answer)

    except Exception as e:
        logging.error(f"Webhook помилка: {e}")

    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(fastapi_app, host="0.0.0.0", port=10000)
