import re

import discord

from src.completion import (
    CompletionsConfig,
    generate_completion_response,
    process_response,
    starter_message_from_thread,
)
from src.constants import ACTIVATE_THREAD_PREFX
from src.moderation import (
    moderate_message,
    send_moderation_blocked_message,
    send_moderation_flagged_message,
)
from src.save import save_a_copy
from src.utils import logger, should_block
from src.base import Preprompt

actor=Preprompt(displayText="Actor", promptText="You are an actor. Follow the instructions to act out the character and the scene.")
dnd_player=Preprompt(displayText="Playing DnD", promptText="You are particpating in a dnd campaign, play your character as described below.")
you_are_you=Preprompt(displayText="No preprompt", promptText="")
assistant=Preprompt(displayText="AI Assistant", promptText="You are an AI assistant, ready to help with anything.")
teacher=Preprompt(displayText="Teacher", promptText="You are a teacher, you are knowledgeable on a variety of topics.")

class CharacterEmbedView(discord.ui.View):
    def __init__(self, bot_name):
        super().__init__(timeout=None)
        self.bot_name = bot_name
        self.selected_preprompt = actor.promptText

    @discord.ui.select(cls=discord.ui.Select, custom_id="select", options=[
        discord.SelectOption(label=actor.displayText, value=actor.promptText, default=True, emoji="🎬"),
        discord.SelectOption(label=assistant.displayText, value=assistant.promptText, emoji="🤖"),
        discord.SelectOption(label=teacher.displayText, value=teacher.promptText,  emoji="👩‍🏫"),
        discord.SelectOption(label=dnd_player.displayText, value=dnd_player.promptText, emoji="🎲"),
        discord.SelectOption(label=you_are_you.displayText, value=you_are_you.promptText, emoji="🪞"),
    ])
    async def preprompt_selector(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_preprompt = select.values[0]
        await interaction.response.defer()

    @discord.ui.button(
        label="Start Chat", custom_id="chat", style=discord.ButtonStyle.green
    )
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
        await create_chat(
            int=interaction,
            preprompt=self.selected_preprompt,
            instructions_user_name=member.name,
            instructions=instruction,
            config=CompletionsConfig(),
            bot_name=self.bot_name,
        )

    @discord.ui.button(
        label="Customize API & Start Chat",
        custom_id="api",
        style=discord.ButtonStyle.secondary,
    )
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
        form = CustomizeForm(
            instructions=instruction,
            instructions_user_name=member.name,
            bot_name=self.bot_name,
            preprompt=self.selected_preprompt,
        )
        await interaction.response.send_modal(form)


class SaveThreadView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Save Chat", custom_id="save", style=discord.ButtonStyle.secondary
    )
    async def save_action(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            await save_a_copy(
                interaction=interaction, thread_message_id=int(interaction.message.id)
            )
        except Exception as e:
            await interaction.response.send_message(
                content=f"**Error**: Failed to save. {str(e)}", ephemeral=True
            )

class ThreadGenerationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Pin", emoji="📌", custom_id="pin", style=discord.ButtonStyle.secondary
    )
    async def pin_action(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.channel is not None and isinstance(interaction.channel, discord.Thread):
            starter, starter_embed = await starter_message_from_thread(guild=interaction.guild, thread=interaction.channel);
            
            message = interaction.message
            if not message or not message.embeds or len(message.embeds) == 0:
                await interaction.response.send_message(content="Failed", ephemeral=True)
                return
            embed = message.embeds[0]
            image_url = embed.image.url

            starter_embed.set_image(url=image_url)
            new_message = await starter.edit(embed=starter_embed)
            await interaction.response.send_message(content=f"[View thread]({new_message.jump_url})", ephemeral=True)
            return
        await interaction.response.send_message(content="Something went wrong!", ephemeral=True)


class CustomizeForm(discord.ui.Modal, title="Customize API Arguments"):
    def __init__(self, instructions: str, instructions_user_name: str, bot_name: str, preprompt: str):
        super().__init__(timeout=None)
        self.instructions = instructions
        self.instructions_user_name = instructions_user_name
        self.preprompt = preprompt
        self.bot_name = bot_name
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
        self.maxt = discord.ui.TextInput(
            label=f"max_tokens: integer [1, 500]",
            placeholder="250",
            style=discord.TextStyle.short,
            required=False,
        )
        self.add_item(self.temp)
        self.add_item(self.top_p)
        self.add_item(self.presence)
        self.add_item(self.freq)
        self.add_item(self.maxt)

    async def on_submit(self, interaction: discord.Interaction):
        temp = self.temp.value
        topp = self.top_p.value
        pres = self.presence.value
        freq = self.freq.value
        maxt = self.maxt.value

        await create_chat(
            int=interaction,
            preprompt=self.preprompt,
            instructions_user_name=self.instructions_user_name,
            instructions=self.instructions,
            config=CompletionsConfig(
                temp_str=temp,
                top_str=topp,
                pres_str=pres,
                freq_str=freq,
                max_tokens_str=maxt,
            ),
            bot_name=self.bot_name,
        )


async def create_chat(
    int: discord.Interaction,
    preprompt: str, 
    instructions_user_name: str,
    instructions: str,
    config: CompletionsConfig,
    bot_name: str,
):
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
            embed.add_field(
                name=f"Instructions", value=preprompt, inline=False
            )
            embed.add_field(
                name=f"Character by {instructions_user_name}", value=instructions, inline=False
            )
            embed.set_footer(text=config.to_str())

            if len(flagged_str) > 0:
                # message was flagged
                embed.color = discord.Color.yellow()
                embed.title = "⚠️ This prompt was flagged by moderation."

            await int.response.send_message(embed=embed, view=SaveThreadView())
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
                preprompt=preprompt,
                bot_name=bot_name,
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
