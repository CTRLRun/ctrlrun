# The README's animation. Regenerate from the repository root whenever the demo's output
# changes:
#
#     PATH=.venv/bin:$PATH vhs docs/assets/demo.tape
#
# What it records is `ctrlrun demo`'s first scenario, byte for byte, and it stops on the line
# that matters: `remote refund calls: 1`. The demo prints all five scenarios without pausing,
# so the command is piped through `sed` to end after that line — and the pipe is typed in the
# open, because a recording that hid how it was made would be the wrong kind of demo for this
# project. `docs/assets/demo.expected.txt` holds the lines the recording ends on, and
# `tests/test_readme_assets.py` asserts they are lines the demo prints and the README quotes.

Output docs/assets/demo.gif

Set Shell bash
Set FontSize 16
Set Width 1120
Set Height 300
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms
Set PlaybackSpeed 1.0

Hide
Type "cd $(mktemp -d) && clear"
Enter
Sleep 500ms
Show

Type "ctrlrun demo | sed '/remote refund calls: 1/q'"
Sleep 600ms
Enter
Sleep 3s
