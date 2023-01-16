from enum import Enum
from dataclasses import dataclass
import openai
from src.moderation import moderate_message
from typing import Optional, List
import discord
from src.base import Message, Prompt, Conversation
from src.utils import split_into_shorter_messages, close_thread, logger
from src.moderation import (
    send_moderation_flagged_message,
    send_moderation_blocked_message,
)
import re

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

    def __init__(self, temp_str: Optional[str]=None, top_str: Optional[str]=None, pres_str: Optional[str]=None, freq_str: Optional[str]=None) -> None:
        if temp_str is not None:
            try:
                self.temperature = float(temp_str)
                self.temperature = max(min(self.temperature, 2), 0)
            except ValueError:
                self.temperature = 1.2
        else:
            self.temperature = 1.2
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
    
    def to_str(self) -> str:
        return f"temp:{self.temperature},topp:{self.top_p},presp:{self.presence_penalty},freqp:{self.frequency_penalty}"

    @classmethod
    def from_str(cls, str) -> "CompletionsConfig":
        matches = re.match('temp:([\d\.]+),topp:([\d\.]+),presp:([\d\.]+),freqp:([\d\.]+)', str)
        if matches is not None:
            temp, topp, presp, freqp = matches.groups()
            return CompletionsConfig(temp_str=temp, top_str=topp, pres_str=presp, freq_str=freqp)
        return CompletionsConfig()

async def generate_completion_response(
    bot_name: str,
    bot_instruction:str,
    messages: List[Message], user: str, config: CompletionsConfig
) -> CompletionData:
    try:
        prompt = Prompt(
            header=Message(
                "System", f"Instructions for {bot_name}: {bot_instruction}"
            ),
            convo=Conversation(messages + [Message(bot_name)]),
        )
        rendered = prompt.render()
        response = openai.Completion.create(
            engine="text-davinci-003",
            prompt=rendered,
            temperature=config.temperature,
            top_p=config.top_p,
            max_tokens=512,
            stop=["<|endoftext|>"],
            presence_penalty=config.presence_penalty,
            frequency_penalty=config.frequency_penalty,
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
