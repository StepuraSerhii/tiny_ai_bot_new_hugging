import os
import time
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

# Пароль на доступ. Можна перевизначити через env BOT_PASSWORD у налаштуваннях Space.
BOT_PASSWORD       = os.environ.get("BOT_PASSWORD", "Chumik")
AUTH_DURATION_DAYS = 30

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
    "🎧 Сапорт: тупі клієнти": [
        "клієнт не читає інструкцію",
        "«у мене все зламалося» без жодних деталей",
        "пробували перезавантажити?",
        "клієнт телефонує замість тікета",
        "клієнт пише о 23:55 у п'ятницю",
        "клієнт у CAPS LOCK",
        "клієнт який знає краще за тебе",
        "ескалація до менеджера через дрібницю",
        "клієнт каже «мені порекомендували вас»",
        "клієнт чекає миттєвої відповіді",
        "клієнт скаржиться на власну помилку",
        "клієнт надсилає скрін без скрін",
        "клієнт «це у вас на сайті не працює» — а в нього вимкнений wi-fi",
        "клієнт відкриває 5 тікетів про одне",
        "клієнт обіцяє «знайомому юристу»",
    ],
    "💻 IT / робота розробників": [
        "продакшн упав у п'ятницю",
        "«у мене працює»",
        "merge conflict на 200 файлів",
        "дедлайн завтра, естімейт два тижні",
        "PM питає «коли буде готово»",
        "девопс і кубернетес",
        "stand-up який міг бути листом",
        "код який працював вчора",
        "1 рядок змін → 4 години рев'ю",
        "тести проходять локально",
        "клієнт хоче «маленьке виправлення»",
        "git push --force у мейн",
    ],
    "🏠 Українські реалії": [
        "комуналка взимку",
        "ЖЕК і опалення",
        "маршрутка і здача",
        "ціни в АТБ",
        "відключення світла за графіком",
        "сусід з перфоратором о 7 ранку",
        "повітряна тривога вночі",
        "паркування у дворі",
        "черга в Укрпошті",
        "Дія яка щось не може",
        "ринок vs супермаркет",
        "ями на дорогах",
    ],
    "❤️ Стосунки і побачення": [
        "Tinder vs реальність",
        "перше побачення",
        "«нам треба поговорити»",
        "пише о 2 ночі «не спиш?»",
        "колишня в інстаграмі",
        "знайомство з батьками",
        "сварка через посуд",
        "годинник тиші після «нічого»",
        "він/вона залипає в телефоні",
        "запросили на весілля",
        "побачення наосліп",
    ],
    "🐱 Тварини": [
        "кіт о 3 ночі",
        "собака і прогулянка під дощем",
        "кіт і клавіатура під час дзвінка",
        "собака і пилосос",
        "кіт у коробці з-під капців",
        "хом'як у колесі о 4 ранку",
        "кіт і ялинка",
        "собака їсть чужу їжу зі столу",
        "кіт дивиться у стіну",
        "пес зустрічає з роботи",
        "кіт і ванна",
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
def describe_photo_scene(image_bytes: bytes) -> str:
    """Короткий опис головного на фото (об'єкт, дія, настрій).
    Спільна «прив'язка до фото» для мемів, демотиваторів тощо."""
    b64 = photo_to_base64(image_bytes)
    try:
        r = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": (
                        "Що на фото? Українською, дуже коротко (1 речення): "
                        "головний об'єкт або хто, що робить, вираз обличчя чи настрій."
                    )}
                ]
            }],
            max_tokens=60
        )
        scene = r.choices[0].message.content.strip()
        logging.info(f"Photo scene: {scene!r}")
        return scene
    except Exception as e:
        logging.error(f"describe_photo_scene: {e}")
        return ""

def generate_meme_text(image_bytes: bytes, theme_hint: str = "", scene: str | None = None) -> tuple[str, str]:
    """Мем, прив'язаний САМЕ до вмісту фото. Тема (theme_hint) — лише кут зору.
    scene можна передати готовим (для батлу), щоб не описувати фото двічі."""
    b64 = photo_to_base64(image_bytes)
    if scene is None:
        scene = describe_photo_scene(image_bytes)

    scene_line = f"На фото: {scene}\n" if scene else ""
    if theme_hint:
        theme_line = (
            f'Тема-настрій для жарту: "{theme_hint}" — використай її як КУТ ЗОРУ, '
            "але жарт ОБОВ'ЯЗКОВО має стосуватися саме того, що на фото.\n"
        )
    else:
        theme_line = "Зроби жарт саме про те, що на фото.\n"

    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": (
                        "Ти генератор мемів українською в стилі живого українського інтернету.\n"
                        f"{scene_line}"
                        f"{theme_line}"
                        "\n"
                        "СТИЛЬ: гострий, з НЕСПОДІВАНИМ ПОВОРОТОМ, абсурдний, саркастичний, "
                        "як пише жива людина в Telegram-чаті, а не редакція дитячого журналу.\n"
                        "Дозволено і навіть ВІТАЄТЬСЯ український мат — бляха, нахуй, пиздець, "
                        "охуївати, заєбати, ой бля, та й інші. Використовуй його там, де він "
                        "природньо ПІДСИЛЮЄ жарт, а не для галочки.\n"
                        "\n"
                        "ЗАБОРОНЕНО:\n"
                        "- банальна спостережливість («коли понеділок» — «знову на роботу»)\n"
                        "- починати верхній рядок зі слова «Коли»\n"
                        "- формула «очікування — реальність»\n"
                        "- пояснювати очевидне з фото («білка чекає пенсії» — і так зрозуміло)\n"
                        "- безпечний нейтральний гумор «для всієї родини»\n"
                        "\n"
                        "ДОБРЕ — твіст, перебільшення, абсурд, темний гумор, або раптовий "
                        "побутово-український контекст (комуналка, ЖЕК, маршрутка, ціни в АТБ, "
                        "сусіди, тарифи, відключення). Жарт має бути САМЕ про те, що на фото.\n"
                        "\n"
                        "Приклади правильного тону (формат, не зміст):\n"
                        "ВЕРХ: Думав буде як у фільмі\n"
                        "НИЗ: А вийшло як завжди, бляха\n"
                        "---\n"
                        "ВЕРХ: Психотерапевт: «розкажи свій страх»\n"
                        "НИЗ: Я: квитанція за газ\n"
                        "\n"
                        "Відповідай СТРОГО у форматі без зайвого тексту:\n"
                        "ВЕРХ: короткий текст (макс 6 слів)\n"
                        "НИЗ: короткий текст (макс 6 слів, з ТВІСТОМ)"
                    )}
                ]
            }],
            max_tokens=120,
            temperature=1.15,
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
def _load_font(font_size: int):
    """Завантажує кириличний шрифт потрібного розміру."""
    if CYRILLIC_FONT:
        try:
            return ImageFont.truetype(CYRILLIC_FONT, font_size)
        except Exception:
            pass
    try:
        result = subprocess.run(
            ["fc-list", ":lang=uk:style=Bold", "--format=%{file}\n"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.endswith(".ttf") or line.endswith(".otf"):
                try:
                    return ImageFont.truetype(line, font_size)
                except Exception:
                    continue
    except Exception:
        pass
    return ImageFont.load_default()

def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int,
              font_path: str | None, start_size: int, min_size: int = 20) -> tuple:
    """
    Підбирає розмір шрифту і переносить рядки так щоб текст
    гарантовано вліз у max_width пікселів.
    Повертає (font, lines, line_height, outline).
    """
    text = text.upper()
    size = start_size
    while size >= min_size:
        font = _load_font(size)
        # Пробуємо перенести по словах
        words = text.split()
        lines = []
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            try:
                bbox = draw.textbbox((0, 0), test, font=font)
                tw   = bbox[2] - bbox[0]
            except Exception:
                tw = size * len(test) // 2
            if tw <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        # Перевіряємо що жоден рядок не ширший за max_width
        fits = True
        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                lw   = bbox[2] - bbox[0]
            except Exception:
                lw = size * len(line) // 2
            if lw > max_width:
                fits = False
                break
        if fits:
            line_height = int(size * 1.25)
            outline     = max(2, size // 12)
            return font, lines, line_height, outline
        size -= 2
    # Якщо нічого не підійшло — беремо мінімальний
    font        = _load_font(min_size)
    line_height = int(min_size * 1.25)
    outline     = max(2, min_size // 12)
    return font, [text], line_height, outline

def add_meme_text(image_bytes: bytes, top_text: str, bottom_text: str) -> io.BytesIO:
    img  = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    start_size = max(28, int(h * 0.08))
    max_w      = int(w * 0.92)   # 4% відступ з кожного боку
    padding    = int(h * 0.02)

    def draw_block(text: str, anchor: str):
        """anchor: 'top' або 'bottom'"""
        font, lines, lh, outline = _fit_text(draw, text, max_w, CYRILLIC_FONT, start_size)
        block_h = len(lines) * lh
        y0 = padding if anchor == "top" else h - padding - block_h
        for i, line in enumerate(lines):
            try:
                bbox   = draw.textbbox((0, 0), line, font=font)
                text_w = bbox[2] - bbox[0]
            except Exception:
                text_w = len(line) * (start_size // 2)
            x = max(4, (w - text_w) // 2)
            y = y0 + i * lh
            for dx in range(-outline, outline + 1):
                for dy in range(-outline, outline + 1):
                    if dx or dy:
                        draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))
            draw.text((x, y), line, font=font, fill=(255, 255, 255))

    draw_block(top_text,    "top")
    draw_block(bottom_text, "bottom")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf

# ────────────────────────────────────────────
# Демотиватор: чорна рамка + фото + заголовок/підпис
# ────────────────────────────────────────────
def make_demotivator(image_bytes: bytes, title: str, subtitle: str = "") -> io.BytesIO:
    """Класичний демотиватор: фото в білій рамці на чорному тлі,
    великий заголовок (Times-style верхній регістр) і менший підпис."""
    photo = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Масштабуємо фото так щоб ширша сторона була ~800px
    base_w = 800
    ratio  = base_w / photo.width
    photo  = photo.resize((base_w, int(photo.height * ratio)))
    pw, ph = photo.size

    # Геометрія рамок
    border      = max(2, pw // 220)          # тонка біла лінія навколо фото
    side_margin = int(pw * 0.09)             # чорне поле з боків
    top_margin  = int(pw * 0.09)             # чорне поле зверху
    title_size  = max(34, int(pw * 0.072))
    sub_size    = max(22, int(pw * 0.040))

    canvas_w = pw + side_margin * 2
    # знизу місце під текст: заголовок + підпис + повітря
    text_zone = top_margin + title_size + (sub_size + 10 if subtitle.strip() else 0) + int(pw * 0.05)
    canvas_h  = top_margin + ph + text_zone

    canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
    draw   = ImageDraw.Draw(canvas)

    # Біла рамка навколо фото
    fx, fy = side_margin, top_margin
    draw.rectangle(
        [fx - border, fy - border, fx + pw + border, fy + ph + border],
        outline=(255, 255, 255), width=border
    )
    canvas.paste(photo, (fx, fy))

    # Заголовок — великими літерами, по центру
    max_text_w = canvas_w - side_margin
    t_font, t_lines, t_lh, _ = _fit_text(draw, title, max_text_w, CYRILLIC_FONT, title_size, min_size=24)
    y = fy + ph + int(pw * 0.04)
    for line in t_lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=t_font)
            tw   = bbox[2] - bbox[0]
        except Exception:
            tw = len(line) * title_size // 2
        draw.text(((canvas_w - tw) // 2, y), line, font=t_font, fill=(255, 255, 255))
        y += t_lh

    # Підпис — менший, теж по центру (звичайний регістр зберігаємо)
    if subtitle.strip():
        s_font = _load_font(sub_size)
        for line in textwrap.wrap(subtitle, width=max(20, int(max_text_w / (sub_size * 0.5)))):
            try:
                bbox = draw.textbbox((0, 0), line, font=s_font)
                sw   = bbox[2] - bbox[0]
            except Exception:
                sw = len(line) * sub_size // 2
            draw.text(((canvas_w - sw) // 2, y + 6), line, font=s_font, fill=(220, 220, 220))
            y += int(sub_size * 1.2)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf

def generate_demotivator_caption(image_bytes: bytes, scene: str | None = None) -> tuple[str, str]:
    """Groq vision вигадує іронічний заголовок і підпис демотиватора,
    прив'язані САМЕ до вмісту фото. scene можна передати готовим."""
    b64 = photo_to_base64(image_bytes)
    if scene is None:
        scene = describe_photo_scene(image_bytes)
    scene_line = f"На фото: {scene}\n" if scene else ""
    try:
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": (
                        "Ти створюєш демотиватори українською — іронічні або псевдо-філософські.\n"
                        f"{scene_line}"
                        "Придумай, спираючись САМЕ на те, що на фото (а не загальне):\n"
                        "ЗАГОЛОВОК: одне-два слова, ВЕЛИКИМИ, влучно й іронічно\n"
                        "ПІДПИС: одне коротке речення-«мораль», саркастичне або життєве\n"
                        "Відповідай СТРОГО у цьому форматі, без зайвого тексту."
                    )}
                ]
            }],
            max_tokens=90
        )
        raw = response.choices[0].message.content.strip()
        logging.info(f"Demotivator raw: {repr(raw)}")
        title, sub = "", ""
        for line in raw.splitlines():
            line = line.strip()
            up = line.upper()
            if up.startswith("ЗАГОЛОВОК:"):
                title = line.split(":", 1)[1].strip()
            elif up.startswith("ПІДПИС:"):
                sub = line.split(":", 1)[1].strip()
        if not title:
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            title = lines[0] if lines else "ЖИТТЯ"
            sub   = lines[1] if len(lines) > 1 else ""
        return title or "ЖИТТЯ", sub
    except Exception as e:
        logging.error(f"generate_demotivator_caption: {e}")
        return "ЖИТТЯ", "Іноді фото говорить більше за слова."

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
pending_textmeme: dict[int, dict]         = {}  # user_id -> {theme, subtopic} поки чекаємо текст
battle_votes:    dict[str, dict]          = {}  # battle_id -> {"A": set(uid), "B": set(uid)}
meme_redo_ctx:   dict[int, dict]          = {}  # user_id -> {"file_id": str, "style_idx": int|None} для рероллу
demot_redo_ctx:  dict[int, dict]          = {}  # user_id -> те саме для демотиватора
authorized_until: dict[int, float]        = {}  # user_id -> unix-час коли закінчується доступ

def is_authorized(user_id: int) -> bool:
    expiry = authorized_until.get(user_id)
    if not expiry:
        return False
    if time.time() > expiry:
        authorized_until.pop(user_id, None)
        return False
    return True

def days_left(user_id: int) -> int:
    expiry = authorized_until.get(user_id, 0)
    delta  = expiry - time.time()
    if delta <= 0:
        return 0
    # округлення вгору, щоб «1 день» означав «у тебе ще є частина останньої доби»
    import math
    return max(1, math.ceil(delta / 86400))

def authorize_user(user_id: int):
    authorized_until[user_id] = time.time() + AUTH_DURATION_DAYS * 86400
    save_auth_state()

# ── Персистентність авторизацій через HF-датасет ──
AUTH_FILE       = "auth.json"
AUTH_DATASET_ID = "Tiny89/tiny-bot-knowledge"

def load_auth_state():
    """Підтягуємо збережені авторизації при старті бота.
    Безпечно ігнорує помилки — якщо файла нема, починаємо з порожнього."""
    try:
        import json
        url = f"https://huggingface.co/datasets/{AUTH_DATASET_ID}/resolve/main/{AUTH_FILE}"
        r = requests.get(url, headers={"Authorization": f"Bearer {HF_TOKEN}"}, timeout=10)
        if r.status_code != 200:
            logging.info(f"Auth file not found (status {r.status_code}) — starting fresh")
            return
        data = json.loads(r.text)
        now  = time.time()
        # одразу відсіюємо протерміновані, щоб у пам'ять не лізло сміття
        for k, v in data.items():
            try:
                expiry = float(v)
                if expiry > now:
                    authorized_until[int(k)] = expiry
            except (ValueError, TypeError):
                continue
        logging.info(f"Auth state loaded: {len(authorized_until)} активних користувачів")
    except Exception as e:
        logging.error(f"load_auth_state: {e}")

def save_auth_state():
    """Записуємо authorized_until у HF-датасет. Викликається після кожної зміни."""
    try:
        import json
        now     = time.time()
        cleaned = {str(uid): exp for uid, exp in authorized_until.items() if exp > now}
        content = json.dumps(cleaned, ensure_ascii=False, indent=2).encode("utf-8")
        api = HfApi(token=HF_TOKEN)
        api.upload_file(
            path_or_fileobj=content,
            path_in_repo=AUTH_FILE,
            repo_id=AUTH_DATASET_ID,
            repo_type="dataset",
            commit_message=f"update auth state ({len(cleaned)} users)",
        )
        logging.info(f"Auth state saved: {len(cleaned)} users")
    except Exception as e:
        logging.error(f"save_auth_state: {e}")

# Підвантажуємо збережені авторизації при старті модуля
load_auth_state()

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
    """Транскрибує аудіо через Groq Whisper.
    Telegram надсилає OGG/OPUS — конвертуємо в MP3 через ffmpeg.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", "pipe:0", "-ar", "16000", "-ac", "1",
             "-f", "mp3", "pipe:1"],
            input=audio_bytes,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout:
            audio_bytes = proc.stdout
            filename    = "voice.mp3"
            logging.info("ffmpeg: конвертовано в MP3")
        else:
            logging.warning(f"ffmpeg код {proc.returncode}: {proc.stderr[:200]}")
    except FileNotFoundError:
        logging.warning("ffmpeg не знайдено — відправляємо як є")
    except Exception as e:
        logging.warning(f"ffmpeg помилка: {e}")

    try:
        transcription = groq_client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=(filename, audio_bytes),
            language="uk",
            response_format="text",
        )
        result = transcription.strip() if isinstance(transcription, str) else transcription.text.strip()
        logging.info(f"Whisper: {result[:80]}")
        return result
    except Exception as e:
        logging.error(f"transcribe_voice Whisper: {e}")
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
        [InlineKeyboardButton("🖼 Демотиватор",    callback_data=f"photo_demot_{user_id}")],
        [InlineKeyboardButton("⚔️ Батл мемів",     callback_data=f"photo_battle_{user_id}")],
        [InlineKeyboardButton("🎨 Змінити стиль", callback_data=f"photo_styles_{user_id}")],
        [InlineKeyboardButton("🔍 Описати фото",  callback_data=f"photo_describe_{user_id}")],
    ])

def battle_vote_keyboard(battle_id: str, votes_a: int = 0, votes_b: int = 0) -> InlineKeyboardMarkup:
    """Кнопки голосування для батлу мемів."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🅰️ {votes_a}", callback_data=f"battle_{battle_id}_A"),
        InlineKeyboardButton(f"🅱️ {votes_b}", callback_data=f"battle_{battle_id}_B"),
    ]])

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
    """Єдиний крок фото-мему — вибір стилю фото.
    Після цього бот сам ловить тему з фото і одразу робить мем."""
    rows = [[InlineKeyboardButton(s["label"], callback_data=f"memestyle_{user_id}_{i}")]
            for i, s in enumerate(STYLES)]
    rows.append([InlineKeyboardButton("⏭ Без стилю — лише текст", callback_data=f"memestyle_{user_id}_none")])
    rows.append([InlineKeyboardButton("⬅️ Назад",                  callback_data=f"photo_back_{user_id}")])
    return InlineKeyboardMarkup(rows)

def meme_redo_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """З'являється під готовим фото-мемом — дозволяє швидко перегенерити."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Інший варіант", callback_data=f"memeredo_{user_id}")]
    ])

def demot_style_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Вибір стилю фото для демотиватора. Працює як для мему,
    але після генерує демотиватор (рамка + заголовок + підпис)."""
    rows = [[InlineKeyboardButton(s["label"], callback_data=f"demotstyle_{user_id}_{i}")]
            for i, s in enumerate(STYLES)]
    rows.append([InlineKeyboardButton("⏭ Без стилю — на оригіналі", callback_data=f"demotstyle_{user_id}_none")])
    rows.append([InlineKeyboardButton("⬅️ Назад",                    callback_data=f"photo_back_{user_id}")])
    return InlineKeyboardMarkup(rows)

def demot_redo_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Рерол для демотиватора — нова перефразовка підпису."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Інший варіант", callback_data=f"demotredo_{user_id}")]
    ])

# ────────────────────────────────────────────
# Текстовий мем: генерує зображення + текст без фото
# ────────────────────────────────────────────
def groq_random_theme() -> tuple[str, str]:
    """Groq вигадує абсолютно рандомну тему і підтему — не з нашого списку."""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": (
                "Вигадай абсолютно рандомну і несподівану тему для українського мему. "
                "Це має бути щось смішне з реального життя — те чого немає в стандартних списках. "
                "Наприклад: 'Wi-Fi у маршрутці', 'кіт і новорічна мішура', "
                "'банківський додаток о 3 ночі', 'сусід з дрилем у неділю вранці'. "
                "Відповідай СТРОГО у форматі (лише 2 рядки):\n"
                "ТЕМА: [коротка категорія, 2-4 слова з емодзі]\n"
                "ПІДТЕМА: [конкретна смішна ситуація, 3-6 слів]"
            )}],
            max_tokens=60,
            temperature=1.0,
        )
        raw = r.choices[0].message.content.strip()
        theme = subtopic = ""
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("ТЕМА:"):
                theme = line.split(":", 1)[1].strip()
            elif line.upper().startswith("ПІДТЕМА:"):
                subtopic = line.split(":", 1)[1].strip()
        if theme and subtopic:
            return theme, subtopic
    except Exception as e:
        logging.error(f"groq_random_theme: {e}")
    # fallback
    theme_name = random.choice(list(MEME_THEMES.keys()))
    return theme_name, random.choice(MEME_THEMES[theme_name])

def generate_text_meme(theme_hint: str, user_text: str) -> tuple[str, str, str]:
    """
    Повертає (top, bottom, image_prompt).
    Groq придумує смішний текст І детальний image prompt для FLUX.
    """
    user_part = f'Додатково юзер написав: "{user_text}". Врахуй це у тексті мему.' if user_text.strip() else ""
    is_support = "сапорт" in theme_hint.lower() or "клієнт" in theme_hint.lower()
    pov_line = (
        "ВАЖЛИВО: жарт з ПЕРСПЕКТИВИ виснаженого сапорт-агента, "
        "клієнт — об'єкт іронії і втоми, не герой.\n"
        if is_support else ""
    )
    system = (
        "Ти експерт з мемів у стилі живого українського інтернету та промптів для FLUX.\n"
        "СТИЛЬ ТЕКСТУ: гострий, з НЕСПОДІВАНИМ ПОВОРОТОМ, абсурдний, саркастичний — "
        "як пише жива людина в Telegram-чаті, а не редакція дитячого журналу.\n"
        "Дозволено і ВІТАЄТЬСЯ український мат (бляха, нахуй, пиздець, охуївати, "
        "заєбати, ой бля) — використовуй там, де він підсилює жарт.\n"
        "\n"
        "ЗАБОРОНЕНО:\n"
        "- банальна спостережливість («коли понеділок» — «знову на роботу»)\n"
        "- починати верхній рядок з «Коли»\n"
        "- формула «очікування — реальність»\n"
        "- безпечний нейтральний гумор «для всієї родини»\n"
        "\n"
        "ДОБРЕ — твіст, перебільшення, абсурд, темний гумор, конкретні деталі.\n"
        "\n"
        "IMAGE PROMPT: конкретна смішна сцена, персонажі, їх емоції та дії, "
        "cartoon/illustration style. НЕ загальний — саме та ситуація з мему.\n"
        "Приклад для 'принтер не працює': "
        "'frustrated office worker staring at smoking printer, cartoon style, "
        "exaggerated angry expression, office background'."
    )
    prompt = (
        f"Тема мему: \"{theme_hint}\". {user_part}\n"
        f"{pov_line}"
        "\n"
        "Відповідай СТРОГО у форматі (лише ці 3 рядки, нічого зайвого):\n"
        "ВЕРХ: [текст українською, макс 6 слів]\n"
        "НИЗ: [текст українською з ТВІСТОМ, макс 6 слів]\n"
        "КАРТИНКА: [детальний англійський prompt для FLUX — конкретна сцена з мему, "
        "персонажі + їх емоції + дія + фон, cartoon illustration style, макс 25 слів]"
    )
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=180,
            temperature=1.1,
        )
        raw = response.choices[0].message.content.strip()
        logging.info(f"TextMeme raw: {repr(raw)}")
        top = bottom = img_prompt = ""
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("ВЕРХ:"):
                top = line.split(":", 1)[1].strip()
            elif line.upper().startswith("НИЗ:"):
                bottom = line.split(":", 1)[1].strip()
            elif line.upper().startswith("КАРТИНКА:"):
                img_prompt = line.split(":", 1)[1].strip()
        if not img_prompt:
            # Fallback — просимо Groq тільки prompt
            img_prompt = _fallback_image_prompt(theme_hint)
        return top or "Коли...", bottom or "...ось так 😅", img_prompt
    except Exception as e:
        logging.error(f"generate_text_meme: {e}")
        return "Коли просиш бота", "зробити мем 😅", _fallback_image_prompt(theme_hint)

def _fallback_image_prompt(theme_hint: str) -> str:
    """Запасний варіант — окремий запит тільки для image prompt."""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": (
                f"Write a FLUX image generation prompt for a meme about: \"{theme_hint}\". "
                "Be specific: describe the exact funny scene, characters, emotions, actions, background. "
                "Cartoon illustration style. Max 25 words. Only the prompt, nothing else."
            )}],
            max_tokens=60,
            temperature=0.8,
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return f"funny cartoon illustration of {theme_hint}, humorous scene, expressive characters"

async def _generate_text_meme_and_send(chat_id: int, user_id: int, theme_name: str, subtopic: str, user_text: str):
    theme_hint = f"{theme_name}: {subtopic}"
    send_message(chat_id, f"⏳ Генерую мем на тему «{subtopic}»...")
    try:
        top, bottom, img_prompt = generate_text_meme(theme_hint, user_text)
        full_prompt = (
            f"{img_prompt}, "
            "high quality, vibrant colors, clean cartoon illustration, "
            "funny expressive characters, detailed background, no text"
        )
        logging.info(f"TextMeme FLUX prompt: {full_prompt}")
        img_buf    = generate_image(full_prompt)
        img_bytes  = img_buf.getvalue()
        result_buf = add_meme_text(img_bytes, top, bottom)
        send_photo(chat_id, result_buf, caption=f"😂 {theme_name} — {subtopic}")
    except Exception as e:
        logging.error(f"_generate_text_meme_and_send: {e}")
        send_message(chat_id, f"❌ Помилка: {e}")

def textmeme_skip_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Кнопка 'Придумай сам' під запитом тексту мему."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 Придумай сам", callback_data=f"tmskip_{user_id}")]
    ])

def textmeme_root_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Точка входу /meme — 3 шляхи замість дерева тема→підтема."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 Повний сюрприз",   callback_data=f"tmroot_{user_id}_surprise")],
        [InlineKeyboardButton("📋 По темі",          callback_data=f"tmroot_{user_id}_themes")],
        [InlineKeyboardButton("✍️ Свій текст",       callback_data=f"tmroot_{user_id}_custom")],
    ])

def textmeme_themes_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """5 широких тем — без підтем (вони обираються рандомно всередині)."""
    rows = [[InlineKeyboardButton(theme, callback_data=f"tmtheme_{user_id}_{i}")]
            for i, theme in enumerate(MEME_THEMES.keys())]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"tmroot_{user_id}_back")])
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

            # ── Auth гейт для кнопок ────────────
            clicker_id = callback["from"]["id"]
            if not is_authorized(clicker_id):
                answer_callback(callback_id)
                send_message(chat_id,
                    "🔒 Сесія закінчилась. Введи <code>/login пароль</code> щоб продовжити.")
                return {"ok": True}

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

            # ── Фото-мем: вибір стилю → ОДРАЗУ генерація ──
            # (теми/підтеми більше нема — бот сам ловить кут з фото)
            if cb_data.startswith("memestyle_"):
                parts   = cb_data.split("_")
                user_id = int(parts[1])
                idx_raw = parts[2]
                file_id = pending_photos.get(user_id)
                if not file_id:
                    send_message(chat_id, "Фото не знайдено, надішли ще раз.")
                    answer_callback(callback_id)
                    return {"ok": True}
                style_idx = None if idx_raw == "none" else int(idx_raw)
                meme_style_sel[user_id] = style_idx
                # Зберігаємо контекст щоб «Інший варіант» міг перегенерувати
                meme_redo_ctx[user_id] = {"file_id": file_id, "style_idx": style_idx}
                answer_callback(callback_id)
                await _generate_meme(chat_id, user_id, file_id)
                pending_photos.pop(user_id, None)
                meme_style_sel.pop(user_id, None)
                return {"ok": True}

            # ── Фото-мем: «🔄 Інший варіант» ──
            if cb_data.startswith("memeredo_"):
                user_id = int(cb_data.split("_")[1])
                ctx     = meme_redo_ctx.get(user_id)
                if not ctx:
                    send_message(chat_id, "Контекст утрачено — надішли фото ще раз.")
                    answer_callback(callback_id)
                    return {"ok": True}
                meme_style_sel[user_id] = ctx["style_idx"]
                answer_callback(callback_id)
                await _generate_meme(chat_id, user_id, ctx["file_id"])
                meme_style_sel.pop(user_id, None)
                return {"ok": True}

            # ── Демотиватор: вибір стилю → ОДРАЗУ генерація ──
            if cb_data.startswith("demotstyle_"):
                parts   = cb_data.split("_")
                user_id = int(parts[1])
                idx_raw = parts[2]
                file_id = pending_photos.get(user_id)
                if not file_id:
                    send_message(chat_id, "Фото не знайдено, надішли ще раз.")
                    answer_callback(callback_id)
                    return {"ok": True}
                style_idx = None if idx_raw == "none" else int(idx_raw)
                meme_style_sel[user_id] = style_idx
                demot_redo_ctx[user_id] = {"file_id": file_id, "style_idx": style_idx}
                answer_callback(callback_id)
                await _generate_demotivator(chat_id, user_id, file_id)
                pending_photos.pop(user_id, None)
                meme_style_sel.pop(user_id, None)
                return {"ok": True}

            # ── Демотиватор: «🔄 Інший варіант» ──
            if cb_data.startswith("demotredo_"):
                user_id = int(cb_data.split("_")[1])
                ctx     = demot_redo_ctx.get(user_id)
                if not ctx:
                    send_message(chat_id, "Контекст утрачено — надішли фото ще раз.")
                    answer_callback(callback_id)
                    return {"ok": True}
                meme_style_sel[user_id] = ctx["style_idx"]
                answer_callback(callback_id)
                await _generate_demotivator(chat_id, user_id, ctx["file_id"])
                meme_style_sel.pop(user_id, None)
                return {"ok": True}

            # ── Текстовий мем: рут-меню (3 шляхи) ──
            if cb_data.startswith("tmroot_"):
                parts   = cb_data.split("_")
                user_id = int(parts[1])
                action  = parts[2]  # surprise | themes | custom | back

                if action == "surprise":
                    # Повний рандом: AI вигадує і тему, і підтему
                    edit_message_text(chat_id, message_id, "🎲 Готую сюрприз...")
                    theme_name, subtopic = groq_random_theme()
                    answer_callback(callback_id)
                    await _generate_text_meme_and_send(chat_id, user_id,
                                                       theme_name, subtopic, user_text="")
                    return {"ok": True}

                if action == "themes":
                    edit_message_text(chat_id, message_id,
                        "📋 Обери тему:",
                        textmeme_themes_keyboard(user_id))
                    answer_callback(callback_id)
                    return {"ok": True}

                if action == "custom":
                    # Чекаємо на свій текст. Без теми — позначимо custom-плейсхолдером.
                    pending_textmeme[user_id] = {"theme": "✍️ Своє", "subtopic": "—", "custom": True}
                    edit_message_text(chat_id, message_id,
                        "✍️ Напиши тему / текст для мему наступним повідомленням.\n"
                        "Або тисни кнопку, щоб бот придумав сам:",
                        textmeme_skip_keyboard(user_id))
                    answer_callback(callback_id)
                    return {"ok": True}

                if action == "back":
                    edit_message_text(chat_id, message_id,
                        "😂 Як зробити мем?",
                        textmeme_root_keyboard(user_id))
                    answer_callback(callback_id)
                    return {"ok": True}

            # ── Текстовий мем: одна з 5 тем → ОДРАЗУ генерація з рандом підтемою ──
            if cb_data.startswith("tmtheme_"):
                parts      = cb_data.split("_")
                user_id    = int(parts[1])
                theme_idx  = int(parts[2])
                theme_name = list(MEME_THEMES.keys())[theme_idx]
                subtopic   = random.choice(MEME_THEMES[theme_name])
                answer_callback(callback_id)
                await _generate_text_meme_and_send(chat_id, user_id,
                                                   theme_name, subtopic, user_text="")
                return {"ok": True}

            # ── Текстовий мем: кнопка "Придумай сам" ──
            if cb_data.startswith("tmskip_"):
                user_id   = int(cb_data.split("_")[1])
                meme_data = pending_textmeme.pop(user_id, None)
                if meme_data:
                    answer_callback(callback_id)
                    await _generate_text_meme_and_send(
                        chat_id, user_id,
                        meme_data["theme"], meme_data["subtopic"], user_text=""
                    )
                else:
                    edit_message_text(chat_id, message_id, "Спочатку обери тему — натисни /meme")
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

            # ── Голосування в батлі мемів ───────
            if cb_data.startswith("battle_"):
                # формат: battle_{battle_id}_{A|B}, battle_id = "<uid>-<rnd>"
                choice    = cb_data.rsplit("_", 1)[1]          # A або B
                battle_id = cb_data[len("battle_"):-(len(choice) + 1)]
                voter     = callback["from"]["id"]
                votes     = battle_votes.setdefault(battle_id, {"A": set(), "B": set()})
                # один голос на людину: знімаємо з протилежного
                other = "B" if choice == "A" else "A"
                votes[other].discard(voter)
                if voter in votes[choice]:
                    votes[choice].discard(voter)          # повторний клік = відкликати
                else:
                    votes[choice].add(voter)
                a, b = len(votes["A"]), len(votes["B"])
                try:
                    edit_message_keyboard(chat_id, message_id,
                                          battle_vote_keyboard(battle_id, a, b))
                except Exception as e:
                    logging.error(f"battle vote: {e}")
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
                                      "😂 Обери стиль фото — далі бот сам зробить жарт по фото:",
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

                if action == "demot":
                    edit_message_text(chat_id, message_id,
                                      "🖼 Обери стиль для демотиватора — далі бот зробить підпис:",
                                      demot_style_keyboard(user_id))
                    answer_callback(callback_id)
                    return {"ok": True}

                if action == "battle":
                    await _generate_meme_battle(chat_id, user_id, file_id)
                    pending_photos.pop(user_id, None)

                answer_callback(callback_id)
                return {"ok": True}

            # ── Перемикач голосу ────────────────
            if cb_data.startswith("voicetoggle_"):
                user_id = int(cb_data.split("_")[1])
                current = user_voice_reply.get(user_id, False)
                user_voice_reply[user_id] = not current
                new_state = not current
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🔊 Увімкнути голос" if not new_state else "🔇 Вимкнути голос",
                        callback_data=f"voicetoggle_{user_id}"
                    )
                ]])
                state_text = "🔊 <b>Увімкнено</b> — відповідаю голосом" if new_state else "🔇 <b>Вимкнено</b> — відповідаю текстом"
                edit_message_text(chat_id, message_id,
                    f"Режим голосових відповідей\n{state_text}", kb)
                answer_callback(callback_id)
                return {"ok": True}

            # ── База знань ──────────────────────
            if cb_data.startswith("notfound_"):
                user_id = int(cb_data.split("_")[1])
                last    = get_history(user_id)
                query   = last[-1]["content"] if last else ""
                answer  = groq_web_answer(user_id, query)
                add_to_history(user_id, "assistant", answer)
                if user_voice_reply.get(user_id, False):
                    send_message(chat_id, "🎙 Генерую голосову відповідь...")
                    voice_buf = text_to_speech(answer)
                    if voice_buf:
                        send_voice(chat_id, voice_buf)
                    else:
                        send_message(chat_id, answer)
                else:
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

        # ════════════════════════════════════════
        # AUTH ГЕЙТ (до будь-якої логіки)
        # ════════════════════════════════════════
        # /login — обробляється без авторизації
        if text and text.startswith("/login"):
            parts = text.split(maxsplit=1)
            pwd   = parts[1].strip() if len(parts) > 1 else ""
            if pwd == BOT_PASSWORD:
                authorize_user(user_id)
                send_message(chat_id,
                    f"✅ Доступ надано на <b>{AUTH_DURATION_DAYS} днів</b>!\n"
                    "Тицяй /start щоб подивитися можливості, або просто пиши.")
            elif not pwd:
                send_message(chat_id, "Використання: <code>/login твій_пароль</code>")
            else:
                send_message(chat_id, "❌ Невірний пароль.")
            return {"ok": True}

        # Якщо не авторизований — нічого крім /start (підказка) не пускаємо
        if user_id and not is_authorized(user_id):
            if text == "/start":
                send_message(chat_id,
                    "👋 Привіт! Цей бот доступний за паролем.\n\n"
                    "Введи: <code>/login пароль</code>\n"
                    f"Після правильного пароля — <b>{AUTH_DURATION_DAYS} днів</b> доступу.")
            else:
                send_message(chat_id,
                    "🔒 Доступ закритий. Введи <code>/login пароль</code>")
            return {"ok": True}

        # /logout і /status — лише для авторизованих
        if text == "/logout":
            authorized_until.pop(user_id, None)
            save_auth_state()
            send_message(chat_id, "👋 Ти вийшла. <code>/login пароль</code> щоб повернутись.")
            return {"ok": True}

        if text == "/status":
            send_message(chat_id, f"✅ Доступ активний. Лишилось: <b>{days_left(user_id)} днів</b>.")
            return {"ok": True}

        # ── Отримали фото ───────────────────────
        if "photo" in message:
            file_id = message["photo"][-1]["file_id"]
            caption = message.get("caption", "").strip().lower()
            pending_photos[user_id] = file_id

            if caption in ["мем", "meme"]:
                send_message(chat_id, "😂 Обери стиль фото — далі бот сам зробить жарт по фото:",
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
                "• 😂 Меми по фото — бот сам ловить кут жарту\n"
                "• 🎲 Рандомні меми без фото — /meme (сюрприз / тема / свій текст)\n"
                "• 🖼 Демотиватори з фото\n"
                "• ⚔️ Батл мемів — два варіанти + голосування\n"
                "• 🎨 Переробляти фото у 10 стилях\n"
                "• 🔍 Описувати що на фото\n"
                "• 🎙 Розуміти голосові повідомлення\n"
                "• 🤖 Змінювати режим спілкування — /mode\n"
                "• 🔊 Відповідати голосом — /voice\n"
                "• 🔐 Перевірити термін доступу — /status (вихід — /logout)\n\n"
                "Надішли фото або войс — і побачиш магію!")
            return {"ok": True}

        if text == "/clear":
            chat_history[user_id] = []
            pending_answers.pop(user_id, None)
            pending_photos.pop(user_id, None)
            pending_voices.pop(user_id, None)
            pending_textmeme.pop(user_id, None)
            meme_style_sel.pop(user_id, None)
            meme_redo_ctx.pop(user_id, None)
            demot_redo_ctx.pop(user_id, None)
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
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔊 Увімкнути голос" if not current else "🔇 Вимкнути голос",
                    callback_data=f"voicetoggle_{user_id}"
                )
            ]])
            state = "🔊 Зараз: <b>увімкнено</b>" if current else "🔇 Зараз: <b>вимкнено</b>"
            send_message(chat_id,
                f"Режим голосових відповідей\n{state}\n\n"
                "Коли увімкнено — бот відповідає тільки голосом (без тексту).",
                reply_markup=kb)
            return {"ok": True}

        if text in ["/meme", "мем", "зроби мем", "мем!"]:
            send_message(chat_id, "😂 Як зробити мем?",
                         reply_markup=textmeme_root_keyboard(user_id))
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

        # ── Перевірка чи юзер вводить текст для мему ──
        meme_data = pending_textmeme.get(user_id)
        if meme_data:
            pending_textmeme.pop(user_id, None)
            if meme_data.get("custom"):
                # «Свій текст»: вживаємо текст юзера як тему-сім'я; підтеми нема.
                await _generate_text_meme_and_send(
                    chat_id, user_id,
                    theme_name="✍️ Своє", subtopic=text,
                    user_text="",
                )
            else:
                await _generate_text_meme_and_send(
                    chat_id, user_id,
                    meme_data["theme"], meme_data["subtopic"],
                    user_text=text,
                )
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
            if user_voice_reply.get(user_id, False):
                # Голосовий режим — спочатку генеруємо, якщо вдалося — тільки войс
                send_message(chat_id, "🎙 Генерую голосову відповідь...")
                voice_buf = text_to_speech(answer)
                if voice_buf:
                    send_voice(chat_id, voice_buf)
                else:
                    # gTTS не спрацював — fallback на текст
                    send_message(chat_id, answer)
            else:
                send_message(chat_id, answer)

    except Exception as e:
        logging.error(f"webhook: {e}")

    return {"ok": True}

# ────────────────────────────────────────────
# Генерація мему (спільна логіка)
# ────────────────────────────────────────────
async def _generate_meme(chat_id: int, user_id: int, file_id: str):
    """Фото-мем: бот сам ловить тему з фото (theme_hint порожній).
    Після генерації показує кнопку «🔄 Інший варіант»."""
    style_idx   = meme_style_sel.get(user_id)
    style_label = "без стилю" if style_idx is None else STYLES[style_idx]["label"]
    send_message(chat_id, f"⏳ Генерую мем ({style_label})...")
    try:
        image_bytes = download_tg_photo(file_id)

        # Якщо обрано стиль — спочатку переробляємо фото
        if style_idx is not None:
            restyled = restyle_image(image_bytes, STYLES[style_idx]["prompt"])
            if restyled:
                image_bytes = restyled
            else:
                send_message(chat_id, "⚠️ Стиль не вдався, накладаю текст на оригінал.")

        # Бот сам обирає кут жарту з фото — theme_hint пустий
        top, bottom = generate_meme_text(image_bytes)
        buf         = add_meme_text(image_bytes, top, bottom)
        send_photo(chat_id, buf, caption="😂")
        send_message(chat_id, "Хочеш ще один варіант — натисни:",
                     reply_markup=meme_redo_keyboard(user_id))
    except Exception as e:
        logging.error(f"_generate_meme: {e}")
        send_message(chat_id, f"❌ Помилка: {e}")

# ────────────────────────────────────────────
# Демотиватор: стиль → переробка фото → підпис
# ────────────────────────────────────────────
async def _generate_demotivator(chat_id: int, user_id: int, file_id: str):
    """Демотиватор: спершу (за бажанням) переробляє фото у стилі,
    потім кладе чорну рамку + заголовок + підпис, прив'язаний до зображення."""
    style_idx   = meme_style_sel.get(user_id)
    style_label = "без стилю" if style_idx is None else STYLES[style_idx]["label"]
    send_message(chat_id, f"⏳ Роблю демотиватор ({style_label})...")
    try:
        image_bytes = download_tg_photo(file_id)

        # Якщо обрано стиль — переробляємо фото перед накладанням підпису
        if style_idx is not None:
            restyled = restyle_image(image_bytes, STYLES[style_idx]["prompt"])
            if restyled:
                image_bytes = restyled
            else:
                send_message(chat_id, "⚠️ Стиль не вдався, роблю на оригіналі.")

        title, sub = generate_demotivator_caption(image_bytes)
        buf        = make_demotivator(image_bytes, title, sub)
        send_photo(chat_id, buf, caption="🖼 Демотиватор готовий")
        send_message(chat_id, "Хочеш інший підпис — натисни:",
                     reply_markup=demot_redo_keyboard(user_id))
    except Exception as e:
        logging.error(f"_generate_demotivator: {e}")
        send_message(chat_id, f"❌ Помилка: {e}")

# ────────────────────────────────────────────
# Батл мемів: два варіанти на одне фото + голосування
# ────────────────────────────────────────────
async def _generate_meme_battle(chat_id: int, user_id: int, file_id: str):
    send_message(chat_id, "⚔️ Готую два меми для батлу...")
    try:
        image_bytes = download_tg_photo(file_id)

        # Описуємо фото один раз — обидва варіанти прив'язані до того ж вмісту.
        # Тем не задаємо — модель сама обирає кути жарту (з temperature 1.15
        # два виклики природньо дають різні результати).
        scene = describe_photo_scene(image_bytes)
        top1, bot1 = generate_meme_text(image_bytes, scene=scene)
        top2, bot2 = generate_meme_text(image_bytes, scene=scene)
        buf_a = add_meme_text(image_bytes, top1, bot1)
        buf_b = add_meme_text(image_bytes, top2, bot2)

        battle_id = f"{user_id}-{random.randint(1000, 9999)}"
        battle_votes[battle_id] = {"A": set(), "B": set()}

        send_photo(chat_id, buf_a, caption="🅰️ Варіант A")
        send_photo(chat_id, buf_b, caption="🅱️ Варіант B")
        send_message(
            chat_id,
            "⚔️ <b>Батл мемів!</b> Який смішніший?\nТисни 🅰️ або 🅱️ (можна змінити голос):",
            reply_markup=battle_vote_keyboard(battle_id, 0, 0)
        )
    except Exception as e:
        logging.error(f"_generate_meme_battle: {e}")
        send_message(chat_id, f"❌ Помилка: {e}")

if __name__ == "__main__":
    uvicorn.run(fastapi_app, host="0.0.0.0", port=10000)
