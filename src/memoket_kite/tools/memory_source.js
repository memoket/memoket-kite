(() => {
  const byId = id => document.getElementById(id);
  const data = JSON.parse(byId('kite-memory-data').textContent);
  const params = new URLSearchParams(location.search);
  const language = (params.get('lang') || navigator.language).startsWith('zh') ? 'zh' : 'en';
  const zh = language === 'zh';
  const copy = zh ? {
    original: '来源原文', historical: '历史版本', current: '当前版本', next: '下一页', refresh: '刷新',
  } : {
    original: 'Original source', historical: 'Historical revision', current: 'Current revision', next: 'Next page', refresh: 'Refresh',
  };
  function element(tag, value, className) {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = value;
    if (className) node.className = className;
    return node;
  }
  const panel = byId('revision-review');
  const header = element('header', undefined, 'compare-header');
  header.append(element('h2', data.title || data.source_reference));
  const status = data.source_revision_active ? copy.current : copy.historical;
  header.append(element('p', [copy.original, status, data.revision_checksum.slice(0, 12), data.source_date || data.started_at].filter(Boolean).join(' · ')));
  panel.append(header);
  for (const line of data.segments) {
    const section = element('section', undefined, 'revision-change');
    const label = element('h3', [data.conversation_id + '#' + line.segment_id, line.speaker_id].filter(Boolean).join(' · '));
    label.title = line.source_line_id;
    section.append(label, element('blockquote', line.text));
    panel.append(section);
  }
  if (data.next_offset !== null) {
    const next = element('a', copy.next, 'revision-next');
    const query = new URLSearchParams(params);
    query.set('offset', data.next_offset);
    next.href = '?' + query.toString();
    panel.append(next);
  }
  document.documentElement.lang = language;
  document.querySelector('main.layout').hidden = true;
  panel.hidden = false;
  renderViewerNavigation(language, 'source', params, data.comparison_available);
  byId('language').textContent = zh ? 'English' : '中文';
  byId('language').onclick = () => { params.set('lang', zh ? 'en' : 'zh'); location.search = params.toString(); };
  byId('refresh').textContent = copy.refresh;
  byId('refresh').onclick = () => location.reload();
})();
