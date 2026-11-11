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

For a directory of timestamped TXT transcripts with `Speaker …` labels, ask
“Index this directory in KITE so you can use the original quotes in future
tasks.” This import builds the local source index without extracting facts.
In a later chat in the same project, ask “Use KITE to draft a handoff from our
discussions about the release. Cite the original lines.” Codex can retrieve
the relevant turns and search again for a newer confirmation if needed.

By default, project memory lives in
`${XDG_DATA_HOME:-$HOME/.local/share}/kite/codex/` under an ID derived from the
project path. Git worktrees of the same checkout share memory. To use a memory
you already created with `kite-tool`, tell Codex its exact workspace directory.
Removing the plugin leaves the memory files on disk. Codex receives the source
text it reads for annotation. The KITE CLI itself stores and queries the
workspace locally.

## View the memory graph

Ask Codex “Open my KITE memory graph.” KITE opens a local browser page on
`127.0.0.1`. Choose **All topics** for the full map, or select a topic in the
sidebar to expand its children. Drag to rotate, scroll or use the zoom buttons
to zoom, and click the zoom percentage to reset.

Open **Entities**, **Facets**, or **Sources** beside the topic tree. In the
evidence view, topics, entities, facets, passages, quoted lines, and source
revisions occupy separate levels. Select a passage to see its original quotes,
or follow a co-occurrence connection to see the passage shared by two entities
or facet values. **Sources** includes transcripts with no annotations. When the
workspace has an original-text index, enter a question in the search field
to see the compiled terms and cited passages, or start the viewer with
`kite-tool view --term PHRASE` for a phrase search. Open **Workflows** to see
committed imports, updates, merges, and deletions alongside recent pipeline
stages. An existing workspace starts recording new changes on upgrade.
Choose **View change** to inspect retained source changes or the recorded topic
merge. For a revised source, **Compare before and after this commit** opens the
two original versions from that commit. Deletion entries show counts and links
to the workspace versions on either side.
On a completed `context-search` run, choose **Open saved search evidence** to
see the original compiled terms and cited passages without running the search
again. After a permanent deletion, reopening or refreshing an old saved search
link shows that the result is unavailable.
For a new search, add `--save` to `kite-tool context --question "$QUESTION" --text`.
Use the returned run ID with `kite-tool view --run RUN_ID` to open its evidence.
Select a historical source revision and choose **Compare with current version**
to see the original lines that changed, with citations for both versions.
Enter a workspace version next to the search question, name it in the question
as `workspace v12`, or select **Search this version** from **Workflows**. Older
workspaces start tracking from the first write or integrity check after upgrade.
Open **Recording dates** beside the search field to set an inclusive meeting
window. You can combine it with a workspace version. The search detail panel
groups the returned passages by recording day and opens each cited passage in
the graph. Effective dates and approval status are in the original wording.
Open **Compare** next to search to add a second question. Each question shows
its own compiled phrases and original passages under the shared date and
version filters.

To browse a live graph alongside saved before-and-after snapshots, start the
viewer with `kite-tool view --compare PATH`. The JSON file contains an `examples`
array. Each example has `name`, `title`, `description`, `before`, and `after`.
Each snapshot has `version`, `topics`, `passages`, `revisions`, and `evidence`,
with `entities` optional. Switch between **Memory graph** and **Compare changes**
in the same tab. Comparison pages show the supplied snapshots.

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
