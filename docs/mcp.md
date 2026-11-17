# KITE MCP adapters

## Read original quotes from a workspace

Install KITE with the `agent,mcp` extras to serve a single existing workspace.
The configured workspace applies to the read-only tools.
The local server reuses its verified Codebook while the database is unchanged.
Each request reads a new SQLite transaction. A commit or file replacement
invalidates the index before the next reuse, and the server releases its read
transaction between requests so an import can commit while it is idle.

- `recall_conversation_evidence` executes a locally compiled KITE plan and
  returns original turns with citations. A question without a plan, receipt
  or citations prepares bounded index hints. An explicit `anchor_quote`
  narrows those hints when all its
  original matches belong to one project. Submit the plan with the returned
  `snapshot` and the same question, project, dates and comparison baseline.
  If the quotation resolves to one project and no dependent query finds
  original turns, the reply offers a small list of its indexed labels.
  Use a relevant label or original wording in a focused follow-up with the
  same receipt. Include the same `since_version` if you are comparing changes.
  Labels describe the scope and require a quoted turn to support a decision.
  Send `known_citations` without a plan to read their original windows.
  Send `after_receipt` and `neighbors` without a plan to read unseen original
  turns around the last leading citations that are still current.
  Direct citation windows accept 0–20 neighboring turns on each side.
  Symbolic plans accept 0–4.
  When a window includes surrounding dialogue, its text labels the requested
  citations or indicates that a receipt already delivered them.
  Use a focused plan when looking for matches beyond those windows.
  When a task supplies known references
  and needs more evidence, pass them as `known_citations` and give the missing
  details to the plan. The tool checks those references and runs the plan in
  the same workspace snapshot, with separate headings and no repeated turns.
  Project and date filters apply to the search plan. Explicit citations use
  their own recording and revision identifiers.
  In a workspace without recorded project assignments, an exact entity name
  supplied as `project` can scope the plan to recordings with a cited
  original mention of that entity. The reply names that basis and gives the
  number of scoped recordings. In a workspace with project assignments, use
  the recorded project name.
  Multi-branch results can include up to two direct references per branch.
  The guide shows original wording matched by required text conditions for
  the leading reference when available. Follow-ups can show that earlier
  wording alongside still-relevant references.
  For multiple branches bound to a project anchor, KITE can reserve a turn
  from another matching recording within the same quote limits. It keeps the
  previously covered branches, recordings and turns matched by multiple
  branches. The trace identifies the turn that received priority.
  When a leading quote has a matching source index label, the tool can
  include a native `Follow-up near` filter and an evidence receipt.
  To find a missing value in that recording, call the same tool with
  `after_receipt` and `follow_up: {"citation":"review#line-12","grep":"30|thirty"}`.
  KITE validates and runs a one-branch symbolic plan with the cited recording
  and index label. The receipt binds this shortcut to the workspace snapshot.
  Recall again after an update. Check that the cited turn establishes the
  subject before reusing it. To look across recordings, pass an explicit plan
  and copy the offered filter into `where.near`. A follow-up on the same
  snapshot and question can also show which of a branch's quoted matches
  share a previously cited and indexed subject, including another leading
  citation from the earlier query. The tool reports up to two native `near`
  checks and identifies original wording that explicitly names each
  subject. The window describes proximity to the indexed subject. Read the
  original turns to determine approval or decision status.
  When `neighbors`
  is omitted, the tool starts with one nearby turn and trims incidental
  adjacent turns above 8,192 original text bytes where possible. Native
  matches, supporting turns, and complete recording scopes keep their context.
  For schedule or decision questions, nearby turns with spoken dates,
  effective dates, or approval status also stay in the reply.
  Set `neighbors` explicitly to request a specific original context window.
  All packs with original quotes include an evidence receipt. To fill a
  specific gap, pass the latest token as `after_receipt` with a focused plan.
  Omit `question` to reuse the receipt's existing retrieval question for the
  same task. Supply a new question when the task changes.
  When the next call supplies no project, quote anchor, date bounds, preparation
  snapshot, staged plan or plan recording filters, it carries over the receipt's project
  or quote anchor and date bounds. KITE applies that scope to the current
  workspace. Use explicit scope to change it.
  Previously delivered direct matches leave room for new evidence within
  the same quote limit. The plan still uses all matches to bind its stages.
  The reply can reference an already delivered leading match of the current
  plan and report complete coverage when its quotes and the receipt together
  cover the selected recordings. A nearby subject can still be quoted when
  a new turn depends on it. A revised turn has
  a new evidence ID, so it can appear
  after a workspace update. Use `known_citations` to reread particular
  references, or omit `after_receipt` to replay all matching quotes.
  Receipts retain up to 4,096 recent turns and expire when the local server
  restarts or evicts an older receipt.
  A complete recording scope notice means every indexed turn in the selected
  recording scope is present. Read the original wording to establish approval
  or change status.
  For a project-bound implementation question, a branch ranking inclusion
  or exclusion wording can reserve one matching turn that reports what was
  included or left out. The reply names that original citation when it fits
  the quote budget. The submitted branch's required constraints still apply.
  When the value and its subject are in separate turns, put the value wording
  in `where.grep` and a known Topic, Entity or Facet code in `where.near`
  with a distance of 1–4 turns. The tool finds the annotation in the same
  recording and returns the supporting original turn with the value.
  For a plain-text plan comparing earlier and current decisions, the tool may
  use a uniquely named index subject to find two explicitly confirmed turns
  in separate recordings. Both turns and their subject evidence must fit in
  the quote budget. The tool names the exact annotated turn that supports
  each decision, so the reader can verify the references. When another
  indexed turn explicitly links both descriptions, the tool quotes that
  turn as separate evidence. If a matched turn questions or distinguishes
  the descriptions, the tool quotes that wording for review. Use the
  original wording to verify what the labels refer to.
  The original submitted plan runs when the comparison is
  unresolved. Set `plan.retry_index_subject` to false to use the submitted
  plan alone.
- `read_conversation_citations` accepts one to eight known citations and
  returns the original turns with optional surrounding context and labels
  the requested citations within that dialogue. A short
  `recording#segment` citation resolves against the active revision. Use a
  revision-bound source line ID for a retained revision, or include the
  `workspace_version` from recall to check that the workspace has stayed on
  the same snapshot.
- `reuse_conversation_query` accepts a trusted saved `symbolic-search` run
  ID. It executes the saved plan against the current workspace with its
  original scope and considers changes since the plan was compiled. It also
  accepts `since_version` for a later comparison baseline. Set
  `include_previous` to compare matched original segment IDs in revised
  recordings with the same segment IDs at the baseline. Earlier turns carry
  exact revision-bound evidence IDs and share the original quote budget.
  Results with original quotes include an evidence receipt. Pass it as
  `after_receipt` to a focused
  `recall_conversation_evidence` call when the saved query leaves a gap.

```bash
kite-mcp --workspace /absolute/path/to/workspace
```

Use the [Codex project setup](codex.md#optional-direct-retrieval-tool) to use
this server in local Codex chats.

## XML memory with Claude Code

`kite-mcp` serves one existing writable KITE XML artifact over stdio:

```text
Claude Code → stdio MCP → local KITE server → Memory public API → local XML
```

The adapter uses `Memory.load`, `remember`, `recall`, and `answer_with_evidence`.
It preserves KITE's retrieval and answer behavior. XML storage is local; the
extraction, query planner, and answer reader still call your configured LLM
provider. Loading memory and listing tools do not require a provider or network.

## Install and configure

Requires Python 3.10+ and Claude Code. In a checkout containing this integration:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[mcp]"
mkdir -p artifacts
test -e artifacts/claude-memory.xml || cp examples/data/mcp_memory.xml artifacts/claude-memory.xml
export OPENAI_API_KEY="your-provider-key"
# Optional, for another OpenAI-compatible provider:
# export OPENAI_BASE_URL="https://your-provider.example/v1"
```

The published `memoket-kite==0.1.0` wheel predates `kite-mcp`. Use this checkout
or install a [CI preview wheel](../examples/agent-workflows/README.md) with the
`mcp` extra. The base library has no mandatory runtime dependencies. The optional
extra uses the [official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk),
`mcp>=2.3,<3`, which supports Python 3.10+, without its CLI extra.

The synthetic seed has an empty timeline and root topics to support extraction.
Copy it once into `artifacts/`; existing memory must not be overwritten when
restarting the server. You can also use any existing writable KITE XML file.
The server rejects missing paths, directories, malformed XML, and paths it
cannot read/write. Atomic updates also require a writable parent directory.

Start directly with:

```bash
kite-mcp --memory ./artifacts/claude-memory.xml
# Equivalent:
python -m memoket_kite.mcp.server --memory ./artifacts/claude-memory.xml
```

It waits for MCP input on stdin. Use Ctrl-C to stop a directly started process.
Configuration errors go to stderr; stdout carries MCP messages.

## Read-only memory

To expose an existing memory for retrieval and evidence-backed answers:

```bash
kite-mcp --memory ./artifacts/claude-memory.xml --read-only
# Offline configuration check:
kite-mcp --memory ./artifacts/claude-memory.xml --read-only --check
```

The server registers only `kite_recall` and `kite_answer`; `kite_remember` is
not registered or listed in `tools/list`. Only read access to the XML file is
required, so a file made read-only with `chmod a-w` works as-is. Recall and
answer leave the XML unchanged and still use the configured LLM provider.
Without `--read-only`, the existing read/write requirements and all three tools
remain the default. `--check` reports `Mode: read-only` or `Mode: read-write`.

To register this mode in Claude Code:

```bash
claude mcp add --transport stdio --scope local kite-readonly \
  -- "$PWD/.venv/bin/kite-mcp" --memory "$PWD/artifacts/claude-memory.xml" --read-only
```

## Register in Claude Code

Run from the checkout root, in the shell where you configured the provider:

```bash
claude mcp add --transport stdio --scope local kite \
  -- "$PWD/.venv/bin/kite-mcp" --memory "$PWD/artifacts/claude-memory.xml"
claude mcp get kite
claude mcp list
claude
```

Claude Code starts the server itself. Absolute executable and memory paths
allow it to start from another working directory. The server inherits provider
environment variables from the Claude Code process; launch Claude Code from
that configured shell. KITE does not load `.env` files automatically or store
provider keys in XML. Restart Claude Code after changing the environment.

Inside Claude Code, run `/mcp` and confirm `kite` is connected with
`kite_remember`, `kite_recall`, and `kite_answer` available. Approve tool use
when Claude Code requests it. The registration and status commands follow the
[Claude Code MCP documentation](https://code.claude.com/docs/en/mcp).

## Two conversations: Tokyo, then Berlin

Use the fresh memory above. Paste these prompts into Claude Code in order.
Each JSON block gives the tool arguments Claude should send. Ask it to show
the returned source IDs and dates rather than answering from chat history.

1. **Store the July conversation.** Tell Claude:

   > Call kite_remember with exactly these arguments and show the result:

   ```json
   {
     "messages": [{"role": "user", "content": "I moved to Tokyo."}],
     "session_id": "move-1",
     "date": "2026-07-26",
     "title": "First move"
   }
   ```

2. **Recall and answer.** Tell Claude:

   > Call kite_recall with the first JSON, then kite_answer with the second.
   > Show the tool answer and its original source ID, date, and content.

   ```json
   {"query": "Where does the user live now?", "limit": 1}
   ```

   ```json
   {"question": "Where does the user live now?", "limit": 1}
   ```

   Expected answer: Tokyo, supported by `move-1L1`, dated `2026-07-26`.

3. **Store the October update.** Tell Claude:

   > Call kite_remember with exactly these arguments and show the result:

   ```json
   {
     "messages": [{"role": "user", "content": "I moved from Tokyo to Berlin."}],
     "session_id": "move-2",
     "date": "2026-10-20",
     "title": "Later move"
   }
   ```

4. **Repeat both calls from step 2.** The answer should now be Berlin, with
   the newer source `move-2L1`, dated `2026-10-20`. Check the returned evidence;
   an answer supported only by the July source has failed the demonstration.

`limit: 1` makes the selected fact easy to inspect. The adapter forwards the
limit; it never sorts facts or applies city-specific logic. Both conversations
remain in the XML. With larger limits KITE may return historical context too.
Model extraction, plan generation, and wording can vary in a live run; inspect
the actual receipts rather than treating the expected answer as a guarantee.

The deterministic regression runs this same flow through MCP with scripted
model responses and real KITE extraction, persistence, query execution, and
evidence packing. It also reloads a new server from the written artifact:

```bash
python -m pip install -e ".[dev,benchmark,mcp]"
pytest tests/public_api/test_mcp.py -q
pytest tests/public_api/test_mcp.py -k temporal_update -s -q
```

Its answer excerpts are:

```text
After move-1: answer = Tokyo; citation = move-1L1; source date = 2026-07-26
After move-2: answer = Berlin; citation = move-2L1; source date = 2026-10-20
```

## Tool contracts and evidence

| Tool | Required arguments | Optional arguments | Structured result |
|---|---|---|---|
| `kite_remember` | `messages`: nonempty array of `{role: string, content: string}` | `session_id`, `date`, `title`: string or null | `status`, `session_id`, `facts` |
| `kite_recall` | `query`: nonblank string | `limit`: positive integer, default 10 | `query`, `facts` |
| `kite_answer` | `question`: nonblank string | `limit`: positive integer, default 10 | `answer`, `citations`, `evidence` |

Roles are nonblank speaker names, not a fixed chat-role enum. Content must be
nonblank. A supplied session ID must start with a letter or digit and contain
only letters, digits, dots, underscores, or hyphens. Dates are valid ISO dates
(`YYYY-MM-DD`). Omitted session IDs and dates use KITE's own defaults; the
response's `session_id` echoes the input (null when omitted), while fact/source
IDs are always those returned by KITE. Reusing a session ID is an error.

Each fact has `id`, `content`, `kind`, `subject`, `topics`, `entities`,
`event_time`, `source_time`, `confidence`, and `sources`. Both recall's `facts`
and answer's `evidence` use this shape. For example, the newer source appears as:

```json
{
  "id": "move-2F1",
  "content": "The user moved from Tokyo to Berlin.",
  "topics": ["places"],
  "sources": [
    {
      "id": "move-2L1",
      "date": "2026-10-20",
      "role": "user",
      "content": "I moved from Tokyo to Berlin."
    }
  ]
}
```

This is an excerpt of the scripted regression output; live fact wording and
topics may vary. No IDs or dates are synthesized by the adapter. Unknown dates
remain null. Empty recall returns `{"query": "...", "facts": []}`. Public
KITE failures become MCP tool errors retaining the exception class name and
message, including provider, query, configuration, and storage errors.

`kite_answer` exposes `Answer.text` as `answer`, preserves `citations`, and
serializes `Answer.evidence`. Evidence records what the reader saw, not a proof
that its answer is correct. As in the public API, a citation may name a raw
line or instance absent from the Fact-only evidence; the adapter does not
invent a matching fact. See [the public API](api.md).

## First-version limits

Use one server process and one writable memory artifact. Calls within that
instance are serialized; other writers and external edits require stopping
and reloading the server. There is no cross-process locking or synchronization.
Remote transport, authentication, authorization, multi-user access, multiple
writable memories, hosted Memoket integration, a broader CLI, and client-specific
examples beyond Claude Code are deferred.
