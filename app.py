import os
import logging
import io
import re
import base64
import textwrap
import random
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
# Кириличний шрифт — завантажуємо при старті
# ────────────────────────────────────────────
FONT_PATH = "/tmp/NotoSans-Bold.ttf"

def ensure_cyrillic_font():
    """Завантажує Noto Sans Bold з Google Fonts якщо нема локально."""
    # Спочатку перевіряємо локальні шрифти з підтримкою кирилиці
    local_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for path in local_paths:
        if os.path.exists(path):
            try:
                test = ImageFont.truetype(path, 20)
                # Перевіряємо чи підтримує кирилицю
                img = Image.new("RGB", (100, 30))
                draw = ImageDraw.Draw(img)
                draw.text((0, 0), "Привіт", font=test)
                logging.info(f"Використовую локальний шрифт: {path}")
                return path
            except Exception:
                continue

    # Якщо локальних нема — завантажуємо з Google Fonts
    if not os.path.exists(FONT_PATH):
        try:
            logging.info("Завантажую NotoSans-Bold з Google Fonts...")
            url = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Bold.ttf"
            r = requests.get(url, timeout=30)
            with open(FONT_PATH, "wb") as f:
                f.write(r.content)
            logging.info("Шрифт завантажено успішно")
        except Exception as e:
            logging.error(f"Не вдалося завантажити шрифт: {e}")
            return None
    return FONT_PATH

CYRILLIC_FONT = ensure_cyrillic_font()

# ────────────────────────────────────────────
# Теми мемів з підтемами
# ────────────────────────────────────────────
MEME_THEMES = {
    "😂 Життя / побут": [
        "понеділок вранці", "прокидатися рано", "забути де поклав ключі",
        "дієта яка не йде", "чекати доставку", "черга в супермаркеті",
        "прибирання яке відкладаєш", "будильник о 6 ранку", "холодильник вночі",
        "загубив навушники", "інтернет завис у важливий момент",
    ],
    "💼 Робота / офіс": [
        "п'ятниця о 17:00", "мітинг який міг бути листом", "дедлайн завтра",
        "начальник і відпустка", "zoom дзвінок", "корпоратив",
        "підвищення зарплати", "понаднормова робота", "колега що шумить",
        "принтер не працює", "відпустка відхилена",
    ],
    "🐶 Тварини": [
        "кіт о 3 ночі", "собака і прогулянка", "кіт і клавіатура",
        "собака і листоноша", "кіт і коробка", "хом'як у колесі",
        "кіт і ялинка", "собака їсть чужу їжу", "кіт і пакет",
        "собака і дощ", "кіт і двері",
    ],
    "🇺🇦 Українські реалії": [
        "комуналка взимку", "черга в держслужбі", "маршрутка",
        "ціни в супермаркеті", "ями на дорогах", "відключення світла",
        "сусід з перфоратором", "ринок vs супермаркет",
        "держпослуги онлайн", "черга в пошті", "парковка в Києві",
    ],
    "💸 Гроші": [
        "зарплата і борги", "знижка 10% і купив непотрібне", "кредит",
        "економія vs бажання", "зарплата прийшла і одразу пішла",
        "інвестиції для початківців", "чай вдома vs кава в кафе",
        "розпродаж і кошик на 5000", "страхування авто", "курс долара",
    ],
}

# ────────────────────────────────────────────
# Стилі для переробки фото
# ────────────────────────────────────────────
STYLES = [
    {"label": "🎨 Олійний живопис",  "prompt": "oil painting style, thick brushstrokes, artistic, museum quality"},
    {"label": "✏️ Олівцевий ескіз",  "prompt": "pencil sketch, hand drawn, graphite, detailed line art"},
    {"label": "🌸 Аніме / манга",     "prompt": "anime style, manga illustration, vibrant colors, japanese animation"},
    {"label": "🤖 Кіберпанк",         "prompt": "cyberpunk style, neon lights, futuristic, dark atmosphere, sci-fi"},
    {"label": "🖼️ Акварель",          "prompt": "watercolor painting, soft colors, artistic watercolor style"},
    {"label": "📸 Вінтаж / ретро",    "prompt": "vintage photo style, retro 1970s, film grain, faded colors"},
    {"label": "🏙️ Комікс / pop-art",  "prompt": "comic book style, pop art, bold outlines, halftone dots, vibrant"},
    {"label": "🌊 Імпресіонізм",       "prompt": "impressionist painting style, monet style, soft brushwork, dreamy"},
    {"label": "🎭 Готичний стиль",     "prompt": "gothic dark art style, dark fantasy, dramatic lighting, mysterious"},
    {"label": "🌅 Фентезі / казка",   "prompt": "fantasy art style, magical, fairytale illustration, enchanted"},
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
                      files={"photo": photo}, timeout=60)
    except Exception as e:
        logging.error(f"send_photo помилка: {e}")

def answer_callback(callback_id: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_id}, timeout=10)
    except Exception as e:
        logging.error(f"answer_callback помилка: {e}")

def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_dict()
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json=payload, timeout=10
        )
    except Exception as e:
        logging.error(f"edit_message_text помилка: {e}")

def edit_message_keyboard(chat_id: int, message_id: int, reply_markup):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id,
                  "reply_markup": reply_markup.to_dict()}, timeout=10
        )
    except Exception as e:
        logging.error(f"edit_message_keyboard помилка: {e}")

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
def generate_meme_text(image_bytes: bytes, theme_hint: str = "") -> tuple[str, str]:
    b64 = photo_to_base64(image_bytes)
    theme_instruction = ""
    if theme_hint:
        theme_instruction = f'\nТема мему: "{theme_hint}". Обов\'язково прив\'яжи текст до цієї теми!'

    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": (
                        f"Ти генератор мемів. Подивись на фото і придумай смішний мем-текст українською.{theme_instruction}\n"
                        "Відповідай СТРОГО у такому форматі, без зайвого тексту:\n"
                        "ВЕРХ: тут короткий текст\n"
                        "НИЗ: тут короткий текст\n"
                        "Текст має бути коротким — максимум 6 слів у кожному рядку!"
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
            upper = line.upper()
            if upper.startswith("ВЕРХ:"):
                top = line.split(":", 1)[1].strip()
            elif upper.startswith("НИЗ:"):
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

    font_size = max(36, int(h * 0.08))
    font = None

    if CYRILLIC_FONT:
        try:
            font = ImageFont.truetype(CYRILLIC_FONT, font_size)
            logging.info(f"Мем шрифт: {CYRILLIC_FONT}, розмір {font_size}")
        except Exception as e:
            logging.error(f"Не вдалося завантажити шрифт: {e}")

    if font is None:
        # Fallback через fc-list
        try:
            result = subprocess.run(
                ["fc-list", ":lang=uk:style=Bold", "--format=%{file}\n"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.endswith(".ttf") or line.endswith(".otf"):
                    try:
                        font = ImageFont.truetype(line, font_size)
                        break
                    except Exception:
                        continue
        except Exception:
            pass

    if font is None:
        logging.warning("Шрифт не знайдено!")
        font = ImageFont.load_default()

    def draw_outlined_text(text: str, y_start: int):
        text = text.upper()
        max_chars = max(8, int(w / (font_size * 0.58)))
        lines = textwrap.wrap(text, width=max_chars) or [text]
        line_height = int(font_size * 1.2)
        outline = max(3, font_size // 10)

        for i, line in enumerate(lines):
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                text_w = bbox[2] - bbox[0]
            except Exception:
                text_w = font_size * len(line) // 2

            x = max(4, (w - text_w) // 2)
            y = y_start + i * line_height

            # Чорна обводка (товста)
            for dx in range(-outline, outline + 1):
                for dy in range(-outline, outline + 1):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
            # Білий текст
            draw.text((x, y), line, font=font, fill=(255, 255, 255))

    # Верхній текст
    draw_outlined_text(top_text, int(h * 0.02))

    # Нижній текст — рахуємо висоту блоку щоб не вилізти
    max_chars = max(8, int(w / (font_size * 0.58)))
    bottom_lines_count = len(textwrap.wrap(bottom_text.upper(), width=max_chars) or [bottom_text])
    line_height = int(font_size * 1.2)
    bottom_y = int(h * 0.97) - bottom_lines_count * line_height
    draw_outlined_text(bottom_text, bottom_y)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf

# ────────────────────────────────────────────
# Переробка стилю: Groq описує → FLUX генерує
# ────────────────────────────────────────────
def restyle_image(image_bytes: bytes, style_prompt: str) -> io.BytesIO | None:
    try:
        b64 = photo_to_base64(image_bytes)
        vision_resp = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": (
                        "Describe this image in detail for an AI image generator. "
                        "Focus on: main subject, composition, lighting, colors, background. "
                        "Be specific and descriptive. Answer in English only. Max 60 words."
                    )}
                ]
            }],
            max_tokens=100
        )
        description = vision_resp.choices[0].message.content.strip()
        logging.info(f"Restyle опис: {description}")

        full_prompt = f"{description}, {style_prompt}, highly detailed, high quality"
        image = hf_client.text_to_image(
            full_prompt,
            model="black-forest-labs/FLUX.1-schnell"
        )
        buf = io.BytesIO()
        image.save(buf, format="PNG")
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
# База знань
# ────────────────────────────────────────────
def load_knowledge() -> list:
    try:
        api = HfApi(token=HF_TOKEN)
        files = api.list_repo_files(repo_id="Tiny89/tiny-bot-knowledge", repo_type="dataset")
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
        after_answer = prev.split("###ANSWER###")[-1] if "###ANSWER###" in prev else prev
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
    return matches

KNOWLEDGE = load_knowledge()

# ────────────────────────────────────────────
# Пам'ять
# ────────────────────────────────────────────
chat_history:    dict[int, list] = {}
pending_answers: dict[int, list] = {}
pending_photos:  dict[int, str]  = {}  # user_id -> file_id

def get_history(user_id: int) -> list:
    return chat_history.setdefault(user_id, [])

def add_to_history(user_id: int, role: str, content: str):
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > 20:
        history.pop(0)

# ────────────────────────────────────────────
# Пошук в інтернеті + Groq
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
            "Ти корисний асистент служби підтримки. Відповідай ВИКЛЮЧНО українською мовою.\n\n"
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
    image = hf_client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ────────────────────────────────────────────
# Клавіатури
# ────────────────────────────────────────────
def photo_action_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("😂 Зробити мем",   callback_data=f"photo_meme_{user_id}")],
        [InlineKeyboardButton("🎨 Змінити стиль", callback_data=f"photo_styles_{user_id}")],
        [InlineKeyboardButton("🔍 Описати фото",  callback_data=f"photo_describe_{user_id}")],
    ])

def styles_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(s["label"], callback_data=f"style_{user_id}_{i}")]
            for i, s in enumerate(STYLES)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"photo_back_{user_id}")])
    return InlineKeyboardMarkup(rows)

def meme_themes_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Меню вибору теми мему."""
    rows = []
    for i, theme in enumerate(MEME_THEMES.keys()):
        rows.append([InlineKeyboardButton(theme, callback_data=f"mtheme_{user_id}_{i}")])
    rows.append([InlineKeyboardButton("🎲 Рандомна тема і підтема", callback_data=f"mtheme_{user_id}_random")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"photo_back_{user_id}")])
    return InlineKeyboardMarkup(rows)

def meme_subtopics_keyboard(user_id: int, theme_idx: int) -> InlineKeyboardMarkup:
    """Меню вибору підтеми після вибору теми."""
    theme_name = list(MEME_THEMES.keys())[theme_idx]
    subtopics  = MEME_THEMES[theme_name]
    rows = []
    for j, sub in enumerate(subtopics):
        rows.append([InlineKeyboardButton(sub, callback_data=f"msub_{user_id}_{theme_idx}_{j}")])
    rows.append([InlineKeyboardButton("🎲 Рандомна підтема", callback_data=f"msub_{user_id}_{theme_idx}_random")])
    rows.append([InlineKeyboardButton("⬅️ До тем",           callback_data=f"photo_meme_{user_id}")])
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
        # CALLBACK QUERY
        # ════════════════════════════════════════
        if "callback_query" in data:
            callback    = data["callback_query"]
            callback_id = callback["id"]
            chat_id     = callback["message"]["chat"]["id"]
            message_id  = callback["message"]["message_id"]
            cb_data     = callback["data"]

            # ── Вибір стилю ────────────────────
            if cb_data.startswith("style_"):
                parts   = cb_data.split("_")
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

            # ── Вибір теми → показуємо підтеми ──
            if cb_data.startswith("mtheme_"):
                parts   = cb_data.split("_")
                user_id = int(parts[1])
                idx_raw = parts[2]

                if idx_raw == "random":
                    # Рандомна тема і підтема — одразу генеруємо
                    file_id = pending_photos.get(user_id)
                    if not file_id:
                        send_message(chat_id, "Фото не знайдено, надішли ще раз.")
                        answer_callback(callback_id)
                        return {"ok": True}
                    theme_name = random.choice(list(MEME_THEMES.keys()))
                    subtopic   = random.choice(MEME_THEMES[theme_name])
                    theme_hint = f"{theme_name}: {subtopic}"
                    send_message(chat_id, f"⏳ Генерую мем на тему «{subtopic}»...")
                    try:
                        image_bytes = download_tg_photo(file_id)
                        top, bottom = generate_meme_text(image_bytes, theme_hint)
                        buf = add_meme_text(image_bytes, top, bottom)
                        send_photo(chat_id, buf, caption=f"😂 {theme_name} — {subtopic}")
                    except Exception as e:
                        logging.error(f"Мем рандом помилка: {e}")
                        send_message(chat_id, f"❌ Помилка: {e}")
                    pending_photos.pop(user_id, None)
                else:
                    # Показуємо список підтем для обраної теми
                    theme_idx  = int(idx_raw)
                    theme_name = list(MEME_THEMES.keys())[theme_idx]
                    edit_message_text(chat_id, message_id,
                                      f"📋 Обери підтему — <b>{theme_name}</b>:",
                                      meme_subtopics_keyboard(user_id, theme_idx))
                answer_callback(callback_id)
                return {"ok": True}

            # ── Вибір підтеми → генеруємо мем ──
            if cb_data.startswith("msub_"):
                parts     = cb_data.split("_")
                user_id   = int(parts[1])
                theme_idx = int(parts[2])
                sub_raw   = parts[3]
                file_id   = pending_photos.get(user_id)

                if not file_id:
                    send_message(chat_id, "Фото не знайдено, надішли ще раз.")
                    answer_callback(callback_id)
                    return {"ok": True}

                theme_name = list(MEME_THEMES.keys())[theme_idx]
                subtopics  = MEME_THEMES[theme_name]

                if sub_raw == "random":
                    subtopic = random.choice(subtopics)
                else:
                    subtopic = subtopics[int(sub_raw)]

                theme_hint = f"{theme_name}: {subtopic}"
                send_message(chat_id, f"⏳ Генерую мем на тему «{subtopic}»...")
                try:
                    image_bytes = download_tg_photo(file_id)
                    top, bottom = generate_meme_text(image_bytes, theme_hint)
                    buf = add_meme_text(image_bytes, top, bottom)
                    send_photo(chat_id, buf, caption=f"😂 {theme_name} — {subtopic}")
                except Exception as e:
                    logging.error(f"Мем підтема помилка: {e}")
                    send_message(chat_id, f"❌ Помилка: {e}")
                pending_photos.pop(user_id, None)
                answer_callback(callback_id)
                return {"ok": True}

            # ── Головне меню фото ──────────────
            if cb_data.startswith("photo_"):
                parts   = cb_data.split("_")
                action  = parts[1]
                user_id = int(parts[2])
                file_id = pending_photos.get(user_id)

                if action == "back":
                    edit_message_keyboard(chat_id, message_id, photo_action_keyboard(user_id))
                    answer_callback(callback_id)
                    return {"ok": True}

                if action == "styles":
                    edit_message_text(chat_id, message_id,
                                      "🎨 Обери стиль переробки:",
                                      styles_keyboard(user_id))
                    answer_callback(callback_id)
                    return {"ok": True}

                if action == "meme":
                    # Показуємо меню тем через редагування повідомлення
                    edit_message_text(chat_id, message_id,
                                      "😂 Обери тему мему:",
                                      meme_themes_keyboard(user_id))
                    answer_callback(callback_id)
                    return {"ok": True}

                if not file_id:
                    send_message(chat_id, "Фото не знайдено, надішли ще раз.")
                    answer_callback(callback_id)
                    return {"ok": True}

                if action == "describe":
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

            # ── База знань ─────────────────────
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

        # ── Отримали фото ──────────────────────
        if "photo" in message:
            file_id = message["photo"][-1]["file_id"]
            caption = message.get("caption", "").strip().lower()
            pending_photos[user_id] = file_id

            if caption in ["мем", "meme"]:
                send_message(chat_id, "😂 Обери тему мему:",
                             reply_markup=meme_themes_keyboard(user_id))

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
                send_message(chat_id, "📸 Що зробити з цим фото?",
                             reply_markup=photo_action_keyboard(user_id))
            return {"ok": True}

        if not text:
            return {"ok": True}

        # ── Команди ────────────────────────────
        if text == "/start":
            send_message(chat_id,
                "👋 Привіт! Я поки тупий AI бот написаний Tiny.\n\n"
                "Що я вмію:\n"
                "• Відповідати з бази знань\n"
                "• Шукати в інтернеті якщо не знаю\n"
                "• Генерувати картинки — команда /img опис\n"
                "• 😂 Робити меми з тем (10 тем, купа підтем)\n"
                "• 🎨 Переробляти фото у 10 стилях\n"
                "• 🔍 Описувати що на фото\n"
                "• Пам'ятаю розмову\n\n"
                "Запитай щось, або надішли фото — і побачиш варіанти!")
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

        # ── База знань + інтернет ──────────────
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
                text=m["title"], callback_data=f"ans_{user_id}_{i}"
            )] for i, m in enumerate(matches)]
            keyboard.append([InlineKeyboardButton(
                text="❌ Не те, що шукав", callback_data=f"notfound_{user_id}"
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
