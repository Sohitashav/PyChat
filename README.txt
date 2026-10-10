PyChat — WhatsApp-style chat you run on your own computer
==========================================================

REQUIREMENTS
- Windows 10/11
- Python 3 (https://www.python.org/downloads/ — tick "Add python.exe to PATH"
  when installing). You already have it if `python --version` works.


HOW TO START (3 steps)
----------------------
1. Put this whole folder anywhere you like (Desktop is fine).
2. Double-click SETUP.bat   — do this only ONCE.
3. Double-click START.bat   — do this every time you want to chat.

A black window opens and, after a few seconds, shows YOUR PUBLIC LINK
something like:  https://something.trycloudflare.com

Copy that link, open it in your browser, sign up, and send the link to
your friend so they can sign up too. Search their username, send a friend
request, and chat.

IMPORTANT: keep both black windows open while chatting — closing them
stops the chat.


WHAT YOU CAN DO
---------------
- 1:1 chats with ticks (sent -> delivered -> blue read), typing dots,
  online/last-seen and unread badges.
- 👥 GROUPS: press the 👥 button at the top, name the group, tick the
  friends you want, done. Everyone sees new messages live, the group
  header shows who is typing, and ➕ / 🚪 let you add people or leave.
- 📎 FILES: click the paperclip in the message bar — or just drag a file
  onto the conversation. Photos and videos show inline, other files show
  as a download chip. Up to 25 MB per file (png/jpg/gif/webp/pdf/txt/
  zip/mp3/mp4/doc/xls and more).
- 🤖 PYBOT: an AI assistant already sits in your chat list. The moment
  you sign in it introduces itself (and on later visits says welcome
  back). Ask it for a joke, a fun fact, the time, a calculation
  ("what is 17 * 3"), or just chat. It joins every new group too — say
  "PyBot" or "@bot" to get its attention, and it occasionally chips in
  on its own.
- Smarter look: gradient accents, animated empty state, group avatars,
  hover effects and a nicer sign-in page. Works on phones too.


MAKING PYBOT A REAL AI (optional)
---------------------------------
Out of the box PyBot answers with a built-in offline brain, so it works
with no internet and no setup.

If you want it powered by a real AI model, set an OpenAI-compatible API
key BEFORE starting the server, in the same window:

    set PYCHAT_AI_API_KEY=sk-...
    set PYCHAT_AI_MODEL=gpt-4o-mini
    start "PyChat server" ".venv\Scripts\python.exe" app.py

Optional extra setting:
    set PYCHAT_AI_BASE_URL=https://api.openai.com/v1

OPENAI_API_KEY is accepted as well. If the AI cannot be reached, PyBot
quietly falls back to its offline brain — it never goes silent.
(These settings are NOT saved in any file; they only last for that
window.)


TROUBLESHOOTING
---------------
- "Python was not found": install Python from python.org and make sure
  you ticked "Add python.exe to PATH", then run SETUP.bat again.
- START.bat says PyChat is not set up yet: run SETUP.bat first.
- Tunnel window shows errors or no link: your network may block it.
  Use START-LOCAL.bat instead — it runs the chat on this computer only
  at http://127.0.0.1:**** (good for testing, or both of you on the
  same Wi-Fi/network).
- "Port 5000 is already in use": another copy of PyChat is running.
  Close the other "PyChat server" window and run START.bat again.
- "That file type is not allowed": PyChat only accepts common document,
  picture, audio and video formats.
- Old chat.db from an earlier version keeps working — PyChat adds the
  new group and file columns on first start.


NOTES
-----
- Your accounts, messages, groups and uploaded files live inside this
  folder (chat.db + static/uploads/). Deleting chat.db resets everything.
- The username "pybot" is reserved for the assistant.
- This is a fun/learning project: no end-to-end encryption, so don't
  use it for real secrets.
