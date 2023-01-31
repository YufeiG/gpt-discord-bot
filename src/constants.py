import os
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CLIENT_ID = os.environ["DISCORD_CLIENT_ID"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

ALLOWED_SERVER_IDS: List[int] = []
server_ids = os.environ["ALLOWED_SERVER_IDS"].split(",")
for s in server_ids:
    ALLOWED_SERVER_IDS.append(int(s))

SERVER_TO_MODERATION_CHANNEL: Dict[int, int] = {}
server_channels = os.environ.get("SERVER_TO_MODERATION_CHANNEL", "").split(",")
for s in server_channels:
    if len(s) == 0:
        continue
    values = s.split(":")
    SERVER_TO_MODERATION_CHANNEL[int(values[0])] = int(values[1])

# Send Messages, Create Public Threads, Send Messages in Threads, Manage Messages, Manage Threads, Read Message History, Use Slash Command
BOT_INVITE_URL = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&permissions=328565073920&scope=bot"

MODERATION_VALUES_FOR_BLOCKED = {
    "hate": 0.5,
    "hate/threatening": 0.4,
    "self-harm": 0.2,
    "sexual": 0.5,
    "sexual/minors": 0.2,
    "violence": 0.95,
    "violence/graphic": 0.95,
}

MODERATION_VALUES_FOR_FLAGGED = {
    "hate": 0.4,
    "hate/threatening": 0.2,
    "self-harm": 0.1,
    "sexual": 0.3,
    "sexual/minors": 0.1,
    "violence": 0.5,
    "violence/graphic": 0.5,
}

SECONDS_DELAY_RECEIVING_MSG = (
    3  # give a delay for the bot to respond so it can catch multiple messages
)
MAX_THREAD_MESSAGES = 50
ACTIVATE_THREAD_PREFX = "💬✅"
INACTIVATE_THREAD_PREFIX = "💬❌"
MAX_CHARS_PER_REPLY_MSG = (
    1500  # discord has a 2k limit, we just break message into 1.5k
)
