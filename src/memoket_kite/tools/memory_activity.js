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
    if (!data.events.length) events.append(element('p', t('emptyEvents')));
    for (const event of data.events) {
      const row = element('section', undefined, 'activity-row');
      const counts = event.kind === 'delete'
        ? event.revision_count + ' ' + t('revisions') + ' · ' + event.fact_count + ' ' + t('facts')
        : event.kind === 'merge' ? '' : t('changed') + ' ' + event.changed_count + '/' + event.source_count + ' ' + t('sources');
      row.append(element('strong', t(event.kind) + ' · v' + event.version));
      row.append(element('p', [counts, date(event.committed_at)].filter(Boolean).join(' · ')));
      events.append(row);
    }
    const runs = byId('activity-runs');
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
