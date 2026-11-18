# Kite by Memoket for Codex desktop

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
**Plugins**, select **Memoket**, open **Kite by Memoket**, and click **Install**. If the
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

## Use memory in a task

For tasks that reference prior conversations, the installed skill can prepare
KITE's symbolic plan in the current Codex chat and execute it on the local
workspace. No separate provider API key is needed for this path. Codex sees
the selected index vocabulary and the evidence returned by KITE.
In an installed project with meeting memory, a task like “Update the release
checklist to reflect the last approved rollout plan” can use that skill
directly in chat. To select it explicitly, ask “Use KITE to update the
release checklist from the last approved rollout plan.”
When the task gives a subject and search clues, the skill submits the question
and plan together in one command, against one workspace snapshot. If it needs
to inspect the scope or vocabulary, `symbolic --prepare QUESTION --brief`
returns index hints and a snapshot token to submit with the plan. Omit
`--brief` to inspect the full compiler prompt.

When a workspace contains several projects, set `KITE_SYMBOLIC_PROJECT` in
the environment that runs KITE, or use `symbolic --project NAME` with the
project name recorded at import. The selection applies to both preparation and
retrieval, so subsequent task requests can refer to decisions without repeating
the project name. Use the same selection when submitting a prepared plan.
In conversation JSON, import the stable project name in `metadata.project`
and familiar names in `metadata.project_aliases`. For example, set the alias
`Prism migration` for `migration-2026`. A question mentioning Prism migration
can then find original turns across that project's recordings. Alias scope
appears in the result. If the names match multiple projects, KITE keeps the
workspace scope open. Explicit project and recording names take precedence.
When a plan is saved against a uniquely matched alias, reuse keeps the
resolved project.

```sh
kite-tool file-source meeting.txt --format text --source-id kickoff --split-lines \
  --project migration-2026 --project-alias "Prism migration"
```

Include the returned source in the normal `index-sources` batch.

### Optional direct retrieval tool

Bind an existing KITE workspace to a trusted Codex project in its
`.codex/config.toml`. Use the absolute paths to the KITE launcher and the
workspace directory that contains `kite.sqlite3`.

```toml
[mcp_servers.kite_context]
command = "bash"
args = ["/absolute/path/to/scripts/kite.sh", "workspace-mcp", "/absolute/path/to/workspace"]
startup_timeout_sec = 120
```

Start a new local chat after configuring the server. Codex can call
`recall_conversation_evidence` with an inline symbolic plan and receive the
original quotes and citations in one tool result. The server reads the
configured workspace on each call, so later imports and updates are visible.
For a first lookup that needs index labels or a quoted project clue, call
`recall_conversation_evidence` with the question and scope and omit `plan`.
Pass `anchor_quote` if a verbatim clue identifies the project. When all
matching recordings belong to one project, KITE returns index hints for it.
Use the returned Topic, Entity and Facet codes to write a symbolic plan,
then send the plan with the same question and scope and the returned
`snapshot`. Include the quote again if the plan should follow that project.
If the workspace changes between calls, prepare again.
To verify an already cited turn or its replies, call
`read_conversation_citations` with `citations` and `neighbors`. A batch
deduplicates overlapping context and keeps each requested turn whole.
When a task also needs new evidence, pass the existing references as
`known_citations` to `recall_conversation_evidence` and use the plan for the
remaining details. Both read the same snapshot.

For recurring work, save a symbolic query once with
`kite-tool symbolic --input PLAN.json --save RUN_ID`. In the project's
guidance, record the query ID and which tasks should reuse it. Codex can
call `reuse_conversation_query` with that ID in a fresh chat. KITE searches
the latest workspace within the saved scope and reports changes since the
stored plan's version. The result includes the compilation version and
current evidence.
When a task needs the wording before a recording was edited, pass
`include_previous: true` in the same MCP call. The CLI equivalent is
`kite-tool symbolic --reuse RUN_ID --include-previous --text`. KITE checks the
same segment IDs at the comparison version and shows earlier turns with exact
revision-bound IDs. If earlier turns do not fit, the text shows up to four IDs
for `read_conversation_citations`. For added or removed segment IDs, use the
[source revision comparison in the memory graph](#view-the-memory-graph).

The server needs no separate model key. The first startup installs an optional runtime
with the MCP SDK. If the download exceeds the configured startup timeout,
prepare the runtime once in a terminal, then start a new chat.

```sh
bash /absolute/path/to/scripts/kite.sh workspace-mcp /absolute/path/to/workspace </dev/null
```

For routine tasks, add this guidance once to the project's `AGENTS.md`.

> If the KITE recall_conversation_evidence MCP tool is available, call it to
> find the recorded decisions needed for the task. For a task that also
> supplies citations, include them as known_citations if offered. Use
> read_conversation_citations to check known quotes that need no additional
> search. Use original sources when the tools leave an evidence gap.

Keep using the KITE skill to import sources, manage memory, and view the graph.
See [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)
for project and desktop setup.

## Load evidence before the task

For a project that already uses KITE, you can configure an optional local
Codex [UserPromptSubmit hook](https://learn.chatgpt.com/docs/hooks#userpromptsubmit).
Run a normal KITE command once to install its runtime. Add this to the trusted
project's `.codex/config.toml`, using the absolute path to `codex_context.py`
beside the installed skill's `scripts/kite.sh`.

```toml
[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = 'python3 -I "/absolute/path/to/scripts/codex_context.py"'
timeout = 8
```

Review and trust the hook in Codex Settings → Hooks or with `/hooks` in the CLI.
Start the first line of a request with `KITE:` or `KITE：`, followed by the
question you want to look up. Put implementation or formatting instructions
on subsequent lines.

```text
KITE: What did we decide about rollout and who owns the handoff?
Update the release checklist and cite the original discussion.
```

The hook searches the current project's KITE workspace locally with
`context --compact` and includes original quotes and citations in Codex's
model input for that turn. If the result exceeds the context budget, the
hook retries once with a smaller quote budget. Ordinary prompts, empty
results, and unavailable runtimes let the chat continue. Use the ordinary
KITE context command for a follow-up or add `--save` to open the compile
and search evidence in the graph.

To use KITE's symbolic engine before a task, start the request with
`KITE symbolic:` and configure `KITE_SYMBOLIC_MODEL` and the model provider
in the hook's environment. Set the hook timeout to 60 seconds or more.
Use this mode with a provider authorized to receive the question and
workspace index vocabulary. For a local run from an existing plan, use
`kite-tool symbolic --input PLAN.json`.

For recurring work, ask Codex to save its symbolic plan. A plan generated in
the current chat can be searched and saved without a separate model provider.
```sh
kite-tool symbolic --input plan.json --save release-schedule --text
kite-tool symbolic --reuse release-schedule --text
```

To compile with a separate provider, start a `symbolic-search` run with a
stable run ID, advance its `compile` stage, then inspect the evidence from
`search`.
```sh
kite-tool start symbolic-search --run-id release-schedule --input request.json
kite-tool stage release-schedule compile --allow-provider
kite-tool stage release-schedule search
kite-tool symbolic --reuse release-schedule --text
```

`request.json` contains the workflow request. For example,
`{"request":{"question":"What was agreed about the release schedule?","model":"gpt-4.1-mini"}}`.
With the submit hook enabled, use the same run ID on the first line of a
later Codex task. Put the implementation request on the next line.

```text
KITE plan: release-schedule
Update the release checklist to match the current decisions and cite the evidence.
```

Each invocation runs the saved plan against the current active original turns
with its saved project and recording window. New or revised recordings in
scope get evidence space based on the changes since compilation. A fixed end
date in the saved plan still applies. Reuse requires no model API call or
provider key. For a changed question, compile a new plan. A permanent workspace
deletion invalidates previous runs, including saved plans.
The symbolic hook executes the plan once. If its rendered quotes exceed the
delivery limit, KITE fits original turns and their references into the
remaining space, giving direct matches priority over adjacent context.
The output says when the delivery limit leaves out some quotes.
The symbolic result reports how many original turns each query or stage
matched and how many appear in the quote pack. Text output calls out empty
queries, unresolved stage bindings, and matches that were left out of the
pack. Use those notices to focus any follow-up on the missing evidence.
Open a saved symbolic search with `kite-tool view --run release-schedule`.
The graph shows the original citations and their linked annotations. Its
replay shows the executed symbolic steps and the indexes those steps matched.
To watch a staged search while it runs, open `view --follow RUN_ID` after
starting `symbolic-search` and before advancing its stages.

## View the memory graph

Ask Codex “Open my KITE memory graph.” KITE opens a local browser page on
`127.0.0.1`. Choose **All topics** for the full map, or select a topic in the
sidebar to expand its children. Drag to rotate, scroll or use the zoom buttons
to zoom, and click the zoom percentage to reset.

Ask “Index these transcripts with KITE and show the import in the graph” or
“Use KITE to find the latest rollout decision and show the search in the
graph.” With that request, Codex opens the graph for the current run before
advancing its stages. After commit, new memory paths glow on the current
graph. Search animates the links to the returned citations. A merge refreshes
the graph and highlights the retained topic. A deletion shows preview counts
while waiting for approval, then refreshes the graph and shows the redacted
receipt. Click the result to open the change comparison, deletion record or
saved evidence. The graph opens on request.

Open **Entities**, **Facets**, or **Sources** beside the topic tree. In the
evidence view, topics, entities, facets, passages, quoted lines, and source
revisions occupy separate levels. Select a passage to see its original quotes,
or follow a co-occurrence connection to see the passage shared by two entities
or facet values. **Sources** includes transcripts with no annotations. When the
workspace has an original-text index, enter a question in the search field
to see the compiled terms and cited passages from `context-search`, or start
the viewer with `kite-tool view --term PHRASE` for a phrase search.
To see the native symbolic execution trace, open a saved `symbolic-search`
run with `kite-tool view --run RUN_ID`. Both views can display the Topic,
Entity, and Facet annotations linked to a citation. The trace identifies
which indexes the search actually used. Open **Workflows** to see
committed imports, updates, merges, and deletions alongside recent pipeline
stages. An existing workspace starts recording new changes on upgrade.
Add `--save` when indexing a transcript directory or a batch JSON file to see
the preview, commit and verification counts under **Workflows**. Open the
linked change to compare each affected source graph and read its original lines.
When following a batch in the graph, replay highlights the affected sources and
their memory paths in the current view. Open **View all changes** to browse the
full batch and compare an individual source.
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
For a missing detail, run `kite-tool context --after RUN_ID --question QUESTION
--compact --text`. KITE skips original lines already delivered by that run and
its predecessors. Add `--save` to open the supplement and its earlier search
from the graph.
Select a historical source revision and choose **Compare with current version**
to see the original lines that changed, with citations for both versions.
Enter a workspace version next to the search question, name it in the question
as `workspace v12`, or select **Search this version** from **Workflows**. Older
workspaces start tracking from the first write or integrity check after upgrade.
Open **Recording dates** beside the search field to set an inclusive meeting
window. You can combine it with a workspace version. The search detail panel
groups the returned passages by recording day and opens each cited passage in
the graph. Effective dates and approval status are in the original wording.
Search subquestions appear on their own layer above the stored topics, entities,
and facets linked to the quoted lines. **Replay search** follows the recorded
paths to those quotes and lights an annotation when the trace identifies it as a
search input. Start `context-search` with `--visualize`, or save a search with
`kite-tool context --explain --save` to record the candidate details for replay.
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
query from the installed copy. Pass `--marketplace memoket/memoket-kite
--ref codex/agent-native-memory --sparse .agents/plugins --sparse plugins/kite`
to test the GitHub directory. Use `--plugin-version` and
`--context-search-version` to check the installed versions explicitly.

To test a checkout in desktop Codex, build its wheel and assemble a local
Memoket directory with that wheel bundled inside the plugin.

```sh
uv build --wheel --out-dir ./artifacts/codex-build
python3 scripts/build_codex_marketplace.py ./artifacts/codex-marketplace \
  --wheel ./artifacts/codex-build/memoket_kite-*-py3-none-any.whl
codex plugin marketplace add "$PWD/artifacts/codex-marketplace"
```

Install **Kite by Memoket** from this directory in **Plugins**. The launcher installs the
bundled wheel into a runtime cache identified by its checksum. A first setup
can still need uv, Python 3.12, and package dependencies. For an isolated
installation check, pass `--marketplace ./artifacts/codex-marketplace` to the
smoke test and set `--context-search-version` to the version in the checkout.

The [bilingual demo guide](codex-bilingual-demo.md) includes the Codex
prompts for Chinese originals, English display, and side-by-side retrieval.
