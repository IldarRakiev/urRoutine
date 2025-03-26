import firebase_admin
from firebase_admin import credentials, db
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler, Application
from datetime import datetime, timedelta
import asyncio
import os, sys, logging, json
from dotenv import load_dotenv

load_dotenv()

# Получаем JSON-ключ из переменной окружения
firebase_config = json.loads(os.getenv('FIREBASE_KEY'))

# Инициализация Firebase
cred = credentials.Certificate(firebase_config)
firebase_admin.initialize_app(cred, {
    "databaseURL": "https://urroutine-default-rtdb.firebaseio.com"
})

# Константы
PRIORITIES = {"urgent": 4, "high": 3, "medium": 2, "low": 1}

# ------------------- 1. Инициализация расписания -------------------

async def init_schedule(user_id: str, days_ahead: int = 30):
    schedule_ref = db.reference(f"schedule/{user_id}")
    
    def sync_create_schedule():
        today = datetime.now()
        existing_schedule = schedule_ref.get() or {}
        
        for day_offset in range(days_ahead):
            current_date = today + timedelta(days=day_offset)
            date_str = current_date.strftime("%Y-%m-%d")
            
            # Если расписание на эту дату уже есть - пропускаем
            if date_str in existing_schedule:
                continue
                
            day_of_week = current_date.strftime("%A").lower()
            day_schedule = {}

            # 1. Блоки сна (01:00–07:30)
            for hour in range(1, 8):
                for minute in [0, 30]:
                    time_key = f"{hour:02d}:{minute:02d}"
                    day_schedule[time_key] = {"type": "sleep", "task": None}

            # 2. Занятые блоки (лекции)
            lecture_times = []
            if day_of_week == "monday":
                lecture_times = ["09:00", "09:30", "10:00"]
            elif day_of_week == "tuesday":
                lecture_times = ["12:30", "13:00", "13:30", "14:00"]
            elif day_of_week == "thursday":
                lecture_times = ["12:30", "13:00", "13:30", "14:00"]
            elif day_of_week == "friday":
                lecture_times = ["17:30", "18:00", "18:30", "19:00"]
            
            for time in lecture_times:
                day_schedule[time] = {
                    "type": "lecture",
                    "task": {
                        "monday": "Лекция по ML",
                        "tuesday": "Лаб. по ML",
                        "thursday": "Лаб. по БД",
                        "friday": "Лаб. по сетям"
                    }[day_of_week]
                }

            # 3. Свободные блоки (08:00-00:30)
            for hour in range(8, 24):
                for minute in [0, 30]:
                    time_key = f"{hour:02d}:{minute:02d}"
                    if time_key not in day_schedule:
                        day_schedule[time_key] = {"type": "free", "task": None}

            # Аккуратно обновляем только нужную дату
            schedule_ref.child(date_str).set(day_schedule)
        
    await asyncio.to_thread(sync_create_schedule)   

# ------------------- 2. Главное меню -------------------
async def start(update: Update, context: CallbackContext):
    user_id = str(update.message.chat.id)
    
    try:
        await init_schedule(user_id)
        menu = ReplyKeyboardMarkup([
            ["🗂 Задачи", "📝 План на день"],
            ["📅 Расписание", "💪 Тренировки", "⚙️ Настройки"]
        ], resize_keyboard=True)
        
        await update.message.reply_text("📋 *Главное меню*", reply_markup=menu, parse_mode="Markdown")
    except Exception as e:
        print(f"Ошибка при инициализации: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при загрузке. Попробуйте позже.")


# ------------------- 3. Добавление задачи -------------------
async def add_task_start(update: Update, context: CallbackContext):
    logging.info('e')
    context.user_data['expecting_priority'] = True 
    buttons = [
        [InlineKeyboardButton("Авто", callback_data="auto")],
        [InlineKeyboardButton("Вручную", callback_data="manual")]
    ]
    await update.message.reply_text(
        "🔹 Выберите режим добавления:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def ask_priority(update: Update, context: CallbackContext):
    buttons = [
        [InlineKeyboardButton("Срочно 🔴", callback_data="urgent 🔴")],
        [InlineKeyboardButton("Высокий 🟠", callback_data="high 🟠")],
        [InlineKeyboardButton("Средний 🟡", callback_data="medium 🟡")],
        [InlineKeyboardButton("Низкий ⚪", callback_data="low ⚪")]
    ]
    await update.callback_query.edit_message_text(
        "📌 Выберите приоритет:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_task_input(update: Update, context: CallbackContext):
    user_id = str(update.effective_chat.id)
    user_data = context.user_data
    
    if update.callback_query:
        print(f"кал")
        # Обработка нажатия кнопки приоритета
        query = update.callback_query
        priority = query.data
        context.user_data['task_data'] = {'priority': priority}
        context.user_data['task_state'] = 'awaiting_name'
        context.user_data['expecting_priority'] = False
        
        await query.answer()  # Подтверждаем нажатие кнопки
        await query.message.reply_text("📝 Введите название задачи:")
        return
    
    elif update.message:
        print(f"то что надо")
        # Обработка текстовых сообщений
        text = update.message.text
        user_data = context.user_data

        # Этап 1: Получение названия задачи
        if 'task_state' not in user_data:
            #Если сюда попали, значит пользователь начал не с кнопок,
            # а например написал текст без выбора приоритета
            user_data['task_state'] = 'awaiting_priority'  # Новое состояние!
            buttons = [
                [InlineKeyboardButton("Срочно 🔴", callback_data="urgent 🔴")],
                [InlineKeyboardButton("Высокий 🟠", callback_data="high 🟠")],
                [InlineKeyboardButton("Средний 🟡", callback_data="medium 🟡")],
                [InlineKeyboardButton("Низкий ⚪", callback_data="low ⚪")]
            ]
            await update.message.reply_text(
                "📌 Сначала выберите приоритет:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        # Этап 2: Получение времени выполнения
        if user_data['task_state'] == 'awaiting_name':
            if len(text) > 100:
                await update.message.reply_text("❌ Слишком длинное название. Максимум 100 символов.")
                return
                
            user_data['task_data']['name'] = text
            user_data['task_state'] = 'awaiting_time'
            await update.message.reply_text("⏳ Сколько часов потребуется на задачу? (например: 1.5)")
            return

        # Этап 3: Получение дедлайна
        elif user_data['task_state'] == 'awaiting_time':
            try:
                time_required = float(text)
                if time_required <= 0:
                    await update.message.reply_text("❌ Введите положительное число.")
                    return
                    
                user_data['task_data']['time_required'] = time_required
                user_data['task_state'] = 'awaiting_deadline'
                await update.message.reply_text("📅 Введите дедлайн в формате ДД.ММ.ГГГГ (например: 25.12.2024):")
            except ValueError:
                await update.message.reply_text("❌ Введите число (например: 2 или 1.5).")
            return

        # Этап 4: Получение описания (опционально)
        elif user_data['task_state'] == 'awaiting_deadline':
            try:
                deadline = datetime.strptime(text, "%d.%m.%Y").strftime("%Y-%m-%d")
                if deadline < datetime.now().strftime("%Y-%m-%d"):
                    await update.message.reply_text("❌ Дедлайн не может быть в прошлом.")
                    return
                    
                user_data['task_data']['deadline'] = deadline
                user_data['task_state'] = 'awaiting_notes'
                await update.message.reply_text("📝 Добавьте описание:")
            except ValueError:
                await update.message.reply_text("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ")
            return

        # Этап 5: Завершение ввода
        elif user_data['task_state'] == 'awaiting_notes':
            if text:
                user_data['task_data']['notes'] = text
            
            # Сохранение задачи в базу
            task_ref = db.reference(f"tasks/{user_id}").push()
            task_id = task_ref.key
            user_data['task_data']['created_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await task_ref.set(user_data['task_data'])
            
            # Распределение задачи
            result = auto_assign_task_with_priority(user_id, task_id)
            
            if result == "need_confirmation":
                await update.message.reply_text(
                    "⚠️ Не хватает места! Перенести менее важные задачи? (/yes или /no)",
                    reply_markup=ReplyKeyboardMarkup([['/yes', '/no']], resize_keyboard=True)
                )
                user_data['pending_task'] = task_id
            else:
                await update.message.reply_text(f"✅ Задача «{user_data['task_data']['name']}» добавлена!")
            
            # Очистка временных данных
            user_data.pop('task_state', None)
            user_data.pop('task_data', None)
            user_data.pop('selected_priority', None)
            
        else:
           print(f"иди нахуй))))")


# ------------------- 4. Удаление задачи -------------------
async def delete_task(update: Update, context: CallbackContext):
    task_name = " ".join(context.args)
    user_id = str(update.message.chat.id)
    tasks_ref = db.reference(f"tasks/{user_id}")
    
    tasks = tasks_ref.get()
    if not tasks:
        await update.message.reply_text("❌ Нет задач для удаления.")
        return

    for task_id, task_data in tasks.items():
        if task_data["name"] == task_name:
            # Освобождаем блоки
            for block in task_data.get("assigned_blocks", []):
                db.reference(f"schedule/{user_id}/{block['date']}/{block['time']}").update({"task": None})
            # Удаляем задачу
            tasks_ref.child(task_id).delete()
            await update.message.reply_text(f"✅ Задача «{task_name}» удалена!")
            return

    await update.message.reply_text("❌ Задача не найдена.")

# ------------------- 5. Автоматическое распределение с переносом -------------------
async def auto_assign_task_with_priority(user_id: str, task_id: str):
    task = db.reference(f"tasks/{user_id}/{task_id}").get()
    time_needed = task["time_required"] * 2  # Блоки по 30 мин
    deadline = datetime.strptime(task["deadline"], "%Y-%m-%d")
    assigned_blocks = []

    current_date = datetime.now()
    while current_date <= deadline and time_needed > 0:
        date_str = current_date.strftime("%Y-%m-%d")
        day_schedule = db.reference(f"schedule/{user_id}/{date_str}").get()

        free_blocks = [
            time for time, block in day_schedule.items()
            if block["type"] == "free" and not block.get("task")
        ][:4]  # Не более 4 блоков в день

        for time in free_blocks:
            assigned_blocks.append({"date": date_str, "time": time})
            time_needed -= 1

        current_date += timedelta(days=1)

    if time_needed > 0:
        if task["priority"] in ("high", "medium"):
            return "need_confirmation"
        else:
            reschedule_low_priority_tasks(user_id, task_id, time_needed)

    # Сохраняем блоки
    db.reference(f"tasks/{user_id}/{task_id}/assigned_blocks").set(assigned_blocks)
    for block in assigned_blocks:
        db.reference(f"schedule/{user_id}/{block['date']}/{block['time']}").update({"task": task_id})
    return "assigned"

# ------------------- 6. Перенос задач -------------------
async def reschedule_low_priority_tasks(user_id: str, new_task_id: str, blocks_needed: int):
    tasks_ref = db.reference(f"tasks/{user_id}")
    new_task_priority = tasks_ref.child(new_task_id).get()["priority"]

    for task_id, task_data in tasks_ref.get().items():
        if PRIORITIES[task_data["priority"]] < PRIORITIES[new_task_priority]:
            # Переносим задачу
            new_blocks = find_free_blocks_after_deadline(user_id, task_data["deadline"])
            if new_blocks:
                tasks_ref.child(task_id).child("assigned_blocks").set(new_blocks)
                blocks_needed -= len(task_data["assigned_blocks"])
                if blocks_needed <= 0:
                    break
                
                
async def cancel_task(update: Update, context: CallbackContext):
    if 'task_state' in context.user_data:
        del context.user_data['task_state']
        await update.message.reply_text("❌ Добавление задачи отменено.")
    else:
        await update.message.reply_text("⚠️ Нет активного процесса добавления.")
        

# ------------------- 7. Просмотр расписания -------------------
async def show_schedule(update: Update, context: CallbackContext):
    user_id = str(update.message.chat.id)
    date = datetime.now().strftime("%Y-%m-%d")
    schedule_ref = db.reference(f"schedule/{user_id}/{date}")

    schedule_text = f"📅 *Расписание на {date}*\n\n"
    for time, block in schedule_ref.get().items():
        if block["type"] != "sleep":
            schedule_text += f"- {time}: {block.get('task', 'Свободно')}\n"

    await update.message.reply_text(schedule_text, parse_mode="Markdown")
    
async def show_daily_plan(update: Update, context: CallbackContext):
    user_id = str(update.message.chat.id)
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 1. Получаем расписание на сегодня
    schedule_ref = db.reference(f"schedule/{user_id}/{today}")
    daily_schedule = schedule_ref.get() or {}
    
    # 2. Получаем все задачи пользователя
    tasks_ref = db.reference(f"tasks/{user_id}")
    all_tasks = tasks_ref.get() or {}

    # 3. Формируем список задач
    plan_text = "📅 *Ваш план на сегодня:*\n\n"
    tasks_found = False

    for time_slot, slot_data in daily_schedule.items():
        if slot_data.get("task"):
            task_id = slot_data["task"]
            task = all_tasks.get(task_id, {})
            
            plan_text += (
                f"⏰ *{time_slot}*\n"
                f"• {task.get('name', 'Задача')}\n"
                f"• Приоритет: {task.get('priority', 'не указан')}\n"
                f"• Заметки: {task.get('notes', 'нет')}\n\n"
            )
            tasks_found = True

    if not tasks_found:
        plan_text = "На сегодня задач нет. Отдыхайте! 😊"

    await update.message.reply_text(plan_text, parse_mode="Markdown")
    
    
async def show_tasks(update: Update, context: CallbackContext):
    user_id = str(update.message.chat.id)
    tasks_ref = db.reference(f"tasks/{user_id}")
    tasks = tasks_ref.get() or {}

    if not tasks:
        await update.message.reply_text("📭 У вас пока нет задач.")
        return

    # Группируем задачи по статусу (активные/просроченные)
    active_tasks = []
    overdue_tasks = []
    today = datetime.now().date()

    for task_id, task_data in tasks.items():
        deadline = datetime.strptime(task_data['deadline'], "%Y-%m-%d").date()
        if deadline < today:
            overdue_tasks.append(task_data)
        else:
            active_tasks.append(task_data)

    # Формируем сообщение
    message_text = "📋 *Ваши задачи:*\n\n"

    if active_tasks:
        message_text += "🔹 *Активные:*\n"
        for task in active_tasks:
            message_text += format_task(task) + "\n\n"

    if overdue_tasks:
        message_text += "🔴 *Просроченные:*\n"
        for task in overdue_tasks:
            message_text += format_task(task) + "\n\n"

    await update.message.reply_text(message_text, parse_mode="Markdown")

def format_task(task: dict) -> str:
    """Форматирует задачу в текст"""
    return (
        f"• *{task['name']}*\n"
        f"  ⏳ {task['time_required']} ч | "
        f"📅 {task['deadline']}\n"
        f"  🏷 Приоритет: {task.get('priority', 'не указан')}\n"
        f"  📝 {task.get('notes', 'без заметок')}"
    )    

# ------------------- Запуск бота -------------------
def main():
    
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addtask", add_task_start))
    application.add_handler(CommandHandler("deletetask", delete_task))
    application.add_handler(CommandHandler("schedule", show_schedule))
    application.add_handler(CommandHandler("cancel", cancel_task))

    # Обработчики кнопок
    
    application.add_handler(MessageHandler(filters.Regex("^📝 План на день$"), show_daily_plan))
    application.add_handler(MessageHandler(filters.Regex("^🗂 Задачи$"), show_tasks))
    application.add_handler(MessageHandler(filters.Regex("^📅 Расписание$"), show_schedule))
    
    application.add_handler(CallbackQueryHandler(ask_priority, pattern="^(auto|manual)$"))
    application.add_handler(CallbackQueryHandler(handle_task_input, pattern="^(urgent|high|medium|low)$"))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_task_input))

    application.run_polling()

if __name__ == "__main__":
    main()