# config.py - Конфигурация с переменными окружения

import os
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# ════════════════════════════════════════
# TELEGRAM
# ════════════════════════════════════════

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN не установлен!")

# ════════════════════════════════════════
# LLM API KEYS
# ════════════════════════════════════════

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
CHATGPT_API_KEY = os.getenv("CHATGPT_API_KEY", "")

# Проверяем что хотя бы один LLM ключ есть
if not DEEPSEEK_API_KEY and not CHATGPT_API_KEY:
    print("⚠️  WARNING: Ни Deepseek, ни ChatGPT ключи не установлены.")
    print("   Бот будет использовать только локальный анализ.")

# ════════════════════════════════════════
# ЛОГИРОВАНИЕ
# ════════════════════════════════════════

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ════════════════════════════════════════
# ДРУГИЕ НАСТРОЙКИ
# ════════════════════════════════════════

MIN_TEXT_LENGTH = 20  # Минимальная длина текста для анализа
ANALYSIS_TIMEOUT = 15  # Таймаут для API запросов в секундах
