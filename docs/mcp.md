# Local MCP memory with Claude Code

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
cp examples/data/mcp_memory.xml artifacts/claude-memory.xml
export OPENAI_API_KEY="your-provider-key"
# Optional, for another OpenAI-compatible provider:
# export OPENAI_BASE_URL="https://your-provider.example/v1"
```

For a published version containing this integration, the package install is
`python -m pip install "memoket-kite[mcp]"`. Normal `pip install memoket-kite`
still has no mandatory runtime dependencies. The optional extra uses the
[official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk),
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
