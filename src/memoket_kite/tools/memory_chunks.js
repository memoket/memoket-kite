(() => {
  const byId = id => document.getElementById(id);
  const data = JSON.parse(byId('kite-memory-data').textContent);
  const query = new URLSearchParams(location.search);
  const requestedLanguage = query.get('lang');
  let language = ['zh', 'en'].includes(requestedLanguage)
    ? requestedLanguage : navigator.language.startsWith('zh') ? 'zh' : 'en';
  const canvas = byId('chunk-graph');
  const openSource = byId('chunk-source');
  let selected;
  const scene = new MemoryScene(canvas, byId('chunk-labels'), node => {
    selected = node;
    scene.selected = node;
    scene.requestDraw();
    renderSource();
  }, value => { byId('chunk-zoom-reset').textContent = Math.round(value * 100) + '%'; });

  const coordinates = [];
  const columns = Math.ceil(Math.cbrt(data.total_chunks * 2.3)) || 1;
  const rows = Math.min(Math.ceil(data.total_chunks / columns) || 1, Math.ceil(columns * .68));
  const levels = Math.ceil(data.total_chunks / (columns * rows)) || 1;
  const spacing = Math.min(1.16, 11.2 / Math.max(columns, rows, levels));
  for (const source of data.sources) {
    for (const [offset, preview] of source.chunks) {
      const index = coordinates.length;
      const column = index % columns;
      const row = Math.floor(index / columns) % rows;
      const level = Math.floor(index / (columns * rows));
      const jitter = (angle) => Math.sin(index * angle) * spacing * .1;
      coordinates.push([{ kind: 'chunk', size: spacing * .28, checksum: source.checksum, offset, preview: Array.from(preview) }, [
        (column - (columns - 1) / 2) * spacing + jitter(2.37),
        (level - (levels - 1) / 2) * spacing + jitter(.82),
        (row - (rows - 1) / 2) * spacing + jitter(1.61),
      ]]);
    }
  }

  function renderSource() {
    openSource.hidden = !selected;
    if (!selected) return;
    openSource.textContent = language === 'zh' ? '打开原文位置' : 'Open original';
    openSource.href = '?' + new URLSearchParams({
      view: 'source', checksum: selected.checksum, offset: selected.offset, lang: language,
    });
  }

  function render() {
    const zh = language === 'zh';
    const number = new Intl.NumberFormat(language);
    document.documentElement.lang = zh ? 'zh-CN' : 'en';
    renderViewerNavigation(language, 'graph', query, data.comparison_available);
    renderMemoryDisplay(language, 'chunks', query);
    const title = zh ? '其他玩家' : 'Other Players';
    byId('title').textContent = zh ? '记忆图谱' : 'Memory graph';
    byId('chunks-title').textContent = title;
    const scope = data.search_scope
      ? zh ? '本次检索引用的来源' : 'Sources cited in this search'
      : zh ? '工作区原文' : 'Workspace recordings';
    const counts = zh
      ? [number.format(data.source_count) + ' 个来源版本', number.format(data.total_chunks) + ' 个切段立方体']
      : [number.format(data.source_count) + ' source revisions', number.format(data.total_chunks) + ' chunk cubes'];
    byId('chunks-summary').textContent = [scope, ...counts].join(' · ');
    canvas.setAttribute('aria-label', title + ' · ' + counts[1]);
    byId('chunk-empty').hidden = data.total_chunks > 0;
    byId('chunk-empty').textContent = zh ? '该范围暂无原文片段。' : 'No original chunks in this scope.';
    byId('chunk-gesture').textContent = zh ? '拖动旋转 · Shift + 拖动平移' : 'Drag to rotate · Shift + drag to pan';
    byId('chunk-zoom').setAttribute('aria-label', zh ? '缩放' : 'Zoom');
    byId('chunk-zoom-out').setAttribute('aria-label', zh ? '缩小' : 'Zoom out');
    byId('chunk-zoom-reset').setAttribute('aria-label', zh ? '重置视角' : 'Reset view');
    byId('chunk-zoom-in').setAttribute('aria-label', zh ? '放大' : 'Zoom in');
    byId('language').textContent = zh ? 'English' : '中文';
    renderSource();
  }

  document.querySelector('main.layout').hidden = true;
  byId('chunk-view').hidden = false;
  byId('refresh').hidden = true;
  byId('language').addEventListener('click', () => {
    language = language === 'zh' ? 'en' : 'zh';
    query.set('lang', language);
    history.replaceState(history.state, '', '?' + query);
    render();
  });
  byId('chunk-zoom-out').addEventListener('click', () => scene.scale(1 / 1.18));
  byId('chunk-zoom-reset').addEventListener('click', () => scene.reset());
  byId('chunk-zoom-in').addEventListener('click', () => scene.scale(1.18));
  render();
  scene.show({ layers: [], edges: [], coordinates }, undefined, 'chunks');
})();
