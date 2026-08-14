<div align="center">

<a href="https://memoket.ai/"><img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/kite-banner.png" width="830" alt="Memoket × KITE: the memory inside Memoket"></a>

### Follow the thread, not the nearest match.

KITE by Memoket turns conversations into structured, source-backed facts, then answers questions
with the exact moment they came from attached.

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

## 💡 Why KITE by Memoket

Most AI memory turns every sentence into a cloud of numbers and hands back whatever
lands closest to your question. It always returns *something*, even when the answer
was never there, and it cannot show you *why*.

KITE by Memoket reads what a question means, not how it sounds.

> **"Who's the decision-maker on the Henderson account now?"**
>
> Nothing in memory says *decision-maker*:
>
> - *March.* Marcus: "I'll sign off on Henderson."
> - *June.* The new VP of Ops: "Dana owns Henderson from here."
>
> Both were filed under the same topic when they were written. Then *now* sorts them
> by date. Similarity has no notion of *now*: nearest-match memory rates March just as
> relevant and answers **"Marcus"**, with nothing to tell you it is wrong. KITE answers
> **"Dana"**, with the June line attached.

|  | Nearest-match memory | 🪁 KITE by Memoket |
|---|---|---|
| **Finds** | whatever *sounds* like the question | the facts that *answer* it |
| **When it was never said** | returns something anyway | comes back empty, and says so |
| **Why this answer?** | a similarity score | a readable plan, with who said it and when |
| **Runtime stack** | embeddings · vector DB · rerankers | one portable, topic-indexed file |
| **Ask again tomorrow** | depends on the index that day | the same plan walks the same steps |

<img src="https://raw.githubusercontent.com/memoket/memoket-kite/main/assets/string-b.svg" width="100%" alt="">

<a id="how-it-works"></a>

## ⚙️ How It Works

The whole idea in one line: **conversations settle into facts under topics, and a
question finds its topic and pulls that topic's memories, receipts included.**

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

## 📊 Benchmark Results

KITE by Memoket posts the top overall score on both public long-conversation memory
benchmarks. It is the only system up there that uses no vectors, and it reads less
context than any capable rival.

<div align="center">

| Benchmark | Overall Accuracy | Avg. Reader Context |
|---|---:|---:|
| **LoCoMo** | 🥇 **93.51%** | 1.51k tokens |
| **LongMemEval-S** | 🥇 **85.60%** | 1.65k tokens |

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
to a cited answer. Integrations across the agent ecosystem (Claude Code, Codex,
Cursor, OpenCode, and more) are next on the roadmap.

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
