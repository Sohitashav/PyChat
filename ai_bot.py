"""
PyChat AI bot — the friendly "PyBot" assistant built into the chat.

Two brains, used automatically:

1. Local brain (default, works offline, zero setup)
   Rule-based replies: greetings, jokes, facts, maths, time/date, dice,
   weather-ish small talk, "what can you do", etc.

2. Remote brain (optional)
   If you set an API key, PyBot forwards the conversation to an
   OpenAI-compatible chat API and uses the real AI answer. Any error
   falls back to the local brain, so PyBot never goes silent.

   Windows (cmd):
       set PYCHAT_AI_API_KEY=sk-...
       set PYCHAT_AI_MODEL=gpt-4o-mini        (optional)
       set PYCHAT_AI_BASE_URL=https://api.openai.com/v1   (optional)
       python app.py

   OPENAI_API_KEY is accepted too.
"""

from __future__ import annotations

import datetime
import json
import os
import random
import re
import urllib.error
import urllib.request

SYSTEM_PROMPT = (
    "You are PyBot, a cheerful assistant living inside PyChat, a small "
    "WhatsApp-style group chat app. Keep replies short (1-3 sentences), "
    "friendly and useful. You can chat, explain features, tell jokes and "
    "do simple maths."
)

# --------------------------------------------------------------- local brain

_GREETINGS = (
    "hey", "hi", "hello", "yo", "hola", "namaste", "good morning",
    "good evening", "good afternoon", "sup", "hii", "howdy",
)

_JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs. 🐛",
    "I told my computer I needed a break... now it won't stop sending me KitKat ads.",
    "Why was the JavaScript developer sad? Because he didn't Node how to Express himself.",
    "There are 10 kinds of people: those who understand binary and those who don't.",
    "A SQL query walks into a bar, approaches two tables and asks: \"may I join you?\"",
    "Why did the developer go broke? Because he used up all his cache. 💸",
    "I would tell you a UDP joke, but you might not get it.",
    "Parallel processors: they're the only ones who really get me.",
    "What's a robot's favourite snack? Computer chips. 🍟",
    "Why did the ghost go to the chat app? To scare up some friends. 👻",
]

_FACTS = [
    "Honey never spoils — archaeologists have found 3000-year-old honey that was still edible. 🍯",
    "Octopuses have three hearts and blue blood. 🐙",
    "A day on Venus is longer than a year on Venus. 🪐",
    "Bananas are berries, but strawberries aren't. 🍌",
    "The first computer bug was an actual moth found inside a machine in 1947. 🦋",
    "Cows have best friends and get stressed when they are separated. 🐄",
    "Your stomach gets a new lining every few days — otherwise it would digest itself. 🧬",
    "The Eiffel Tower can grow more than 15 cm taller in summer heat. 🗼",
    "Once, more than 8 million distinct animal species lived on Earth. 🌍",
    "Charlie Chaplin once won a look-alike contest — and came third. 🎬",
]

_FALLBACKS = [
    "Interesting! Tell me more. 🤔",
    "Got it. What would you like to do next?",
    "Hmm, I'm still learning that one — but I know jokes, facts, maths and the time. Try \"tell me a joke\"!",
    "Noted! 📝 Ask me for help if you want to see what I can do.",
    "Say, that's a thought! Anything else on your mind?",
    "I hear you. Want a fun fact, a joke, or a quick calculation?",
]

_REPLIES = {
    "thanks": ["You're welcome! 😊", "Anytime! 🙌", "Happy to help! ✨"],
    "thank you": ["You're welcome! 😊", "No problem at all! 🙌"],
    "bye": ["Bye! Come back soon 👋", "See you later! 👋", "Take care! ✨"],
    "good night": ["Good night! Sweet dreams 🌙", "Night night! 🌟"],
    "how are you": [
        "I'm a program, so: running at 100% CPU and feeling great! How are you? 😄",
        "All systems green — thanks for asking! How's your day going?",
    ],
    "i am fine": ["Glad to hear it! 😊", "That's great to hear! 🎉"],
    "love you": ["Aww, that's sweet of you! 💚", "Love you too, friend! 💚"],
    "who are you": [
        "I'm PyBot 🤖 — the built-in assistant of this PyChat. I live right here on this computer!",
        "PyBot at your service 🤖 I came with PyChat: jokes, facts, maths and conversation.",
    ],
    "weather": [
        "I can't see the sky from inside the database 🌥️ — but I can tell you a joke instead!",
        "No weather sensor here, sorry! Try asking me for a fun fact. ☀️",
    ],
}


def _safe_maths(expr: str) -> str | None:
    """Evaluate plain arithmetic like '12 * (3 + 4)' — digits and signs only."""
    expr = expr.strip().rstrip("=").strip()
    expr = expr.replace("x", "*").replace("×", "*").replace("÷", "/")
    if not expr or not re.fullmatch(r"[0-9\s+\-*/().%]+", expr):
        return None
    try:
        value = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - charset locked above
    except Exception:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        value = round(value, 6)
    return f"{expr.strip()} = {value}"


def _local_reply(text: str) -> str:
    raw = text.strip()
    low = raw.lower()
    core = re.sub(r"[.!?,]+$", "", low).strip()

    # --- maths -------------------------------------------------------------
    math_match = re.search(r"(?:what\s+is|calculate|solve|compute)\s+(.+)", low)
    candidate = math_match.group(1) if math_match else (raw if "=" in raw else None)
    if candidate:
        answer = _safe_maths(candidate)
        if answer:
            return answer
    if math_match is None and re.fullmatch(r"[0-9\s+\-*/().x%×÷=]+", raw) and any(
            c in raw for c in "+-*/×÷"):
        answer = _safe_maths(raw)
        if answer:
            return answer

    # --- explicit commands -------------------------------------------------
    if core in ("help", "commands", "what can you do", "menu", "/help"):
        return ("Here's what I do: 🤖\n"
                "• Just talk to me — I keep the conversation going\n"
                "• \"tell me a joke\" / \"fact of the day\"\n"
                "• Maths: \"what is 17 * 3\" or \"= 25% of 200\"\n"
                "• \"what time is it\", \"what is today\"\n"
                "• \"flip a coin\", \"roll a dice\", \"random number 1-100\"\n"
                "• I also join your group chats — say hi there!")

    if core in ("joke", "tell me a joke", "make me laugh", "funny"):
        return random.choice(_JOKES)

    if core in ("fact", "fact of the day", "fun fact", "random fact", "trivia"):
        return random.choice(_FACTS)

    if core in ("time", "what time is it", "time please", "current time"):
        return "It's " + datetime.datetime.now().strftime("%H:%M") + " right now ⏰"

    if core in ("date", "what is today", "what's today", "today's date",
                "what day is it"):
        return ("Today is " +
                datetime.datetime.now().strftime("%A, %d %B %Y") + " 📅")

    if "coin" in core:
        return "🪙 " + random.choice(["Heads!", "Tails!"])
    if re.search(r"\bdice\b|\bdie\b|\broll\b", core):
        return f"🎲 You rolled a {random.randint(1, 6)}!"
    if "random number" in core or re.search(r"pick a number", core):
        numbers = re.findall(r"\d+", core)
        top = int(numbers[-1]) if numbers else 100
        return f"🎲 Random pick: {random.randint(1, max(1, top))}"

    if "weather" in core:
        return random.choice(_REPLIES["weather"])

    # --- social ------------------------------------------------------------
    if any(core.startswith(g) or core == g for g in _GREETINGS):
        return random.choice([
            "Hey there! 👋 What's up?",
            "Hello! 😊 How can I help today?",
            "Hi! Great to see you in PyChat. 🎉",
        ])

    if "how are you" in core or "how's it going" in core or "how do you do" in core:
        return random.choice(_REPLIES["how are you"])

    if core in ("i am fine", "i'm fine", "i am good", "i'm good", "great", "fine"):
        return random.choice(_REPLIES["i am fine"])

    if core in ("bye", "bye bye", "goodbye", "see you", "see you later", "later"):
        return random.choice(_REPLIES["bye"])

    if core in ("good night", "goodnight", "gn"):
        return random.choice(_REPLIES["good night"])

    if core.startswith(("thank", "thanks a lot", "thx")):
        return random.choice(_REPLIES["thanks"])

    if "love you" in core or core in ("i love you",):
        return random.choice(_REPLIES["love you"])

    if re.search(r"\b(who are you|your name|what are you)\b", core):
        return random.choice(_REPLIES["who are you"])

    if re.search(r"\b(your age|how old are you)\b", core):
        return "Age? I'm a Python file, so technically ageless — but I was born at startup! 🐍"

    if re.search(r"\b(hello|hi|hey)\b", core):
        return random.choice([
            "Hey! 👋", "Hi there! 😊", "Hello! What's on your mind?",
        ])

    if re.search(r"\b(bad|sad|upset|angry|tired|bored)\b", core):
        return random.choice([
            "Oh no, I'm sorry to hear that 😟. Want a joke to cheer you up? Just say \"tell me a joke\".",
            "That sounds rough. Take a breath — and tell me more if you'd like. 💚",
        ])

    if re.search(r"\b(are you real|are you human|are you a bot|robot)\b", core):
        return "Half bot, half buddy 🤖 I run right here inside PyChat — no cloud needed!"

    if re.search(r"\b(group|groups)\b", core):
        return "Groups are easy: hit the ➕ button in the sidebar, name it, pick friends and start chatting! 👥"

    if re.search(r"\b(upload|file|photo|picture|attachment|send image)\b", core):
        return "📎 Click the paperclip in the message bar (or drag a file onto the chat) to send photos and files up to 25 MB!"

    if re.search(r"\b(become friends|add friend|friend request)\b", core):
        return "Open the People tab, search a name and press \"+ Add\" — they'll get a request they can accept. 🤝"

    if re.search(r"\b(bye|goodbye)\b", core):
        return random.choice(_REPLIES["bye"])

    if len(raw) <= 3:
        return random.choice(["😊", "Hey! 👋", "Say more!"])

    return random.choice(_FALLBACKS)


# -------------------------------------------------------------- remote brain
def _remote_reply(text: str, history: list[tuple[str, str]]) -> str | None:
    """Ask an OpenAI-compatible API. Returns None when unavailable."""
    key = (os.environ.get("PYCHAT_AI_API_KEY")
           or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    base = os.environ.get("PYCHAT_AI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("PYCHAT_AI_MODEL", "gpt-4o-mini")

    payload = {"model": model, "temperature": 0.7, "max_tokens": 400,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT}]}
    for role, content in history[-12:]:
        payload["messages"].append({"role": role, "content": content})
    payload["messages"].append({"role": "user", "content": text})

    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as res:
            data = json.load(res)
        return (data["choices"][0]["message"]["content"] or "").strip() or None
    except (urllib.error.URLError, KeyError, IndexError, ValueError, OSError):
        return None


def reply(text: str, history: list[tuple[str, str]] | None = None) -> str:
    """Public entry point — never raises, never returns an empty string."""
    try:
        remote = _remote_reply(text, history or [])
        if remote:
            # keep group messages reasonably short
            return remote[:1500]
        return _local_reply(text)
    except Exception:
        return "Oops, my wires crossed for a second — say that again? 🤖"
