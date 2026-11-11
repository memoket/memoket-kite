<div align="center">

<a href="https://memoket.ai/"><img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/kite-banner.png" width="830" alt="Memoket × KITE: the memory inside Memoket"></a>

### Give your AI the decisions from meetings it missed.

Recall what your team decided, who owns it now, and why the plan changed.

<a href="https://memoket.ai/"><img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/memoket-website-badge-navy.svg" alt="Memoket website"></a>
<a href="https://discord.com/invite/tFh4nur4Vn"><img src="https://img.shields.io/badge/-Discord-232B3A?style=for-the-badge&logo=discord&logoColor=white" alt="Join Memoket on Discord"></a>
<a href="https://x.com/Memoket_AI"><img src="https://img.shields.io/badge/-X-232B3A?style=for-the-badge&logo=x&logoColor=white" alt="Follow Memoket on X"></a>
<a href="https://www.instagram.com/memoket_ai/"><img src="https://img.shields.io/badge/-Instagram-232B3A?style=for-the-badge&logo=instagram&logoColor=white" alt="Follow Memoket on Instagram"></a>
<a href="https://www.facebook.com/833979799808573/"><img src="https://img.shields.io/badge/-Facebook-232B3A?style=for-the-badge&logo=facebook&logoColor=white" alt="Follow Memoket on Facebook"></a>

</div>

<br>

<p align="center">
  <img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/kite-architecture-flow.png" width="100%" alt="KITE at a glance: conversations become source-linked memory facts, memory settles under a governed topic map, a question compiles to an inspectable plan, and the answer arrives with receipts.">
</p>

## Ask what the team decided

**Who owns the release, and when are we shipping?** In the demo, Ravi owned
it as of November 4 and the plan was Friday. The next meeting assigned Maya and approved
Tuesday because Friday's build failed validation. A later Monday proposal stays
in the history while Tuesday remains the confirmed plan.

KITE stores each claim with its topic, status, original evidence, and date when
known. A new Codex or Claude Code session can use the same memory to answer
questions and act on the team's latest confirmed decisions.

### Install KITE in Codex desktop

Add the Memoket plugin directory once, then open **Plugins → Memoket → KITE →
Install** in Codex desktop. Start a new chat and ask **“Show me how KITE remembers
a changed release decision.”**

```sh
codex plugin marketplace add memoket/memoket-kite --ref codex/agent-native-memory \
  --sparse .agents/plugins --sparse plugins/kite
```

See the [Codex installation guide](docs/codex.md) for first-run setup, import,
and citation examples. Ask **“Open my KITE memory graph”** and choose **All topics**
to see the whole workspace in one rotatable view. Select a topic to see its
parents, children, and other topics that share its memories. Open a memory to
follow its citations to the original lines and source revisions, and inspect
any entities and facets indexed for that memory. Use **Entities** and **Facets**
to browse the workspace's annotations and follow a value to the passages that
support it. The evidence view gives topics, entities, facets, passages, quotes,
and source revisions separate levels. A co-occurrence edge opens the quotes
shared by its endpoints. Use **Sources** to browse the full transcript inventory,
including sources without labels. Original spans can carry `facets` for object,
place, event, and duration. These labels are also searchable with `kite-tool context`.
When `kite-tool view --compare` is given saved comparison snapshots, the viewer
offers both the live graph and the snapshots in one browser tab.

### Try the command line

From a KITE checkout, install the package and try the release decision demo.

```bash
python -m pip install '.[agent]'
kite-tool demo --decision-story
```

The demo shows the prior and current answers, the later proposal, and an actual
memory record with its original quote. It runs locally on three synthetic
meetings without model calls. Add `--save-workspace ./kite-demo` to keep the
sample and ask it again from another process.

```sh
kite-tool --workspace ./kite-demo query --pipeline decision-evolution --topic 'release plan' --text
```

Run `kite-tool demo --text` for the text and Memoket import walkthrough. Use
`kite-tool ingest --input FILE` to import a prepared source and its annotations.
See the [agent quickstart](examples/agent-workflows/README.md) for stage-by-stage
commands, or [add KITE to your project's agent instructions](examples/agent-workflows/README.md#give-your-agent-a-task).
The [operating guide](AGENT_GUIDE.md) describes the workflow and tool contracts.

### Give an agent the original context

Index timestamped transcripts once, then bring cited excerpts into tasks such
as writing a handoff, checking a requirement, or explaining a changed plan.

```sh
kite-tool --workspace ./team-memory index-sources --transcripts ./transcripts
kite-tool --workspace ./team-memory context --question 'What changed in the rollout plan?' --text
```

KITE keeps the source turns, speaker labels, offsets, and exact line IDs in
its local workspace. Context queries return bounded original passages. Use
`--term` to search for a specific phrase, or `--conversation-id` to stay in
one conversation. An
agent can use new terms found in a passage to follow the discussion across
meetings. Source revisions and deletion also update the search index. Use the
[conversation benchmark](benchmarks/agent/README.md#evaluate-original-conversation-context)
to compare this workflow with giving Codex the transcript directory to search.

If a TXT export has recording metadata in separate files, add
`--recording-metadata ./recordings` to the import. For `42_transcript.txt`,
KITE checks `42.json` for a matching `conversation_id` and uses `started_at`
for date-scoped search. Search and the graph display the original recording
clock when available. A timestamp with no offset is labeled as having an
unknown timezone. Reimporting matching source-only transcripts can fill in
missing recording times while preserving their line citations and annotations.
Open `kite-tool view` and choose **Workflows** to inspect committed changes
and recent pipeline stages.

<a id="news"></a>

## 📢 News

- **[2026-11-02]**: 📄 Our paper **[KITE: Retrieval as a Decision Procedure for Long-Term Conversational Memory](https://arxiv.org/abs/2610.41521)** is now on arXiv.
- **[2026-10-26]**: 🎬 Released an **[explainer video](#video)** of how KITE works, from a message to a cited answer.
- **[2026-08-13]**: 🎉 First public release: **[KITE v0.1.0](https://github.com/memoket/memoket-kite/releases/tag/v0.1.0)** is open source under Apache-2.0 and on **[PyPI](https://pypi.org/project/memoket-kite/)** as `memoket-kite`.

## 💡 Why KITE by Memoket

KITE keeps facts about the same decision or assignment together as meetings
accumulate. You can start with an earlier owner, follow the assignment forward,
and verify the handoff in the original conversation. Date filters let you ask
what was known before a change. If the selected evidence leaves a question open
or supports competing answers, the trace surfaces that uncertainty.

The agent CLI stores conversation revisions, their evidence, and a Codebook
index in a local workspace. Once the agent has annotated a conversation, the
index can retrieve its facts and calculate reported states without an embedding
service or a model call.

<img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/string-b.svg" width="100%" alt="">

<a id="how-it-works"></a>

## ⚙️ How It Works

The whole idea in one line: **conversations settle into facts under topics, and a
question finds its topic and pulls that topic's memories, receipts included.**

<a id="video"></a>

https://github.com/user-attachments/assets/a5c053fb-36de-4eeb-8d2d-d652792314f4

#### 1 · Message → Fact

A user says, **"I moved to Tokyo on July 26."** KITE by Memoket extracts what happened and
files it under its topics, keeping the original message as evidence:

```xml
<fact t="2026-07-26" kind="event" who="user"
      place="tokyo" event="move" src="chat1L1">
  The user moved to Tokyo on 26 July 2026.
</fact>
<line id="chat1L1" who="user">I moved to Tokyo on July 26.</line>
```

#### 2 · Question → Plan

When the agent asks **"Where does the user live now?"**, KITE by Memoket compiles the
question into a plan you can read, validate, and cache:

```json
{
  "select": "facts",
  "where": {"who": ["user"], "event": ["move"]},
  "pipe": [
    {"op": "sort", "key": "t", "desc": true},
    {"op": "head", "n": 1}
  ]
}
```

#### 3 · Plan → Execution

The executor runs the plan over the topic index. Keywords and associations come
first, then conditions filter down to the one moment that answers the question:

```text
SEARCH facts
FILTER who = "user" AND event = "move"
SORT BY event_time DESC
TAKE 1
```

The same validated plan always follows the same steps over the same memory, with no
embeddings and no vector database anywhere.

#### 4 · Evidence → Answer

```text
Answer: Tokyo
Source: chat1L1, "I moved to Tokyo on July 26."
```

### Key Features

<table>
<tr>
<td width="33%" valign="top"><h4 align="center">🚫 No vector stack</h4>
No embeddings, no vector database, no layers to embed, sort, and re-rank every result.</td>
<td width="33%" valign="top"><h4 align="center">🗂️ Structured facts</h4>
Conversations crystallize into typed, dated, topic-indexed memory you can open and read.</td>
<td width="33%" valign="top"><h4 align="center">🔍 Inspectable queries</h4>
Every question becomes a symbolic plan, revealing exactly how each answer was matched.</td>
</tr>
<tr>
<td valign="top"><h4 align="center">🕐 Time-aware retrieval</h4>
People, events, dates, and updates handled directly, so <em>"now"</em> means now.</td>
<td valign="top"><h4 align="center">🧾 Shows its work</h4>
Evidence links straight back to the moment it came from: who said it, when, and the actual words.</td>
<td valign="top"><h4 align="center">🤷 Knows what it doesn't know</h4>
When something was never said, the search comes back genuinely empty, and KITE says so.</td>
</tr>
</table>

<img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/string-a.svg" width="100%" alt="">

<a id="quick-start"></a>

## 🚀 Quick Start

### 1. Install and configure

```bash
python -m pip install memoket-kite
export OPENAI_API_KEY="sk-..."
mkdir -p artifacts
curl -fsSL \
  https://raw.githubusercontent.com/memoket/memoket-kite/main/examples/data/demo_codebook.xml \
  -o artifacts/quickstart.xml
```

Using another OpenAI-compatible provider? Set `OPENAI_BASE_URL` as well.

### 2. Remember

Turn a conversation into structured, source-backed facts:

```python
from memoket_kite import Memory

memory = Memory.load("artifacts/quickstart.xml")
memory.remember(
    [{"role": "user", "content": "I moved to Tokyo."}],
    session_id="session_1",
)
```

### 3. Recall

Compile a question into a plan and retrieve matching facts, each carrying its sources:

```python
from memoket_kite import Memory

memory = Memory.load("artifacts/quickstart.xml")

for fact in memory.recall("Where did the user move?"):
    print(fact.content)
    print(fact.sources)
```

### 4. Answer

Generate an answer from the retrieved evidence and keep the receipts:

```python
from memoket_kite import Memory

memory = Memory.load("artifacts/quickstart.xml")

result = memory.answer_with_evidence("Where did the user move?")
print(result.text)  # "The user moved to Tokyo."
for fact in result.evidence:
    print(fact.content, fact.sources)  # the moments the answer stands on

print(memory.answer("Where did the user move?"))  # just the text
```

Full reference in [docs/api.md](https://github.com/memoket/memoket-kite/blob/main/docs/api.md);
more runnable examples in
[`examples/`](https://github.com/memoket/memoket-kite/tree/main/examples).

<img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/string-b.svg" width="100%" alt="">

<a id="benchmarks"></a>

## 📊 Historical Benchmark Results

These figures come from the recorded reference runs. A corpus audit found two
scored LoCoMo answers that matched examples in the old answer prompt. The
current prompt omits those examples. A new model run is needed to measure the
effect.

<div align="center">

| Benchmark | Overall Accuracy | Avg. Reader Context |
|---|---:|---:|
| **LoCoMo** | **93.51%** | 1.51k tokens |
| **LongMemEval-S** | **85.60%** | 1.65k tokens |

</div>

All systems are evaluated under one shared protocol, with `gpt-4.1-mini` as the
common reader and judge; the exact rubrics are in the
[LoCoMo protocol](https://github.com/memoket/memoket-kite/blob/main/benchmarks/locomo/protocol.py)
and
[LongMemEval protocol](https://github.com/memoket/memoket-kite/blob/main/benchmarks/longmemeval/protocol.py).
Reader context is the mean per-question evidence budget: what the reader actually
gets to see, measured by the same ledger on every question.

Each figure comes from a reference run under the default configuration, with
`gpt-4.1-mini` reading and judging and no ablation flags set. The reproduction
contract pins the dataset revisions, protocols, model IDs, expected row counts,
and reported scores. The provided scripts rebuild the evaluation locally from
the official datasets with a configured compatible LLM endpoint.

<details>
<summary><b>How a local run is recorded</b></summary>
<br>

Every rebuilt run writes a local manifest naming the exact commit, corpus
digest, tokenizer, and plan-cache digest it used. Judging records the answers,
verdicts, and score in a second manifest keyed to the first. A completed run can
validate that seal and recompute its metrics offline; the reference verifier
also checks the manifest-declared paths, counts, and reported aggregate. Details
are in the
[benchmark guide](https://github.com/memoket/memoket-kite/blob/main/benchmarks/README.md)
and
[reproducibility notes](https://github.com/memoket/memoket-kite/blob/main/docs/reproducibility.md);
the benchmark corpora keep their own licences, spelled out in
[`LICENSE-DATA.md`](https://github.com/memoket/memoket-kite/blob/main/LICENSE-DATA.md).

</details>

<img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/string-a.svg" width="100%" alt="">

## 🔗 Integrations

KITE by Memoket is a plain Python library: three calls take you from a raw conversation
to a cited answer. The optional [local MCP server](docs/mcp.md) exposes those calls
to Claude Code over stdio, with source IDs and dates attached to evidence. Install
the MCP extra from this checkout and check it against the sample XML file.

```bash
python -m pip install '.[mcp]'
kite-mcp --memory examples/data/mcp_memory.xml --read-only --check
```

Follow the [Claude Code example](docs/mcp.md) to create a fresh sample memory,
register the server, and trace a two-conversation update from Tokyo to Berlin.
Normal library installation remains dependency-free.

Want yours first? [Tell us on Discord](https://discord.com/invite/tFh4nur4Vn).

<p align="center">
  <img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/memoket-anchor.png" width="100%" alt="the string ties into Memoket">
</p>

<p align="center">
  <a href="https://apps.apple.com/us/app/memoket/id6758686146"><img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/badge-appstore.png" height="50" alt="Download on the App Store"></a>
  &nbsp;&nbsp;
  <a href="https://play.google.com/store/apps/details?id=com.ssheng.memoket"><img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/badge-googleplay.png" height="50" alt="Get it on Google Play"></a>
</p>

## ✨ Experience KITE with Memoket

**The memory you just read about ships today.** KITE is the algorithm inside
every Memoket: the same topic-filed facts, the same line back to the source,
running on your own device. Ask it *"didn't this client say their fiscal year ends
in March?"* months later, and it surfaces the exact moment: who said it, when, and
in their words.

Memoket captures memories; KITE is the memory itself.

https://github.com/user-attachments/assets/685ac90d-95ab-4114-a7d3-c22ec3db94a0

<p align="center">
  <a href="https://memoket.ai/pages/use-cases"><strong>See more of Memoket in action →</strong></a>
</p>

## 🤝 Community

- 💬 [Discord](https://discord.com/invite/tFh4nur4Vn) for questions, ideas, and integration requests
- 🐛 [Issues](https://github.com/memoket/memoket-kite/issues) for bugs and feature requests
- 🔧 [Contributing guide](https://github.com/memoket/memoket-kite/blob/main/CONTRIBUTING.md) · 🔒 [Security policy](https://github.com/memoket/memoket-kite/blob/main/SECURITY.md)

## 📖 Citation

A technical report is in preparation; BibTeX will land here with the arXiv
release. Until then, please cite this repository.

## 📄 License

KITE by Memoket is released under the
[Apache License 2.0](https://github.com/memoket/memoket-kite/blob/main/LICENSE).

<br>

<p align="center">
  <em>Like its name, KITE by Memoket stays light enough to fly, always on a line back to the truth.</em> 🪁
</p>
