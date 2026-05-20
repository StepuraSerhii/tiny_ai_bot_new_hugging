import os
import logging
import io
import re
import requests
from fastapi import FastAPI, Request
from huggingface_hub import InferenceClient, HfApi
from ddgs import DDGS
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from groq import Groq
import uvicorn

logging.basicConfig(level=logging.INFO)

BOT_TOKEN    = os.environ["BOT_TOKEN"]
HF_TOKEN     = os.environ["HF_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

hf_client   = InferenceClient(token=HF_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

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
        answer_raw = parts[1].strip()
        # Обрізаємо answer до наступного ---
        answer = re.split(r"\n---\n", answer_raw)[0].strip()

        # Заголовок: шукаємо # в тексті між попередньою відповіддю і поточним ###KEYWORDS###
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

        # Keywords через кому — зберігаємо фрази
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
chat_history: dict[int, list] = {}
pending_answers: dict[int, list] = {}

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
# Groq — тільки для інтернет відповідей
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
# Генерація картинки
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

        # ── Кнопки ──
        if "callback_query" in data:
            callback    = data["callback_query"]
            callback_id = callback["id"]
            chat_id     = callback["message"]["chat"]["id"]
            cb_data     = callback["data"]

            if cb_data.startswith("notfound_"):
                user_id = int(cb_data.split("_")[1])
                last = get_history(user_id)
                query = last[-1]["content"] if last else ""
                answer = groq_web_answer(user_id, query)
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

        # ── Повідомлення ──
        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        user_id = message.get("from", {}).get("id")
        text    = message.get("text", "")

        if not text or not chat_id:
            return {"ok": True}

        if text == "/start":
            send_message(chat_id,
                "👋 Привіт! Я розумний бот.\n\n"
                "Що я вмію:\n"
                "• Відповідати з бази знань\n"
                "• Шукати в інтернеті якщо не знаю\n"
                "• Генерувати картинки — /img опис\n"
                "• Пам'ятаю розмову\n\n"
                "Питай що завгодно!")
            return {"ok": True}

        if text == "/clear":
            chat_history[user_id] = []
            pending_answers.pop(user_id, None)
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

        add_to_history(user_id, "user", text)

        # ── Крок 1: локальний пошук по БЗ ──
        matches = search_knowledge(text)

        if len(matches) == 1:
            pending_answers[user_id] = matches
            keyboard = [
                [InlineKeyboardButton(text=matches[0]["title"], callback_data=f"ans_{user_id}_0")],
                [InlineKeyboardButton(text="❌ Не те, що шукав", callback_data=f"notfound_{user_id}")]
            ]
            send_message(chat_id,
                "🔍 Знайшов варіант — обери потрібний:",
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
            send_message(chat_id,
                "🔍 Знайшов кілька варіантів — обери потрібний:",
                reply_markup=InlineKeyboardMarkup(keyboard))

        else:
            # ── Крок 2: інтернет + Groq ──
            answer = groq_web_answer(user_id, text)
            add_to_history(user_id, "assistant", answer)
            send_message(chat_id, answer)

    except Exception as e:
        logging.error(f"Webhook помилка: {e}")

    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(fastapi_app, host="0.0.0.0", port=10000)
