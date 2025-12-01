import logging
import asyncio
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import aiohttp
import json

# Импортируем конфиг
from config import (
    TELEGRAM_BOT_TOKEN,
    DEEPSEEK_API_KEY,
    CHATGPT_API_KEY,
    LOG_LEVEL,
    MIN_TEXT_LENGTH,
    ANALYSIS_TIMEOUT,
)

# ════════════════════════════════════════
# ЛОГИРОВАНИЕ
# ════════════════════════════════════════

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL, logging.INFO)
)
logger = logging.getLogger(__name__)

# ════════════════════════════════════════
# СИСТЕМА АНАЛИЗА (ЛОКАЛЬНАЯ)
# ════════════════════════════════════════

class FraudAnalyzer:
    """Локальный анализ текста на мошенничество"""
    
    FRAUD_PATTERNS = {
        "credit": {
            "keywords": [
                "кредит", "одобрен", "займ", "деньги", "банк",
                "счет", "реквизиты", "карта", "линия", "заём"
            ],
            "urgency": ["срочно", "быстро", "немедленно", "прямо сейчас", "срок"],
            "red_flags": [
                "нужны ваши данные", "дайте коды", "отправьте смс",
                "подтвердите личность", "ввести пин", "скопируй код"
            ]
        },
        "sim_swap": {
            "keywords": [
                "номер", "симка", "оператор", "идентификация",
                "переход", "мегафон", "мтс", "билайн", "теле2"
            ],
            "urgency": ["заблокирован", "закрыли", "проблема", "внимание"],
            "red_flags": [
                "перевести номер", "новая симка", "переходи на нас",
                "перезагрузи телефон", "тариф изменится"
            ]
        },
        "investment": {
            "keywords": [
                "инвестиции", "прибыль", "доход", "акции", "крипто",
                "биток", "ethereum", "трейдинг", "форекс"
            ],
            "urgency": ["только сегодня", "последняя возможность", "ограничено", "завтра подорожает"],
            "red_flags": [
                "гарантированный доход", "100% прибыль", "откупок гарантирован",
                "внесите депозит", "инвестируй сейчас"
            ]
        },
        "utility": {
            "keywords": [
                "квартира", "коммунальные", "электричество", "вода",
                "газ", "интернет", "счет", "оплата", "ЖКХ"
            ],
            "urgency": ["перекроют", "отключат", "срок", "задолженность", "немедленно"],
            "red_flags": [
                "пополните счет", "переведите деньги", "срок истекает",
                "деньги нужны сегодня", "иначе отключим"
            ]
        },
        "lottery": {
            "keywords": [
                "выиграл", "приз", "лотерея", "подарок", "везёт",
                "удача", "миллион", "награда", "получить"
            ],
            "urgency": ["спеши", "скоро истечет", "срок ограничен"],
            "red_flags": [
                "отправь комиссию", "внеси деньги", "подтверди участие",
                "переведи", "активируй приз"
            ]
        }
    }
    
    @staticmethod
    def analyze_text(text: str) -> dict:
        """Локальный анализ текста (без LLM)"""
        
        text_lower = text.lower()
        scores = {}
        
        for fraud_type, patterns in FraudAnalyzer.FRAUD_PATTERNS.items():
            score = 0
            matched_flags = []
            
            # Keywords: 1 балл
            for keyword in patterns["keywords"]:
                if keyword in text_lower:
                    score += 1
            
            # Urgency: 2 балла
            for urgency in patterns["urgency"]:
                if urgency in text_lower:
                    score += 2
            
            # Red flags: 3 балла
            for flag in patterns["red_flags"]:
                if flag in text_lower:
                    score += 3
                    matched_flags.append(flag)
            
            scores[fraud_type] = {
                "score": score,
                "flags": matched_flags
            }
        
        # Находим лучший результат
        best_type = max(scores, key=lambda x: scores[x]["score"])
        best_score = scores[best_type]["score"]
        
        # Определяем риск
        if best_score >= 40:
            risk_level = "high"
            confidence = min(0.95, best_score / 100)
        elif best_score >= 20:
            risk_level = "medium"
            confidence = min(0.80, best_score / 50)
        elif best_score >= 5:
            risk_level = "low"
            confidence = best_score / 20
        else:
            risk_level = "none"
            confidence = 0.0
        
        return {
            "fraud_type": best_type if best_score > 0 else "unknown",
            "risk_level": risk_level,
            "confidence": confidence,
            "red_flags": scores[best_type]["flags"][:5],
            "local_score": best_score,
            "method": "local_patterns"
        }

# ════════════════════════════════════════
# LLM ПРОВАЙДЕР (Deepseek + ChatGPT)
# ════════════════════════════════════════

class LLMProvider:
    """Провайдер LLM с fallback логикой"""
    
    def __init__(self, deepseek_key: str = None, chatgpt_key: str = None):
        self.deepseek_key = deepseek_key
        self.chatgpt_key = chatgpt_key
    
    async def analyze(self, text: str) -> Optional[dict]:
        """Анализируем с fallback стратегией"""
        
        # ШАГ 1: Пытаемся Deepseek (быстро, дешево)
        if self.deepseek_key:
            logger.info("🔄 Trying Deepseek...")
            result = await self._analyze_deepseek(text)
            if result:
                logger.info("✅ Deepseek успешно вернул результат")
                result["provider"] = "deepseek"
                return result
            logger.warning("⚠️ Deepseek failed, trying ChatGPT...")
        
        # ШАГ 2: Fallback на ChatGPT
        if self.chatgpt_key:
            logger.info("🔄 Trying ChatGPT...")
            result = await self._analyze_chatgpt(text)
            if result:
                logger.info("✅ ChatGPT успешно вернул результат")
                result["provider"] = "chatgpt"
                return result
            logger.warning("⚠️ ChatGPT failed")
        
        logger.error("❌ All LLM providers failed")
        return None
    
    # ════════════════════════════════════════
    # DEEPSEEK
    # ════════════════════════════════════════
    
    async def _analyze_deepseek(self, text: str) -> Optional[dict]:
        """Deepseek API"""
        
        prompt = f"""Ты эксперт в анализе мошеннических звонков в России.

Проанализируй текст разговора и определи:
1. Тип мошенничества (credit/sim_swap/investment/utility/lottery/legitimate/unknown)
2. Уровень опасности (low/medium/high)
3. Признаки скама (список, 3-5 штук)
4. Рекомендацию (что делать)

Ответь ТОЛЬКО JSON (без маркдауна, без ```
{{
  "fraud_type": "...",
  "risk_level": "low|medium|high",
  "red_flags": ["флаг1", "флаг2"],
  "recommendation": "текст рекомендации",
  "confidence": 0.85
}}

ТЕКСТ:
{text}"""
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {self.deepseek_key}",
                    "Content-Type": "application/json",
                }
                
                payload = {
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 800
                }
                
                logger.debug(f"📤 Sending request to Deepseek")
                
                async with session.post(
                    "https://api.deepseek.com/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=ANALYSIS_TIMEOUT)
                ) as resp:
                    logger.info(f"📥 Deepseek response status: {resp.status}")
                    
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"❌ Deepseek HTTP error {resp.status}: {error_text[:200]}")
                        return None
                    
                    result = await resp.json()
                    logger.debug(f"📦 Deepseek raw response: {str(result)[:500]}")
                    
                    try:
                        # ✅ ИСПРАВКА: Правильный парсинг
                        if "choices" not in result:
                            logger.error(f"❌ No 'choices' in response: {result}")
                            return None
                        
                        if not isinstance(result["choices"], list) or len(result["choices"]) == 0:
                            logger.error(f"❌ 'choices' is not a list or empty: {result['choices']}")
                            return None
                        
                        choice = result["choices"]
                        
                        if "message" not in choice:
                            logger.error(f"❌ No 'message' in choice: {choice}")
                            return None
                        
                        if "content" not in choice["message"]:
                            logger.error(f"❌ No 'content' in message: {choice['message']}")
                            return None
                        
                        response_text = choice["message"]["content"]
                        logger.debug(f"📝 Deepseek message: {response_text[:300]}")
                        
                        # Парсим JSON
                        gpt_result = json.loads(response_text)
                        logger.info(f"✅ Successfully parsed Deepseek JSON: {gpt_result.get('fraud_type')}")
                        return gpt_result
                        
                    except json.JSONDecodeError as e:
                        logger.error(f"❌ Deepseek JSON decode error: {e}")
                        logger.error(f"   Response text: {response_text[:300] if 'response_text' in locals() else 'N/A'}")
                        return None
                    except (KeyError, IndexError, TypeError) as e:
                        logger.error(f"❌ Deepseek structure error: {type(e).__name__}: {e}")
                        logger.error(f"   Full response: {result}")
                        return None
        
        except asyncio.TimeoutError:
            logger.error("❌ Deepseek timeout (15s)")
            return None
        except Exception as e:
            logger.error(f"❌ Deepseek exception: {type(e).__name__}: {e}", exc_info=True)
            return None
    
    # ════════════════════════════════════════
    # CHATGPT (OpenAI)
    # ════════════════════════════════════════
    
    async def _analyze_chatgpt(self, text: str) -> Optional[dict]:
        """ChatGPT API (OpenAI)"""
        
        prompt = f"""Analyze this phone call text for scam/fraud in Russian context.

Determine:
1. Fraud type (credit/sim_swap/investment/utility/lottery/legitimate/unknown)
2. Risk level (low/medium/high)
3. Scam indicators (3-5 items)
4. Recommendation for user

Answer ONLY as JSON (no markdown):
{{
  "fraud_type": "...",
  "risk_level": "low|medium|high",
  "red_flags": ["flag1", "flag2"],
  "recommendation": "advice",
  "confidence": 0.85
}}

TEXT:
{text}"""
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {self.chatgpt_key}",
                    "Content-Type": "application/json",
                }
                
                payload = {
                    "model": "gpt-4-mini",
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 800
                }
                
                logger.debug(f"📤 Sending request to ChatGPT")
                
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=ANALYSIS_TIMEOUT)
                ) as resp:
                    logger.info(f"📥 ChatGPT response status: {resp.status}")
                    
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"❌ ChatGPT HTTP error {resp.status}: {error_text[:200]}")
                        return None
                    
                    result = await resp.json()
                    logger.debug(f"📦 ChatGPT raw response: {str(result)[:500]}")
                    
                    try:
                        # ✅ ИСПРАВКА: Правильный парсинг
                        if "choices" not in result:
                            logger.error(f"❌ No 'choices' in response: {result}")
                            return None
                        
                        if not isinstance(result["choices"], list) or len(result["choices"]) == 0:
                            logger.error(f"❌ 'choices' is not a list or empty: {result['choices']}")
                            return None
                        
                        choice = result["choices"]
                        
                        if "message" not in choice:
                            logger.error(f"❌ No 'message' in choice: {choice}")
                            return None
                        
                        if "content" not in choice["message"]:
                            logger.error(f"❌ No 'content' in message: {choice['message']}")
                            return None
                        
                        response_text = choice["message"]["content"]
                        logger.debug(f"📝 ChatGPT message: {response_text[:300]}")
                        
                        # Парсим JSON
                        gpt_result = json.loads(response_text)
                        logger.info(f"✅ Successfully parsed ChatGPT JSON: {gpt_result.get('fraud_type')}")
                        return gpt_result
                        
                    except json.JSONDecodeError as e:
                        logger.error(f"❌ ChatGPT JSON decode error: {e}")
                        logger.error(f"   Response text: {response_text[:300] if 'response_text' in locals() else 'N/A'}")
                        return None
                    except (KeyError, IndexError, TypeError) as e:
                        logger.error(f"❌ ChatGPT structure error: {type(e).__name__}: {e}")
                        logger.error(f"   Full response: {result}")
                        return None
        
        except asyncio.TimeoutError:
            logger.error("❌ ChatGPT timeout (15s)")
            return None
        except Exception as e:
            logger.error(f"❌ ChatGPT exception: {type(e).__name__}: {e}", exc_info=True)
            return None

# ════════════════════════════════════════
# ГЛОБАЛЬНЫЙ LLM ПРОВАЙДЕР
# ════════════════════════════════════════

llm_provider: Optional[LLMProvider] = None

async def initialize_llm():
    """Инициализируем LLM при запуске бота"""
    global llm_provider
    
    llm_provider = LLMProvider(
        deepseek_key=DEEPSEEK_API_KEY,
        chatgpt_key=CHATGPT_API_KEY
    )
    
    available_providers = []
    if DEEPSEEK_API_KEY:
        available_providers.append("Deepseek")
    if CHATGPT_API_KEY:
        available_providers.append("ChatGPT")
    
    if available_providers:
        print(f"✅ LLM провайдеры: {', '.join(available_providers)}")
    else:
        print("⚠️  LLM провайдеры не конфигурированы, будет использован только локальный анализ")

# ════════════════════════════════════════
# TELEGRAM ОБРАБОТЧИКИ
# ════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    
    welcome_text = """
🛡️ <b>Добро пожаловать в анализатор мошенничества!</b>

Я анализирую текст звонков и определяю, является ли это мошенничеством.

<b>📝 Как использовать:</b>
1️⃣ Получил мошеннический звонок?
2️⃣ Открой диктофон (или выпиши текст)
3️⃣ Отправь мне текст разговора
4️⃣ За 1-2 сек получишь анализ

<b>⏱️ Пример:</b>
de>Привет, это служба банка. У вас одобрена кредитная линия на 500000 рублей. Срочно нужны коды с вашей карты для активации.</code>

/help - справка
/example - пример анализа
/stats - статистика
    """
    
    await update.message.reply_text(welcome_text, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    
    help_text = """
<b>📖 Справка</b>

<b>Что я анализирую:</b>
✅ Кредитные мошенничества
✅ SIM-swap (перевод номера)
✅ Инвестиционные афёры
✅ Коммунальные платежи (поддельные)
✅ Лотереи и розыгрыши
✅ Легитимные звонки

<b>Как это работает:</b>
1. Ты отправляешь текст разговора
2. Я анализирую локально (приватно)
3. Потом отправляю на проверку AI (Deepseek/ChatGPT)
4. Возвращаю результат с вероятностью

<b>Приватность:</b>
🔐 Данные НЕ сохраняются
🔐 НЕ передаются третьим лицам
🔐 Только анализируются

/start - начать
/example - пример
/stats - статистика
    """
    
    await update.message.reply_text(help_text, parse_mode="HTML")

async def example_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пример анализа"""
    
    example_text = """
<b>ПРИМЕР МОШЕННИЧЕСКОГО ЗВОНКА:</b>

"Привет, это служба банка. У вас одобрена кредитная линия на 500000 рублей. Срочно нужны коды с вашей карты для активации."

🔴 <b>РЕЗУЛЬТАТ АНАЛИЗА:</b>

<b>🎯 Тип:</b> credit
<b>⚠️ Опасность:</b> HIGH
<b>📊 Уверенность:</b> 95%

<b>🚩 Признаки скама:</b>
-  нужны ваши данные
-  дайте коды с карты
-  срочно

<b>💡 Рекомендация:</b>
НИКОГДА не сообщайте коды с карты! Это 100% мошенничество. Повесьте трубку и заблокируйте номер.
    """
    
    await update.message.reply_text(example_text, parse_mode="HTML")

async def analyze_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основной анализ текста"""
    
    text = update.message.text
    user_id = update.effective_user.id
    
    logger.info(f"📨 New message from user {user_id}: {text[:50]}...")
    
    # Проверяем длину
    if len(text) < MIN_TEXT_LENGTH:
        await update.message.reply_text(
            f"❌ Текст слишком короткий.\n"
            f"Отправь полный текст разговора (минимум {MIN_TEXT_LENGTH} символов)."
        )
        return
    
    # Показываем статус
    status_msg = await update.message.reply_text("⏳ Анализирую разговор...\n\n⚡ Локальный анализ...")
    
    try:
        # ШАГ 1: Локальный анализ (быстро, ~100ms)
        logger.info("🔄 Starting local analysis...")
        local_result = FraudAnalyzer.analyze_text(text)
        logger.info(f"✅ Local analysis done: {local_result.get('fraud_type')} ({local_result.get('risk_level')})")
        
        # ШАГ 2: GPT анализ (дополнительный)
        await status_msg.edit_text(
            "⏳ Анализирую разговор...\n\n"
            "⚡ Локальный анализ: ✅\n"
            "🧠 AI анализ (Deepseek/ChatGPT)..."
        )
        
        gpt_result = None
        if llm_provider:
            logger.info("🔄 Requesting LLM analysis...")
            gpt_result = await llm_provider.analyze(text)
            if gpt_result:
                logger.info(f"✅ LLM analysis done: {gpt_result.get('provider')}")
            else:
                logger.warning("⚠️ LLM analysis returned None")
        
        # ШАГ 3: Комбинируем результаты
        if gpt_result:
            final_result = gpt_result
            logger.info(f"📊 Using LLM result: {gpt_result.get('provider')}")
        else:
            final_result = local_result
            logger.info("📊 Using local result (LLM failed)")
        
        # ШАГ 4: Форматируем ответ
        risk_emoji = {
            "low": "🟢",
            "medium": "🟡",
            "high": "🔴"
        }.get(final_result.get("risk_level"), "❓")
        
        confidence_percent = int(final_result.get("confidence", 0) * 100)
        fraud_type = final_result.get("fraud_type", "unknown")
        risk_level = final_result.get("risk_level", "unknown").upper()
        
        red_flags = final_result.get("red_flags", [])
        recommendation = final_result.get("recommendation", "Проверьте источник звонка")
        provider = final_result.get("provider", "local")
        
        response = f"""
{risk_emoji} <b>РЕЗУЛЬТАТ АНАЛИЗА</b>

<b>🎯 Тип мошенничества:</b> {fraud_type}
<b>⚠️ Уровень опасности:</b> {risk_level}
<b>📊 Уверенность:</b> {confidence_percent}%

<b>🚩 Признаки скама:</b>
{chr(10).join(f"-  {flag}" for flag in red_flags) if red_flags else "-  Не обнаружены"}

<b>💡 Рекомендация:</b>
{recommendation}

<b>📝 Анализ:</b> {provider}
        """
        
        await status_msg.edit_text(response, parse_mode="HTML")
        logger.info(f"✅ Response sent to user {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Analysis error: {type(e).__name__}: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ Ошибка при анализе:\n{str(e)[:100]}\n\n"
            "Попробуй ещё раз."
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика"""
    
    stats_text = """
📊 <b>Статистика (бета)</b>

Статистика будет добавлена в следующей версии.
Пока просто анализируй разговоры! 🛡️
    """
    
    await update.message.reply_text(stats_text, parse_mode="HTML")

# ════════════════════════════════════════
# ЗАПУСК БОТА
# ════════════════════════════════════════

def main():
    """Запуск бота"""
    
    logger.info("=" * 50)
    logger.info("🤖 Starting GuardCall Bot...")
    logger.info("=" * 50)
    
    # Создаём приложение
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Post-init callback
    async def post_init(context):
        logger.info("🔄 Initializing bot...")
        await initialize_llm()
        logger.info("✅ Bot initialization complete!")
    
    app.post_init = post_init
    
    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("example", example_command))
    app.add_handler(CommandHandler("stats", stats_command))
    
    # Регистрируем обработчик текстовых сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_message))
    
    # Запускаем
    logger.info("🤖 Bot is running!")
    logger.info(f"📱 Open: https://t.me/guardcallbot")
    logger.info("=" * 50)
    
    app.run_polling()

if __name__ == "__main__":
    main()
