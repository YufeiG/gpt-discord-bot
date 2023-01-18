import io
from typing import Tuple

import discord

from src.completion import character_info_from_thread
from src.utils import discord_message_to_message


async def history_from_thread(
    guild: discord.Guild, thread_id: int, bot_user: discord.ClientUser
) -> Tuple[bool, str]:
    try:
        thread = await guild.fetch_channel(thread_id)
    except Exception as e:
        return (
            False,
            "Not the thread starter. Please use this action on the thread starter message.",
        )

    if not thread:
        return (False, "Thread does not exist")
    if not isinstance(thread, discord.Thread):
        return (False, "Thread is wrong type")

    if thread.owner != bot_user:
        return (False, "Not a thread opened by the bot")

    conversation_text = f"Conversation with {bot_user.name}\nCreated at: {thread.created_at}\nApproximate message count:{thread.message_count}\nApproximate member count:{thread.member_count}\n{thread.jump_url}"
    config, prompt = await character_info_from_thread(guild=guild, thread=thread)
    conversation_text += f"\nInstructions\n{prompt}\nConfig\n{config.to_str()}\n"
    messages = [
        await discord_message_to_message(message=message)
        async for message in thread.history(limit=None)
    ]
    messages.reverse()
    for m in messages:
        if m:
            conversation_text += f"\n{m.render()}"
    return (True, conversation_text)


async def save_a_copy(interaction: discord.Interaction, thread_message_id: int):
    guild = interaction.guild
    channel = interaction.channel
    if not guild:
        await interaction.response.send_message(
            content=f"**Error**: Missing guild", ephemeral=True
        )
        return
    if not channel:
        await interaction.response.send_message(
            content=f"**Error**: Missing channel", ephemeral=True
        )
        return

    success, text = await history_from_thread(
        guild=guild, thread_id=thread_message_id, bot_user=guild.me
    )
    if not success:
        await interaction.response.send_message(
            content=f"**Error**: {text}", ephemeral=True
        )
    else:
        try:
            filename = f"conversation-{guild.me}-{guild.name}-{channel.name}-{thread_message_id}-{len(text)}"
            with io.StringIO(text) as file:
                await interaction.user.send(
                    content=f"Hello! You wanted a copy of our conversation. Here it is!\n(I do not reply to DMs, sorry!)\n{interaction.message.jump_url}",
                    file=discord.File(fp=file, filename=f"{filename[:240]}.txt"),
                )
            await interaction.response.send_message(
                content=f"Sent! Check your DMS!", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                content=f"**Error**: Failed to send file. {str(e)}", ephemeral=True
            )
