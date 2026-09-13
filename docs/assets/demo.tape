# The README's animation: the "Use it in three steps" section of README.md, recorded against
# the real library. Regenerate from the repository root whenever that section or the library's
# output changes:
#
#     PATH=.venv/bin:$PATH vhs docs/assets/demo.tape
#
# What it records is the section itself. `docs/assets/recording/` holds the policy and the two
# programs, byte for byte the README's own runnable blocks, and `tests/test_readme_assets.py`
# asserts that they are. Nothing is staged: the provider is a fake that records calls instead
# of making them, so the recording opens no socket, and both programs exit non-zero on the
# path where the refusal did not happen, so a recording of a broken build fails instead of
# looking fine. The only thing that runs off screen is the copy of those three files into an
# empty directory. `docs/assets/demo.expected.txt` holds the lines the recording ends on, and
# the test runs the same three files and asserts every one of them is printed.

Output docs/assets/demo.gif

Set Shell bash
Set FontSize 20
Set Width 940
Set Height 420
Set Padding 28
Set Framerate 24
Set Theme { "name": "ctrlrun", "background": "#14161b", "foreground": "#e6e8ec", "cursor": "#F5A623", "selection": "#2c3038", "black": "#14161b", "red": "#f28b82", "green": "#8fd694", "yellow": "#F5A623", "blue": "#7aa2f7", "magenta": "#c8a1f0", "cyan": "#7fd6d6", "white": "#e6e8ec", "brightBlack": "#6f747d", "brightRed": "#f28b82", "brightGreen": "#8fd694", "brightYellow": "#F5A623", "brightBlue": "#7aa2f7", "brightMagenta": "#c8a1f0", "brightCyan": "#7fd6d6", "brightWhite": "#ffffff" }
Set TypingSpeed 40ms
Set PlaybackSpeed 1.0

# Off screen: the three files, copied into an empty directory, so every run starts from
# nothing and the store the recording writes is thrown away with it.
Hide
Type `W=$(mktemp -d) && cp -R docs/assets/recording/. "$W" && cd "$W" && PS1=$'\e[33m$\e[0m ' && clear`
Enter
Sleep 600ms
Show

# 1. One file says how much autonomy each action gets.
Type "cat ctrlrun.yaml"
Sleep 400ms
Enter
Sleep 5s

Type "clear"
Sleep 300ms
Enter
Sleep 300ms

# 2. The agent runs. €500 is autonomous. €5,000 stops and names the request a human answers.
Type "python agent.py"
Sleep 400ms
Enter
Sleep 4s

# 3. A human answers from the shell. The grant names the exact action hash it authorizes.
Type `ctrlrun approve "$(cat request_id.txt)"`
Sleep 400ms
Enter
Sleep 3500ms

# 4. The agent presents that approval, and then tries to spend it on €9,000.
Type "python approved.py"
Sleep 400ms
Enter
Sleep 6500ms
