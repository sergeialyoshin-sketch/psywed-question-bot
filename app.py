#!/usr/bin/env python3
"""Telegram question bot for the PsyWed project.

Uses only the Python standard library and reads configuration from environment
variables or a local .env file.
"""

from __future__ import annotations

import json
import os
import re
import signal
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


ENV_FILE = load_env(ROOT / ".env")


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name) or ENV_FILE.get(name) or default


TOKEN = setting("TELEGRAM_BOT_TOKEN") or setting("BOT_TOKEN") or setting("API_TOKEN")
ADMIN_CHAT_ID = setting("ADMIN_CHAT_ID")
MAX_QUESTION_LENGTH = int(setting("MAX_QUESTION_LENGTH", "1500"))
SUBMISSION_COOLDOWN_SECONDS = int(setting("SUBMISSION_COOLDOWN_SECONDS", "60"))

if not TOKEN or TOKEN.startswith("PASTE_"):
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
if not ADMIN_CHAT_ID or ADMIN_CHAT_ID.startswith("PASTE_"):
    raise RuntimeError("ADMIN_CHAT_ID is not configured")


SESSIONS: dict[str, dict[str, str]] = {}
LAST_SUBMISSION: dict[str, float] = {}
RUNNING = True

CATEGORIES = {
    "family": "Семья и отношения",
    "school": "Школа и учителя",
    "children": "Дети и подростки",
    "parents": "Родительские вопросы",
    "wellbeing": "Эмоции и состояние",
    "other": "Другая тема",
    "collaboration": "Сотрудничество",
}

URGENT_RE = re.compile(
    r"суицид|самоубий|покончить с собой|убить себя|не хоч(?:у|ешь|ет|ем|ете|ут) жить|"
    r"угрожает жизни|непосредственной опасност|избивает|насилие",
    re.IGNORECASE,
)


class TelegramApiError(RuntimeError):
    pass


def telegram_api(method: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=json.dumps(body or {}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise TelegramApiError(f"Telegram request failed: {exc}") from exc
    if not result.get("ok"):
        raise TelegramApiError(result.get("description", "Unknown Telegram API error"))
    return result


def send_message(chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    }
    if reply_markup:
        body["reply_markup"] = reply_markup
    telegram_api("sendMessage", body)


def main_menu() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Задать вопрос", "callback_data": "action:new"}],
            [
                {"text": "Как это работает", "callback_data": "action:how"},
                {"text": "О конфиденциальности", "callback_data": "action:privacy"},
            ],
        ]
    }


def send_main_menu(chat_id: str, text: str = "Выберите действие:") -> None:
    send_message(chat_id, text, main_menu())


def send_start(chat_id: str) -> None:
    send_main_menu(
        chat_id,
        "Здравствуйте! Это бот проекта «Психологическая среда».\n\n"
        "Здесь можно предложить вопрос о семье, школе, детях, родителях и "
        "психологическом благополучии. Редакция сможет использовать его при "
        "подготовке будущих выпусков.\n\n"
        "Бот не проводит консультации и не заменяет психологическую или экстренную помощь.",
    )


def send_category_picker(chat_id: str) -> None:
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Семья", "callback_data": "category:family"},
                {"text": "Школа", "callback_data": "category:school"},
            ],
            [{"text": "Дети и подростки", "callback_data": "category:children"}],
            [
                {"text": "Родителям", "callback_data": "category:parents"},
                {"text": "Эмоции", "callback_data": "category:wellbeing"},
            ],
            [{"text": "Другая тема", "callback_data": "category:other"}],
            [{"text": "Сотрудничество", "callback_data": "category:collaboration"}],
            [{"text": "Отмена", "callback_data": "action:cancel"}],
        ]
    }
    send_message(chat_id, "К какой теме относится вопрос?", keyboard)


def send_identity_picker(chat_id: str) -> None:
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "С моим именем", "callback_data": "identity:named"},
                {"text": "Анонимно", "callback_data": "identity:anonymous"},
            ],
            [
                {"text": "Изменить вопрос", "callback_data": "submit:edit"},
                {"text": "Отмена", "callback_data": "action:cancel"},
            ],
        ]
    }
    send_message(chat_id, "Вопрос пока не отправлен. Выберите, как передать его редакции:", keyboard)


def display_name(user: dict[str, Any]) -> str:
    parts = [str(user.get(key, "")).strip() for key in ("first_name", "last_name")]
    return " ".join(part for part in parts if part) or "Без имени"


def send_urgent_notice(chat_id: str) -> None:
    send_message(
        chat_id,
        "Если прямо сейчас кому-то угрожает опасность, не ждите ответа редакции: "
        "позвоните 112 или обратитесь к взрослому, которому доверяете. Этот бот "
        "только собирает вопросы для проекта и не является службой экстренной помощи.",
    )


def send_confirmation(chat_id: str) -> None:
    session = SESSIONS.get(chat_id)
    if not session:
        send_main_menu(chat_id, "Черновик вопроса не найден. Начните заново.")
        return
    category = CATEGORIES[session["category"]]
    identity = "Анонимно" if session["identity"] == "anonymous" else "С именем"
    intro = "Предложение пока не отправлено." if session["category"] == "collaboration" else "Вопрос пока не отправлен."
    preview = (
        f"{intro} Проверьте текст и нажмите «Отправить».\n\n"
        f"Тема: {category}\nАвтор: {identity}\n\n{session['question']}"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Отправить", "callback_data": "submit:confirm"},
                {"text": "Изменить", "callback_data": "submit:edit"},
            ],
            [{"text": "Отмена", "callback_data": "action:cancel"}],
        ]
    }
    send_message(chat_id, preview, keyboard)


def submit_question(chat_id: str) -> None:
    session = SESSIONS.get(chat_id)
    if not session:
        send_main_menu(chat_id, "Черновик вопроса не найден. Начните заново.")
        return
    now = time.monotonic()
    previous = LAST_SUBMISSION.get(chat_id)
    if previous is not None and now - previous < SUBMISSION_COOLDOWN_SECONDS:
        wait = int(SUBMISSION_COOLDOWN_SECONDS - (now - previous) + 0.999)
        send_message(chat_id, f"Вопрос уже отправлялся совсем недавно. Повторите через {wait} сек.")
        return

    is_collaboration = session["category"] == "collaboration"
    prefix = "C" if is_collaboration else "Q"
    question_id = f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
    category = CATEGORIES[session["category"]]
    author = "Анонимно"
    if session["identity"] == "named":
        author = session["display_name"]
        if session.get("username"):
            author += f" (@{session['username']})"

    header = "НОВОЕ ПРЕДЛОЖЕНИЕ О СОТРУДНИЧЕСТВЕ" if is_collaboration else "НОВЫЙ ВОПРОС ДЛЯ «ПСИХОЛОГИЧЕСКОЙ СРЕДЫ»"
    label = "Предложение" if is_collaboration else "Вопрос"
    admin_text = (
        f"{header}\n\nНомер: {question_id}\nТема: {category}\nАвтор: {author}\n\n"
        f"{label}:\n{session['question']}\n\n"
        "Не публиковать автоматически: сообщение требует редакторского отбора."
    )
    try:
        send_message(ADMIN_CHAT_ID, admin_text)
    except TelegramApiError:
        send_message(chat_id, "Не удалось передать вопрос редакции. Попробуйте отправить его ещё раз немного позже.")
        return

    LAST_SUBMISSION[chat_id] = now
    del SESSIONS[chat_id]
    if is_collaboration:
        success = f"Спасибо! Предложение передано редакции под номером {question_id}. Мы свяжемся с вами, если формат сотрудничества заинтересует проект."
    else:
        success = f"Спасибо! Вопрос передан редакции под номером {question_id}. Мы не обещаем личный ответ, но можем обсудить тему в одном из будущих выпусков."
    if chat_id == ADMIN_CHAT_ID:
        success += "\n\nЭтот аккаунт указан как администратор, поэтому копия находится отдельным сообщением прямо выше в этом же диалоге."
    send_main_menu(chat_id, success)


def start_new_question(chat_id: str, user: dict[str, Any]) -> None:
    SESSIONS[chat_id] = {
        "stage": "category",
        "category": "",
        "question": "",
        "identity": "",
        "display_name": display_name(user),
        "username": str(user.get("username", "")),
    }
    send_category_picker(chat_id)


def handle_message(message: dict[str, Any]) -> None:
    chat_id = str(message["chat"]["id"])
    text = message.get("text")
    if not isinstance(text, str) or not text:
        send_message(chat_id, "Пока я принимаю вопросы только текстом.")
        return
    if re.match(r"^/start(?:@\w+)?(?:\s|$)", text):
        SESSIONS.pop(chat_id, None)
        send_start(chat_id)
        return
    if re.match(r"^/(?:question|ask)(?:@\w+)?\s*$", text):
        start_new_question(chat_id, message.get("from", {}))
        return
    if re.match(r"^/cancel(?:@\w+)?\s*$", text):
        SESSIONS.pop(chat_id, None)
        send_main_menu(chat_id, "Отправка вопроса отменена.")
        return
    if re.match(r"^/myid(?:@\w+)?\s*$", text):
        send_message(chat_id, f"Chat ID этого диалога: {chat_id}")
        return
    if re.match(r"^/help(?:@\w+)?(?:\s|$)", text):
        send_message(chat_id, "Команды:\n/start - главное меню\n/question - задать вопрос\n/cancel - отменить черновик\n/myid - показать chat ID\n/help - справка")
        return
    if text.startswith("/"):
        send_message(chat_id, "Такой команды нет. Напишите /help.")
        return

    session = SESSIONS.get(chat_id)
    if not session:
        send_main_menu(chat_id, "Чтобы отправить вопрос, нажмите кнопку «Задать вопрос».")
        return
    if session["stage"] != "question":
        send_message(chat_id, "Сначала выберите один из вариантов на кнопках выше или отмените действие командой /cancel.")
        return

    question = text.strip()
    if len(question) < 10:
        send_message(chat_id, "Вопрос получился слишком коротким. Опишите ситуацию немного подробнее.")
        return
    if len(question) > MAX_QUESTION_LENGTH:
        send_message(chat_id, f"Вопрос слишком длинный. Максимум: {MAX_QUESTION_LENGTH} знаков. Сейчас: {len(question)}.")
        return

    session["question"] = question
    session["display_name"] = display_name(message.get("from", {}))
    session["username"] = str(message.get("from", {}).get("username", ""))
    if URGENT_RE.search(question):
        send_urgent_notice(chat_id)
    if session["category"] == "collaboration":
        session["identity"] = "named"
        session["stage"] = "confirm"
        send_confirmation(chat_id)
        return
    session["stage"] = "identity"
    send_identity_picker(chat_id)


def handle_callback(callback: dict[str, Any]) -> None:
    chat_id = str(callback["message"]["chat"]["id"])
    data = str(callback.get("data", ""))
    try:
        telegram_api("answerCallbackQuery", {"callback_query_id": callback["id"]})
    except TelegramApiError:
        pass

    if data == "action:new":
        start_new_question(chat_id, callback.get("from", {}))
        return
    if data == "action:how":
        send_main_menu(chat_id, "Вы выбираете тему, пишете вопрос и решаете, передать его с именем или анонимно. После подтверждения вопрос приходит редакции проекта. Личный ответ и включение вопроса в выпуск не гарантируются.")
        return
    if data == "action:privacy":
        send_main_menu(chat_id, "Бот передаёт редакции текст вопроса и выбранную тему. Если выбрать «С моим именем», также будут переданы имя профиля и username. При анонимной отправке эти данные в сообщении редакции не указываются. Не присылайте адреса, телефоны, полные имена детей и другие чувствительные данные.")
        return
    if data == "action:cancel":
        SESSIONS.pop(chat_id, None)
        send_main_menu(chat_id, "Отправка вопроса отменена.")
        return

    category_match = re.fullmatch(r"category:(family|school|children|parents|wellbeing|other|collaboration)", data)
    if category_match:
        if chat_id not in SESSIONS:
            start_new_question(chat_id, callback.get("from", {}))
        session = SESSIONS[chat_id]
        session["category"] = category_match.group(1)
        session["stage"] = "question"
        category = CATEGORIES[session["category"]]
        if session["category"] == "collaboration":
            send_message(chat_id, f"Тема: {category}\n\nОпишите предложение одним сообщением: кто вы, какой формат сотрудничества предлагаете и как с вами удобнее связаться. Имя профиля и username будут переданы редакции для ответа.")
        else:
            send_message(chat_id, f"Тема: {category}\n\nНапишите вопрос одним сообщением. Не указывайте полные имена детей, адреса, телефоны и другие личные данные.")
        return

    identity_match = re.fullmatch(r"identity:(named|anonymous)", data)
    if identity_match:
        session = SESSIONS.get(chat_id)
        if not session or session["stage"] != "identity":
            send_main_menu(chat_id, "Черновик вопроса не найден. Начните заново.")
            return
        session["identity"] = identity_match.group(1)
        session["stage"] = "confirm"
        send_confirmation(chat_id)
        return
    if data == "submit:edit":
        session = SESSIONS.get(chat_id)
        if not session:
            send_main_menu(chat_id, "Черновик вопроса не найден. Начните заново.")
            return
        session["stage"] = "question"
        send_message(chat_id, "Напишите новую версию вопроса одним сообщением.")
        return
    if data == "submit:confirm":
        session = SESSIONS.get(chat_id)
        if not session or session["stage"] != "confirm":
            send_main_menu(chat_id, "Черновик вопроса не найден. Начните заново.")
            return
        submit_question(chat_id)
        return
    send_main_menu(chat_id, "Эта кнопка больше неактивна. Начните заново.")


def register_commands() -> None:
    try:
        telegram_api(
            "setMyCommands",
            {"commands": [
                {"command": "start", "description": "Открыть главное меню"},
                {"command": "question", "description": "Задать вопрос редакции"},
                {"command": "cancel", "description": "Отменить текущий вопрос"},
                {"command": "help", "description": "Показать справку"},
            ]},
        )
    except TelegramApiError as exc:
        print(f"Could not update Telegram command menu: {exc}", flush=True)


def request_shutdown(signum: int, _frame: Any) -> None:
    global RUNNING
    print(f"Stopping after signal {signum}...", flush=True)
    RUNNING = False


def run() -> None:
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    register_commands()
    print("PsyWed question bot started.", flush=True)
    offset = 0
    while RUNNING:
        try:
            updates = telegram_api("getUpdates", {
                "offset": offset,
                "timeout": 25,
                "allowed_updates": ["message", "callback_query"],
            })
            for update in updates.get("result", []):
                offset = int(update["update_id"]) + 1
                try:
                    if update.get("message"):
                        handle_message(update["message"])
                    elif update.get("callback_query"):
                        handle_callback(update["callback_query"])
                except Exception as exc:
                    print(f"Update handling error: {exc}", flush=True)
        except TelegramApiError as exc:
            print(f"Bot loop error: {exc}", flush=True)
            time.sleep(3)
    print("PsyWed question bot stopped.", flush=True)


if __name__ == "__main__":
    run()
