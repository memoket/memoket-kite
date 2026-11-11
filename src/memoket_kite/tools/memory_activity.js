(() => {
  const byId = id => document.getElementById(id);
  const data = JSON.parse(byId('kite-memory-data').textContent);
  const params = new URLSearchParams(location.search);
  const copy = {
    zh: {
      activity: '记忆工作流', version: '工作区版本', events: '已提交变更', runs: '近期运行',
      index: '批量构建索引', ingest: '导入', update: '修订', merge: '融合', delete: '删除',
      normalize: '整理来源', annotate: '关联原文', extract: '准备标注', commit: '写入',
      verify: '校验', preview: '预览', apply: '执行', compile: '编译检索', search: '搜索',
      compare: '原文版本对照',
      searchVersion: '在此版本检索',
      savedSearch: '打开当次检索原文',
      changeDetails: '查看变更', back: '返回所有变更', searchPrevious: '检索提交前版本',
      sourceRevisions: '保留的来源变更', openSource: '查看此版本原文', compareSource: '对照提交前后的原文',
      previousPage: '上一页', nextPage: '下一页', openTopic: '查看当前主题',
      missingDetails: '此提交没有可展示的来源版本或主题明细。',
      deletedEvidence: '删除记录保留数量。提交前的快照也无法读取已删除的原文。',
      completed: '完成', pending: '待执行', failed: '失败', running: '运行中', awaiting_approval: '待批准',
      redacted: '已隐去明细',
      changed: '已变更', sources: '来源', revisions: '来源版本', facts: '事实', refresh: '刷新',
      emptyEvents: '还没有可展示的提交记录。', emptyRuns: '还没有可展示的工作流运行。',
    },
    en: {
      activity: 'Memory workflows', version: 'Workspace version', events: 'Committed changes', runs: 'Recent runs',
      index: 'Build source index', ingest: 'Ingest', update: 'Update', merge: 'Merge', delete: 'Delete',
      normalize: 'Normalize source', annotate: 'Link evidence', extract: 'Prepare annotations', commit: 'Commit',
      verify: 'Verify', preview: 'Preview', apply: 'Apply', compile: 'Compile query', search: 'Search',
      compare: 'Compare source revisions',
      searchVersion: 'Search this version',
      savedSearch: 'Open saved search evidence',
      changeDetails: 'View change', back: 'Back to all changes', searchPrevious: 'Search before this change',
      sourceRevisions: 'Retained source changes', openSource: 'Read this revision', compareSource: 'Compare before and after this commit',
      previousPage: 'Previous page', nextPage: 'Next page', openTopic: 'Open current topic',
      missingDetails: 'No retained source revisions or topic details are available for this commit.',
      deletedEvidence: 'The deletion record retains counts. Earlier snapshots omit the deleted originals.',
      completed: 'Completed', pending: 'Pending', failed: 'Failed', running: 'Running', awaiting_approval: 'Awaiting approval',
      redacted: 'Details redacted',
      changed: 'Changed', sources: 'sources', revisions: 'source revisions', facts: 'facts', refresh: 'Refresh',
      emptyEvents: 'No recorded changes yet.', emptyRuns: 'No visible workflow runs yet.',
    },
  };
  let language = params.get('lang') || (navigator.language.startsWith('zh') ? 'zh' : 'en');
  if (!(language in copy)) language = 'en';
  const t = key => copy[language][key] || key;

  function element(tag, value, className) {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = value;
    if (className) node.className = className;
    return node;
  }

  function link(label, target) {
    const item = element('a', t(label), 'citation-link');
    item.href = '?' + new URLSearchParams({ lang: language, ...target });
    return item;
  }

  function changeDetail(panel, event) {
    if (event.kind === 'delete') {
      panel.append(element('p', t('deletedEvidence'), 'detail-meta'));
    } else if (event.topic_merge) {
      const merge = event.topic_merge;
      panel.append(element('p', `${merge.from_topic} → ${merge.to_topic}`));
      if (merge.current_topic) panel.append(link('openTopic', { view: 'graph', topic: merge.current_topic }));
    } else if (event.source_changes?.length) {
      const list = element('ul', undefined, 'change-sources');
      const first = event.source_offset + 1;
      const last = event.source_offset + event.source_changes.length;
      panel.append(element('h4', `${t('sourceRevisions')} ${first}–${last}/${event.retained_source_changes}`));
      for (const source of event.source_changes) {
        const entry = element('li');
        entry.append(element('strong', source.title || source.conversation_id));
        entry.append(link('openSource', { view: 'source', checksum: source.checksum }));
        if (source.before) entry.append(link('compareSource', { view: 'revision', before: source.before, after: source.checksum }));
        list.append(entry);
      }
      panel.append(list);
      const target = { view: 'activity', event: event.version };
      if (event.source_offset) panel.append(link('previousPage', { ...target, offset: Math.max(0, event.source_offset - 20) }));
      if (last < event.retained_source_changes) panel.append(link('nextPage', { ...target, offset: last }));
    } else {
      panel.append(element('p', t('missingDetails'), 'detail-meta'));
    }
  }

  function render() {
    document.documentElement.lang = language;
    renderViewerNavigation(language, 'activity', params, data.comparison_available);
    byId('activity-title').textContent = t('activity');
    byId('activity-version').textContent = t('version') + ' ' + data.version;
    byId('language').textContent = language === 'zh' ? 'English' : '中文';
    byId('refresh').textContent = t('refresh');
    const date = value => new Date(value).toLocaleString(language);
    const events = byId('activity-events');
    events.replaceChildren(element('h3', t('events')));
    const details = data.selected_event != null;
    if (details) events.append(link('back', { view: 'activity' }));
    if (!data.events.length) events.append(element('p', t('emptyEvents')));
    for (const event of data.events) {
      const row = element('section', undefined, 'activity-row');
      const counts = event.kind === 'delete'
        ? event.revision_count + ' ' + t('revisions') + ' · ' + event.fact_count + ' ' + t('facts')
        : event.kind === 'merge' ? '' : t('changed') + ' ' + event.changed_count + '/' + event.source_count + ' ' + t('sources');
      row.append(element('strong', t(event.kind) + ' · v' + event.version));
      row.append(element('p', [counts, date(event.committed_at)].filter(Boolean).join(' · ')));
      if (!details) row.append(link('changeDetails', { view: 'activity', event: event.version }));
      if (details && event.can_search_previous) {
        row.append(link('searchPrevious', { view: 'graph', at: event.version - 1 }));
      }
      if (event.can_search_version) {
        row.append(link('searchVersion', { view: 'graph', at: event.version }));
      }
      if (details) changeDetail(row, event);
      events.append(row);
    }
    const runs = byId('activity-runs');
    runs.hidden = details;
    runs.replaceChildren(element('h3', t('runs')));
    if (!data.runs.length) runs.append(element('p', t('emptyRuns')));
    for (const run of data.runs) {
      const row = element('section', undefined, 'activity-row');
      row.append(element('strong', t(run.kind) + ' · ' + t(run.state)));
      row.append(element('p', 'v' + run.version + ' · ' + date(run.updated_at)));
      const stages = element('ol', undefined, 'activity-stages');
      for (const stage of run.stages) {
        const label = t(stage.name) + ' · ' + t(stage.state);
        const item = element('li', stage.attempts > 1 ? label + ' ×' + stage.attempts : label, 'activity-stage');
        item.dataset.state = stage.state;
        stages.append(item);
      }
      row.append(stages);
      if (run.saved_search) {
        row.append(link('savedSearch', { view: 'graph', run: run.saved_search }));
      }
      runs.append(row);
    }
  }

  document.querySelector('main.layout').hidden = true;
  byId('activity').hidden = false;
  byId('language').onclick = () => {
    language = language === 'zh' ? 'en' : 'zh';
    render();
  };
  byId('refresh').onclick = () => {
    params.set('lang', language);
    location.search = params.toString();
  };
  render();
})();
