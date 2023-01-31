import asyncio
import io
import discord
from discord import Message as DiscordMessage
from src.completion import (
    character_info_from_thread,
    generate_completion_response,
    process_response,
    generate_summary,
    generate_visual,
)
from src.constants import (
    ACTIVATE_THREAD_PREFX,
    BOT_INVITE_URL,
    DISCORD_BOT_TOKEN,
    MAX_THREAD_MESSAGES,
    SECONDS_DELAY_RECEIVING_MSG,
)
from src.create import CharacterEmbedView, SaveThreadView
from src.moderation import (
    moderate_message,
    send_moderation_blocked_message,
    send_moderation_flagged_message,
)
from src.save import save_a_copy
from src.utils import (
    close_thread,
    discord_message_to_message,
    is_last_message_stale,
    logger,
    should_block,
)
from typing import Optional, Union
import base64

import openai

intents = discord.Intents.default()
intents.message_content = True


class PersistentClient(discord.Client):
    async def setup_hook(self) -> None:
        self.add_view(CharacterEmbedView(bot_name=self.user.name))
        self.add_view(SaveThreadView())


client = PersistentClient(intents=intents)
tree = discord.app_commands.CommandTree(client)


@client.event
async def on_ready():
    logger.info(f"We have logged in as {client.user}. Invite URL: {BOT_INVITE_URL}")
    await tree.sync()


# /characterize message:
@tree.command(
    name="characterize",
    description="Tell me what I should roleplay",
)
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def character_command(int: discord.Interaction, backstory: str):
    try:
        instructions = backstory
        # only support creating thread in text channel
        if not isinstance(int.channel, discord.TextChannel):
            return

        # block servers not in allow list
        if should_block(guild=int.guild):
            return
        user = int.user
        logger.info(f"Character command by {user} {instructions[:20]}")
        # moderate the message
        flagged_str, blocked_str = moderate_message(message=instructions, user=user)
        await send_moderation_blocked_message(
            guild=int.guild,
            user=user,
            blocked_str=blocked_str,
            message=instructions,
        )
        if len(blocked_str) > 0:
            # message was blocked
            await int.response.send_message(
                f"Your prompt has been blocked by moderation.\n{instructions}",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            description=f"<@{user.id}> created a character! 🎬🤖",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Backstory", value=instructions)
        if len(flagged_str) > 0:
            # message was flagged
            embed.color = discord.Color.yellow()
            embed.title = "⚠️ This prompt was flagged by moderation."

        view = CharacterEmbedView(bot_name=client.user.name)
        await int.response.send_message(embed=embed, view=view)
        response = await int.original_response()

        await send_moderation_flagged_message(
            guild=int.guild,
            user=user,
            flagged_str=flagged_str,
            message=instructions,
            url=response.jump_url,
        )
    except Exception as e:
        logger.exception(e)
        try:
            await int.response.send_message(
                f"Failed to start characterize {str(e)}", ephemeral=True
            )
        except Exception as e:
            logger.exception(e)


# /characterize message:
@tree.command(
    name="summarize",
    description="Summarize the story, can only be used in a thread.",
)
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def summarize(interaction: discord.Interaction):
    try:
        thread = await process_thread(channel=interaction.channel)
        if thread is None:
            return

        await interaction.response.defer()

        config, prompt = await character_info_from_thread(
            guild=interaction.guild, thread=thread
        )
        channel_messages = [
            await discord_message_to_message(message)
            async for message in thread.history(limit=MAX_THREAD_MESSAGES)
        ]
        channel_messages = [x for x in channel_messages if x is not None]
        channel_messages.reverse()

        # generate the response

        response_data = await generate_summary(
            bot_name=client.user.name,
            bot_instruction=prompt,
            messages=channel_messages,
            user=str(interaction.user.id),
            config=config,
        )
        status = response_data.status
        reply_text = response_data.reply_text
        status_text = response_data.status_text
        if reply_text is None:
            await interaction.followup.send(
                content=f"Failed to summarize. {status_text}", emphemeral=True
            )

            return

        embed = discord.Embed(
            description=f"<@{interaction.user.id}> requested a summary! 🤖📝",
            color=discord.Color.fuchsia(),
        )
        embed.add_field(name="Summary", value=reply_text)
        await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.exception(e)
        try:
            await interaction.followup.send(
                f"Failed to summarize {str(e)}", ephemeral=True
            )
        except Exception as e:
            logger.exception(e)


# /characterize message:
@tree.command(
    name="visualize",
    description="Illustrate this story, can only be used in a thread.",
)
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def visualize(interaction: discord.Interaction, style: str = "digital art"):
    try:
        thread = await process_thread(channel=interaction.channel)
        if thread is None:
            return

        await interaction.response.defer()

        config, prompt = await character_info_from_thread(
            guild=interaction.guild, thread=thread
        )
        config.max_tokens = 50
        channel_messages = [
            await discord_message_to_message(message)
            async for message in thread.history(limit=MAX_THREAD_MESSAGES)
        ]
        channel_messages = [x for x in channel_messages if x is not None]
        channel_messages.reverse()

        # generate the response

        response_data = await generate_visual(
            bot_name=client.user.name,
            bot_instruction=prompt,
            messages=channel_messages,
            user=str(interaction.user.id),
            config=config,
        )
        status = response_data.status
        reply_text = response_data.reply_text
        status_text = response_data.status_text
        if reply_text is None:
            await interaction.followup.send(
                content=f"Failed to visualize. {status_text}", emphemeral=True
            )

            return
        response = openai.Image.create(
            prompt=f"{reply_text}, {style}",
            n=1,
            size="256x256",
            response_format="b64_json",
        )
        image_str = response["data"][0]["b64_json"]
        image = base64.b64decode(image_str)
        embed = discord.Embed(
            description=f"<@{interaction.user.id}> requested a visualization! 🤖🎨",
            color=discord.Color.fuchsia(),
        )
        embed.add_field(name="Imagery", value=reply_text)
        embed.set_image(url="attachment://image.png")
        await interaction.followup.send(
            file=discord.File(io.BytesIO(image), filename="image.png"), embed=embed
        )

    except Exception as e:
        logger.exception(e)
        try:
            await interaction.followup.send(
                f"Failed to visualize {str(e)}", ephemeral=True
            )
        except Exception as e:
            logger.exception(e)


async def process_thread(
    channel: Optional[
        Union[discord.abc.GuildChannel, discord.PartialMessageable, discord.Thread]
    ]
) -> Optional[discord.Thread]:
    if channel is None:
        return None
        # block servers not in allow list
    if should_block(guild=channel.guild):
        return None

    # ignore messages not in a thread
    if not isinstance(channel, discord.Thread):
        return None

    # ignore threads not created by the bot
    thread = channel
    if thread.owner_id != client.user.id:
        return None

    # ignore threads that are archived locked or title is not what we want
    if (
        thread.archived
        or thread.locked
        or not thread.name.startswith(ACTIVATE_THREAD_PREFX)
    ):
        # ignore this thread
        return None

    if thread.message_count > MAX_THREAD_MESSAGES:
        # too many messages, no longer going to reply
        await close_thread(thread=thread)
        return None
    return thread


# calls for each message
@client.event
async def on_message(message: DiscordMessage):
    try:
        # ignore messages from the bot
        if message.author == client.user:
            return

        thread = await process_thread(channel=message.channel)
        if thread is None:
            return

        # moderate the message
        flagged_str, blocked_str = moderate_message(
            message=message.content, user=message.author
        )
        await send_moderation_blocked_message(
            guild=message.guild,
            user=message.author,
            blocked_str=blocked_str,
            message=message.content,
        )
        if len(blocked_str) > 0:
            try:
                await message.delete()
                await thread.send(
                    embed=discord.Embed(
                        description=f"❌ **{message.author}'s message has been deleted by moderation.**",
                        color=discord.Color.red(),
                    )
                )
                return
            except Exception as e:
                await thread.send(
                    embed=discord.Embed(
                        description=f"❌ **{message.author}'s message has been blocked by moderation but could not be deleted. Missing Manage Messages permission in this Channel.**",
                        color=discord.Color.red(),
                    )
                )
                return
        await send_moderation_flagged_message(
            guild=message.guild,
            user=message.author,
            flagged_str=flagged_str,
            message=message.content,
            url=message.jump_url,
        )
        if len(flagged_str) > 0:
            await thread.send(
                embed=discord.Embed(
                    description=f"⚠️ **{message.author}'s message has been flagged by moderation.**",
                    color=discord.Color.yellow(),
                )
            )

        # wait a bit in case user has more messages
        if SECONDS_DELAY_RECEIVING_MSG > 0:
            await asyncio.sleep(SECONDS_DELAY_RECEIVING_MSG)
            if is_last_message_stale(
                interaction_message=message,
                last_message=thread.last_message,
                bot_id=client.user.id,
            ):
                # there is another message, so ignore this one
                return

        logger.info(
            f"Thread message to process - {message.author}: {message.content[:50]} - {thread.name} {thread.jump_url}"
        )

        config, prompt = await character_info_from_thread(
            guild=message.guild, thread=thread
        )
        channel_messages = [
            await discord_message_to_message(message)
            async for message in thread.history(limit=MAX_THREAD_MESSAGES)
        ]
        channel_messages = [x for x in channel_messages if x is not None]
        channel_messages.reverse()

        # generate the response
        async with thread.typing():
            response_data = await generate_completion_response(
                bot_name=client.user.name,
                bot_instruction=prompt,
                messages=channel_messages,
                user=str(message.author.id),
                config=config,
            )

        if is_last_message_stale(
            interaction_message=message,
            last_message=thread.last_message,
            bot_id=client.user.id,
        ):
            # there is another message and its not from us, so ignore this response
            return

        # send response
        await process_response(
            user=message.author, thread=thread, response_data=response_data
        )
    except Exception as e:
        logger.exception(e)


client.run(DISCORD_BOT_TOKEN)
