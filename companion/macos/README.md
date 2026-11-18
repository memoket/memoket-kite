# KITE Twin for macOS

KITE Twin is a floating pixel character. Click it to open the controls, drag
it to reposition it, and use its context menu or the menu bar icon to exit.
The character remembers its desktop position.
The companion works with an ordinary KITE workspace and its existing memory
viewer. KITE holds source-backed memory and evidence for the user profile. Codex
or Claude Code selects and carries out agent work. The desktop integration
owns capture, notifications and writing into local editors. The current
native integration targets macOS; the memory and agent interfaces do not
depend on AppKit.

## Build and launch

Use macOS 15 or later, Apple command-line development tools, and a checkout
with the KITE `agent` extra installed.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[agent]'
bash companion/macos/build.sh artifacts/kite-twin/local
open -a "$PWD/artifacts/kite-twin/local/KITE Twin.app" --args \
  --kite-tool "$PWD/.venv/bin/kite-tool" \
  --workspace "$HOME/.local/share/kite/twin" \
  --project "$PWD"
```

In Twin settings you can use a different KITE workspace and project. Use a
workspace with sources you intend to use with the selected host. An editable
checkout or a plugin bundled with a wheel built from this checkout includes
the `kite-tool twin` commands. With such a plugin, put the companion in
`~/Applications` or `/Applications` and ask KITE to open the desktop Twin.
The launcher accepts an explicit workspace path and `KITE_TWIN_APP` for a
companion built in another directory.

When testing macOS permissions across updates, build with the same signing
identity each time. Set `KITE_CODESIGN_IDENTITY` to an installed code-signing
identity before running `build.sh`. macOS can require a fresh grant when
switching from an ad hoc build to a signed build.

## Capture and use memory

Click the sprite to open the six-action wheel. Click the sprite in the
center again to close it. The separate Overview button
shows the saved source count and what unlocks the sprite's next
stage. The detailed panel has Overview, Memory, Task and Reply tabs. The wheel
also opens the project's saved skills directly.

Use **Settings → Display Language** to switch the companion between Chinese
and English. The memory graph opens in the selected language. New Twin
captures generate an English display alongside the original text and
citations. The Chinese graph displays each memory in its original language.

In the wheel or Memory tab, press **Command–O** to import a file,
**Shift–Command–S** to take a screenshot, or **Shift–Command–R** to start or
stop a recording. **Command–1 through 4** switch among the detailed tabs.
**Escape** closes the panel. Settings lists these shortcuts.

- **Screenshot** uses the macOS selection overlay, then runs Vision OCR
  locally. Press Escape or click Cancel to close the selection overlay.
  Preview the original image and extracted text before saving. Add a short
  description when the image has no readable text.
- **Document** accepts UTF-8 text and Markdown, text PDFs, and formats
  supported by macOS `textutil` such as DOCX and RTF. A document can be dragged
  onto the open panel. PDF segment IDs include page and line numbers.
- **Import from Memoket** uses the official Memoket CLI. Choose Import from
  Memoket in Memory to load recordings using an existing CLI login. If the
  session has expired, sign in again through your browser. With Node.js
  available, Twin can install the
  CLI in its own application support folder. Select a recording to preview
  the transcript. Saving imports the transcript with speaker and time
  references. Opening the list or preview leaves the KITE workspace untouched.
- **Recording** uses ScreenCaptureKit for system audio and the microphone.
  It saves audio tracks and discards video. Start and stop the capture from
  Twin and grant the relevant macOS permissions. Select an existing audio
  file through the document picker as another option. Stop recording to
  play it back, transcribe it, and save it as memory. Local transcription uses
  `ffmpeg`, `whisper-cli`, and a model selected in Settings. A model is needed
  when transcribing.
- **Overview** shows relevant changes and their available next action.
  In Settings, choose a watch subject and the task that should follow a new
  decision. When the KITE workspace advances, Twin uses native symbolic
  retrieval to find new matching original lines. The sprite shows a bubble
  with the evidence and the configured task. A dismissed bubble is still
  available in the overview until the user reviews that item.
- **Task** shows the original evidence and the editable request before
  handing the task to your chosen host. Codex opens a new chat in
  the project and pre-fills the task with local commands for the cited turns.
  Review and send it there. The Claude desktop route copies the task and
  project to the clipboard. Paste it in the selected project in Claude Code.
  Paste a reviewed result back into Twin if you want to save the workflow.
- **Reply**: open the specific message's reply, then select the original
  message body. The macOS clients use Control–Option–R (or **用 KITE 起草回复**
  in Services). The Gmail, Slack and Feishu web clients use the
  [browser extension](../browser/README.md). Drafting opens the same Twin
  panel. The options apply to the next draft immediately; saving a default is
  separate. A subject uses KITE's typed topic lookup and can fall back to a
  text match. Twin keeps evidence out of the outgoing draft.
  **填入原应用回复框** requires the same message, reply editor and account or
  application context. For native apps it also requires accessibility
  support for inserting text at the caret. If a client doesn't expose these
  signals, the draft stays in Twin. Send in the original app when ready.
- **Editor hints** can be enabled in Settings with macOS Accessibility
  authorization. KITE shows the feature toggle and the permission status in
  Settings. Twin checks the foreground Slack or Feishu application
  and whether focus rests in a writable multiline editor. In supported
  browsers it additionally requires the focused web area to expose the
  `https://mail.google.com` origin. When neither signal is available, the
  reply shortcut is still available. Hints use focus and editor metadata.
  Twin reads the selected message when explicitly asked to draft a reply.
  Repeated focus on a channel is throttled.
- **Save as Skill** asks the host to propose reusable steps from the selected
  result. Review and edit them before confirming a new `SKILL.md` in the
  current project's `.agents/skills` or `.claude/skills` directory. The same
  name is never overwritten. Open Skill from the wheel or Task tab and attach
  the current subject and instructions for that run.

All four inputs enter the same [twin-ingestion definition](../../pipeline_defs/twin-ingestion.yaml).
When you choose **构建记忆**, Twin sends the previewed text to the chosen CLI:
Codex uses GPT-5.6 Terra with low effort, and Claude Code uses the `haiku`
alias. OCR and transcription are local. KITE runs `normalize → annotate →
commit → verify`, and stores Topic, Entity and Facet indexes and original
citations without creating facts. Before committing, KITE organizes the topic
labels from the entire source, using one label for equivalent names and a
shared parent for distinct subtopics. The CLI must be installed, signed in and
able to use the requested model. A failure leaves the capture available for
retry. Previewing Memoket recordings does not import them. Previous source-only
captures can be selected and indexed again.

Open **我的画像** from Overview or Memory. It updates automatically when incoming source evidence
supports a new user trait. The profile presents an abstract description and
its original citation. For a Memoket capture, Twin uses the participant marked
as the signed-in user and matches that participant to an unambiguous speaker
in the selected transcript. For other captures, set your speaker names under
**设置 → 采集与外观** or mark a note as your own statement to identify supporting
turns. Edit a profile field directly or
restore its automatically generated value. Communication and relevant work
habits can guide replies. Work habits, preferences and background can guide
tasks. The current request takes priority. Profile changes can update the
sprite's visual details. Deleting a source removes its profile evidence.
A personal edit remains in the workspace until you restore the generated
value. You can disable profile-driven appearance independently.

Original files and profile evidence are stored by revision. Open an original
from a private temporary copy. Twin does not save agent sessions to KITE.
