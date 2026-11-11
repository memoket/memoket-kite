# KITE for Codex desktop

Recall what your team decided, who owns it now, and why the plan changed.
KITE gives Codex the original evidence when it uses past decisions in a new chat.

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

1. **Demo.** Ask “Show me how KITE remembers a changed release decision.” KITE
   reads three sample meetings from a temporary workspace and shows the previous
   decision, the current decision, a later proposal, and a memory record you can
   check against the original quote.
2. **Import.** Attach a UTF-8 text or Markdown transcript, a Memoket export,
   or an SRT/VTT caption file. Ask “Use KITE to import this transcript and
   list the confirmed decisions and owners.” Codex reads and annotates the
   selected text, and KITE verifies the cited segments and stores the memory.
3. **Act on the decision.** In a new chat in the same project, ask “Use KITE to
   draft my next steps based on the latest release decision and its owner.
   Explain what changed and cite the handoff.” Use a topic from your meetings.

By default, project memory lives in
`${XDG_DATA_HOME:-$HOME/.local/share}/kite/codex/` under an ID derived from the
project path. Git worktrees of the same checkout share memory. To use a memory
you already created with `kite-tool`, tell Codex its exact workspace directory.
Removing the plugin leaves the memory files on disk. Codex receives the source
text it reads for annotation. The KITE CLI itself stores and queries the
workspace locally.

## View the memory graph

Ask Codex “Open my KITE memory graph.” KITE opens a local browser page on
`127.0.0.1`. Drag to rotate, scroll or use the zoom buttons to zoom, and
click the zoom percentage to reset. Select a memory to see its topic,
state key, source version, and original quotes. Select a topic, state key, or
source revision to see which memories connect to it.

Select a topic to see its stored parents and children and other topics that
share a memory or source revision. Select a memory to see explicit corrections
and peers that share its state key, original line, or source revision. Dashed
edges show shared evidence or state. The detail panel names the basis for each
relationship.

The page reads the current workspace when opened or refreshed. The server
listens only on this machine and stops after an hour without a request. You
can start it from a terminal with `kite-tool view` if your Codex environment
restricts local ports.

## Refresh

Refresh the Memoket directory with `codex plugin marketplace upgrade memoket`,
then reopen the desktop app to load changes. When updating the plugin in this
repository, keep `plugin.json` and `.codex-plugin/plugin.json` in sync and pin
the launcher to a public KITE commit whose CLI and workflow contracts match the
bundled skill.

Run `python3 tests/packaging/codex_smoke.py` from a checkout to test marketplace
discovery, installation through Codex's app server, and a transcript import and
query from the installed copy.
