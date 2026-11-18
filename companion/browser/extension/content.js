(() => {
  'use strict';
  let bound;
  const api = globalThis.KiteReplyBinding;
  const failure = error => ({ok: false, error});
  chrome.runtime.onMessage.addListener((message, sender, response) => {
    if (sender.id !== chrome.runtime.id) return;
    if (message.type === 'capture') {
      const target = api.capture(document, location, getSelection());
      if (!target) { response(failure('请在支持的网页里只选中一条消息的正文。')); return; }
      bound = {...target, request: message.id, expires: Date.now() + 15 * 60_000};
      response({ok: true, channel: target.channel, text: target.selected});
      return;
    }
    if (message.type !== 'fill') return;
    if (!bound || message.id !== bound.request || Date.now() > bound.expires) {
      response(failure('原消息选择已失效，请回到原消息重新起草。')); return;
    }
    const draft = message.draft;
    if (typeof draft !== 'string' || !api.plain(draft) || draft.length > 12000) {
      response(failure('草稿为空或超出长度限制。')); return;
    }
    const target = api.resolve(document, location, bound);
    if (!target) {
      response(failure('无法核对原回复框。请在原消息上点回复，保留该页面后重试。')); return;
    }
    if (api.plain(target.innerText) || target.querySelector('img, a, [contenteditable="false"], [data-mention]')) {
      response(failure('回复框已有内容，KITE 没有覆盖它。')); return;
    }
    target.focus();
    // Focus itself may make the site re-render its composer. Verify again before inserting.
    if (document.activeElement !== target || api.resolve(document, location, bound) !== target ||
        api.plain(target.innerText) || target.querySelector('img, a, [contenteditable="false"], [data-mention]')) {
      response(failure('回复框在操作期间变了。')); return;
    }
    // Use the browser editing transaction so the site's rich-text model gets an input event.
    // No clipboard, synthetic Enter, send click, or provider message API is involved.
    const didInsert = document.execCommand('insertText', false, draft);
    if (!didInsert || api.plain(target.innerText) !== api.plain(draft)) {
      response(failure('网站没有确认完整写入，请检查原回复框后再试。')); return;
    }
    bound = undefined;
    response({ok: true});
  });
})();
