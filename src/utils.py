from src.constants import (
    ALLOWED_SERVER_IDS,
)
import logging
import io
from src.base import Message
from discord import Message as DiscordMessage
from typing import List, Optional, Tuple
import discord

from src.constants import MAX_CHARS_PER_REPLY_MSG, INACTIVATE_THREAD_PREFIX

logging.basicConfig(
    format="[%(asctime)s] [%(filename)s:%(lineno)d] %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def discord_message_to_message(message: DiscordMessage) -> Optional[Message]:
    if (
        message.type == discord.MessageType.thread_starter_message
        and message.reference
    ):
        original_message = message.reference.cached_message
        if not original_message:
            channel = await message.guild.fetch_channel(message.reference.channel_id)
            if channel:
                original_message = await channel.fetch_message(message.reference.message_id)

        if len(original_message.embeds) > 0 and len(original_message.embeds[0].fields) > 0:
            field = original_message.embeds[0].fields[0]
            if field.value:
                return Message(user="System", text=field.value)
    else:
        if message.content:
            return Message(user=message.author.name, text=message.content)
    logger.info(f"Empty message {message} {message.content} {message.embeds}")
    return None


def split_into_shorter_messages(message: str) -> List[str]:
    return [
        message[i : i + MAX_CHARS_PER_REPLY_MSG]
        for i in range(0, len(message), MAX_CHARS_PER_REPLY_MSG)
    ]


def is_last_message_stale(
    interaction_message: DiscordMessage, last_message: DiscordMessage, bot_id: str
) -> bool:
    return (
        last_message
        and last_message.id != interaction_message.id
        and last_message.author
        and last_message.author.id != bot_id
    )


async def close_thread(thread: discord.Thread):
    await thread.edit(name=INACTIVATE_THREAD_PREFIX)
    await thread.send(
        embed=discord.Embed(
            description="**Thread closed** - Context limit reached, closing...",
            color=discord.Color.blue(),
        )
    )
    await thread.edit(archived=True, locked=True)


def should_block(guild: Optional[discord.Guild]) -> bool:
    if guild is None:
        # dm's not supported
        logger.info(f"DM not supported")
        return True

    if guild.id and guild.id not in ALLOWED_SERVER_IDS:
        # not allowed in this server
        logger.info(f"Guild {guild} not allowed")
        return True
    return False

async def history_from_thread(guild: discord.Guild, thread_id: int, bot_user: discord.ClientUser) -> Tuple[bool, str]:
    try:
        thread = await guild.fetch_channel(thread_id)
    except Exception as e:
        return (False, "Not the thread starter. Please use this action on the thread starter message.")

    if not thread:
        return (False, "Thread does not exist")
    if not isinstance(thread, discord.Thread):
        return (False, "Thread is wrong type")

    if thread.owner != bot_user:
        return (False, "Not a thread opened by the bot")

    conversation_text = f"Conversation with {bot_user.name}\nCreated at: {thread.created_at}\nApproximate message count:{thread.message_count}\nApproximate member count:{thread.member_count}\n{thread.jump_url}"

    messages = [await discord_message_to_message(message=message) async for message in thread.history(limit=None)]
    messages.reverse()
    print(messages)
    for m in messages:
        if m:
            conversation_text += f"\n{m.render()}"
    return (True, conversation_text)

async def save_a_copy(interaction: discord.Interaction, thread_message_id: int):
    guild = interaction.guild
    channel = interaction.channel
    if not guild:
        await interaction.response.send_message(content=f"**Error**: Missing guild", ephemeral=True)
        return
    if not channel:
        await interaction.response.send_message(content=f"**Error**: Missing channel", ephemeral=True)
        return


    success, text = await history_from_thread(guild=guild, thread_id=thread_message_id, bot_user=guild.me)
    if not success:
        await interaction.response.send_message(content=f"**Error**: {text}", ephemeral=True)
    else:
        try:
            filename = f"conversation-{guild.me}-{guild.name}-{channel.name}-{thread_message_id}-{len(text)}"
            with io.StringIO(text) as file:
                await interaction.user.send(content="Hello! You wanted a copy of our conversation. Here it is!\n(I do not reply to DMs, sorry!)", file=discord.File(fp=file, filename=f"{filename[:240]}.txt"))
            await interaction.response.send_message(content=f"Sent! Check your DMS!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(content=f"**Error**: Failed to send file. {str(e)}", ephemeral=True)
