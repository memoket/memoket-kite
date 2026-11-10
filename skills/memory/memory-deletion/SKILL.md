---
name: memory-deletion
description: Preview, authorize and verify permanent workspace deletion.
---

Use the source namespace with an optional `conversation_id` or an explicit
`conversation_ids` list. Run the preview stage and show the user the exact selector,
revision and fact counts, IDs without stored revisions, and artifact impact.
Use its token with `--approve` only after the user authorizes that preview. An old token cannot authorize
a different snapshot. If memory changes, show a fresh preview and request fresh approval.
Verify the receipt and use a new read run to check that the target is absent.
Manage separately exported copies, backups and logs under the user's retention policy.
