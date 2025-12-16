import requests
from bs4 import BeautifulSoup
import asyncio
import json
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

# URLs форумов
ADMIN_COMPLAINTS_URL = "https://forum.gambit-rp.com/forums/64/"
PLAYER_COMPLAINTS_URL = "https://forum.gambit-rp.com/forums/70/"

TOKEN = "8375119236:AAEgRFf75tpgmDcO-CDarFHAMfo2bUdE7r8"
USERS_FILE = "subscribed_users.json"
SEEN_ADMIN_FILE = "seen_admin.json"
SEEN_PLAYER_FILE = "seen_player.json"
CHECK_INTERVAL = 60  # секунд

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Работа с файлами ===
def load_users():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_users(users):
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(users), f)
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователей: {e}")

def load_seen(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen, filename):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(list(seen), f)
    except Exception as e:
        logger.error(f"Ошибка сохранения {filename}: {e}")

# === Парсинг ===
def extract_topic_id(url):
    match = re.search(r'threads/(\d+)', url)
    return match.group(1) if match else url

async def get_forum_topics(forum_url):
    topics = []
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(forum_url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Ищем все темы на странице
        for item in soup.select('.structItem--thread'):
            classes = item.get('class', [])
            
            # Пропускаем закрепленные темы
            if 'is-sticky' in classes:
                logger.debug("Пропускаем закрепленную тему")
                continue
            
            # Находим основную ссылку темы - пробуем разные селекторы
            title_link = item.select_one('a[data-tp-primary]')
            if not title_link:
                title_link = item.select_one('.structItem-title a')
            if not title_link:
                continue
            
            # Извлекаем ссылку
            link = title_link.get('href', '')
            if not link:
                continue
            if not link.startswith('http'):
                link = "https://forum.gambit-rp.com" + link
            
            # Убираем якорь /unread если есть
            link = re.sub(r'/(unread|latest).*$', '', link).rstrip('/')
            
            # Извлекаем чистое название без префиксов
            # Клонируем элемент чтобы не портить оригинал
            title_clone = BeautifulSoup(str(title_link), 'html.parser')
            
            # Удаляем все span элементы (префиксы)
            for span in title_clone.find_all('span'):
                span.decompose()
            
            # Получаем чистый текст
            title = title_clone.get_text(strip=True)
            
            if not title:
                logger.debug(f"Пустое название для {link}")
                continue
            
            topic_id = extract_topic_id(link)
            topics.append((title, link, topic_id))
            logger.debug(f"Найдена тема: {title[:50]}... | ID: {topic_id}")
            
    except Exception as e:
        logger.error(f"Ошибка парсинга {forum_url}: {e}")
    
    logger.info(f"Найдено тем в {forum_url}: {len(topics)}")
    return topics

# === Отправка сообщений ===
async def send_complaint_notification(application, users, title, link, complaint_type):
    emoji = "🚨" if complaint_type == "admin" else "⚠️"
    type_text = "администрацию" if complaint_type == "admin" else "игрока"
    
    text = (
        f"{emoji} <b>Новая жалоба на {type_text}!</b>\n\n"
        f"📋 <b>Тема:</b> {title}"
    )
    
    keyboard = [[InlineKeyboardButton("🔍 Проверить жалобу", url=link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    for user_id in users:
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            logger.info(f"Отправлено уведомление пользователю {user_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки {user_id}: {e}")

# === Инициализация ===
async def initialize_seen(forum_url, filename):
    seen = load_seen(filename)
    if not seen:
        logger.info(f"Инициализация {filename}...")
        topics = await get_forum_topics(forum_url)
        seen = set(topic_id for _, _, topic_id in topics)
        save_seen(seen, filename)
        logger.info(f"Добавлено {len(seen)} тем в {filename}")
    else:
        logger.info(f"Загружено {len(seen)} тем из {filename}")
    return seen

# === Наблюдатели ===
async def admin_watcher(application):
    users = load_users()
    seen = await initialize_seen(ADMIN_COMPLAINTS_URL, SEEN_ADMIN_FILE)
    
    while True:
        try:
            topics = await get_forum_topics(ADMIN_COMPLAINTS_URL)
            new_count = 0
            for title, link, topic_id in topics:
                if topic_id not in seen:
                    await send_complaint_notification(application, users, title, link, "admin")
                    seen.add(topic_id)
                    save_seen(seen, SEEN_ADMIN_FILE)
                    new_count += 1
            if new_count > 0:
                logger.info(f"Найдено новых жалоб на администрацию: {new_count}")
        except Exception as e:
            logger.error(f"Ошибка в admin_watcher: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

async def player_watcher(application):
    users = load_users()
    seen = await initialize_seen(PLAYER_COMPLAINTS_URL, SEEN_PLAYER_FILE)
    
    while True:
        try:
            topics = await get_forum_topics(PLAYER_COMPLAINTS_URL)
            new_count = 0
            for title, link, topic_id in topics:
                if topic_id not in seen:
                    await send_complaint_notification(application, users, title, link, "player")
                    seen.add(topic_id)
                    save_seen(seen, SEEN_PLAYER_FILE)
                    new_count += 1
            if new_count > 0:
                logger.info(f"Найдено новых жалоб на игроков: {new_count}")
        except Exception as e:
            logger.error(f"Ошибка в player_watcher: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

# === Команды ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    users = load_users()
    if user_id not in users:
        users.add(user_id)
        save_users(users)
    
    keyboard = [
        [InlineKeyboardButton("📋 Жалобы на администрацию", callback_data="list_admin")],
        [InlineKeyboardButton("⚠️ Жалобы на игроков", callback_data="list_player")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🤖 <b>Бот мониторинга жалоб Gambit RP</b>\n\n"
        "✅ Вы подписаны на уведомления!\n\n"
        "Вы будете получать уведомления о:\n"
        "🚨 Новых жалобах на администрацию\n"
        "⚠️ Новых жалобах на игроков\n\n"
        "Выберите действие:"
    )
    
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "list_admin":
        topics = await get_forum_topics(ADMIN_COMPLAINTS_URL)
        await send_complaint_list(query, topics, "администрацию", "admin")
    
    elif query.data == "list_player":
        topics = await get_forum_topics(PLAYER_COMPLAINTS_URL)
        await send_complaint_list(query, topics, "игроков", "player")
    
    elif query.data == "help":
        help_text = (
            "ℹ️ <b>Справка по боту</b>\n\n"
            "Бот автоматически отслеживает новые жалобы на форуме и присылает уведомления.\n\n"
            "<b>Доступные команды:</b>\n"
            "/start - главное меню\n"
            "/admin - список жалоб на администрацию\n"
            "/player - список жалоб на игроков\n\n"
            "💡 Используйте кнопки для удобной навигации!"
        )
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_main")]]
        await query.edit_message_text(help_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif query.data == "back_main":
        keyboard = [
            [InlineKeyboardButton("📋 Жалобы на администрацию", callback_data="list_admin")],
            [InlineKeyboardButton("⚠️ Жалобы на игроков", callback_data="list_player")],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
        ]
        text = (
            "🤖 <b>Бот мониторинга жалоб Gambit RP</b>\n\n"
            "Выберите действие:"
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def send_complaint_list(query, topics, complaint_type, emoji_type):
    if not topics:
        await query.edit_message_text(f"❌ Жалобы на {complaint_type} не найдены.")
        return
    
    emoji = "🚨" if emoji_type == "admin" else "⚠️"
    msg = f"{emoji} <b>Текущие жалобы на {complaint_type}:</b>\n\n"
    
    for n, (title, link, _) in enumerate(topics[:15], 1):  # Показываем только первые 15
        msg += f"<b>{n}.</b> {title}\n🔗 <a href=\"{link}\">Открыть</a>\n\n"
    
    keyboard = [[InlineKeyboardButton("◀️ Назад в меню", callback_data="back_main")]]
    
    await query.edit_message_text(
        msg,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True
    )

async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = await get_forum_topics(ADMIN_COMPLAINTS_URL)
    if not topics:
        await update.message.reply_text("❌ Жалобы на администрацию не найдены.")
        return
    
    msg = "🚨 <b>Текущие жалобы на администрацию:</b>\n\n"
    for n, (title, link, _) in enumerate(topics, 1):
        msg += f"<b>{n}.</b> {title}\n🔗 <a href=\"{link}\">Открыть</a>\n\n"
    
    for i in range(0, len(msg), 4000):
        await update.message.reply_text(msg[i:i+4000], parse_mode="HTML", disable_web_page_preview=True)

async def player_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = await get_forum_topics(PLAYER_COMPLAINTS_URL)
    if not topics:
        await update.message.reply_text("❌ Жалобы на игроков не найдены.")
        return
    
    msg = "⚠️ <b>Текущие жалобы на игроков:</b>\n\n"
    for n, (title, link, _) in enumerate(topics, 1):
        msg += f"<b>{n}.</b> {title}\n🔗 <a href=\"{link}\">Открыть</a>\n\n"
    
    for i in range(0, len(msg), 4000):
        await update.message.reply_text(msg[i:i+4000], parse_mode="HTML", disable_web_page_preview=True)

# === Главная функция ===
def main():
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_list))
    application.add_handler(CommandHandler("player", player_list))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Запускаем два наблюдателя параллельно
    application.job_queue.run_repeating(
        lambda ctx: asyncio.create_task(admin_watcher(application)),
        interval=CHECK_INTERVAL,
        first=0
    )
    application.job_queue.run_repeating(
        lambda ctx: asyncio.create_task(player_watcher(application)),
        interval=CHECK_INTERVAL,
        first=5  # Сдвиг на 5 секунд
    )
    
    application.run_polling()

if __name__ == "__main__":
    main()
