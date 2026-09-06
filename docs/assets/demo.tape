# The README's animation. Regenerate from the repository root whenever the demo's output
# changes:
#
#     PATH=.venv/bin:$PATH vhs docs/assets/demo.tape
#
# What it records is `ctrlrun demo`'s first two scenarios, byte for byte: the refund that
# commits behind a lost response and the retry that is refused, then the approval that is bound
# to one action and refused for another. The demo prints all five scenarios at once, so the
# command is piped twice, and both pipes are typed in the open, because a recording that hid
# how it was made would be the wrong kind of demo for this project: `sed` ends the output on
# scenario 2's BLOCKED line, and `slow` — the only thing this file sets up off screen, a
# `while read` loop with a sleep — releases one line every 0.7 s so a reader can follow. Nothing
# is added, reordered or removed by either. `docs/assets/demo.expected.txt` holds the lines the
# recording ends on, and `tests/test_readme_assets.py` asserts they are lines the demo prints
# and the README quotes.

Output docs/assets/demo.gif

Set Shell bash
Set FontSize 17
Set Width 1340
Set Height 470
Set Padding 28
Set Theme { "name": "ctrlrun", "background": "#14161b", "foreground": "#e6e8ec", "cursor": "#F5A623", "selection": "#2c3038", "black": "#14161b", "red": "#f28b82", "green": "#8fd694", "yellow": "#F5A623", "blue": "#7aa2f7", "magenta": "#c8a1f0", "cyan": "#7fd6d6", "white": "#e6e8ec", "brightBlack": "#6f747d", "brightRed": "#f28b82", "brightGreen": "#8fd694", "brightYellow": "#F5A623", "brightBlue": "#7aa2f7", "brightMagenta": "#c8a1f0", "brightCyan": "#7fd6d6", "brightWhite": "#ffffff" }
Set TypingSpeed 45ms
Set PlaybackSpeed 1.0

Hide
Type `cd $(mktemp -d) && slow() { while IFS= read -r line; do printf '%s\n' "$line"; sleep 0.7; done; } && PS1=$'\e[33m$\e[0m ' && clear`
Enter
Sleep 500ms
Show

Type "ctrlrun demo | sed '/approved action/q' | slow"
Sleep 700ms
Enter
Sleep 16500ms
