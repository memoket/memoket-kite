'use strict';

let current;
const host = 'org.memoket.kite.twin.replies';

function status(tabId, text) {
  chrome.action.setBadgeText({tabId, text: '!' }).catch(() => {});
  chrome.action.setTitle({tabId, title: text}).catch(() => {});
}
chrome.action.onClicked.addListener(async tab => {
  if (!Number.isInteger(tab.id)) return;
  if (current) {
    status(tab.id, 'KITE 已有一条未结束的回复，请先回到 KITE 核对草稿。');
    return;
  }
  const id = crypto.randomUUID();
  current = {id};
  let captured;
  try { captured = await chrome.tabs.sendMessage(tab.id, {type: 'capture', id}); }
  catch {
    current = undefined;
    status(tab.id, '请在 Gmail、Slack 或飞书网页选中原消息正文。'); return;
  }
  if (!captured?.ok) {
    current = undefined;
    status(tab.id, captured?.error || '未能读取这条消息。'); return;
  }
  const port = chrome.runtime.connectNative(host);
  current = {id, port};
  port.onDisconnect.addListener(() => {
    if (current?.id === id) current = undefined;
    if (chrome.runtime.lastError) status(tab.id, '请安装本地回复桥接并打开 KITE Twin。');
  });
  port.onMessage.addListener(async command => {
    if (command.id !== id) return;
    if (command.error) {
      status(tab.id, command.error);
      port.disconnect();
      return;
    }
    if (command.type !== 'fill') return;
    let result;
    try {
      const currentTab = await chrome.tabs.get(tab.id);
      if (!currentTab.active) throw new Error('请先切回原来的浏览器标签页。');
      result = await chrome.tabs.sendMessage(tab.id, {type:'fill', id, draft: command.draft});
    } catch (error) { result = {ok:false, error: error.message || '原标签页已失效。'}; }
    if (result.ok) await chrome.windows.update(tab.windowId, {focused: true}).catch(() => {});
    port.postMessage({type: 'result', id, attempt: command.attempt, ...result});
  });
  port.postMessage({type:'begin', id, channel: captured.channel, text: captured.text});
  chrome.action.setBadgeText({tabId: tab.id, text: ''}).catch(() => {});
  chrome.action.setTitle({tabId: tab.id, title: 'KITE Twin 回复'}).catch(() => {});
});
