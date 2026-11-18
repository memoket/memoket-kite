/* Site adapters are deliberately conservative. A generic focused editor is not a target. */
(() => {
  "use strict";
  const edit = '[contenteditable="true"][role="textbox"], [contenteditable="true"][g_editable="true"]';
  const sites = {
    Gmail: {
      message: '[data-message-id]',
      body: '.a3s, [data-message-body]',
      id: ['data-message-id'],
      parent: ['data-reply-to-message-id'],
      composer: '[data-reply-to-message-id]',
    },
    Slack: {
      message: '[data-item-key], [data-message-id]',
      body: '.p-rich_text_section, [data-qa="message-text"]',
      id: ['data-message-id', 'data-item-key'],
      parent: ['data-thread-ts', 'data-parent-message-id', 'data-reply-to-message-id'],
      composer: '[data-thread-ts], [data-parent-message-id], [data-reply-to-message-id]',
    },
    "飞书": {
      message: '[data-message-id], [data-msg-id]',
      body: '[data-message-content], [data-testid="message-content"], [data-testid="message-text"]',
      id: ['data-message-id', 'data-msg-id'],
      parent: ['data-reply-to', 'data-reply-to-message-id', 'data-thread-root-id'],
      composer: '[data-reply-to], [data-reply-to-message-id], [data-thread-root-id]',
    },
  };
  function channel(host) {
    if (host === 'mail.google.com') return 'Gmail';
    if (host === 'app.slack.com') return 'Slack';
    if (/(^|\.)(feishu\.cn|larksuite\.com)$/.test(host)) return "飞书";
    return null;
  }
  function plain(value) {
    return (value || '').replace(/[\s\u200b\ufeff]+/g, ' ').trim();
  }
  function label(node, attrs) {
    for (const attr of attrs) {
      const value = node.getAttribute(attr);
      if (value && value.length <= 180 && /^[\w.:-]+$/.test(value)) return value;
    }
    return null;
  }
  function visible(node) {
    return node.isConnected && node.getClientRects().length > 0 && !node.closest('[aria-hidden="true"], [inert]');
  }
  function editorsUnder(root) {
    return [...root.querySelectorAll(edit)].filter(node => visible(node) && node.isContentEditable)
      .filter(node => !node.parentElement?.closest(edit)); // Ignore nested editables.
  }
  function editors(doc, adapter, id, original) {
    const result = new Set();
    for (const node of doc.querySelectorAll(adapter.composer)) {
      if (label(node, adapter.parent) === id) {
        if (node.matches(edit) && visible(node) && node.isContentEditable) result.add(node);
        for (const item of editorsUnder(node)) result.add(item);
      }
    }
    // Gmail's inline editor sometimes lives within the specific expanded message.
    if (adapter === sites.Gmail && original) {
      for (const item of editorsUnder(original)) {
        if (item.closest(adapter.message) === original) result.add(item);
      }
    }
    return [...result];
  }
  function account(doc, name, location) {
    if (name === 'Gmail') return /^\/mail\/u\/(\d+)\//.exec(location.pathname)?.[1] ?? null;
    if (name === 'Slack') return /^\/client\/(T[A-Z0-9]+)\//.exec(location.pathname)?.[1] ?? null;
    return doc.querySelector('[data-tenant-id]')?.getAttribute('data-tenant-id') ?? location.hostname;
  }
  function capture(doc, location, selection) {
    const name = channel(location.hostname);
    const adapter = sites[name];
    if (!adapter || location.protocol !== 'https:' || !selection || selection.rangeCount !== 1 || selection.isCollapsed) return null;
    const body = node => (node?.nodeType === 1 ? node : node?.parentElement)?.closest(adapter.body);
    const from = body(selection.anchorNode);
    const to = body(selection.focusNode);
    if (!from || from !== to || from.closest(edit)) return null;
    const message = from.closest(adapter.message);
    const id = message && label(message, adapter.id);
    const selected = plain(selection.toString());
    const scope = account(doc, name, location);
    if (!id || !scope || !selected || selected.length > 6000 || !plain(from.textContent).includes(selected)) return null;
    return {channel: name, id, selected, scope, url: location.href, message};
  }
  function resolve(doc, location, bound) {
    if (!bound || location.href !== bound.url || account(doc, bound.channel, location) !== bound.scope) return null;
    const adapter = sites[bound.channel];
    if (!adapter || !bound.message.isConnected || label(bound.message, adapter.id) !== bound.id) return null;
    if (![...bound.message.querySelectorAll(adapter.body)].some(node =>
      node.closest(adapter.message) === bound.message && plain(node.textContent).includes(bound.selected))) return null;
    const candidates = editors(doc, adapter, bound.id, bound.message);
    return candidates.length === 1 ? candidates[0] : null;
  }
  globalThis.KiteReplyBinding = Object.freeze({capture, resolve, plain});
})();
