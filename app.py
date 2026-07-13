"""Cloud-ready Dora Pinterest bot for Bothost (Python 3.11)."""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from ddgs import DDGS


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


DOTENV = load_env(ENV_PATH)


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name) or DOTENV.get(name) or default


TOKEN = setting("TELEGRAM_BOT_TOKEN") or setting("BOT_TOKEN") or setting("API_TOKEN")
if not TOKEN or TOKEN == "PASTE_TOKEN_FROM_BOTFATHER_HERE":
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

try:
    IMAGE_COUNT = max(1, min(10, int(setting("IMAGE_COUNT", "10"))))
except ValueError:
    IMAGE_COUNT = 10

SAFE_MODE = setting("SAFE_MODE", "true").lower() != "false"
ALLOWED_CHAT_IDS = {
    item.strip() for item in setting("ALLOWED_CHAT_IDS").split(",") if item.strip()
}
STATE_PATH = Path(setting("BOT_STATE_PATH", str(ROOT / "runtime-state.json")))
CHAT_MODES: dict[str, str] = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("dora-pinterest-bot")

SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://duckduckgo.com/",
}

BLOCKED_WORDS = (
    "18+", "adult", "nude", "naked", "sex", "sexy", "erotic", "porn",
    "violence", "blood", "gore", "weapon", "guns", "drugs",
    "ню", "голая", "голые", "секс", "эротика", "порно", "кровь",
    "оружие", "наркотики", "насилие", "убийство",
)

ILLUSTRATION_PATTERN = re.compile(
    r"\b(?:cartoon|clip[ -]?art|vector|sticker|drawing|illustration|printable|"
    r"craft|logo|emoji|icon|kawaii|anime|manga|pixar|avatar|game|gaming|"
    r"cat[ -]?girl|neko|digital art|concept art|ai[ -]generated|"
    r"3d (?:art|character|render)|cgi|render)\b|"
    r"раскраск|рисун|иллюстрац|стикер|"
    r"вектор|шаблон|поделк|мульт|аниме|манг|рендер|нейросет",
    re.IGNORECASE,
)

# These terms usually describe visual noise when the user asked for an ordinary
# subject rather than a particular illustration style.
STYLE_NOISE_PATTERN = re.compile(
    r"\b(?:cartoon|clip[ -]?art|vector|sticker|drawing|illustration|printable|"
    r"craft|logo|emoji|icon|kawaii|anime|manga|avatar|talking tom|tom cat|"
    r"cat[ -]?girl|neko|game|gaming|cosplay|face filter|snapchat filter|"
    r"virtual pet|digital art|concept art|ai[ -]generated|"
    r"3d (?:art|character|render)|cgi)\b|"
    r"раскраск|рисун|иллюстрац|стикер|вектор|шаблон|поделк|мульт|аниме|"
    r"манг|аватар|игров|косплей|персонаж|рендер|нейросет",
    re.IGNORECASE,
)

COLORS = {
    "розовый": "soft pink pastel",
    "пудровый": "powder pink",
    "голубой": "sky blue pastel",
    "синий": "blue",
    "мятный": "mint green pastel",
    "зеленый": "soft green",
    "зелёный": "soft green",
    "желтый": "warm yellow",
    "жёлтый": "warm yellow",
    "оранжевый": "soft orange",
    "красный": "red",
    "фиолетовый": "lavender purple",
    "лавандовый": "lavender",
    "сиреневый": "lilac",
    "бежевый": "beige cozy",
    "кремовый": "cream beige",
    "белый": "white bright",
    "черный": "black elegant",
    "чёрный": "black elegant",
    "серый": "soft gray",
}


def telegram_call(method: str, body: dict[str, Any] | None = None) -> Any:
    payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Telegram {method} failed: {error}") from error

    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} returned an error.")
    return data.get("result")


def send_message(chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        body["reply_markup"] = reply_markup
    telegram_call("sendMessage", body)


def send_photo_album(chat_id: str, image_urls: list[str], caption: str) -> None:
    if not image_urls:
        send_message(chat_id, "Не нашла подходящих картинок. Попробуйте другую тему или оттенок.")
        return

    media: list[dict[str, str]] = []
    for index, image_url in enumerate(image_urls[:10]):
        item: dict[str, str] = {"type": "photo", "media": image_url}
        if index == 0:
            item["caption"] = caption
        media.append(item)

    if len(media) == 1:
        telegram_call(
            "sendPhoto",
            {"chat_id": chat_id, "photo": media[0]["media"], "caption": caption},
        )
        return

    try:
        telegram_call("sendMediaGroup", {"chat_id": chat_id, "media": media})
    except RuntimeError:
        LOGGER.warning("Media group failed; sending images one by one.")
        for image_url in image_urls[:10]:
            telegram_call("sendPhoto", {"chat_id": chat_id, "photo": image_url})
            time.sleep(0.35)


def is_allowed_chat(chat_id: str) -> bool:
    return not ALLOWED_CHAT_IDS or chat_id in ALLOWED_CHAT_IDS


def is_safe_query(query: str) -> bool:
    if not SAFE_MODE:
        return True
    normalized = query.lower()
    return not any(word in normalized for word in BLOCKED_WORDS)


def get_mode(chat_id: str) -> str:
    return CHAT_MODES.get(chat_id, "normal")


def set_mode(chat_id: str, mode: str) -> str:
    CHAT_MODES[chat_id] = mode if mode in {"normal", "top", "random"} else "normal"
    return CHAT_MODES[chat_id]


def mode_title(mode: str) -> str:
    return {"top": "ТОП", "random": "РАНДОМ"}.get(mode, "ОБЫЧНЫЙ")


def color_query(color: str) -> str:
    return f"{COLORS.get(color.strip().lower(), color + ' color')} aesthetic"


def is_illustration_intent(query: str) -> bool:
    return bool(ILLUSTRATION_PATTERN.search(query))


def read_url(url: str, headers: dict[str, str], timeout: int = 30) -> bytes:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def candidate_score(result: dict[str, Any], query: str, index: int) -> int:
    score = 1200 - index * 6
    width, height = as_int(result.get("width")), as_int(result.get("height"))
    if width and height:
        shortest_side = min(width, height)
        score += min(220, shortest_side // 5)
        ratio = width / height
        if 0.5 <= ratio <= 2.0:
            score += 60

    metadata = " ".join(
        str(result.get(field, "")) for field in ("title", "source", "url")
    ).lower()
    normalized_query = " ".join(query.lower().split())
    if normalized_query and normalized_query in metadata:
        score += 140

    for word in {part for part in re.split(r"[^\w]+", normalized_query) if len(part) >= 3}:
        if word in metadata:
            score += 50

    image_url = str(result.get("image", "")).lower()
    if "/originals/" in image_url:
        score += 120
    elif "/736x/" in image_url:
        score += 70
    elif "/564x/" in image_url:
        score += 35

    return score


def get_ddgs_image_results(
    query: str,
    limit: int,
    illustration_requested: bool,
) -> list[dict[str, Any]]:
    search_hint = "" if illustration_requested else " photography"
    full_query = f"site:pinterest.com/pin/ {query}{search_hint}"
    errors: list[str] = []

    for backend in ("bing", "auto"):
        try:
            results = DDGS(timeout=25).images(
                query=full_query,
                region="ru-ru",
                safesearch="moderate",
                max_results=limit,
                backend=backend,
                type_image=None if illustration_requested else "photo",
            )
            if results:
                LOGGER.info("Image search backend %s returned %s results.", backend, len(results))
                return list(results)
        except Exception as error:
            errors.append(f"{backend}: {error}")
            LOGGER.warning("Image search backend %s failed: %s", backend, error)

    raise RuntimeError("All DDGS image backends failed: " + " | ".join(errors))


def get_legacy_duckduckgo_results(query: str) -> list[dict[str, Any]]:
    full_query = f"site:pinterest.com/pin/ {query}"
    encoded_query = quote(full_query)
    search_url = f"https://duckduckgo.com/?q={encoded_query}&iax=images&ia=images"
    search_html = read_url(search_url, SEARCH_HEADERS).decode("utf-8", errors="ignore")
    token_match = re.search(r"vqd=[\"']([^\"']+)", search_html)
    if not token_match:
        raise RuntimeError("Image search token was not returned.")

    api_url = (
        "https://duckduckgo.com/i.js?l=ru-ru&o=json"
        f"&q={encoded_query}&vqd={quote(token_match.group(1))}&f=,,,&p=1"
    )
    payload = json.loads(read_url(api_url, SEARCH_HEADERS).decode("utf-8"))
    return list(payload.get("results", []))


def get_pinterest_image_urls(query: str, count: int, mode: str) -> list[str]:
    candidate_limit = max(120, count * 12)
    illustration_requested = is_illustration_intent(query)
    try:
        results = get_ddgs_image_results(query, candidate_limit, illustration_requested)
    except RuntimeError as ddgs_error:
        LOGGER.warning("DDGS search failed; trying the legacy endpoint: %s", ddgs_error)
        results = get_legacy_duckduckgo_results(query)

    candidates: list[dict[str, Any]] = []
    seen_images: set[str] = set()

    for result_index, result in enumerate(results):
        image_url = str(result.get("image", "")).split("?", 1)[0]
        parsed = urlsplit(image_url)
        if parsed.scheme != "https" or parsed.netloc != "i.pinimg.com":
            continue
        if not parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        if re.search(r"/(75x75|170x|236x)/", parsed.path):
            continue
        image_fingerprint = parsed.path.rsplit("/", 1)[-1].lower()
        if image_fingerprint in seen_images:
            continue

        width, height = as_int(result.get("width")), as_int(result.get("height"))
        if width and height:
            ratio = width / height
            if min(width, height) < 300 or ratio < 0.28 or ratio > 3.6:
                continue

        metadata = " ".join(
            str(result.get(field, "")) for field in ("title", "source", "url")
        )
        if not illustration_requested and STYLE_NOISE_PATTERN.search(metadata):
            continue

        seen_images.add(image_fingerprint)
        candidates.append(
            {
                "url": image_url,
                "index": result_index,
                "score": candidate_score(result, query, result_index),
            }
        )
        if len(candidates) >= candidate_limit:
            break

    if mode == "top":
        candidates.sort(key=lambda item: -item["score"])
    elif mode == "random":
        candidates.sort(key=lambda item: -item["score"])
        pool = candidates[:60]
        return [item["url"] for item in random.sample(pool, k=min(count, len(pool)))]
    else:
        candidates.sort(key=lambda item: item["index"])

    return [item["url"] for item in candidates[:count]]


def send_images_for_query(chat_id: str, raw_query: str, mode_label: str = "теме", search_mode: str = "") -> None:
    query = raw_query.strip()
    if not query:
        send_message(chat_id, "Напишите тему после команды. Например: /topic уютный домик в саду")
        return
    if not is_safe_query(query):
        send_message(chat_id, "Этот запрос я не буду искать. Давайте выберем безопасную тему или цвет.")
        return

    mode = search_mode or get_mode(chat_id)
    title = mode_title(mode)
    send_message(chat_id, f"Ищу до 10 подходящих картинок по {mode_label}: {query}\nРежим: {title}")

    try:
        images = get_pinterest_image_urls(query, IMAGE_COUNT, mode)
        count_note = "" if len(images) == IMAGE_COUNT else f"\nПодходящих результатов: {len(images)}"
        send_photo_album(
            chat_id,
            images,
            f"Картинки по запросу: {query}\nРежим: {title}{count_note}",
        )
    except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
        LOGGER.exception("Image search failed.")
        send_message(
            chat_id,
            "Не получилось получить картинки из Pinterest. Попробуйте другой запрос или повторите позже.",
        )


def send_mode_picker(chat_id: str) -> None:
    keyboard = {
        "inline_keyboard": [[
            {"text": "Обычный", "callback_data": "mode:normal"},
            {"text": "Топ", "callback_data": "mode:top"},
            {"text": "Рандом", "callback_data": "mode:random"},
        ]]
    }
    text = (
        f"Выберите режим поиска. Сейчас: {mode_title(get_mode(chat_id))}\n\n"
        "Обычный — результаты по релевантности.\n"
        "Топ — наиболее релевантные и качественные результаты поиска.\n"
        "Рандом — случайные картинки из расширенной качественной выдачи.\n\n"
        "Точных данных о просмотрах Pinterest не предоставляет."
    )
    send_message(chat_id, text, keyboard)


def send_palette(chat_id: str) -> None:
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Розовый", "callback_data": "color:розовый"},
                {"text": "Голубой", "callback_data": "color:голубой"},
                {"text": "Мятный", "callback_data": "color:мятный"},
            ],
            [
                {"text": "Лавандовый", "callback_data": "color:лавандовый"},
                {"text": "Желтый", "callback_data": "color:желтый"},
                {"text": "Бежевый", "callback_data": "color:бежевый"},
            ],
        ]
    }
    send_message(chat_id, "Выберите оттенок:", keyboard)


def handle_message(message: dict[str, Any]) -> None:
    chat_id = str(message["chat"]["id"])
    text = str(message.get("text", ""))
    if not is_allowed_chat(chat_id):
        send_message(chat_id, "Этот бот сейчас доступен только для разрешенных пользователей.")
        return
    if not text:
        send_message(chat_id, "Я понимаю текстовые запросы. Напишите /help.")
        return

    if re.fullmatch(r"/start(?:@\w+)?\s*", text):
        send_message(chat_id, "Привет! Я бот для поиска картинок из Pinterest. Выберите режим, а затем напишите тему или цвет.")
        send_mode_picker(chat_id)
        return
    if re.fullmatch(r"/help(?:@\w+)?\s*", text):
        send_message(
            chat_id,
            "Примеры:\n/topic маленький домик в саду\n/color розовый\n/palette\n"
            "/mode — выбрать режим\n/normal — обычный режим\n/top — рекомендованная выдача\n"
            "/random — случайная выдача\n\nМожно просто написать тему обычным сообщением.",
        )
        return
    if re.fullmatch(r"/mode(?:@\w+)?\s*", text):
        send_mode_picker(chat_id)
        return

    match = re.fullmatch(r"/(normal|top|random)(?:@\w+)?(?:\s+(.+))?", text)
    if match:
        mode, immediate_query = match.groups()
        set_mode(chat_id, mode)
        if immediate_query:
            send_images_for_query(chat_id, immediate_query, search_mode=mode)
        else:
            send_message(chat_id, f"Включён режим: {mode_title(mode)}. Теперь напишите тему или выберите цвет.")
        return

    if re.fullmatch(r"/palette(?:@\w+)?\s*", text) or re.fullmatch(r"/color(?:@\w+)?\s*", text):
        send_palette(chat_id)
        return

    match = re.fullmatch(r"/color(?:@\w+)?\s+(.+)", text)
    if match:
        send_images_for_query(chat_id, color_query(match.group(1)), mode_label="цвету")
        return

    match = re.fullmatch(r"/(?:topic|pins|find)(?:@\w+)?\s+(.+)", text)
    if match:
        send_images_for_query(chat_id, match.group(1))
        return

    if text.startswith("/"):
        send_message(chat_id, "Такой команды нет. Напишите /help.")
        return
    send_images_for_query(chat_id, text)


def handle_callback(callback: dict[str, Any]) -> None:
    telegram_call("answerCallbackQuery", {"callback_query_id": callback["id"]})
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    if not chat_id or not is_allowed_chat(chat_id):
        return

    data = str(callback.get("data", ""))
    if data.startswith("color:"):
        send_images_for_query(chat_id, color_query(data.split(":", 1)[1]), mode_label="цвету")
    elif data.startswith("mode:") and data.split(":", 1)[1] in {"normal", "top", "random"}:
        mode = set_mode(chat_id, data.split(":", 1)[1])
        send_message(chat_id, f"Включён режим: {mode_title(mode)}. Теперь напишите тему или выберите цвет.")


def load_offset() -> int:
    try:
        return max(0, int(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("offset", 0)))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return 0


def save_offset(offset: int) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = STATE_PATH.with_suffix(".tmp")
        temporary_path.write_text(json.dumps({"offset": offset}), encoding="utf-8")
        temporary_path.replace(STATE_PATH)
    except OSError:
        LOGGER.warning("Could not save the update offset.")


def configure_commands() -> None:
    commands = [
        {"command": "start", "description": "Запустить бота и выбрать режим"},
        {"command": "topic", "description": "Найти картинки по теме"},
        {"command": "color", "description": "Найти картинки по оттенку"},
        {"command": "palette", "description": "Открыть палитру цветов"},
        {"command": "mode", "description": "Выбрать режим поиска"},
        {"command": "normal", "description": "Обычный режим"},
        {"command": "top", "description": "Рекомендованная выдача"},
        {"command": "random", "description": "Случайная выдача"},
        {"command": "help", "description": "Показать справку"},
    ]
    try:
        telegram_call("setMyCommands", {"commands": commands})
    except RuntimeError:
        LOGGER.warning("Could not update Telegram command menu.")


def run() -> None:
    configure_commands()
    offset = load_offset()
    LOGGER.info("Dora Pinterest bot started. Public access: %s.", not ALLOWED_CHAT_IDS)

    while True:
        try:
            updates = telegram_call(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message", "callback_query"],
                },
            )
            for update in updates:
                next_offset = int(update["update_id"]) + 1
                # Saving before an answer prevents duplicate responses after a restart.
                save_offset(next_offset)
                offset = next_offset
                if update.get("message"):
                    handle_message(update["message"])
                elif update.get("callback_query"):
                    handle_callback(update["callback_query"])
        except Exception:
            LOGGER.exception("Bot loop error.")
            time.sleep(3)


if __name__ == "__main__":
    run()
