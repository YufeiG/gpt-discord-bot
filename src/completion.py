import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

import discord
import openai

from src.base import Conversation, Message, Prompt
from src.moderation import (
    moderate_message,
    send_moderation_blocked_message,
    send_moderation_flagged_message,
)
from src.utils import close_thread, logger, split_into_shorter_messages


class CompletionResult(Enum):
    OK = 0
    TOO_LONG = 1
    INVALID_REQUEST = 2
    OTHER_ERROR = 3
    MODERATION_FLAGGED = 4
    MODERATION_BLOCKED = 5


@dataclass
class CompletionData:
    status: CompletionResult
    reply_text: Optional[str]
    status_text: Optional[str]


class CompletionsConfig:
    temperature: float
    top_p: float
    presence_penalty: float
    frequency_penalty: float
    max_tokens: int

    def __init__(
        self,
        temp_str: Optional[str] = None,
        top_str: Optional[str] = None,
        pres_str: Optional[str] = None,
        freq_str: Optional[str] = None,
        max_tokens_str: Optional[str] = None,
    ) -> None:
        if temp_str is not None:
            try:
                self.temperature = float(temp_str)
                self.temperature = max(min(self.temperature, 2), 0)
            except ValueError:
                self.temperature = 1.1
        else:
            self.temperature = 1.1
        if top_str is not None:
            try:
                self.top_p = float(top_str)
                self.top_p = max(min(self.top_p, 1), 0)
            except ValueError:
                self.top_p = 1.0
        else:
            self.top_p = 1.0

        if pres_str is not None:
            try:
                self.presence_penalty = float(pres_str)
                self.presence_penalty = max(min(self.presence_penalty, 2), -2)
            except ValueError:
                self.presence_penalty = 0
        else:
            self.presence_penalty = 0
        if freq_str is not None:
            try:
                self.frequency_penalty = float(freq_str)
                self.frequency_penalty = max(min(self.frequency_penalty, 2), -2)
            except ValueError:
                self.frequency_penalty = 0
        else:
            self.frequency_penalty = 0
        if max_tokens_str is not None:
            try:
                self.max_tokens = int(max_tokens_str)
                self.max_tokens = max(min(self.max_tokens, 500), 1)
            except ValueError:
                self.max_tokens = 250
        else:
            self.max_tokens = 250

    def to_str(self) -> str:
        return f"temperature:{self.temperature},top_p:{self.top_p},presence_penalty:{self.presence_penalty},frequency_penalty:{self.frequency_penalty},max_tokens:{self.max_tokens}"

    @classmethod
    def from_str(cls, str) -> "CompletionsConfig":
        matches = re.match(
            "temperature:([\d\.]+),top_p:([\d\.]+),presence_penalty:([\d\.]+),frequency_penalty:([\d\.]+),max_tokens:([\d\.]+)",
            str,
        )
        if matches is not None:
            temp, topp, presp, freqp, maxt = matches.groups()
            return CompletionsConfig(
                temp_str=temp,
                top_str=topp,
                pres_str=presp,
                freq_str=freqp,
                max_tokens_str=maxt,
            )
        return CompletionsConfig()


async def generate_summary(
    bot_name: str,
    bot_instruction: str,
    messages: List[Message],
    user: str,
    config: CompletionsConfig,
) -> CompletionData:
    prompt = Prompt(
        preprompt="",
        header=Message("Instructions", "Provide a summary of the following story."),
        convo=Conversation([Message("Setting", bot_instruction)] + messages + [Message("Summary")]),
    )
    return await _generate_response(prompt=prompt, user=user, config=config)


async def generate_visual(
    bot_name: str,
    bot_instruction: str,
    messages: List[Message],
    user: str,
    config: CompletionsConfig,
) -> CompletionData:
    prompt = Prompt(
        preprompt="",
        header=Message(
            "Instructions",
            "Provide a short description of an image depicting the following story.",
        ),
        convo=Conversation([Message("Setting", bot_instruction)] + messages + [Message("Imagery")]),
    )
    return await _generate_response(prompt=prompt, user=user, config=config)


async def generate_completion_response(
    preprompt: str,
    bot_name: str,
    bot_instruction: str,
    messages: List[Message],
    user: str,
    config: CompletionsConfig,
) -> CompletionData:
    prompt = Prompt(
        preprompt=preprompt,
        header=Message("System", f"Instructions for {bot_name}: {bot_instruction}"),
        convo=Conversation(messages + [Message(bot_name)]),
    )
    return await _generate_response(prompt=prompt, user=user, config=config)


async def _generate_response(
    prompt: Prompt,
    user: str,
    config: CompletionsConfig,
) -> CompletionData:
    try:
        rendered = prompt.render()
        response = openai.Completion.create(
            engine="text-davinci-003",
            prompt=rendered,
            temperature=config.temperature,
            top_p=config.top_p,
            max_tokens=config.max_tokens,
            stop=["<|endoftext|>"],
            presence_penalty=config.presence_penalty,
            frequency_penalty=config.frequency_penalty,
            logit_bias={
                "25": -80,
                "1298": -80,
                "2599": -80,
                "1058": -80,
                "47308": -80,
                "11207": -80,
            },  # all to avoid :
            user=user,
        )
        reply = response.choices[0].text.strip()
        if reply:
            flagged_str, blocked_str = moderate_message(
                message=(rendered + reply)[-500:], user=user
            )
            if len(blocked_str) > 0:
                return CompletionData(
                    status=CompletionResult.MODERATION_BLOCKED,
                    reply_text=reply,
                    status_text=f"from_response:{blocked_str}",
                )

            if len(flagged_str) > 0:
                return CompletionData(
                    status=CompletionResult.MODERATION_FLAGGED,
                    reply_text=reply,
                    status_text=f"from_response:{flagged_str}",
                )

        return CompletionData(
            status=CompletionResult.OK, reply_text=reply, status_text=None
        )
    except openai.error.InvalidRequestError as e:
        if "This model's maximum context length" in e.user_message:
            return CompletionData(
                status=CompletionResult.TOO_LONG, reply_text=None, status_text=str(e)
            )
        else:
            logger.exception(e)
            return CompletionData(
                status=CompletionResult.INVALID_REQUEST,
                reply_text=None,
                status_text=str(e),
            )
    except Exception as e:
        logger.exception(e)
        return CompletionData(
            status=CompletionResult.OTHER_ERROR, reply_text=None, status_text=str(e)
        )


async def process_response(
    user: str, thread: discord.Thread, response_data: CompletionData
):
    status = response_data.status
    reply_text = response_data.reply_text
    status_text = response_data.status_text
    if status is CompletionResult.OK or status is CompletionResult.MODERATION_FLAGGED:
        sent_message = None
        if not reply_text:
            sent_message = await thread.send(
                embed=discord.Embed(
                    description=f"**Invalid response** - empty response",
                    color=discord.Color.yellow(),
                )
            )
        else:
            shorter_response = split_into_shorter_messages(reply_text)
            for r in shorter_response:
                sent_message = await thread.send(r)
        if status is CompletionResult.MODERATION_FLAGGED:
            await send_moderation_flagged_message(
                guild=thread.guild,
                user=user,
                flagged_str=status_text,
                message=reply_text,
                url=sent_message.jump_url if sent_message else "no url",
            )

            await thread.send(
                embed=discord.Embed(
                    description=f"⚠️ **This conversation has been flagged by moderation.**",
                    color=discord.Color.yellow(),
                )
            )
    elif status is CompletionResult.MODERATION_BLOCKED:
        await send_moderation_blocked_message(
            guild=thread.guild,
            user=user,
            blocked_str=status_text,
            message=reply_text,
        )

        await thread.send(
            embed=discord.Embed(
                description=f"❌ **The response has been blocked by moderation.**",
                color=discord.Color.red(),
            )
        )
    elif status is CompletionResult.TOO_LONG:
        await close_thread(thread)
    elif status is CompletionResult.INVALID_REQUEST:
        await thread.send(
            embed=discord.Embed(
                description=f"**Invalid request** - {status_text}",
                color=discord.Color.yellow(),
            )
        )
    else:
        await thread.send(
            embed=discord.Embed(
                description=f"**Error** - {status_text}",
                color=discord.Color.yellow(),
            )
        )


async def character_info_from_thread(
    guild: discord.Guild, thread: discord.Thread
) -> Tuple[CompletionsConfig, str, str]:
    channel = await guild.fetch_channel(thread.parent_id)
    prompt = "You are a discord user"
    config = CompletionsConfig()
    if channel:
        original_message = await channel.fetch_message(thread.id)
        if original_message is not None and len(original_message.embeds) > 0:
            embed = original_message.embeds[0]
            if len(embed.fields) == 2:
                preprompt = embed.fields[0].value
                prompt = embed.fields[1].value
            elif len(embed.fields) == 1:
                preprompt = ""
                prompt = embed.fields[0].value
            if embed.footer is not None:
                if isinstance(embed.footer, str):
                    config = CompletionsConfig.from_str(embed.footer)
                elif embed.footer.text is not None:
                    config = CompletionsConfig.from_str(embed.footer.text)
    return (config, prompt, preprompt)
