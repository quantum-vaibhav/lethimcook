<div align="center">

# 🧑‍🍳 lethimcook

### your AI is cooking. this is the kitchen soundtrack.

**claude code plays theme music while it thinks — pauses the moment the dish is served.**

<br/>

![Platform](https://img.shields.io/badge/platform-windows%20%7C%20macos%20%7C%20linux-blueviolet?style=for-the-badge)
![Python](https://img.shields.io/badge/python-3.8+-yellow?style=for-the-badge&logo=python&logoColor=white)
![Vibes](https://img.shields.io/badge/vibes-immaculate-ff69b4?style=for-the-badge)
![Brain](https://img.shields.io/badge/claude-locked%20in-orange?style=for-the-badge)

<br/>

*claude thinking* 🧠 → 🎶 *music plays* → *claude stops* 🛑 → 🔇 *silence. instantly.*

</div>

---

## 💭 the lore

you prompt claude. it starts cooking. you sit there watching a spinner like it's 2009.

**what if the spinner had a theme song?**

lethimcook hooks into claude code's lifecycle and plays
[**"Claude's Plan" by Jeff Guo**](https://www.youtube.com/watch?v=9kT0oLBPiOw)
(or literally any mp3 you want) while claude is thinking. when claude finishes,
asks for permission, or waits on you — the music **pauses**. when claude locks
back in — it **resumes from the exact same spot**. no restarts. no chaos.
the song always plays through and only loops when it naturally ends.

it's giving *main character energy* to your terminal. fr.

## ⚡ get it running (one click, no cap)

> you need python 3.8+. that's it. that's the dependency.

**1.** clone it / download it / yoink the folder

```bash
git clone https://github.com/YOUR_USERNAME/lethimcook.git
```

**2.** run the setup for your OS

| your machine | do this |
|:---:|:---|
| 🪟 windows | double-click **`setup.bat`** |
| 🍎 macos | `bash setup.sh` |
| 🐧 linux | `bash setup.sh` |

**3.** restart claude code. prompt something. vibe. ✨

<sub>the setup auto-installs pygame, grabs the song if it's missing (needs ffmpeg
for that one step), and wires the hooks into `~/.claude/settings.json` with paths
for *your* machine. run it again anytime — it cleans up after itself and never
touches your other settings (backs them up to `settings.json.bak` first, we're
not monsters).</sub>

## 🧠 how it actually works

```mermaid
flowchart LR
    A["🫵 you send a prompt"] -->|UserPromptSubmit| B["🎶 music plays"]
    B --> C{"🤖 claude..."}
    C -->|"...finishes (Stop)"| D["🔇 music pauses"]
    C -->|"...needs you (Notification)"| D
    C -->|"...keeps cooking (PostToolUse)"| B
    D -->|you prompt again| B
```

two tiny scripts, zero background clutter:

- **`scripts/player.py`** — a hidden daemon. loads your mp3 with pygame, loops it
  forever, and polls a state file 5×/sec to pause/unpause. pausing keeps the
  playback position — that's the secret sauce. a lock + heartbeat file guarantee
  **exactly one** player ever runs, even with multiple claude sessions open.
  goes touch grass (exits) after 2 hours of silence.
- **`scripts/hook.py`** — what the hooks call. writes `play` or `pause` to the
  state file, spawns the daemon if needed, exits in milliseconds. every hook is
  async so your claude stays **zero-latency**.

<details>
<summary>📋 <b>full hook table</b> (for the nerds — click)</summary>
<br/>

| claude code event | action | translation |
|---|:---:|---|
| `UserPromptSubmit` | ▶️ | you said something, claude's cooking |
| `PostToolUse` | ▶️ | tool finished, still cooking |
| `PostToolUseFailure` | ▶️ | tool flopped, claude's coping + cooking |
| `PermissionDenied` | ▶️ | you said no, claude's pivoting |
| `Notification` | ⏸️ | claude needs you. pick up the phone |
| `Stop` | ⏸️ | claude's done. silence. |
| `SessionEnd` | ⏸️ | you left. it noticed. |

</details>

## 🎛️ make it yours

**volume** — edit `config.json`, applies **live** while the music plays (yes, really):

```json
{ "volume": 100 }
```

**change the song** — drop any mp3 in the root as `thinking-song.mp3`. done.
your thinking music can be phonk, boccherini, or the seinfeld theme. we don't judge.

```bash
# or grab any track off youtube:
python -m yt_dlp -x --audio-format mp3 -o "thinking-song.%(ext)s" <url>
```

**panic button** — music stuck? get him out of the kitchen:

```bash
python scripts/hook.py quit
```

## 🗑️ uninstall (why would you though)

delete the hook entries tagged `"statusMessage": "lethimcook"` from
`~/.claude/settings.json` (or use `/hooks` inside claude code), then delete the
folder. no registry gunk, no leftover daemons, no hard feelings. 💔

## ❓ faq

<details>
<summary><b>does this slow claude down?</b></summary>
<br/>
nope. every hook runs async and exits in milliseconds. the music is a whole
separate process minding its own business.
</details>

<details>
<summary><b>i have multiple claude sessions open. do i get a remix?</b></summary>
<br/>
no — one daemon, one song, shared by all sessions. the lockfile said "one mic only."
</details>

<details>
<summary><b>why does the song resume mid-track instead of restarting?</b></summary>
<br/>
because restarting a banger at 0:00 every 30 seconds is a war crime.
pause/resume preserves position; the track only loops when it fully ends.
</details>

<details>
<summary><b>is the song included in the repo?</b></summary>
<br/>
no — the mp3 is gitignored because it's someone's actual music
(<a href="https://www.youtube.com/watch?v=9kT0oLBPiOw">"Claude's Plan" by Jeff Guo</a>, go stream it).
setup downloads it to your machine for personal use, or you supply your own mp3.
</details>

## 🤝 contributing

PRs welcome. ideas that would go hard:

- 🎚️ different songs for different hook events (boss music for `PostToolUseFailure`?)
- 🔀 playlist folder support
- 🍎 menu-bar / tray controls
- 🎮 konami code easter egg (we'll figure out where)

---

<div align="center">

**built with ☕, one prompt, and questionable priorities.**

*if this made your terminal 200% more cinematic, drop a* ⭐

<sub>not affiliated with anthropic. claude just deserved a soundtrack.</sub>

</div>
