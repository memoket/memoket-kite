# KITE for Codex desktop

Import meeting notes and transcripts from a Codex chat, then ask about past
decisions, owners, and timelines. KITE links answers to the original quotes.

## Install

The plugin runs on macOS and Linux with Bash. From a terminal with the
[Codex CLI](https://learn.chatgpt.com/docs/codex/cli) available, add the Memoket
plugin directory once:

```sh
codex plugin marketplace add memoket/memoket-kite --ref codex/agent-native-memory \
  --sparse .agents/plugins --sparse plugins/kite
```

If you already opened the KITE checkout as a Codex project, its local Memoket
directory is also available from that project. In Codex desktop, open
**Plugins**, select **Memoket**, open **KITE**, and click **Install**. If the
directory is missing after adding it, restart the desktop app and reopen
**Plugins**. Start a new chat in the project whose meeting memory you want to
use.

The first run needs network access to fetch the pinned KITE runtime and, when
needed, [uv](https://docs.astral.sh/uv/) and Python 3.12. The launcher reuses an
existing uv installation or installs a private copy in the user cache. Later
commands run the installed KITE runtime directly. The sample and local KITE
commands need no separate model API key. If Codex prompts for permission to
download the runtime or write to the local memory directory, allow the specific
operation.

## Try it in a chat

1. **Demo.** Ask “Run the KITE demo.” KITE imports two sample meetings into a
   temporary workspace and reports the confirmed checklist decision, its owner,
   and the quotes that support it.
2. **Import.** Attach a UTF-8 text or Markdown transcript, a Memoket export,
   or an SRT/VTT caption file. Ask “Use KITE to import this transcript and
   list the confirmed decisions and owners.” Codex reads and annotates the
   selected text, and KITE verifies the cited segments and stores the memory.
3. **Query.** In a new chat in the same project, ask “Use KITE to find what we
   decided about onboarding and cite the original meeting lines.” Use a topic
   from your imported transcript.

By default, project memory lives in
`${XDG_DATA_HOME:-$HOME/.local/share}/kite/codex/` under an ID derived from the
project path. Git worktrees of the same checkout share memory. To use a memory
you already created with `kite-tool`, tell Codex its exact workspace directory.
Removing the plugin leaves the memory files on disk. Codex receives the source
text it reads for annotation. The KITE CLI itself stores and queries the
workspace locally.

## Refresh

Refresh the Memoket directory with `codex plugin marketplace upgrade memoket`,
then reopen the desktop app to load changes. When updating the plugin in this
repository, keep `plugin.json` and `.codex-plugin/plugin.json` in sync and pin
the launcher to a public KITE commit whose CLI and workflow contracts match the
bundled skill.

Run `python3 tests/packaging/codex_smoke.py` from a checkout to test marketplace
discovery, installation through Codex's app server, and a transcript import and
query from the installed copy.
