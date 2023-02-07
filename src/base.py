from dataclasses import dataclass
from typing import List, Optional

SEPARATOR_TOKEN = "<|endoftext|>"


@dataclass(frozen=True)
class Message:
    user: str
    text: Optional[str] = None

    def render(self):
        result = self.user + ":"
        if self.text is not None:
            result += " " + self.text
        return result


@dataclass
class Conversation:
    messages: List[Message]

    def prepend(self, message: Message):
        self.messages.insert(0, message)
        return self

    def render(self):
        return f"\n{SEPARATOR_TOKEN}".join(
            [message.render() for message in self.messages]
        )


@dataclass(frozen=True)
class Config:
    name: str
    instructions: str

@dataclass(frozen=True)
class Preprompt:
    displayText: str
    promptText: str


@dataclass(frozen=True)
class Prompt:
    preprompt: str
    header: Message
    convo: Conversation

    def render(self):
        return f"\n{SEPARATOR_TOKEN}".join(
            [
                self.preprompt,
                self.header.render(),
            ]
            + [Message("System", "Conversation:").render()]
            + [self.convo.render()],
        )
