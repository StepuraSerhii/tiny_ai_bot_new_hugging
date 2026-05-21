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
from gtts import gTTS
import uvicorn

logging.basicConfig(level=logging.INFO)

BOT_TOKEN    = os.environ["BOT_TOKEN"]
HF_TOKEN     = os.environ["HF_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

hf_client   = InferenceClient(token=HF_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ────────────────────────────────────────────
# Режими особистості бота
# ────────────────────────────────────────────
BOT_PERSONAS = {
    "assistant": {
        "label": "🤖 Асистент",
        "system": (
            "Ти корисний асистент служби підтримки. "
            "Відповідай чітко, по суті, українською мовою. "
            "Максимум 5 речень."
        ),
    },
    "bro": {
        "label": "😎 Бро",
        "system": (
            "Ти найкращий бро юзера. Розмовляєш як реальний пацан — "
            "сленг, короткі фрази, іноді матюки (зірочками), емодзі. "
            "Завжди підтримуєш, але стьобаєш по-дружньому. "
            "Тільки українська / суржик."
        ),
    },
    "teacher": {
        "label": "🧑‍🏫 Вчитель",
        "system": (
            "Ти терплячий вчитель. Пояснюєш все просто і зрозуміло, "
            "з прикладами, аналогіями, кроками. Хвалиш за правильні питання. "
            "Мова — українська, академічна але доступна."
        ),
    },
    "comedian": {
        "label": "😂 Комік",
        "system": (
            "Ти стенд-ап комік. На будь-яке питання відповідаєш з гумором, "
            "жартами, абсурдом і самоіронією. Навіть серйозні теми перетворюєш "
            "на жарт. Але в кінці все ж даєш реальну відповідь. Тільки українська."
        ),
    },
    "sage": {
        "label": "🧙 Мудрець",
        "system": (
            "Ти давній мудрець. Відповідаєш глибокодумно, з метафорами, "
            "притчами, філософськими роздумами. Кожна відповідь — маленька мудрість. "
            "Мова урочиста, українська."
        ),
    },
    "psychologist": {
        "label": "🧠 Психолог",
        "system": (
            "Ти емпатичний психолог. Уважно слухаєш, ставиш уточнюючі питання, "
            "допомагаєш розібратися в почуттях і ситуації. Не засуджуєш. "
            "Даєш практичні поради. Тільки українська."
        ),
    },
}

# user_id -> persona key (default: "assistant")
user_personas: dict[int, str] = {}

# ────────────────────────────────────────────
# Кириличний шрифт — завантажуємо при старті
# ────────────────────────────────────────────
FONT_PATH = "/tmp/NotoSans-Bold.ttf"

def ensure_cyrillic_font():
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
                img  = Image.new("RGB", (100, 30))
                ImageDraw.Draw(img).text((0, 0), "Привіт", font=test)
                logging.info(f"Локальний шрифт: {path}")
                return path
            except Exception:
                continue
    if not os.path.exists(FONT_PATH):
        try:
            logging.info("Завантажую NotoSans-Bold...")
            r = requests.get(
                "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Bold.ttf",
                timeout=30
            )
            with open(FONT_PATH, "wb") as f:
                f.write(r.content)
            logging.info("Шрифт завантажено")
        except Exception as e:
            logging.error(f"Шрифт не завантажено: {e}")
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
        logging.error(f"send_message: {r.status_code} {r.text}")
        return False
    except Exception as e:
        logging.error(f"send_message: {e}")
        return False

def send_photo(chat_id: int, photo: io.BytesIO, caption: str = ""):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": photo}, timeout=60
        )
    except Exception as e:
        logging.error(f"send_photo: {e}")

def send_voice(chat_id: int, voice_buf: io.BytesIO):
    """Надсилає голосове повідомлення (OGG/MP3)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendVoice",
            data={"chat_id": chat_id},
            files={"voice": ("voice.mp3", voice_buf, "audio/mpeg")},
            timeout=30,
        )
    except Exception as e:
        logging.error(f"send_voice: {e}")

def answer_callback(callback_id: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id}, timeout=10
        )
    except Exception as e:
        logging.error(f"answer_callback: {e}")

def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup.to_dict()
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json=payload, timeout=10
        )
    except Exception as e:
        logging.error(f"edit_message_text: {e}")

def edit_message_keyboard(chat_id: int, message_id: int, reply_markup):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id,
                  "reply_markup": reply_markup.to_dict()}, timeout=10
        )
    except Exception as e:
        logging.error(f"edit_message_keyboard: {e}")

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

def download_tg_file(file_id: str) -> tuple[bytes, str]:
    """Повертає (bytes, file_path). Універсальна версія для будь-яких файлів."""
    r = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
        params={"file_id": file_id}, timeout=10
    )
    file_path = r.json()["result"]["file_path"]
    r2 = requests.get(
        f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}",
        timeout=60
    )
    return r2.content, file_path

def photo_to_base64(image_bytes: bytes) -> str:
    return base64.standard_b64encode(image_bytes).decode("utf-8")

# ────────────────────────────────────────────
# Мем: генерація тексту через Groq vision
# ────────────────────────────────────────────
def generate_meme_text(image_bytes: bytes, theme_hint: str = "") -> tuple[str, str]:
    b64 = photo_to_base64(image_bytes)
    theme_instruction = f'\nТема мему: "{theme_hint}". Прив\'яжи текст до цієї теми!' if theme_hint else ""
    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": (
                        f"Ти генератор мемів. Придумай смішний мем-текст українською.{theme_instruction}\n"
                        "Відповідай СТРОГО у форматі без зайвого тексту:\n"
                        "ВЕРХ: короткий текст (макс 6 слів)\n"
                        "НИЗ: короткий текст (макс 6 слів)"
                    )}
                ]
            }],
            max_tokens=80
        )
        raw = response.choices[0].message.content.strip()
        logging.info(f"Meme raw: {repr(raw)}")
        top, bottom = "", ""
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("ВЕРХ:"):
                top = line.split(":", 1)[1].strip()
            elif line.upper().startswith("НИЗ:"):
                bottom = line.split(":", 1)[1].strip()
        if not top and not bottom:
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            top    = lines[0] if lines else "Коли просиш зробити мем"
            bottom = lines[1] if len(lines) > 1 else "А він повертає фото 😅"
        return top or "Коли...", bottom or "...ось так 😅"
    except Exception as e:
        logging.error(f"generate_meme_text: {e}")
        return "Коли просиш бота", "зробити мем 😅"

# ────────────────────────────────────────────
# Мем: накладання тексту через Pillow
# ────────────────────────────────────────────
def add_meme_text(image_bytes: bytes, top_text: str, bottom_text: str) -> io.BytesIO:
    img  = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    font_size = max(36, int(h * 0.08))
    font = None
    if CYRILLIC_FONT:
        try:
            font = ImageFont.truetype(CYRILLIC_FONT, font_size)
        except Exception as e:
            logging.error(f"Шрифт: {e}")
    if font is None:
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
        font = ImageFont.load_default()

    def draw_outlined_text(text: str, y_start: int):
        text = text.upper()
        max_chars   = max(8, int(w / (font_size * 0.58)))
        lines       = textwrap.wrap(text, width=max_chars) or [text]
        line_height = int(font_size * 1.2)
        outline     = max(3, font_size // 10)
        for i, line in enumerate(lines):
            try:
                bbox   = draw.textbbox((0, 0), line, font=font)
                text_w = bbox[2] - bbox[0]
            except Exception:
                text_w = font_size * len(line) // 2
            x = max(4, (w - text_w) // 2)
            y = y_start + i * line_height
            for dx in range(-outline, outline + 1):
                for dy in range(-outline, outline + 1):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=(255, 255, 255))

    draw_outlined_text(top_text, int(h * 0.02))
    max_chars   = max(8, int(w / (font_size * 0.58)))
    n_lines     = len(textwrap.wrap(bottom_text.upper(), width=max_chars) or [bottom_text])
    line_height = int(font_size * 1.2)
    draw_outlined_text(bottom_text, int(h * 0.97) - n_lines * line_height)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf

# ────────────────────────────────────────────
# Переробка стилю: Groq описує → FLUX генерує
# ────────────────────────────────────────────
def restyle_image(image_bytes: bytes, style_prompt: str) -> bytes | None:
    """Повертає bytes (не BytesIO) щоб зручно передавати далі."""
    try:
        b64 = photo_to_base64(image_bytes)
        vision = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": (
                        "Describe this image for an AI image generator. "
                        "Main subject, composition, lighting, colors, background. "
                        "English only. Max 60 words."
                    )}
                ]
            }],
            max_tokens=100
        )
        description  = vision.choices[0].message.content.strip()
        full_prompt  = f"{description}, {style_prompt}, highly detailed, high quality"
        logging.info(f"Restyle prompt: {full_prompt[:80]}")
        image = hf_client.text_to_image(full_prompt, model="black-forest-labs/FLUX.1-schnell")
        buf   = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logging.error(f"restyle_image: {e}")
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
                    {"type": "text", "text": "Опиши детально що зображено на цьому фото. Відповідай українською."}
                ]
            }],
            max_tokens=300
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"describe_image: {e}")
        return "Не вдалося розпізнати фото."

# ────────────────────────────────────────────
# База знань
# ────────────────────────────────────────────
def load_knowledge() -> list:
    try:
        api   = HfApi(token=HF_TOKEN)
        files = api.list_repo_files(repo_id="Tiny89/tiny-bot-knowledge", repo_type="dataset")
        all_entries = []
        for file in files:
            if file.endswith(".md"):
                url      = f"https://huggingface.co/datasets/Tiny89/tiny-bot-knowledge/resolve/main/{file}"
                response = requests.get(url, headers={"Authorization": f"Bearer {HF_TOKEN}"})
                if response.status_code == 200:
                    entries = parse_knowledge(response.text)
                    all_entries.extend(entries)
                    logging.info(f"Завантажено: {file} ({len(entries)} записів)")
        logging.info(f"База знань: {len(all_entries)} записів")
        return all_entries
    except Exception as e:
        logging.error(f"load_knowledge: {e}")
        return []

def parse_knowledge(text: str) -> list:
    entries  = []
    sections = re.split(r"\n?###KEYWORDS###\n?", text)
    for i, section in enumerate(sections):
        if "###ANSWER###" not in section:
            continue
        parts = section.split("###ANSWER###", 1)
        if len(parts) < 2:
            continue
        keywords_raw = parts[0].strip()
        answer       = re.split(r"\n---\n", parts[1].strip())[0].strip()
        prev         = sections[i - 1] if i > 0 else ""
        after_answer = prev.split("###ANSWER###")[-1] if "###ANSWER###" in prev else prev
        h_match      = re.search(r"^#\s+(.+)$", after_answer, re.MULTILINE)
        if h_match:
            title = h_match.group(1).strip()
        else:
            b_match = re.search(r"<b>(.*?)</b>", answer)
            title = b_match.group(1).strip() if b_match else (
                next((l.strip().split(",")[0].strip() for l in keywords_raw.split("\n") if l.strip()), "Без назви")
            )
        kw_list = [k.strip().lower() for k in keywords_raw.split(",") if k.strip()]
        if kw_list and answer:
            entries.append({"title": title, "keywords": kw_list, "answer": answer})
    return entries

def search_knowledge(query: str) -> list:
    q       = query.lower().strip()
    matches = []
    seen    = set()
    for entry in KNOWLEDGE:
        for kw in entry["keywords"]:
            kw = kw.strip()
            if q == kw or q.startswith(kw+" ") or q.endswith(" "+kw) or (" "+kw+" ") in q \
               or kw.startswith(q+" ") or kw.endswith(" "+q) or (" "+q+" ") in kw:
                if entry["title"] not in seen:
                    seen.add(entry["title"])
                    matches.append(entry)
                break
    return matches

KNOWLEDGE = load_knowledge()

# ────────────────────────────────────────────
# Пам'ять
# ────────────────────────────────────────────
chat_history:    dict[int, list]         = {}
pending_answers: dict[int, list]         = {}
pending_photos:  dict[int, str]          = {}  # user_id -> file_id
pending_voices:  dict[int, str]          = {}  # user_id -> file_id войсу
meme_style_sel:  dict[int, int | None]   = {}  # user_id -> style idx або None
user_voice_reply: dict[int, bool]        = {}  # user_id -> чи відповідати голосом

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
        logging.error(f"search_web: {e}")
        return ""

def ask_llm(system_prompt: str, user_id: int, user_text: str) -> str:
    messages = [{"role": "system", "content": system_prompt}]
    messages += get_history(user_id)
    messages.append({"role": "user", "content": user_text})
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile", messages=messages, max_tokens=500
    )
    return response.choices[0].message.content

def ask_llm_persona(user_id: int, user_text: str) -> str:
    """Питає LLM із урахуванням поточної особистості юзера."""
    persona_key = user_personas.get(user_id, "assistant")
    system      = BOT_PERSONAS[persona_key]["system"]
    return ask_llm(system, user_id, user_text)

def groq_web_answer(user_id: int, text: str) -> str:
    web = search_web(text)
    if not web:
        # Якщо веб нічого не дав — питаємо LLM напряму з персоною
        try:
            return ask_llm_persona(user_id, text)
        except Exception:
            return "Вибач, не знайшов відповіді ні в базі знань, ні в інтернеті."
    try:
        persona_key = user_personas.get(user_id, "assistant")
        persona_sys = BOT_PERSONAS[persona_key]["system"]
        answer = ask_llm(
            persona_sys + f"\n\nДжерела з інтернету:\n{web}",
            user_id, text
        )
        return "🌐 (з інтернету)\n\n" + answer
    except Exception as e:
        logging.error(f"groq_web_answer: {e}")
        return web

# ────────────────────────────────────────────
# STT: Groq Whisper — войс → текст
# ────────────────────────────────────────────
def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """Транскрибує аудіо через Groq Whisper."""
    try:
        transcription = groq_client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=(filename, audio_bytes),
            language="uk",
            response_format="text",
        )
        return transcription.strip() if isinstance(transcription, str) else transcription.text.strip()
    except Exception as e:
        logging.error(f"transcribe_voice: {e}")
        return ""

# ────────────────────────────────────────────
# TTS: gTTS — текст → голосове повідомлення
# ────────────────────────────────────────────
def text_to_speech(text: str, lang: str = "uk") -> io.BytesIO | None:
    """Генерує MP3 з тексту через gTTS."""
    try:
        # Обрізаємо HTML-теги перед озвучкою
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"[🌐🔍⏳❌✅😂🎨📸🖼🗑👋🤖😎🧑‍🏫🧙🧠💬🎙]", "", clean)
        clean = clean.strip()
        if not clean:
            return None
        tts = gTTS(text=clean[:800], lang=lang, slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        return buf
    except Exception as e:
        logging.error(f"text_to_speech: {e}")
        return None

# ────────────────────────────────────────────
# Генерація картинки (text-to-image)
# ────────────────────────────────────────────
def generate_image(prompt: str) -> io.BytesIO:
    image = hf_client.text_to_image(prompt, model="black-forest-labs/FLUX.1-schnell")
    buf   = io.BytesIO()
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

def persona_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавіатура вибору особистості бота."""
    current = user_personas.get(user_id, "assistant")
    rows = []
    for key, p in BOT_PERSONAS.items():
        label = f"✅ {p['label']}" if key == current else p["label"]
        rows.append([InlineKeyboardButton(label, callback_data=f"persona_{user_id}_{key}")])
    return InlineKeyboardMarkup(rows)

def styles_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(s["label"], callback_data=f"style_{user_id}_{i}")]
            for i, s in enumerate(STYLES)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"photo_back_{user_id}")])
    return InlineKeyboardMarkup(rows)

def meme_style_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Крок 1 мему — вибір стилю фото."""
    rows = [[InlineKeyboardButton(s["label"], callback_data=f"memestyle_{user_id}_{i}")]
            for i, s in enumerate(STYLES)]
    rows.append([InlineKeyboardButton("⏭ Без стилю — лише текст", callback_data=f"memestyle_{user_id}_none")])
    rows.append([InlineKeyboardButton("⬅️ Назад",                  callback_data=f"photo_back_{user_id}")])
    return InlineKeyboardMarkup(rows)

def meme_themes_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Крок 2 мему — вибір теми."""
    rows = [[InlineKeyboardButton(theme, callback_data=f"mtheme_{user_id}_{i}")]
            for i, theme in enumerate(MEME_THEMES.keys())]
    rows.append([InlineKeyboardButton("🎲 Рандомна тема і підтема", callback_data=f"mtheme_{user_id}_random")])
    rows.append([InlineKeyboardButton("⬅️ Назад (стиль)",           callback_data=f"photo_meme_{user_id}")])
    return InlineKeyboardMarkup(rows)

def meme_subtopics_keyboard(user_id: int, theme_idx: int) -> InlineKeyboardMarkup:
    """Крок 3 мему — вибір підтеми."""
    theme_name = list(MEME_THEMES.keys())[theme_idx]
    rows = [[InlineKeyboardButton(sub, callback_data=f"msub_{user_id}_{theme_idx}_{j}")]
            for j, sub in enumerate(MEME_THEMES[theme_name])]
    rows.append([InlineKeyboardButton("🎲 Рандомна підтема", callback_data=f"msub_{user_id}_{theme_idx}_random")])
    rows.append([InlineKeyboardButton("⬅️ До тем",           callback_data=f"mtheme_back_{user_id}")])
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

            # ── Змінити стиль (без мему) ────────
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
                    result      = restyle_image(image_bytes, style["prompt"])
                    if result:
                        send_photo(chat_id, io.BytesIO(result), caption=f"{style['label']} ✅")
                    else:
                        send_message(chat_id, "Не вдалося переробити. Спробуй інший стиль.")
                except Exception as e:
                    logging.error(f"style: {e}")
                    send_message(chat_id, f"❌ Помилка: {e}")
                pending_photos.pop(user_id, None)
                answer_callback(callback_id)
                return {"ok": True}

            # ── Мем Крок 1: вибір стилю ─────────
            if cb_data.startswith("memestyle_"):
                parts   = cb_data.split("_")
                user_id = int(parts[1])
                idx_raw = parts[2]
                meme_style_sel[user_id] = None if idx_raw == "none" else int(idx_raw)
                style_label = "без стилю" if idx_raw == "none" else STYLES[int(idx_raw)]["label"]
                edit_message_text(
                    chat_id, message_id,
                    f"✅ Стиль: <b>{style_label}</b>\n\n😂 Крок 2 з 3 — Обери тему:",
                    meme_themes_keyboard(user_id)
                )
                answer_callback(callback_id)
                return {"ok": True}

            # ── Мем: повернення до тем ──────────
            if cb_data.startswith("mtheme_back_"):
                user_id = int(cb_data.split("_")[2])
                style_idx   = meme_style_sel.get(user_id)
                style_label = "без стилю" if style_idx is None else STYLES[style_idx]["label"]
                edit_message_text(
                    chat_id, message_id,
                    f"✅ Стиль: <b>{style_label}</b>\n\n😂 Обери тему:",
                    meme_themes_keyboard(user_id)
                )
                answer_callback(callback_id)
                return {"ok": True}

            # ── Мем Крок 2: вибір теми ──────────
            if cb_data.startswith("mtheme_"):
                parts   = cb_data.split("_")
                user_id = int(parts[1])
                idx_raw = parts[2]

                if idx_raw == "random":
                    # Повністю рандомно — одразу генеруємо
                    file_id = pending_photos.get(user_id)
                    if not file_id:
                        send_message(chat_id, "Фото не знайдено, надішли ще раз.")
                        answer_callback(callback_id)
                        return {"ok": True}
                    theme_name = random.choice(list(MEME_THEMES.keys()))
                    subtopic   = random.choice(MEME_THEMES[theme_name])
                    await _generate_meme(chat_id, user_id, file_id, theme_name, subtopic)
                    pending_photos.pop(user_id, None)
                    meme_style_sel.pop(user_id, None)
                else:
                    theme_idx  = int(idx_raw)
                    theme_name = list(MEME_THEMES.keys())[theme_idx]
                    style_idx  = meme_style_sel.get(user_id)
                    style_label = "без стилю" if style_idx is None else STYLES[style_idx]["label"]
                    edit_message_text(
                        chat_id, message_id,
                        f"✅ Стиль: <b>{style_label}</b>\n"
                        f"✅ Тема: <b>{theme_name}</b>\n\n"
                        f"📋 Крок 3 з 3 — Обери підтему:",
                        meme_subtopics_keyboard(user_id, theme_idx)
                    )
                answer_callback(callback_id)
                return {"ok": True}

            # ── Мем Крок 3: вибір підтеми ───────
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
                subtopic   = random.choice(subtopics) if sub_raw == "random" else subtopics[int(sub_raw)]

                await _generate_meme(chat_id, user_id, file_id, theme_name, subtopic)
                pending_photos.pop(user_id, None)
                meme_style_sel.pop(user_id, None)
                answer_callback(callback_id)
                return {"ok": True}

            # ── Голосовий підтверджуючий callback ──
            if cb_data.startswith("voice_yes_") or cb_data.startswith("voice_no_"):
                parts   = cb_data.split("_")
                action  = parts[1]          # "yes" або "no"
                user_id = int(parts[2])
                file_id = pending_voices.get(user_id)

                if action == "no" or not file_id:
                    edit_message_text(chat_id, message_id, "👌 Добре, пропускаємо.")
                    pending_voices.pop(user_id, None)
                    answer_callback(callback_id)
                    return {"ok": True}

                # action == "yes"
                edit_message_text(chat_id, message_id, "⏳ Розпізнаю...")
                try:
                    audio_bytes, file_path = download_tg_file(file_id)
                    ext        = file_path.split(".")[-1] if "." in file_path else "ogg"
                    transcript = transcribe_voice(audio_bytes, filename=f"voice.{ext}")
                    if not transcript:
                        edit_message_text(chat_id, message_id, "❌ Не вдалося розпізнати. Спробуй ще раз.")
                    else:
                        edit_message_text(chat_id, message_id, f"🎙 <b>Текст з аудіо:</b>\n\n{transcript}")
                except Exception as e:
                    logging.error(f"voice_yes: {e}")
                    edit_message_text(chat_id, message_id, f"❌ Помилка: {e}")

                pending_voices.pop(user_id, None)
                answer_callback(callback_id)
                return {"ok": True}

            # ── Зміна особистості бота ──────────
            if cb_data.startswith("persona_"):
                parts       = cb_data.split("_", 2)
                user_id     = int(parts[1])
                persona_key = parts[2]
                if persona_key in BOT_PERSONAS:
                    user_personas[user_id] = persona_key
                    label = BOT_PERSONAS[persona_key]["label"]
                    edit_message_text(
                        chat_id, message_id,
                        f"✅ Режим змінено на <b>{label}</b>\n\nТепер я буду спілкуватися по-новому 😏",
                        persona_keyboard(user_id)
                    )
                answer_callback(callback_id)
                return {"ok": True}

            # ── Головне меню фото ───────────────
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
                    # Крок 1 — вибір стилю для мему
                    edit_message_text(chat_id, message_id,
                                      "😂 Крок 1 з 3 — Обери стиль фото для мему:",
                                      meme_style_keyboard(user_id))
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
                        logging.error(f"describe: {e}")
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
                logging.error(f"callback: {e}")
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

            if caption in ["мем", "meme"]:
                send_message(chat_id, "😂 Крок 1 з 3 — Обери стиль фото для мему:",
                             reply_markup=meme_style_keyboard(user_id))
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

        # ── Голосове повідомлення ───────────────
        if "voice" in message or "audio" in message:
            media   = message.get("voice") or message.get("audio")
            file_id = media["file_id"]
            pending_voices[user_id] = file_id
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Так, транскрибувати", callback_data=f"voice_yes_{user_id}"),
                    InlineKeyboardButton("❌ Ні",                   callback_data=f"voice_no_{user_id}"),
                ]
            ])
            send_message(chat_id, "🎙 Транскрибувати цей войс?", reply_markup=keyboard)
            return {"ok": True}

        if not text:
            return {"ok": True}

        # ── Команди ─────────────────────────────
        if text == "/start":
            send_message(chat_id,
                "👋 Привіт! Я розумний бот.\n\n"
                "Що я вмію:\n"
                "• 💬 Відповідати з бази знань\n"
                "• 🌐 Шукати в інтернеті\n"
                "• 🖼 Генерувати картинки — /img опис\n"
                "• 😂 Меми: стиль + тема + підтема\n"
                "• 🎨 Переробляти фото у 10 стилях\n"
                "• 🔍 Описувати що на фото\n"
                "• 🎙 Розуміти голосові повідомлення\n"
                "• 🤖 Змінювати режим спілкування — /mode\n"
                "• 🔊 Відповідати голосом — /voice\n\n"
                "Надішли фото або войс — і побачиш магію!")
            return {"ok": True}

        if text == "/clear":
            chat_history[user_id] = []
            pending_answers.pop(user_id, None)
            pending_photos.pop(user_id, None)
            pending_voices.pop(user_id, None)
            meme_style_sel.pop(user_id, None)
            user_personas.pop(user_id, None)
            user_voice_reply.pop(user_id, None)
            send_message(chat_id, "🗑 Пам'ять очищена, режим скинуто!")
            return {"ok": True}

        if text == "/mode":
            current_key   = user_personas.get(user_id, "assistant")
            current_label = BOT_PERSONAS[current_key]["label"]
            send_message(
                chat_id,
                f"🤖 Поточний режим: <b>{current_label}</b>\n\nОбери нову особистість:",
                reply_markup=persona_keyboard(user_id)
            )
            return {"ok": True}

        if text == "/voice":
            current = user_voice_reply.get(user_id, False)
            user_voice_reply[user_id] = not current
            state = "увімкнено 🔊" if not current else "вимкнено 🔇"
            send_message(chat_id, f"Голосові відповіді {state}")
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
                logging.error(f"img: {e}")
                send_message(chat_id, "Не вдалося згенерувати, спробуй пізніше.")
            return {"ok": True}

        # ── База знань + інтернет ────────────────
        add_to_history(user_id, "user", text)
        matches = search_knowledge(text)

        if len(matches) == 1:
            pending_answers[user_id] = matches
            keyboard = [
                [InlineKeyboardButton(matches[0]["title"], callback_data=f"ans_{user_id}_0")],
                [InlineKeyboardButton("❌ Не те, що шукав", callback_data=f"notfound_{user_id}")]
            ]
            send_message(chat_id, "🔍 Знайшов варіант — обери потрібний:",
                         reply_markup=InlineKeyboardMarkup(keyboard))
        elif len(matches) > 1:
            pending_answers[user_id] = matches
            keyboard = [[InlineKeyboardButton(m["title"], callback_data=f"ans_{user_id}_{i}")]
                        for i, m in enumerate(matches)]
            keyboard.append([InlineKeyboardButton("❌ Не те, що шукав", callback_data=f"notfound_{user_id}")])
            send_message(chat_id, "🔍 Знайшов кілька варіантів — обери потрібний:",
                         reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            answer = groq_web_answer(user_id, text)
            add_to_history(user_id, "assistant", answer)
            send_message(chat_id, answer)
            # Голосова відповідь якщо увімкнено
            if user_voice_reply.get(user_id, False):
                voice_buf = text_to_speech(answer)
                if voice_buf:
                    send_voice(chat_id, voice_buf)

    except Exception as e:
        logging.error(f"webhook: {e}")

    return {"ok": True}

# ────────────────────────────────────────────
# Генерація мему (спільна логіка)
# ────────────────────────────────────────────
async def _generate_meme(chat_id: int, user_id: int, file_id: str, theme_name: str, subtopic: str):
    style_idx   = meme_style_sel.get(user_id)
    style_label = "без стилю" if style_idx is None else STYLES[style_idx]["label"]
    send_message(chat_id, f"⏳ Генерую мем «{subtopic}» ({style_label})...")
    try:
        image_bytes = download_tg_photo(file_id)

        # Якщо обрано стиль — спочатку переробляємо фото
        if style_idx is not None:
            restyled = restyle_image(image_bytes, STYLES[style_idx]["prompt"])
            if restyled:
                image_bytes = restyled
            else:
                send_message(chat_id, "⚠️ Стиль не вдався, накладаю текст на оригінал.")

        theme_hint  = f"{theme_name}: {subtopic}"
        top, bottom = generate_meme_text(image_bytes, theme_hint)
        buf         = add_meme_text(image_bytes, top, bottom)
        send_photo(chat_id, buf, caption=f"😂 {theme_name} — {subtopic}")
    except Exception as e:
        logging.error(f"_generate_meme: {e}")
        send_message(chat_id, f"❌ Помилка: {e}")

if __name__ == "__main__":
    uvicorn.run(fastapi_app, host="0.0.0.0", port=10000)
