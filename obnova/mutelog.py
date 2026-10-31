async def mutelogs_command(message, arguments, user_id, chat_id, get_role, message_replyLocalizedMessage, getID, sql, datetime, timedelta, chats_log, get_user_name):
    if await get_role(user_id, chat_id) < 1:
        await message_replyLocalizedMessage('not_preminisionss')
        return True

    target_user = None
    page = 1

    # Проверяем реплай
    if message.reply_message:
        target_user = message.reply_message.from_id
    # Проверяем аргументы
    elif len(arguments) >= 2:
        if arguments[1].isdigit():
            page = int(arguments[1])
        else:
            target_user = await getID(arguments[1])
            if len(arguments) >= 3 and arguments[2].isdigit():
                page = int(arguments[2])

    sql.execute(f"SELECT * FROM mutelogs_{chat_id} ORDER BY date DESC")
    all_mutes = sql.fetchall()

    if target_user:
        # Ищем где пользователь получал ИЛИ выдавал мут
        all_mutes = [mute for mute in all_mutes if mute[0] == target_user or mute[1] == target_user]

    if not all_mutes:
        await message.reply("Логи мутов не найдены!")
        return True

    total_pages = (len(all_mutes) + 19) // 20
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * 20
    end_idx = start_idx + 20
    page_mutes = all_mutes[start_idx:end_idx]

    logs_text = ""

    for i, mute in enumerate(page_mutes, start=1):
        user_id_mute, moder_id, reason, date_ts, date_str, mute_time, status = mute

        # Определяем текст модератора
        if moder_id < 0:
            group_id = abs(moder_id)
            moderator_text = f"[https://vk.com/club{group_id}|Система]"
        else:
            try:
                int(moder_id)
                moderator_text = f"@id{moder_id} (Модератор)"
            except:
                moderator_text = "Система"

        logs_text += f"{i}) @id{user_id_mute} (Пользователь) | {moderator_text} | {status} | {date_str} | {reason}\n"
        

    await message.reply(logs_text)
    await chats_log(user_id=user_id, target_id=target_user, role=None, log=f"посмотрел(-а) логи мутов")
    return True
