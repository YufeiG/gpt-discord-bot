import discord
from discord import Message as DiscordMessage
import logging
from src.base import Message, Conversation
from src.constants import (
    BOT_INVITE_URL,
    DISCORD_BOT_TOKEN,
    ACTIVATE_THREAD_PREFX,
    MAX_THREAD_MESSAGES,
    SECONDS_DELAY_RECEIVING_MSG,
)
import asyncio
from src.utils import (
    logger,
    should_block,
    close_thread,
    is_last_message_stale,
    discord_message_to_message,
    save_a_copy,
)
import re
import io
from src import completion
from src.completion import generate_completion_response, process_response, CompletionsConfig
from src.moderation import (
    moderate_message,
    send_moderation_blocked_message,
    send_moderation_flagged_message,
)

intents = discord.Intents.default()
intents.message_content = True

class PersistentClient(discord.Client):
    async def setup_hook(self) -> None:
        self.add_view(CharacterEmbedView())
client = PersistentClient(intents=intents)
tree = discord.app_commands.CommandTree(client)


@client.event
async def on_ready():
    logger.info(f"We have logged in as {client.user}. Invite URL: {BOT_INVITE_URL}")
    await tree.sync()

# /chat message:
@tree.command(name="save_convo", description="Saves a copy of the conversation, same as the Save a copy button")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
async def save_conversation_command(interaction: discord.Interaction, thread_message_id: str):
    try:
        await save_a_copy(interaction=interaction, thread_message_id=int(thread_message_id))
    except Exception as e:
        await interaction.response.send_message(content=f"**Error**: Failed to save. {str(e)}", ephemeral=True)

@tree.context_menu(name="Save Conversation")
async def save_menu(interaction: discord.Interaction, message: discord.Message):
    try:
        await save_a_copy(interaction=interaction, thread_message_id=message.id)
    except Exception as e:
        await interaction.response.send_message(content=f"**Error**: Failed to save. {str(e)}", ephemeral=True)

class CharacterEmbedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start Chat", custom_id="chat", style=discord.ButtonStyle.green)
    async def chat_action(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.guild is None:
            return
        if interaction.message is None or len(interaction.message.embeds) == 0:
            return
        embed = interaction.message.embeds[0]
        if len(embed.fields) == 0:
            return
        instruction = embed.fields[0].value
        match = re.search("<@(\d+)>", embed.description)
        if not match:
            return
        instruction_user_id = str(match.group(1))

        member = await interaction.guild.fetch_member(instruction_user_id)
        if member is None:
            member = "Unknown"
        await create_chat(int=interaction, instructions_user_name=member.name, instructions=instruction, config=CompletionsConfig())

    @discord.ui.button(label="Customize API & Start Chat", custom_id="api", style=discord.ButtonStyle.secondary)
    async def api_action(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.guild is None:
            return
        if interaction.message is None or len(interaction.message.embeds) == 0:
            return
        embed = interaction.message.embeds[0]
        if len(embed.fields) == 0:
            return
        instruction = embed.fields[0].value
        match = re.search("<@(\d+)>", embed.description)
        if not match:
            return
        instruction_user_id = str(match.group(1))

        member = await interaction.guild.fetch_member(instruction_user_id)
        if member is None:
            member = "Unknown"
        form = CustomizeForm(instructions=instruction, instructions_user_name=member.name)
        await interaction.response.send_modal(
            form
        )
class CustomizeForm(discord.ui.Modal, title="Customize API Arguments"):
    def __init__(
        self, instructions: str, instructions_user_name: str
    ):
        super().__init__(timeout=None)
        self.instructions = instructions
        self.instructions_user_name = instructions_user_name
        self.temp = discord.ui.TextInput(
            label=f"temperature: number [0, 2]",
            placeholder="1.1",
            style=discord.TextStyle.short,
            required=False,
        )
        self.top_p = discord.ui.TextInput(
            label=f"top_p: number [0, 1]",
            placeholder="1.0",
            style=discord.TextStyle.short,
            required=False,
        )
        self.presence = discord.ui.TextInput(
            label=f"presence_penalty: number [-2. 2]",
            placeholder="0.0",
            style=discord.TextStyle.short,
            required=False,
        )
        self.freq = discord.ui.TextInput(
            label=f"frequency_penalty: number [-2, 2]",
            placeholder="0.0",
            style=discord.TextStyle.short,
            required=False,
        )
        self.add_item(self.temp)
        self.add_item(self.top_p)
        self.add_item(self.presence)
        self.add_item(self.freq)

    async def on_submit(self, interaction: discord.Interaction):
        temp = self.temp.value
        topp = self.top_p.value
        pres = self.presence.value
        freq = self.freq.value

        await create_chat(int=interaction, instructions_user_name=self.instructions_user_name, instructions=self.instructions, config=CompletionsConfig(temp_str=temp, top_str=topp, pres_str=pres, freq_str=freq))

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message(
            f"**There was an error creating chat.**\n{str(error)}",
            ephemeral=True,
        )

# /characterize message:
@tree.command(name="characterize", description="Write character instructions for how the bot should act.")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
async def character_command(int: discord.Interaction, instructions: str):
    try:
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
        embed.add_field(name="Instructions", value=instructions)
        if len(flagged_str) > 0:
            # message was flagged
            embed.color = discord.Color.yellow()
            embed.title = "⚠️ This prompt was flagged by moderation."

        view = CharacterEmbedView()
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


async def create_chat(int: discord.Interaction, instructions_user_name: str, instructions: str, config: CompletionsConfig):
    try:
        # only support creating thread in text channel
        if not isinstance(int.channel, discord.TextChannel):
            return

        # block servers not in allow list
        if should_block(guild=int.guild):
            return

        user = int.user
        logger.info(f"Create chat by {user}")
        try:
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
                description=f"<@{user.id}> wants to chat! 🤖💬",
                color=discord.Color.from_str("#e7779d"),
            )
            embed.add_field(name=f"Instructions by {instructions_user_name}", value=instructions)
            embed.set_footer(text=config.to_str())

            if len(flagged_str) > 0:
                # message was flagged
                embed.color = discord.Color.yellow()
                embed.title = "⚠️ This prompt was flagged by moderation."

            await int.response.send_message(embed=embed)
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
            await int.response.send_message(
                f"Failed to start chat {str(e)}", ephemeral=True
            )
            return

        # create the thread
        thread = await response.create_thread(
            name=f"{ACTIVATE_THREAD_PREFX} {user.name[:20]} - {instructions[:30]}",
            slowmode_delay=1,
            reason="gpt-bot",
            auto_archive_duration=60,
        )
        # generate the response
        async with thread.typing():
            response_data = await generate_completion_response(
                bot_name=client.user.name,
                bot_instruction=instructions,
                messages=[], 
                user=int.user.name,
                config=config,
            )

        # send response
        await process_response(
            user=int.user.name, thread=thread, response_data=response_data
        )
    except Exception as e:
        logger.exception(e)
        try:
            await int.response.send_message(
                f"Failed to start chat {str(e)}", ephemeral=True
            )
        except Exception as e:
            logger.exception(e)



# calls for each message
@client.event
async def on_message(message: DiscordMessage):
    try:
        # ignore messages from the bot
        if message.author == client.user:
            return

        # block servers not in allow list
        if should_block(guild=message.guild):
            return

        # ignore messages not in a thread
        channel = message.channel
        if not isinstance(channel, discord.Thread):
            return

        # ignore threads not created by the bot
        thread = channel
        if thread.owner_id != client.user.id:
            return

        # ignore threads that are archived locked or title is not what we want
        if (
            thread.archived
            or thread.locked
            or not thread.name.startswith(ACTIVATE_THREAD_PREFX)
        ):
            # ignore this thread
            return

        if thread.message_count > MAX_THREAD_MESSAGES:
            # too many messages, no longer going to reply
            await close_thread(thread=thread)
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

        channel = await message.guild.fetch_channel(message.reference.channel_id)
        prompt = "You are a discord user"
        config = CompletionsConfig()
        if channel:
            original_message = await channel.fetch_message(message.reference.message_id)
            if original_message is not None and len(original_message.embeds) > 0:
                embed = original_message.embeds[0]
                if len(embed.fields) > 0:
                    prompt = embed.fields[0].value
                config = CompletionsConfig.from_str(embed.footer)
                        

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
                messages=channel_messages, user=str(message.author.id), config=config,
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
