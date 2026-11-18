# KITE Twin replies for Chrome and Edge

Enable Developer mode in the browser's Extensions page and load the
`companion/browser/extension` directory with **Load unpacked**. Copy the
extension ID shown there. Register the local bridge from the checkout:

```sh
python3 companion/browser/install.py chrome EXTENSION_ID
# Use "edge" instead for Microsoft Edge.
```

The extension can read selected text on Gmail, Slack and Feishu/Lark pages
when you trigger it. Chrome's native messaging host entry admits only the
installation's extension ID. The host exchanges messages with the running
KITE Twin in a private local directory. It deletes the request when the
browser disconnects or the reply ends. The selected desktop agent receives
the selected message to prepare the draft. The extension does not call
mail or messaging send APIs.

1. Open the reply for the message you want to answer. Select the original
   message's text. Click the pinned extension icon or use its shortcut
   (on macOS, Control–Shift–R by default). KITE Twin must be running.
2. Review the draft in Twin. You can change the reply instructions and ask
   for a new draft.
3. Keep the original browser tab selected and click **填入原应用回复框** in Twin.
   Verify the text in the original reply editor, then send it yourself.

The adapter refuses to fill if the page or account changes, the original
message goes away, or the site's markup does not identify one specific,
empty reply editor. If markup for your client variant does not expose the
connection, the draft stays in Twin. The test fixtures represent these
structural requirements; they are not live account validation.
