# LoCoMo-ASR dataset schema

Each file is a JSON array of LoCoMo samples. Upstream structure and ordering are preserved. LoCoMo-ASR adds exactly two generated fields to every dialogue turn: `asr_text` and `wer`.

## Sample

| Field | Meaning |
|---|---|
| `sample_id` | Upstream LoCoMo conversation identifier. |
| `conversation` | Speakers, timestamped sessions, and dialogue turns. |
| `qa` | Upstream questions, answers or adversarial answers, categories, and evidence. |
| `event_summary` | Preserved upstream annotation. |
| `observation` | Preserved upstream annotation. |
| `session_summary` | Preserved upstream annotation. |

The JSON does not contain a `qid` field. Use `(sample_id, zero-based QA array index)` as a stable QA identity for a fixed file.

## Conversation and turns

`conversation` contains `speaker_a`, `speaker_b`, session arrays named `session_<number>`, and corresponding timestamp fields named `session_<number>_date_time`.

A turn may contain these upstream fields:

- `dia_id`: dialogue identifier, unique within one sample but not across the full dataset;
- `speaker`: speaker name;
- `text`: clean upstream dialogue text;
- `img_url`: optional list of HTTP(S) references or embedded `data:image/...` values;
- `blip_caption`: optional textual image caption;
- `query` and `re-download`: optional upstream image-retrieval metadata.

The release preserves those upstream values. External URLs and embedded media are untrusted third-party content and must not be fetched automatically.

LoCoMo-ASR adds:

| Field | Type | Meaning |
|---|---|---|
| `asr_text` | string | Whisper transcript for this tier; exactly equal to `text` in the clean view. An empty string is a valid ASR output. |
| `wer` | finite number | Word error rate against `text`, rounded to four decimal places for storage. |

WER is word-level Levenshtein distance divided by the normalized reference length. Normalization lowercases and retains only ASCII letters, digits, and apostrophes inside whitespace-separated tokens. An empty normalized reference is assigned `0.0`. Insertions can make WER greater than `1.0`.

## QA annotations

A QA item may contain:

| Field | Meaning |
|---|---|
| `question` | Question presented to the answering system. |
| `answer` | Gold answer for answerable categories when present; it can be a string or integer upstream. |
| `adversarial_answer` | Upstream adversarial annotation when present. |
| `category` | Integer category ID from 1 through 5. |
| `evidence` | Upstream evidence strings when present. |

Optional fields may be absent. Evidence is scorer-side metadata; consumers must not assume every evidence string is a single resolvable `dia_id`.

## Leak-safe system view

Use [`loader.py`](loader.py) to construct a new object instead of passing a raw sample or turn downstream. For the default **LoCoMo-compatible ASR** protocol, the allowlist is:

- session identifier and timestamp;
- `dia_id`;
- `speaker`;
- `asr_text`, renamed to `content`;
- `blip_caption` when present.

The loader fails when `asr_text` is missing or is not a string. It preserves `asr_text == ""` and never falls back to `text`, strips content, normalizes text, or downloads media.

Fields outside memory ingestion, retrieval, routing, and answer prompts include `img_url`, `query`, `re-download`, clean `text`, `wer`, `answer`, `adversarial_answer`, and `evidence`. An `asr_text`-only experiment may omit `blip_caption`, but must be reported as a separate ablation.
