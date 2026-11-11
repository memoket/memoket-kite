(() => {
  const byId = id => document.getElementById(id);
  const { comparison } = JSON.parse(byId('kite-memory-data').textContent);
  const phases = ['before', 'after'];
  const kinds = ['topic', 'entity', 'memory', 'line', 'source'];
  const zh = {
    before: '操作前', after: '操作后', title: '记忆变更对照', version: '工作区版本',
    topic: '主题', entity: '实体', memory: '记忆', line: '引用行', source: '来源版本',
    added: '新增', removed: '移除', historical: '转为历史', changed: '有变更',
    aliases: '别名', citations: '引用原文', changes: '本次变更', details: '节点详情',
    hierarchy: '上下级', shared: '共享记忆', membership: '归属主题', mention: '关联实体', citation: '引用',
    links: '连接', select: '点任一图中的节点查看引用。两侧视角同步。',
    same: '无变化', snapshot: '演示快照',
  };
  const en = {
    before: 'Before', after: 'After', title: 'Compare memory changes', version: 'Workspace version',
    topic: 'Topic', entity: 'Entity', memory: 'Memory', line: 'Quoted line', source: 'Source revision',
    added: 'Added', removed: 'Removed', historical: 'Became historical', changed: 'Changed',
    aliases: 'Aliases', citations: 'Original evidence', changes: 'Changes', details: 'Node details',
    hierarchy: 'Parent and child', shared: 'Shared memory', membership: 'Filed under', mention: 'Mentions', citation: 'Cites',
    links: 'Connections', select: 'Select a node on either side. Both views rotate together.',
    same: 'Unchanged', snapshot: 'Example snapshots',
  };
  const query = new URLSearchParams(location.search);
  let chinese = query.get('lang') !== 'en';
  const t = key => (chinese ? zh : en)[key];
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function graph(frame) {
    const nodes = new Map();
    const edges = new Map();
    const add = (kind, key, label, fields = {}) => {
      const node = { ...fields, id: kind + ':' + key, kind, label };
      nodes.set(node.id, node);
      return node.id;
    };
    function link(from, to, kind) {
      if (nodes.has(from) && nodes.has(to)) edges.set(JSON.stringify([from, to, kind]), { from, to, kind });
    }
    for (const topic of frame.topics) add('topic', topic.code, topic.code, { aliases: topic.aliases, parents: topic.parents });
    for (const entity of frame.entities || []) add('entity', entity.code, entity.label, { aliases: entity.aliases });
    for (const revision of frame.revisions) {
      const label = revision.title?.startsWith('演示记录') ? '演示来源 · ' + revision.checksum.slice(0, 6)
        : revision.title || revision.reference;
      add('source', revision.checksum, label, { active: revision.active });
    }
    const active = new Map(frame.revisions.map(revision => [revision.checksum, revision.active]));
    for (const [key, line] of Object.entries(frame.evidence)) {
      const name = line.conversation_id.startsWith('kite-demo-') ? '演示 ' + line.segment_id : line.conversation_id + '#' + line.segment_id;
      const id = add('line', key, name, { active: active.get(line.revision_checksum), quotes: [line] });
      link(id, 'source:' + line.revision_checksum, 'citation');
    }
    for (const memory of frame.passages) {
      const key = JSON.stringify([memory.label, ...memory.evidence]);
      const id = add('memory', key, memory.label, {
        active: memory.active, quotes: memory.evidence.map(key => frame.evidence[key]).filter(Boolean),
      });
      for (const topic of memory.topics) link('topic:' + topic, id, 'membership');
      if (memory.active !== false && memory.evidence.length) {
        const topics = [...new Set(memory.topics)].sort();
        for (const [index, first] of topics.entries()) for (const second of topics.slice(index + 1)) {
          link('topic:' + first, 'topic:' + second, 'shared');
        }
      }
      for (const entity of memory.entities || []) link('entity:' + entity, id, 'mention');
      for (const line of memory.evidence) link(id, 'line:' + line, 'citation');
    }
    for (const topic of frame.topics) for (const parent of topic.parents || []) {
      link('topic:' + parent, 'topic:' + topic.code, 'hierarchy');
    }
    return { nodes, edges };
  }

  function compare(example) {
    const frames = Object.fromEntries(phases.map(phase => [phase, graph(example[phase])]));
    const { before, after } = frames;
    const union = new Map([...before.nodes, ...after.nodes]);
    const changes = { added: [], removed: [], historical: [], changed: [] };
    const changeFor = new Map();
    for (const [id, node] of union) {
      const previous = before.nodes.get(id);
      const next = after.nodes.get(id);
      let change;
      if (!previous) change = 'added';
      else if (!next) change = 'removed';
      else if (previous.active !== false && next.active === false) change = 'historical';
      else if (JSON.stringify(previous.aliases) !== JSON.stringify(next.aliases)) change = 'changed';
      if (change) changeFor.set(id, change);
    }
    const addedEdges = [...after.edges].filter(([id]) => !before.edges.has(id));
    const removedEdges = [...before.edges].filter(([id]) => !after.edges.has(id));
    for (const [, edge] of [...addedEdges, ...removedEdges]) {
      if (edge.kind === 'membership' && !changeFor.has(edge.to)) changeFor.set(edge.to, 'changed');
    }
    for (const [id, change] of changeFor) changes[change].push(union.get(id));

    function topicDepth(node, visited = new Set()) {
      if (visited.has(node.id)) return 0;
      const parents = (node.parents || []).map(code => union.get('topic:' + code)).filter(Boolean);
      return parents.length ? 1 + Math.max(...parents.map(parent => topicDepth(parent, new Set([...visited, node.id])))) : 0;
    }
    const layers = [];
    const topics = [...union.values()].filter(node => node.kind === 'topic');
    const maxDepth = Math.max(0, ...topics.map(node => topicDepth(node)));
    for (let depth = 0; depth <= maxDepth; depth++) {
      const nodes = topics.filter(node => topicDepth(node) === depth);
      if (nodes.length) layers.push({ kind: 'topic', nodes });
    }
    for (const kind of kinds.slice(1)) {
      const nodes = [...union.values()].filter(node => node.kind === kind);
      if (nodes.length) layers.push({ kind, nodes });
    }
    return { frames, changes, changeFor, layers, addedEdges, removedEdges };
  }

  let exampleIndex = 0;
  let result;
  let selection;
  const views = {};
  for (const phase of phases) {
    views[phase] = new MemoryScene(byId(phase + '-graph'), byId(phase + '-labels'), node => select(phase, node.id),
      zoom => { byId('compare-reset').textContent = Math.round(zoom * 100) + '%'; });
  }
  for (const [phase, other] of [['before', 'after'], ['after', 'before']]) {
    views[phase].onCameraChange = camera => {
      views[other].camera = { ...camera };
      views[other].requestDraw();
    };
  }

  function renderGraphs() {
    for (const phase of phases) {
      const frame = result.frames[phase];
      const nodes = new Map();
      const layers = result.layers.map(layer => ({
        kind: layer.kind,
        nodes: layer.nodes.map(original => {
          const node = { ...original, ...frame.nodes.get(original.id), ghost: !frame.nodes.has(original.id) };
          const change = result.changeFor.get(node.id);
          if ((phase === 'after' && change !== 'removed') || (phase === 'before' && change === 'removed')) node.change = change;
          nodes.set(node.id, node);
          return node;
        }),
      }));
      const other = result.frames[phase === 'before' ? 'after' : 'before'];
      const edges = [...frame.edges].map(([id, edge]) => ({
        from: nodes.get(edge.from), to: nodes.get(edge.to),
        kind: edge.kind,
        change: !other.edges.has(id) ? (phase === 'before' ? 'removed' : 'added')
          : phase === 'after' && edge.kind === 'citation' && result.changeFor.get(edge.from) === 'historical'
            ? 'historical' : undefined,
      }));
      const highlighted = selection && frame.nodes.has(selection.id) ? nodes.get(selection.id) : undefined;
      views[phase].show({ layers, edges }, highlighted, 'comparison');
    }
  }

  function select(phase, id) {
    selection = { phase, id };
    renderGraphs();
    renderDetail();
  }

  function renderDetail() {
    const panel = byId('compare-detail');
    const frame = result.frames[selection?.phase];
    const node = frame?.nodes.get(selection?.id);
    panel.replaceChildren();
    if (!node) {
      panel.append(element('h3', t('details')), element('p', t('select')));
      return;
    }
    panel.append(element('p', t(selection.phase) + ' · ' + t(node.kind), 'detail-meta'), element('h2', node.label));
    if (node.aliases?.length) panel.append(element('p', t('aliases') + ' · ' + node.aliases.join('、')));
    if (node.active === false) panel.append(element('p', t('historical'), 'detail-meta'));
    const connections = [...frame.edges.values()].filter(edge => edge.from === node.id || edge.to === node.id);
    let quotes = node.quotes || [];
    if (node.kind === 'entity') {
      const evidence = connections.filter(edge => edge.kind === 'mention').flatMap(edge => frame.nodes.get(edge.to).quotes);
      quotes = MemoryScene.entityQuotes(node, evidence);
    }
    if (quotes.length) {
      const citations = element('section');
      citations.append(element('h3', t('citations')));
      for (const quote of quotes) {
        const row = element('div', undefined, 'citation');
        row.append(element('p', quote.source_reference + '#' + quote.segment_id), element('blockquote', quote.text));
        citations.append(row);
      }
      panel.append(citations);
    }
    if (connections.length) {
      const group = element('section');
      group.append(element('h3', t('links')));
      for (const edge of connections) {
        const other = frame.nodes.get(edge.from === node.id ? edge.to : edge.from);
        const button = element('button', t(edge.kind) + ' · ' + other.label, 'detail-link');
        button.type = 'button';
        button.onclick = () => select(selection.phase, other.id);
        group.append(button);
      }
      panel.append(group);
    }
  }

  function render() {
    const example = comparison[exampleIndex];
    result = compare(example);
    byId('title').textContent = t('title');
    document.documentElement.lang = chinese ? 'zh-CN' : 'en';
    renderViewerNavigation(chinese ? 'zh' : 'en', 'changes', query, true);
    byId('language').textContent = chinese ? 'English' : '中文';
    byId('compare-title').textContent = example.title;
    byId('compare-snapshot').textContent = t('snapshot');
    byId('compare-description').textContent = example.description;
    byId('compare-examples').replaceChildren(...comparison.map((item, index) => {
      const button = element('button', item.name);
      button.type = 'button';
      button.setAttribute('aria-pressed', String(index === exampleIndex));
      button.onclick = () => { exampleIndex = index; selection = undefined; views.before.reset(); render(); };
      return button;
    }));
    const legend = byId('compare-legend');
    const kindsShown = new Set(result.layers.map(layer => layer.kind));
    legend.replaceChildren();
    for (const kind of [...kindsShown, ...Object.keys(result.changes).filter(key => result.changes[key].length)]) {
      const item = element('span');
      if (kindsShown.has(kind)) item.dataset.kind = kind;
      else item.dataset.change = kind;
      item.append(element('i'), element('span', t(kind)));
      legend.append(item);
    }
    for (const phase of phases) {
      byId(phase + '-heading').textContent = t(phase);
      byId(phase + '-version').textContent = t('version') + ' ' + example[phase].version;
    }
    const panel = byId('compare-changes');
    panel.replaceChildren(element('h3', t('changes')));
    for (const kind of kinds) {
      const counts = phases.map(phase => [...result.frames[phase].nodes.values()].filter(node => node.kind === kind).length);
      if (counts.some(Boolean)) panel.append(element('p', t(kind) + ' ' + counts.join(' → '), 'compare-change-line'));
    }
    panel.append(element('p', t('links') + ' +' + result.addedEdges.length + ' / −' + result.removedEdges.length, 'compare-changes-count'));
    for (const [change, nodes] of Object.entries(result.changes)) {
      const meaningful = nodes.filter(node => node.kind === 'topic' || node.kind === 'memory' || node.kind === 'entity');
      if (!meaningful.length) continue;
      const list = element('section');
      list.append(element('p', t(change), 'compare-change-key'));
      for (const node of meaningful) {
        const button = element('button', node.label, 'detail-link');
        button.type = 'button';
        button.onclick = () => select(change === 'removed' ? 'before' : 'after', node.id);
        list.append(button);
      }
      panel.append(list);
    }
    renderGraphs();
    renderDetail();
  }

  document.querySelector('main.layout').hidden = true;
  byId('comparison').hidden = false;
  byId('refresh').hidden = true;
  byId('language').onclick = () => { chinese = !chinese; render(); };
  byId('compare-in').onclick = () => views.before.scale(1.18);
  byId('compare-out').onclick = () => views.before.scale(1 / 1.18);
  byId('compare-reset').onclick = () => views.before.reset();
  render();
})();
