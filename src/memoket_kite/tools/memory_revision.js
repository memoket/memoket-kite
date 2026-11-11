(() => {
  const byId = id => document.getElementById(id);
  const data = JSON.parse(byId('kite-memory-data').textContent);
  const params = new URLSearchParams(location.search);
  const language = (params.get('lang') || navigator.language).startsWith('zh') ? 'zh' : 'en';
  const zh = language === 'zh';
  const copy = zh ? {
    title: '原文版本对照', before: '对照基准', after: '对照版本', added: '新增', removed: '移除', modified: '修改',
    metadata: '变更字段', order: '原文行顺序有变动', empty: '原文行无改动', none: '此版本没有这行',
    noDate: '日期未知', timezoneUnknown: '时区未知', recording: '录制时间', omitted: '此引文超过本页预算', next: '下一页', refresh: '刷新',
  } : {
    title: 'Source revision comparison', before: 'Baseline', after: 'Compared version', added: 'Added', removed: 'Removed', modified: 'Modified',
    metadata: 'Changed fields', order: 'Source line order changed', empty: 'No changed source lines', none: 'Line absent in this revision',
    noDate: 'Date unknown', timezoneUnknown: 'timezone unknown', recording: 'Recording time', omitted: 'Quote exceeds this page’s budget', next: 'Next page', refresh: 'Refresh',
  };
  function element(tag, value, className) {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = value;
    if (className) node.className = className;
    return node;
  }
  const panel = byId('revision-review');
  const header = element('header', undefined, 'compare-header');
  header.append(element('h2', copy.title), element('p', `${data.title || data.conversation_id} · v${data.memory_version}`));
  header.append(element('p', ['added', 'removed', 'modified'].map(key => `${copy[key]} ${data.counts[key]}`).join(' · ')));
  if (data.conversation_fields.length) header.append(element('p', `${copy.metadata} · ${data.conversation_fields.join(' · ')}`));
  if (data.segment_order_changed) header.append(element('p', copy.order));
  panel.append(header);
  if (data.conversation_fields.some(field => ['source_date', 'started_at', 'local_started_at'].includes(field))) {
    const section = element('section', undefined, 'revision-change');
    section.append(element('h3', copy.recording));
    const pair = element('div', undefined, 'compare-grid');
    for (const side of ['before', 'after']) {
      const revision = data[side];
      const clock = revision.local_started_at
        ? `${revision.local_started_at.replace('T', ' ')} · ${copy.timezoneUnknown}`
        : revision.started_at?.replace('T', ' ') || revision.date || copy.noDate;
      const column = element('section', undefined, 'revision-side');
      column.append(element('h4', copy[side]), element('p', clock));
      pair.append(column);
    }
    section.append(pair);
    panel.append(section);
  }
  if (!data.segments.length) panel.append(element('p', copy.empty, 'detail-meta'));
  for (const change of data.segments) {
    const row = element('section', undefined, 'revision-change');
    row.append(element('h3', `${copy[change.change]} · ${change.segment_id}`));
    const pair = element('div', undefined, 'compare-grid');
    for (const side of ['before', 'after']) {
      const revision = data[side];
      const quote = change[side];
      const column = element('section', undefined, 'revision-side');
      column.append(element('h4', `${copy[side]} · ${revision.date || copy.noDate}`));
      if (quote) {
        const metadata = [`${quote.conversation_id}#${quote.segment_id}`, quote.speaker_id,
          formatViewerOffset(quote.start_ms), revision.checksum.slice(0, 8)];
        const reference = element('p', metadata.filter(Boolean).join(' · '), 'detail-meta');
        reference.title = quote.source_line_id;
        column.append(reference, element('blockquote', quote.text ?? copy.omitted));
      } else column.append(element('p', copy.none, 'detail-meta'));
      pair.append(column);
    }
    row.append(pair);
    panel.append(row);
  }
  if (data.next_cursor) {
    const next = element('a', copy.next, 'revision-next');
    const link = new URLSearchParams(params);
    link.set('cursor', data.next_cursor);
    next.href = '?' + link.toString();
    panel.append(next);
  }
  document.documentElement.lang = language;
  document.querySelector('main.layout').hidden = true;
  panel.hidden = false;
  renderViewerNavigation(language, 'revision', params, data.comparison_available);
  byId('language').textContent = zh ? 'English' : '中文';
  byId('language').onclick = () => { params.set('lang', zh ? 'en' : 'zh'); location.search = params.toString(); };
  byId('refresh').textContent = copy.refresh;
  byId('refresh').onclick = () => { params.delete('cursor'); location.search = params.toString(); };
})();
