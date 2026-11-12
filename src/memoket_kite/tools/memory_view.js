(() => {
  const byId = id => document.getElementById(id);
  const snapshot = JSON.parse(byId('kite-memory-data').textContent);
  const query = new URLSearchParams(location.search);
  let progress = snapshot.workflow_progress;
  const filterTrace = snapshot.filter_trace;
  let filterStep = -1;
  let filterTimer;
  const number = new Intl.NumberFormat();
  const searching = Boolean(snapshot.search_terms?.length);
  const groupedSearch = snapshot.search_subquestion_branches?.length > 0;
  const comparison = snapshot.search_comparison || [];
  const versionComparison = snapshot.search_version_comparison;
  const recordingBounds = snapshot.search_recording_bounds || {};
  const compareDateOverrides = snapshot.search_comparison_recording_overrides || {};
  const sourceScope = snapshot.search_source_scope || {};
  let searchAllSources = false;
  let explainCandidates = Boolean(snapshot.search_trace?.packing?.length);
  const copy = {
    en: {
      title: 'Memory Graph', searchTitle: 'Search Evidence', topics: 'Topics', terms: 'Search terms', questions: 'Search questions',
      topic: 'Topic', memory: 'Memory', line: 'Quoted line', source: 'Source revision', state: 'State',
      allMode: 'All topics', topicMode: 'Topic hierarchy', memoryMode: 'Evidence path', overview: 'Back to topics',
      entity: 'Entity', entities: 'Related entities', entityCatalog: 'Entities', mention: 'Entity link', aliases: 'Aliases',
      facet: 'Facet', facets: 'Facets', facetLink: 'Facet link', annotations: 'Indexed annotations',
      object: 'Objects', place: 'Places', event: 'Events', duration: 'Durations',
      noEntities: 'No entity annotations are stored in this workspace. Entity names may still appear in the original conversations.',
      noFacets: 'No facet values have been indexed for these memories.',
      sourceCatalog: 'Sources', noSources: 'No sources are available.', filterCatalog: 'Filter this list',
      noMatches: 'No matching items.', more: 'Show more', scanned: 'sources with entity or facet labels',
      compile: 'Compiled phrases', candidates: 'candidate spans', quoteBytes: 'quote bytes',
      sharedQuotes: 'shared lines',
      candidateSources: 'candidate sources', compareRevision: 'Compare with current version',
      readRevision: 'Read full transcript', topicSources: 'Original sources in this topic', sourceLines: 'lines',
      anchorSources: 'sources matching the name',
      matchedTerms: 'Terms in this quote',
      inspectCandidates: 'Rerun with candidate details', packingOrder: 'Packing order',
      sortCandidatesByDate: 'Order by recording date',
      wordForms: 'English word forms',
      repeatedTerms: 'Repeated query terms', followupQuote: 'Follow-up quote', followupQuotes: 'follow-up quotes',
      firstLinesPrioritized: 'More passages from the leading source',
      versionLinesPrioritized: 'Original lines first, then surrounding turns',
      openVersionEvidence: 'Read differing line',
      packingSegments: 'Segments',
      corroboratingTerms: 'Multiple clues match this passage',
      recordingSelection: {
        other_recording_followup: 'Follow-up in another recording',
        selective_original_clue: 'Rare query clue matches multiple original lines',
        dated_plan_followup: 'A later recording repeats a date from the earlier discussion and mentions a change',
        date_corroboration: 'Another recording mentions this date for the phase',
        role_corroboration: 'Another original line discusses both named roles',
        focused_date: 'Original date statement for the named subject',
        focused_color: 'Original color statement for the named subject',
        change_subquestion: 'Original evidence selected for this subquestion',
        subquestion_clues: 'Name and another clue match the same original line',
        subquestion_index: 'A cited index label and original wording locate this line',
        change_phase: 'Original evidence selected for this phase of the question',
        version_evidence: 'Original line is missing or differs in the other snapshot',
      },
      changePhases: 'Phases in the question',
      subquestions: 'Subquestions in the search plan',
      implementationSearch: 'Task wording used for retrieval',
      indexLabels: 'Index labels',
      sameTextSources: 'Sources with the same transcript text',
      duplicateLines: 'duplicate lines reused',
      trimmedLines: 'outer lines trimmed',
      packingReasons: { quoted: 'Quoted', span_limit: 'Span limit reached', quote_budget: 'Quote budget', duplicate_text: 'Text already quoted from another source',
        already_quoted: 'Already quoted', no_matching_line: 'No matching original line' },
      sourceScope: 'Source scope', allSources: 'Search all sources',
      invalidMatching: 'Enter up to twelve phrases of at most 128 characters.',
      versionFilter: 'Version', versionFilterLabel: 'Search at a workspace version', history: 'Workspace snapshot',
      baselineVersion: 'Earlier workspace version', comparisonQuestionRequired: 'Enter the question to compare.',
      invalidVersionOrder: 'The earlier version must precede the target version.',
      recordingDates: 'Recording dates', recordingFrom: 'From', recordingThrough: 'Through',
      evidenceRecordingDates: 'Cited recording dates',
      secondRecordingDates: 'Second question recording dates (blank uses first range)', allRecordingDates: 'All recording dates',
      recordingTimeline: 'Evidence by recording date', invalidDateRange: 'Start date must not follow end date.',
      noDatedResults: 'No matching passages in this recording window.', timezoneUnknown: 'timezone unknown', matchingLines: 'Matching lines',
      compareQuestions: 'Compare', secondQuestion: 'Second question', firstQuestionRequired: 'Enter the first question.',
      savedSearch: 'Saved search', currentWorkspace: 'Current workspace',
      historyDeletes: 'Later deletions affected the evidence available for this snapshot.',
      cooccurrence: 'Same annotated passage', relatedEntities: 'Co-occurring entities', relatedFacets: 'Co-occurring facets',
      sharedPassages: 'shared passages', sharedEvidence: 'Shared quotes with', linkedTopics: 'Topics in these passages',
      annotated: 'Indexed passages with labels', unannotated: 'No entity or facet labels on this source',
      search: 'Search', placeholder: 'Ask a question or enter comma-separated phrases', invalid: 'Enter a question or up to three phrases of 128 characters each.',
      refresh: 'Refresh', gesture: 'Drag to rotate · Shift + drag to pan', hierarchy: 'Parent → child',
      shared: 'Shared memory', cites: 'Cites', correction: 'Correction', original: 'Original evidence', memories: 'Memories',
      related: 'Related memories', relatedTopics: 'Topics with shared memories', sources: 'Source revisions', parents: 'Parent topics', children: 'Child topics',
      sameLine: 'Same quoted line', sameSource: 'Same source revision', sameTopic: 'Same topic',
      correctedBy: 'Superseded by', supersedes: 'Supersedes', quotes: 'quoted lines', noDate: 'Date unknown',
      historical: 'Historical', active: 'Current version', openEvidence: 'Explore evidence path',
      current: 'Current state', fallback: 'Conversation memories',
      empty: 'No memories to display in this view.', reset: 'Reset view', zoom: 'Zoom',
      zoomIn: 'Zoom in', zoomOut: 'Zoom out', path: 'Path', view: 'View', graph: 'Three-dimensional memory graph',
      alsoFiled: 'Filed under', scope: 'shown in the graph',
      proposal: 'Proposal', confirmed: 'Confirmed', rejected: 'Rejected', inference: 'Inference', statement: 'Statement',
      record: 'original passages', records: 'memory records', coverage: 'sources with topics', workspace: 'sources in workspace', remaining: 'more',
    },
    zh: {
      title: '记忆图谱', searchTitle: '原文检索', topics: '主题', terms: '检索词', questions: '检索问题',
      topic: '主题', memory: '记忆', line: '原文行', source: '来源版本', state: '状态',
      allMode: '全部主题', topicMode: '主题层级', memoryMode: '原文路径', overview: '返回主题图谱',
      entity: '实体', entities: '相关实体', entityCatalog: '实体', mention: '实体关联', aliases: '别名',
      facet: 'Facet', facets: 'Facets', facetLink: '维度关联', annotations: '已索引标注',
      object: '对象', place: '地点', event: '事件', duration: '时长',
      noEntities: '当前工作区没有已标注的实体。人物、产品或术语仍可能出现在转写原文里。',
      noFacets: '当前工作区还没有标注对象、地点、事件或时长。',
      sourceCatalog: '来源', noSources: '当前工作区没有来源。', filterCatalog: '筛选当前列表',
      noMatches: '没有匹配项。', more: '显示更多', scanned: '份来源有实体或 Facet 标注',
      compile: '编译出的检索词', candidates: '个候选片段', quoteBytes: '字节原文',
      sharedQuotes: '行共用引文',
      candidateSources: '份候选来源', compareRevision: '与当前版本对照',
      readRevision: '查看全部原文', topicSources: '该主题的原文来源', sourceLines: '行',
      anchorSources: '份来源命中名称',
      matchedTerms: '原文命中',
      inspectCandidates: '重查并查看候选', packingOrder: '引文选取顺序',
      wordForms: '英文词形匹配',
      repeatedTerms: '问题中重复出现', followupQuote: '后续原文命中', followupQuotes: '行后续引文',
      firstLinesPrioritized: '先取同一来源的多个片段',
      versionLinesPrioritized: '先保留引文，再补相邻原文',
      openVersionEvidence: '查看差异原文',
      packingSegments: '原文段', sortCandidatesByDate: '按录制日期排列',
      corroboratingTerms: '同一片段命中多个线索',
      recordingSelection: {
        other_recording_followup: '另一份录制有匹配的后续原文',
        selective_original_clue: '低频检索词命中多行原文',
        dated_plan_followup: '后续录制重提前次讨论的日期并提到改期',
        date_corroboration: '另一份录制提到该阶段的同一日期',
        role_corroboration: '另一句原文讨论了两个对象的分工',
        focused_date: '主题相关的日期原文',
        focused_color: '主题相关的颜色原文',
        change_subquestion: '为这个子问题选取的原文',
        subquestion_clues: '名称和另一条线索命中同一行原文',
        subquestion_index: '索引标签和原文措辞定位这一行',
        change_phase: '为问题中这个阶段选取的原文',
        version_evidence: '该原文在对照版本中缺失或措辞不同',
      },
      changePhases: '问题中的阶段',
      subquestions: '检索计划中的子问题',
      implementationSearch: '用于检索的任务文字',
      indexLabels: '索引标签',
      sameTextSources: '原文相同的来源',
      duplicateLines: '行重复原文已共用',
      trimmedLines: '行外围原文已精简',
      packingReasons: { quoted: '已引用', span_limit: '片段数已达上限', quote_budget: '引文预算不足', duplicate_text: '已引用其他来源中的相同原文',
        already_quoted: '已在前面引用', no_matching_line: '原文行无完整命中' },
      sourceScope: '限定来源', allSources: '搜索全部来源',
      invalidMatching: '请输入最多十二个检索词，每个不超过 128 字符。',
      versionFilter: '版本', versionFilterLabel: '按工作区版本检索', history: '工作区快照',
      baselineVersion: '对照起始版本', comparisonQuestionRequired: '请输入要对照的问题。',
      invalidVersionOrder: '对照起始版本必须早于目标版本。',
      recordingDates: '录制日期', recordingFrom: '起始日期', recordingThrough: '截止日期',
      evidenceRecordingDates: '引文录制日期',
      secondRecordingDates: '第二问录制日期（留空沿用第一问）', allRecordingDates: '全部录制日期',
      recordingTimeline: '按录制日期查看原文', invalidDateRange: '起始日期应早于或等于截止日期。',
      noDatedResults: '这个录制日期范围内没有命中的原文片段。', timezoneUnknown: '时区未知', matchingLines: '仅命中行',
      compareQuestions: '对比', secondQuestion: '第二个问题', firstQuestionRequired: '请输入第一个问题。',
      savedSearch: '已保存的检索', currentWorkspace: '当前工作区',
      historyDeletes: '后来执行的删除可能影响此快照可见的原文。',
      cooccurrence: '同段共现', relatedEntities: '同段出现的实体', relatedFacets: '同段出现的 Facets',
      sharedPassages: '个共同片段', sharedEvidence: '与此节点共现的引用', linkedTopics: '这些片段涉及的主题',
      annotated: '个片段有实体或 Facet 标注', unannotated: '此来源暂无实体或 Facet 标注。',
      search: '检索', placeholder: '输入问题，或用逗号分隔检索词', invalid: '请输入问题，或最多三个不超过 128 字符的检索词。',
      refresh: '刷新', gesture: '拖动旋转 · Shift + 拖动平移', hierarchy: '父主题 → 子主题',
      shared: '共享记忆', cites: '引用', correction: '更正关系', original: '引用原文', memories: '原文记忆',
      related: '相关记忆', relatedTopics: '共享记忆的主题', sources: '来源版本', parents: '上级主题', children: '下级主题',
      sameLine: '引用同一原文行', sameSource: '来自同一版本', sameTopic: '属于同一主题',
      correctedBy: '被以下记忆更正', supersedes: '更正以下记忆', quotes: '行引文', noDate: '日期未知',
      historical: '历史版本', active: '当前版本', openEvidence: '查看原文路径',
      current: '当前状态', fallback: '会话记忆',
      empty: '这个视图里没有可展示的记忆。', reset: '重置视角', zoom: '缩放',
      zoomIn: '放大', zoomOut: '缩小', path: '路径', view: '图谱视图', graph: '可旋转的三维记忆图谱',
      alsoFiled: '归属主题', scope: '项显示在图中',
      proposal: '提议', confirmed: '已确认', rejected: '已否决', inference: '推测', statement: '记录',
      record: '原文片段', records: '条记忆', coverage: '份来源已归类', workspace: '份来源', remaining: '项更多',
    },
  };
  let language = query.get('lang') === 'en' ? 'en' : query.get('lang') === 'zh' ? 'zh'
    : navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en';
  const t = key => copy[language][key] || key;

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  const topics = new Map();
  const memories = new Map();
  const sources = new Map();
  const lines = new Map();
  const states = new Map();
  const entities = new Map();
  const facets = new Map();
  const facetAxes = ['object', 'place', 'event', 'duration'];
  const isAnnotation = node => node?.kind === 'entity' || node?.kind === 'facet';

  for (const topic of snapshot.topics) {
    topics.set(topic.code, {
      id: 'topic:' + topic.code, kind: 'topic', code: topic.code,
      label: topic.code.replaceAll('_', ' '), parents: topic.parents,
      aliases: topic.aliases, children: [], memories: [],
    });
  }
  for (const term of snapshot.search_terms || []) {
    const part = comparison.find(part => part.key === term);
    const subject = groupedSearch && term === snapshot.search_question
      ? term.match(/^([^。？！?]+?)(?:。|\.\s)/)?.[1] : undefined;
    const label = part?.history ? `v${part.history.version}` : part?.question || subject || term;
    const parents = snapshot.search_subquestion_branches?.includes(term) ? [snapshot.search_question] : [];
    topics.set(term, { id: 'term:' + term, kind: 'topic', code: term, label, parents, aliases: [], children: [], memories: [] });
  }
  for (const topic of topics.values()) {
    for (const parent of topic.parents) topics.get(parent)?.children.push(topic);
  }
  for (const revision of snapshot.revisions) {
    sources.set(revision.checksum, {
      ...revision, id: 'source:' + revision.checksum, kind: 'source',
      label: revision.title || revision.reference, memories: [],
    });
  }
  for (const [id, quote] of Object.entries(snapshot.evidence)) {
    lines.set(id, { ...quote, id: 'line:' + id, kind: 'line', label: quote.segment_id, memories: [] });
  }
  for (const [key, state] of Object.entries(snapshot.states)) {
    states.set(key, { ...state, id: 'state:' + key, kind: 'state', label: key, memories: [] });
  }
  for (const entity of snapshot.entities || []) {
    entities.set(entity.code, { ...entity, id: 'entity:' + entity.code, kind: 'entity', memories: [] });
  }
  const rows = [
    ...snapshot.facts.map(fact => ({ ...fact, label: fact.content, key: fact.fact_id, active: fact.source_revision_active })),
    ...(snapshot.passages || []).map((span, index) => ({ ...span, key: 'passage:' + index })),
  ];
  for (const row of rows) {
    const evidence = [...new Set(row.evidence)].map(id => lines.get(id)).filter(Boolean);
    const associations = [...new Set(row.terms || row.topics || [])].map(code => topics.get(code)).filter(Boolean);
    const primary = evidence.map(line => sources.get(line.revision_checksum)).filter(Boolean);
    const copies = (row.same_text_sources || []).map(checksum => sources.get(checksum)).filter(Boolean);
    const revisions = [...new Set([...primary, ...copies])];
    const linkedEntities = [...new Set(row.entities || [])].map(code => entities.get(code)).filter(Boolean);
    const linkedFacets = facetAxes.flatMap(axis => {
      const values = row.facets?.[axis];
      return (Array.isArray(values) ? values : values ? [values] : []).map(value => {
        const id = 'facet:' + axis + ':' + value;
        if (!facets.has(id)) facets.set(id, { id, kind: 'facet', axis, label: value.replaceAll('_', ' '), memories: [] });
        return facets.get(id);
      });
    });
    const memory = {
      ...row, id: 'memory:' + row.key, kind: 'memory', evidence,
      topics: associations, sources: revisions, sameTextSources: copies,
      entities: linkedEntities, facets: linkedFacets, state: states.get(row.state_key), corrections: [],
    };
    memories.set(row.key, memory);
    for (const item of [...evidence, ...associations, ...revisions, ...linkedEntities, ...linkedFacets]) item.memories.push(memory);
    memory.state?.memories.push(memory);
  }
  for (const memory of memories.values()) {
    const previous = memories.get(memory.supersedes);
    if (previous) previous.corrections.push(memory);
  }
  const roots = [...topics.values()].filter(topic => !topic.parents.some(parent => topics.has(parent)));
  const topicTotals = new Map();

  function topicMemories(topic, visited = new Set()) {
    if (!topic || visited.has(topic)) return [];
    if (topicTotals.has(topic)) return topicTotals.get(topic);
    const trail = new Set([...visited, topic]);
    const all = [...new Set([...topic.memories, ...topic.children.flatMap(child => topicMemories(child, trail))])];
    topicTotals.set(topic, all);
    return all;
  }

  function lineage(topic) {
    const parents = [];
    let current = topic;
    while (current && !parents.includes(current)) {
      parents.unshift(current);
      current = current.parents.map(key => topics.get(key)).find(Boolean);
    }
    return parents;
  }

  let currentTopic = topics.get(query.get('topic')) || roots[0];
  const requestedNode = query.get('node');
  const linkedNode = requestedNode && [...entities.values(), ...facets.values(), ...sources.values()]
    .find(node => node.id === requestedNode);
  let selected = linkedNode || currentTopic || memories.values().next().value || sources.values().next().value;
  const overviewEntry = query.get('mode') === 'all' || query.has('show') || linkedNode;
  let browseTopic = topics.get(query.get('topic')) || (overviewEntry ? undefined : currentTopic);
  let mode = searching || linkedNode || query.get('mode') === 'memory' || !currentTopic ? 'memory'
    : query.get('mode') === 'all' ? 'all' : 'topic';
  const catalogs = {
    entity: { label: 'entityCatalog', empty: 'noEntities', nodes: [...entities.values()] },
    facet: { label: 'facets', empty: 'noFacets', nodes: [...facets.values()] },
    source: { label: 'sourceCatalog', empty: 'noSources', nodes: [...sources.values()] },
  };
  let catalog = !searching && Object.hasOwn(catalogs, query.get('show')) ? query.get('show') : undefined;
  const catalogFilter = byId('catalog-filter');
  let relatedFrom;
  let displayed;
  let referenceMemory;
  let catalogRendered;

  function rankedMemories(topic) {
    return topic ? topicMemories(topic) : [...memories.values()];
  }

  function compareRecentSources(a, b) {
    return Number(b.active !== false) - Number(a.active !== false)
      || (b.opened_version ?? -1) - (a.opened_version ?? -1);
  }

  function newestTopicMemories(candidates) {
    const version = memory => Math.max(-1, ...memory.sources
      .filter(source => source.active !== false).map(source => source.opened_version ?? -1));
    return [...candidates].sort((a, b) => version(b) - version(a));
  }

  function isImportedMemory(memory) {
    return progress?.change?.paths?.some(path => memory.sources.some(source => source.checksum === path.checksum));
  }

  function importedFirst(candidates) {
    if (!progress?.change?.paths?.length) return candidates;
    const imported = [];
    const other = [];
    for (const memory of candidates) (isImportedMemory(memory) ? imported : other).push(memory);
    return [...imported, ...other];
  }

  function neighbors(memory) {
    const result = new Map();
    function add(items, reason) {
      for (const item of items) if (item !== memory && !result.has(item)) result.set(item, reason);
    }
    for (const line of memory.evidence) add(line.memories, 'sameLine');
    for (const source of memory.sources) add(source.memories, 'sameSource');
    for (const topic of memory.topics) add(topic.memories, 'sameTopic');
    return result;
  }

  function cooccurring(node, records = node.memories) {
    const peers = new Map();
    for (const memory of records) {
      for (const peer of [...memory.entities, ...memory.facets]) {
        if (peer === node) continue;
        if (!peers.has(peer)) peers.set(peer, new Set());
        peers.get(peer).add(memory);
      }
    }
    return [...peers].sort((first, second) => second[1].size - first[1].size || first[0].label.localeCompare(second[0].label));
  }

  function connection(from, to, kind) {
    return { from, to, kind };
  }

  function sharedTopics(topic) {
    const peers = new Map();
    for (const memory of topic.memories) {
      if (memory.active === false || !memory.evidence.length) continue;
      for (const other of memory.topics) {
        if (other !== topic) peers.set(other, (peers.get(other) || 0) + 1);
      }
    }
    return [...peers].sort((first, second) => second[1] - first[1]);
  }

  function topicConnections(present, focus, limit) {
    const edges = [];
    const shared = [];
    for (const node of present) {
      for (const parent of node.parents) {
        const upper = topics.get(parent);
        if (present.has(upper)) edges.push(connection(upper, node, 'hierarchy'));
      }
      for (const [peer, count] of sharedTopics(node)) {
        if (!present.has(peer) || node.code >= peer.code || node.parents.includes(peer.code) || peer.parents.includes(node.code)) continue;
        shared.push({ ...connection(node, peer, 'shared'), count });
      }
    }
    const touchesFocus = edge => edge.from === focus || edge.to === focus;
    shared.sort((a, b) => Number(touchesFocus(b)) - Number(touchesFocus(a)) || b.count - a.count);
    const focusedCount = shared.filter(touchesFocus).length;
    return [...edges, ...shared.slice(0, Math.max(focusedCount, limit))];
  }

  function allTopicScene() {
    const groups = new Map();
    for (const topic of topics.values()) {
      const path = lineage(topic);
      const root = path[0];
      if (!groups.has(root)) groups.set(root, new Map());
      const levels = groups.get(root);
      const depth = path.length - 1;
      if (!levels.has(depth)) levels.set(depth, []);
      levels.get(depth).push(topic);
    }
    const coordinates = new Map();
    const sector = 2 * Math.PI / Math.max(groups.size, 1);
    const rootRadius = groups.size === 1 ? 0 : Math.max(2.6, groups.size * .36);
    for (const [branch, levels] of [...groups.values()].entries()) {
      const center = branch * sector - Math.PI / 2;
      for (const [depth, nodes] of levels) {
        const arc = sector * .88;
        const rows = Math.ceil(nodes.length / Math.max(1, Math.floor(5.2 * arc)));
        const width = Math.ceil(nodes.length / rows);
        for (const [slot, node] of nodes.entries()) {
          const row = Math.floor(slot / width);
          const angle = center + arc * ((slot % width + .5) / width - .5);
          const radius = depth ? rootRadius + 1.35 + Math.min(depth, 3) * .45 + row * 1.05 : rootRadius;
          coordinates.set(node, [Math.cos(angle) * radius, 1.65 - depth * 1.45, Math.sin(angle) * radius]);
          node.overviewRoot = depth === 0;
        }
      }
    }
    const present = new Set(topics.values());
    return { layers: [{ kind: 'topic', nodes: [...present] }], coordinates, edges: topicConnections(present, currentTopic, 26) };
  }

  function topicScene(topic) {
    if (!topic) return { layers: [], edges: [] };
    const path = lineage(topic);
    const trunk = path.slice(-3, -1);
    const below = topic.children.slice(0, 14);
    const peers = new Set();
    if (!below.length) {
      const relatives = [...sharedTopics(topic).map(([node]) => node), ...(trunk.at(-1)?.children || [])];
      for (const node of relatives) {
        if (node !== topic && !trunk.includes(node)) peers.add(node);
        if (peers.size === 11) break;
      }
    }
    const grandchildren = below.flatMap(child => child.children).filter(node => !below.includes(node)).slice(0, 8);
    const levels = [
      ...trunk.map(parent => [parent]),
      [topic, ...peers],
      below,
      [...new Set(grandchildren)],
    ].filter(level => level.length);
    const present = new Set(levels.flat());
    return { layers: levels.map(nodes => ({ kind: 'topic', nodes })), edges: topicConnections(present, topic, 12) };
  }

  function memoryScene(node) {
    const focused = node?.kind === 'memory' ? node
      : node?.kind === 'line' ? referenceMemory : undefined;
    let candidates = ['source', 'state', 'entity', 'facet'].includes(node?.kind)
      ? node.memories : rankedMemories(currentTopic);
    if (isAnnotation(node)) {
      const related = new Set(relatedFrom?.memories || []);
      const ranked = [...candidates].sort((a, b) => Number(related.has(b)) - Number(related.has(a))
        || Number(b.active !== false) - Number(a.active !== false) || b.entities.length + b.facets.length - a.entities.length - a.facets.length);
      const seenSources = new Set();
      const diverse = ranked.filter(memory => {
        if (seenSources.has(memory.sources[0])) return false;
        seenSources.add(memory.sources[0]);
        return true;
      });
      candidates = [...ranked.filter(memory => related.has(memory)), ...diverse, ...ranked];
    }
    if (searching && node?.kind === 'topic') candidates = [...candidates, ...memories.values()];
    if (node?.kind === 'topic' && !searching) candidates = importedFirst(newestTopicMemories(candidates));
    if (focused) {
      const explicit = [memories.get(focused.supersedes), ...focused.corrections].filter(Boolean);
      candidates = [focused, ...explicit, ...neighbors(focused).keys(), ...candidates];
    }
    const selectedMemories = [];
    const sourceSet = new Set(node?.kind === 'source' ? [node] : []);
    if (!memories.size) for (const source of [...sources.values()].slice(0, 9)) sourceSet.add(source);
    for (const memory of new Set(candidates)) {
      const newSources = memory.sources.filter(source => !sourceSet.has(source));
      if (selectedMemories.length >= (focused || isAnnotation(node) ? 6 : 10)) break;
      if (selectedMemories.length && sourceSet.size + newSources.length > 7) continue;
      selectedMemories.push(memory);
      for (const source of newSources) sourceSet.add(source);
    }

    const topicSet = new Set(currentTopic && !isAnnotation(node) && node?.kind !== 'source' ? [currentTopic] : []);
    if (focused) for (const topic of focused.topics) topicSet.add(topic);
    if (searching && node?.kind === 'topic') for (const topic of topics.values()) topicSet.add(topic);
    for (const memory of selectedMemories) {
      for (const topic of memory.topics) if (topicSet.size < 9) topicSet.add(topic);
    }
    let quoted = focused?.evidence || [];
    if (node?.kind === 'topic' && progress?.change?.paths?.length) {
      quoted = [...new Set(selectedMemories.filter(isImportedMemory).flatMap(memory => memory.evidence))];
    }
    if (searching && node?.kind === 'topic' && progress?.saved_search) {
      quoted = [...new Set(selectedMemories.flatMap(memory => memory.evidence))];
    }
    if (isAnnotation(node) || node?.kind === 'source') {
      quoted = [...new Set(selectedMemories.flatMap(memory => memory.evidence))];
      if (node.kind === 'entity') quoted = MemoryScene.entityQuotes(node, quoted);
    }
    quoted = quoted.slice(0, 8);
    if (node?.kind === 'line' && !quoted.includes(node)) quoted.unshift(node);
    const stateSet = new Set([focused?.state, node?.kind === 'state' ? node : null].filter(Boolean));
    const entitySet = new Set(node?.kind === 'entity' ? [node] : focused?.entities || []);
    const facetSet = new Set(node?.kind === 'facet' ? [node] : focused?.facets || []);
    for (const memory of selectedMemories) {
      for (const entity of memory.entities) if (entitySet.size < 8) entitySet.add(entity);
      for (const facet of memory.facets) if (facetSet.size < 8) facetSet.add(facet);
    }
    const edges = [];
    if (isAnnotation(node)) for (const [peer] of cooccurring(node, selectedMemories)) {
      if (entitySet.has(peer) || facetSet.has(peer)) edges.push(connection(node, peer, 'cooccurrence'));
    }
    for (const topic of topicSet) {
      for (const parent of topic.parents) if (topicSet.has(topics.get(parent))) {
        edges.push(connection(topics.get(parent), topic, 'hierarchy'));
      }
    }
    for (const memory of selectedMemories) {
      for (const topic of memory.topics) if (topicSet.has(topic)) edges.push(connection(topic, memory, 'association'));
      for (const entity of memory.entities) if (entitySet.has(entity)) edges.push(connection(entity, memory, 'mention'));
      for (const facet of memory.facets) if (facetSet.has(facet)) edges.push(connection(facet, memory, 'facet'));
      if (memory.state && stateSet.has(memory.state)) edges.push(connection(memory.state, memory, 'association'));
      const visibleEvidence = memory.evidence.filter(line => quoted.includes(line));
      for (const line of visibleEvidence) edges.push(connection(memory, line, 'citation'));
      for (const source of memory.sources) if (!visibleEvidence.some(line => line.revision_checksum === source.checksum)) {
        edges.push(connection(memory, source, 'citation'));
      }
      const previous = memories.get(memory.supersedes);
      if (selectedMemories.includes(previous)) edges.push(connection(memory, previous, 'correction'));
    }
    for (const line of quoted) {
      const source = sources.get(line.revision_checksum);
      if (source) edges.push(connection(line, source, 'citation'));
    }
    return {
      layers: [
        { kind: 'topic', nodes: [...topicSet] },
        { kind: 'entity', nodes: [...entitySet] },
        { kind: 'facet', nodes: [...facetSet] },
        { kind: 'state', nodes: [...stateSet] },
        { kind: 'memory', nodes: selectedMemories },
        { kind: 'line', nodes: quoted },
        { kind: 'source', nodes: [...sourceSet] },
      ].filter(layer => layer.nodes.length),
      edges,
    };
  }

  const canvas = byId('graph');
  const view = new MemoryScene(canvas, byId('graph-labels'), node => {
    const pair = displayed.edges.some(edge => edge.kind === 'cooccurrence' && edge.from === selected && edge.to === node);
    choose(node, pair ? selected : undefined);
  }, renderZoom);
  const covered = new Set([...memories.values()].filter(memory => memory.active !== false && memory.topics.length)
    .flatMap(memory => memory.evidence.map(line => JSON.stringify([line.source, line.conversation_id])))).size;
  const annotatedSources = [...sources.values()].filter(source => source.active !== false
    && source.memories.some(memory => memory.entities.length || memory.facets.length)).length;

  function choose(node, via) {
    if (!node) return;
    const previousMode = mode;
    const previous = selected;
    relatedFrom = via;
    selected = node;
    if (node.kind === 'topic') {
      currentTopic = node;
      browseTopic = node;
    } else {
      mode = 'memory';
      if (node.kind === 'memory') referenceMemory = node;
      if (node.kind === 'line') {
        const anchor = isAnnotation(previous) ? previous.memories.find(memory => memory.evidence.includes(node)) : undefined;
        referenceMemory = anchor || (node.memories.includes(referenceMemory) ? referenceMemory : node.memories[0]);
      }
      const candidates = node.topics || node.memories?.[0]?.topics || [];
      if (candidates.length && !rankedMemories(currentTopic).includes(node.kind === 'memory' ? node : node.memories?.[0])) {
        currentTopic = candidates[0];
      }
    }
    if (previousMode !== mode) view.reset();
    render();
    byId('detail').scrollTop = 0;
  }

  function changeMode(next) {
    if (next === mode) return;
    if (mode === 'all' && next !== 'all' && !browseTopic) {
      const imported = progress?.change?.paths?.[0];
      const topic = topics.get(imported?.topics?.[0]);
      if (topic) {
        currentTopic = browseTopic = selected = topic;
      }
    }
    mode = next;
    if (mode !== 'memory') {
      selected = currentTopic;
      relatedFrom = undefined;
    }
    view.reset();
    render();
    if (progress?.change?.paths?.length) playWorkflowEffect();
  }

  function button(text, onClick, className) {
    const node = element('button', text, className);
    node.type = 'button';
    node.addEventListener('click', onClick);
    return node;
  }

  function paged(list, entries, size, append) {
    let offset = 0;
    const more = button(undefined, add, 'overview catalog-more');
    function add() {
      const end = Math.min(offset + size, entries.length);
      for (const entry of entries.slice(offset, end)) list.insertBefore(append(entry), more);
      offset = end;
      more.hidden = offset >= entries.length;
      more.textContent = t('more') + ' · ' + number.format(entries.length - offset);
    }
    list.append(more);
    add();
  }

  function renderCatalog() {
    const { nodes, empty } = catalogs[catalog];
    const list = byId('catalog-items');
    list.replaceChildren();
    const term = catalogFilter.value.normalize('NFKC').trim().toLowerCase().replaceAll('_', ' ');
    const matched = nodes.filter(node => [node.label, node.reference, ...(node.aliases || [])]
      .some(label => label?.normalize('NFKC').toLowerCase().replaceAll('_', ' ').includes(term)))
      .sort((a, b) => (catalog === 'source' ? compareRecentSources(a, b) : 0)
        || b.memories.length - a.memories.length || a.label.localeCompare(b.label));
    if (!matched.length) {
      list.append(element('p', t(nodes.length ? 'noMatches' : empty), 'catalog-group'));
      return;
    }
    const groups = catalog === 'facet' ? facetAxes.map(axis => [axis, matched.filter(node => node.axis === axis)]) : [[undefined, matched]];
    for (const [axis, items] of groups) {
      const group = element('div', undefined, 'catalog-group');
      if (axis) group.append(element('h3', t(axis) + ' · ' + number.format(items.length)));
      if (items.length) paged(group, items, axis ? 16 : 36, node => {
        const row = button(undefined, () => choose(node), 'tree-row');
        row.dataset.kind = node.kind;
        row.dataset.node = node.id;
        row.setAttribute('aria-current', String(node === selected));
        row.title = node.label;
        row.append(element('span', '', 'tree-symbol'), element('span', node.label),
          element('span', number.format(node.memories.length), 'tree-count'));
        return row;
      });
      list.append(group);
    }
  }

  function renderCatalogNavigation() {
    byId('sidebar').dataset.catalog = String(Boolean(catalog));
    byId('catalog-panel').hidden = !catalog;
    if (!catalog) {
      catalogRendered = undefined;
      return;
    }
    byId('catalog-title').textContent = t(catalogs[catalog].label);
    byId('catalog-coverage').textContent = number.format(annotatedSources) + '/'
      + number.format(snapshot.sources) + ' ' + t('scanned');
    catalogFilter.placeholder = t('filterCatalog');
    catalogFilter.setAttribute('aria-label', t('filterCatalog'));
    const key = catalog + ':' + language;
    if (catalogRendered !== key) {
      catalogRendered = key;
      renderCatalog();
      byId('catalog-items').scrollTop = 0;
    } else {
      for (const row of byId('catalog-items').querySelectorAll('[data-node]')) {
        row.setAttribute('aria-current', String(row.dataset.node === selected?.id));
      }
    }
  }

  function renderNavigation() {
    byId('nav-title').textContent = t(searching ? (snapshot.search_question ? 'questions' : 'terms') : 'topics');
    byId('coverage').textContent = searching || !topics.size
      ? number.format(snapshot.sources) + ' ' + t('workspace')
      : number.format(covered) + '/' + number.format(snapshot.sources) + ' ' + t('coverage');
    byId('overview').hidden = !searching;
    byId('overview').textContent = t('overview');
    const expanded = new Set(lineage(browseTopic));
    function branch(nodes, seen = new Set()) {
      const list = element('ul');
      for (const node of nodes) {
        if (seen.has(node)) continue;
        const item = element('li');
        const row = button(undefined, () => choose(node), 'tree-row');
        row.setAttribute('aria-current', String(node === browseTopic));
        row.title = node.label;
        row.append(element('span', '', 'tree-symbol'), element('span', node.label),
          element('span', number.format(topicMemories(node).length), 'tree-count'));
        item.append(row);
        if (expanded.has(node) && node.children.length) item.append(branch(node.children, new Set([...seen, node])));
        list.append(item);
      }
      return list;
    }
    byId('categories').replaceChildren(branch(roots));
    renderCatalogNavigation();
  }

  function renderBreadcrumbs() {
    const trail = isAnnotation(selected) || selected?.kind === 'source' ? [] : lineage(currentTopic);
    const path = byId('breadcrumbs');
    path.setAttribute('aria-label', t('path'));
    path.hidden = !trail.length || (trail.length < 2 && selected?.kind === 'topic');
    path.replaceChildren();
    for (const item of trail) {
      if (path.childElementCount) path.append(element('span', '/'));
      path.append(item === selected ? element('span', item.label) : button(item.label, () => choose(item)));
    }
    if (selected && selected.kind !== 'topic') {
      if (path.childElementCount) path.append(element('span', '/'));
      path.append(element('span', t(selected.kind)));
    }
  }

  function section(panel, key, entries) {
    if (!entries.length) return;
    const group = element('section');
    if (key) group.append(element('h3', t(key)));
    const list = element('div', undefined, 'detail-list');
    paged(list, entries, 12, entry => {
      const node = entry.node || entry;
      const row = button(undefined, () => choose(node, entry.via), 'detail-link');
      row.dataset.kind = node.kind;
      const text = element('span', undefined, 'link-copy');
      if (entry.note) text.append(element('small', entry.note));
      text.append(element('span', node.label));
      row.append(element('span', undefined, 'link-dot'), text, element('span', '›', 'link-arrow'));
      return row;
    });
    group.append(list);
    panel.append(group);
    return group;
  }

  function recordingClock(source) {
    if (source.local_started_at) return `${source.local_started_at.replace('T', ' ')} · ${t('timezoneUnknown')}`;
    return source.started_at?.replace('T', ' ');
  }

  function recordingScope(bounds) {
    const dates = [
      bounds.start_date && `${t('recordingFrom')} ${bounds.start_date}`,
      bounds.end_date && `${t('recordingThrough')} ${bounds.end_date}`,
    ].filter(Boolean);
    return dates.length ? [t('recordingDates'), ...dates].join(' · ') : '';
  }

  function recordingTimeline(panel, entries) {
    const days = new Map();
    for (const node of entries) {
      const source = node.sources[0];
      const day = source?.day || '';
      if (!days.has(day)) days.set(day, []);
      const note = source ? [recordingClock(source), source.label].filter(Boolean).join(' · ') : '';
      days.get(day).push({ node, note });
    }
    if (!days.size || (days.size === 1 && days.has(''))) return false;
    const timeline = element('section', undefined, 'recording-timeline');
    timeline.append(element('h3', t('recordingTimeline')));
    const dated = [...days.keys()].filter(Boolean).sort();
    if (days.has('')) dated.push('');
    for (const day of dated) {
      section(timeline, null, days.get(day)).prepend(element('h4', day || t('noDate')));
    }
    panel.append(timeline);
    return true;
  }

  function renderCatalogControls() {
    byId('annotations').hidden = searching;
    byId('annotations').setAttribute('aria-label', t('annotations'));
    for (const [kind, entry] of Object.entries(catalogs)) {
      const control = byId(kind + '-catalog');
      control.textContent = t(entry.label) + ' ' + number.format(entry.nodes.length);
      control.setAttribute('aria-pressed', String(catalog === kind));
    }
  }

  function graphQuery() {
    const params = new URLSearchParams(query);
    if (!searching && browseTopic) params.set('topic', browseTopic.code);
    else params.delete('topic');
    if (!searching) params.set('mode', mode);
    if (catalog) params.set('show', catalog);
    else params.delete('show');
    if (isAnnotation(selected) || selected?.kind === 'source') params.set('node', selected.id);
    else params.delete('node');
    if (snapshot.comparison_available) params.set('view', 'graph');
    params.set('lang', language);
    return params;
  }

  function renderViewLinks() {
    renderViewerNavigation(language, 'graph', graphQuery(), snapshot.comparison_available);
  }

  function citation(line, interactive = false) {
    const quote = element('div', undefined, 'citation');
    const metadata = [line.source_reference + '#' + line.segment_id, line.speaker_id,
      formatViewerOffset(line.start_ms)].filter(Boolean);
    const label = metadata.join(' · ');
    const heading = interactive ? button(label, () => choose(line), 'citation-link') : element('p', label);
    quote.append(heading, element('blockquote', line.text));
    if (line.matched_terms?.length) {
      quote.append(element('p', `${t('matchedTerms')} · ${line.matched_terms.join(' · ')}`, 'detail-meta'));
    }
    if (line.followup_terms?.length) {
      quote.append(element('p', `${t('followupQuote')} · ${line.followup_terms.join(' · ')}`, 'detail-meta'));
    }
    return quote;
  }

  function citationSection(panel, title, quotes) {
    if (!quotes.length) return;
    const original = element('section');
    original.append(element('h3', title + ' · ' + number.format(quotes.length)));
    const list = element('div');
    paged(list, quotes, 8, line => citation(line, true));
    original.append(list);
    panel.append(original);
  }

  function recordQuotes(records, diverse = false) {
    const quotes = [...new Set(records.flatMap(memory => memory.evidence))];
    if (!diverse) return quotes;
    const first = new Map();
    const rest = [];
    for (const quote of quotes) {
      if (first.has(quote.revision_checksum)) rest.push(quote);
      else first.set(quote.revision_checksum, quote);
    }
    return [...first.values(), ...rest];
  }

  function renderDetail() {
    const panel = byId('detail');
    panel.replaceChildren();
    if (!selected) {
      panel.append(element('p', t('empty')));
      return;
    }
    const kind = selected.kind;
    const header = element('div');
    const badge = element('p', t(kind), 'detail-kind');
    badge.dataset.kind = kind;
    header.append(badge, element('h2', selected.label));
    panel.append(header);

    if (kind === 'topic') {
      const all = rankedMemories(selected);
      header.append(element('p', number.format(all.length) + ' ' + t('records'), 'detail-meta'));
      if (searching && !all.length) panel.append(element('p', t('noMatches'), 'detail-meta'));
      if (!searching) {
        const originals = [...new Set(all.flatMap(memory => memory.sources))]
          .filter(source => source.active !== false)
          .sort((a, b) => compareRecentSources(a, b) || a.label.localeCompare(b.label));
        section(panel, 'topicSources', originals.map(node => ({
          node, note: node.line_count != null ? number.format(node.line_count) + ' ' + t('sourceLines') : undefined,
        })));
      }
      section(panel, 'parents', selected.parents.map(key => topics.get(key)).filter(Boolean));
      section(panel, 'children', selected.children.map(node => ({ node, note: number.format(topicMemories(node).length) + ' ' + t('records') })));
      section(panel, 'relatedTopics', sharedTopics(selected).map(([node, count]) => ({ node, note: number.format(count) + ' ' + t('shared') })));
      section(panel, 'entities', [...new Set(all.flatMap(memory => memory.entities))]);
      section(panel, 'facets', [...new Set(all.flatMap(memory => memory.facets))].map(node => ({ node, note: t(node.axis) })));
      if (mode !== 'memory' && all.length) panel.append(button(t('openEvidence'), () => changeMode('memory'), 'overview'));
      if (!searching || !recordingTimeline(panel, all)) {
        const entries = mode === 'memory' ? all : selected.memories;
        section(panel, 'memories', searching ? entries : importedFirst(newestTopicMemories(entries)));
      }
      return;
    }

    if (kind === 'memory') {
      const metadata = [];
      if (searching && selected.sources[0]) {
        const source = selected.sources[0];
        metadata.push(recordingClock(source) || source.day || t('noDate'));
      }
      if (selected.status) metadata.push(t(selected.status));
      if (selected.active === false || selected.effective === false) metadata.push(t('historical'));
      if (selected.state?.value) metadata.push(t('current') + ' · ' + selected.state.value);
      if (metadata.length) header.append(element('p', metadata.join(' · '), 'detail-meta'));
      section(panel, 'alsoFiled', selected.topics);
      section(panel, 'entities', selected.entities);
      section(panel, 'facets', selected.facets.map(node => ({ node, note: t(node.axis) })));
      citationSection(panel, t('original'), selected.evidence);
      section(panel, 'sources', selected.sources.filter(source => !selected.sameTextSources.includes(source)));
      section(panel, 'sameTextSources', selected.sameTextSources.map(node => ({
        node, note: recordingClock(node) || node.day || t('noDate'),
      })));
      section(panel, 'supersedes', [memories.get(selected.supersedes)].filter(Boolean));
      section(panel, 'correctedBy', selected.corrections);
      section(panel, 'related', [...neighbors(selected)].map(([node, reason]) => ({ node, note: t(reason) })));
      return;
    }

    if (kind === 'line') {
      const original = element('section');
      original.append(element('h3', t('original')), citation(selected));
      panel.append(original);
      section(panel, 'sources', [sources.get(selected.revision_checksum)].filter(Boolean));
    } else if (kind === 'source') {
      const metadata = [recordingClock(selected) || selected.day || t('noDate'), selected.active === false ? t('historical') : t('active')];
      header.append(element('p', metadata.join(' · '), 'detail-meta'));
      const count = selected.memories.filter(memory => memory.entities.length || memory.facets.length).length;
      header.append(element('p', count ? number.format(count) + ' ' + t('annotated') : t('unannotated'), 'detail-meta'));
      const countLabel = selected.line_count != null ? ' · ' + number.format(selected.line_count) + ' ' + t('sourceLines') : '';
      const original = element('a', t('readRevision') + countLabel, 'citation-link');
      original.href = '?' + new URLSearchParams({
        view: 'source', checksum: selected.checksum, lang: language,
        mode: 'memory', node: selected.id, ...(currentTopic && !searching ? { topic: currentTopic.code } : {}),
      });
      header.append(original);
      if (selected.active === false) {
        const link = element('a', t('compareRevision'), 'citation-link');
        link.href = '?' + new URLSearchParams({ view: 'revision', before: selected.checksum, lang: language });
        header.append(link);
      }
      citationSection(panel, t('original'), recordQuotes(selected.memories).filter(line => line.revision_checksum === selected.checksum));
    } else if (kind === 'entity' || kind === 'facet') {
      if (kind === 'facet') header.append(element('p', t(selected.axis), 'detail-meta'));
      if (selected.aliases?.length) header.append(element('p', t('aliases') + ' · ' + selected.aliases.join(' · '), 'detail-meta'));
      if (relatedFrom) {
        const fromRecords = new Set(relatedFrom.memories);
        const quotes = recordQuotes(selected.memories.filter(memory => fromRecords.has(memory)), true);
        citationSection(panel, t('sharedEvidence') + ' · ' + relatedFrom.label, quotes);
      }
      const peers = cooccurring(selected).map(([node, records]) => ({
        node, via: selected, note: [node.axis && t(node.axis), number.format(records.size) + ' ' + t('sharedPassages')].filter(Boolean).join(' · '),
      }));
      section(panel, 'relatedEntities', peers.filter(item => item.node.kind === 'entity'));
      section(panel, 'relatedFacets', peers.filter(item => item.node.kind === 'facet'));
      section(panel, 'linkedTopics', [...new Set(selected.memories.flatMap(memory => memory.topics))]);
      citationSection(panel, t('original'), recordQuotes(selected.memories, true));
    } else if (kind === 'state' && selected.value) {
      header.append(element('p', selected.value, 'detail-meta'));
    }
    section(panel, 'memories', selected.memories);
  }

  function renderLegend() {
    const kinds = new Set(displayed.layers.flatMap(layer => layer.nodes.map(node => node.kind)));
    const legend = byId('legend');
    legend.replaceChildren();
    for (const kind of kinds) {
      const item = element('span');
      item.dataset.kind = kind;
      item.append(element('i'), element('span', t(kind)));
      legend.append(item);
    }
    const meanings = [
      ['shared', 'shared', 'dashed'], ['correction', 'correction', 'correction'],
      ['hierarchy', 'hierarchy', 'solid'], ['citation', 'cites', 'solid'],
      ['mention', 'mention', 'mention'],
      ['facet', 'facetLink', 'facet'],
      ['cooccurrence', 'cooccurrence', 'cooccurrence'],
    ];
    for (const [kind, label, style] of meanings) if (displayed.edges.some(edge => edge.kind === kind)) {
      const item = element('span');
      item.append(element('i', undefined, style), element('span', t(label)));
      legend.append(item);
    }
  }

  function renderZoom(value) {
    byId('zoom-reset').textContent = Math.round(value * 100) + '%';
    byId('zoom-in').disabled = value >= 2.5;
    byId('zoom-out').disabled = value <= .65;
  }

  function renderWordForms(container, forms) {
    if (!forms?.length) return;
    const pairs = forms.map(({query_term, indexed_term}) => `${query_term} → ${indexed_term}`);
    container.append(element('p', [t('wordForms'), ...pairs].join(' · ')));
  }

  function renderRepeatedTerms(container, terms) {
    if (!terms?.length) return;
    const counts = terms.map(({term, occurrences}) => `${term} ×${number.format(occurrences)}`);
    container.append(element('p', [t('repeatedTerms'), ...counts].join(' · ')));
  }

  function termSummary(terms, coverage) {
    if (!coverage?.length) return element('p', terms.join(' · '));
    const list = element('ul', undefined, 'term-coverage');
    for (const row of coverage) {
      const counts = [number.format(row.candidate_spans) + ' ' + t('candidates'),
        number.format(row.quoted_lines) + ' ' + t('quotes')];
      const item = element('li');
      item.toggleAttribute('data-empty', !row.quoted_lines);
      item.append(element('strong', row.term), element('span', counts.join(' · ')));
      list.append(item);
    }
    return list;
  }

  function candidateTrace(packing, label) {
    const details = element('details', undefined, 'packing-trace');
    details.append(element('summary', [t('packingOrder'), label,
      `${number.format(packing.candidates.length)}/${number.format(packing.total)} ${t('candidates')}`].filter(Boolean).join(' · ')));
    const list = element('div', undefined, 'packing-candidates');
    function renderCandidate(candidate) {
      const row = element('div');
      const labels = [`#${candidate.rank}`, `${candidate.source}/${candidate.conversation_id}`];
      if ('source_date' in candidate) labels.push(candidate.source_date || t('noDate'));
      labels.push(`${t('packingSegments')} ${candidate.start_offset + 1}–${candidate.end_offset}`);
      const link = element('a', labels.join(' · '), 'citation-link');
      const params = new URLSearchParams({ view: 'source', checksum: candidate.revision_checksum,
        offset: candidate.start_offset, lang: language });
      if (query.has('run')) params.set('run', query.get('run'));
      link.href = '?' + params.toString();
      const status = [t('packingReasons')[candidate.reason]];
      if (candidate.corroborating_terms?.length) {
        status.push(t('corroboratingTerms') + ' · ' + candidate.corroborating_terms.join(' / '));
      }
      if (candidate.selection) {
        status.push(t('recordingSelection')[candidate.selection.reason] + ' · ' + candidate.selection.term);
        if (candidate.selection.labels?.length) {
          status.push(t('indexLabels') + ' · ' + candidate.selection.labels.join(' / '));
        }
        if (candidate.selection.clues?.length) status.push(candidate.selection.clues.join(' / '));
      }
      if (candidate.quoted_lines) status.push(`${number.format(candidate.quoted_lines)} ${t('quotes')}`);
      if (candidate.duplicate_lines) status.push(`${number.format(candidate.duplicate_lines)} ${t('duplicateLines')}`);
      if (candidate.trimmed_lines) status.push(`${number.format(candidate.trimmed_lines)} ${t('trimmedLines')}`);
      if (candidate.reason === 'quoted' && candidate.lines_over_budget) {
        status.push(`${t('packingReasons').quote_budget} ${number.format(candidate.lines_over_budget)}`);
      }
      row.append(link, element('span', status.join(' · ')));
      return row;
    }
    if (new Set(packing.candidates.map(candidate => candidate.source_date)).size > 1) {
      const toggle = element('input');
      toggle.type = 'checkbox';
      const control = element('label', undefined, 'packing-sort');
      control.append(toggle, t('sortCandidatesByDate'));
      details.append(control);
      toggle.addEventListener('change', () => {
        const entries = toggle.checked ? [...packing.candidates].sort((left, right) =>
          Number(!left.source_date) - Number(!right.source_date)
          || (left.source_date || '').localeCompare(right.source_date || '')
          || left.rank - right.rank) : packing.candidates;
        list.replaceChildren();
        paged(list, entries, 12, renderCandidate);
      });
    }
    paged(list, packing.candidates, 12, renderCandidate);
    details.append(list);
    return details;
  }

  const workflowCopy = {
    zh: {
      import: '记忆导入', query: '记忆查询', preview: '已预览来源', normalize: '已整理原文',
      annotate: '已关联标注', commit: '已写入', verify: '已验证', compile: '已编译检索词',
      search: '已找到原文', pending: '等待开始', failed: '阶段失败',
      unchanged: '无新增记忆', empty: '没有命中原文', unavailable: '运行记录不可用',
      changes: '查看变更', evidence: '查看引用', replay: '重播', locateTopic: '定位主题', importedPath: '新增原文',
    },
    en: {
      import: 'Memory import', query: 'Memory search', preview: 'Sources previewed',
      normalize: 'Original normalized', annotate: 'Annotations linked', commit: 'Committed',
      verify: 'Verified', compile: 'Query compiled', search: 'Evidence found',
      pending: 'Waiting to start', failed: 'Stage failed', unchanged: 'No new memory',
      empty: 'No matching evidence', unavailable: 'Run unavailable',
      changes: 'View change', evidence: 'View citations', replay: 'Replay',
      locateTopic: 'Locate topic', importedPath: 'New source',
    },
  };

  function renderFilterTrace() {
    if (!filterTrace) return;
    const zh = language === 'zh';
    const overlay = byId('workflow-activity');
    overlay.hidden = false;
    byId('workflow-status').textContent = (zh ? '已标注片段' : 'Annotated passages')
      + ' · ' + number.format(filterTrace.initial_count);
    const replay = byId('workflow-replay');
    replay.hidden = false;
    replay.textContent = workflowCopy[language].replay;
    const steps = byId('filter-trace-steps');
    steps.hidden = false;
    steps.replaceChildren(...filterTrace.steps.map((step, i) => {
      const shown = i <= filterStep;
      const item = element('li', `${t(step.axis || step.kind)}：${step.label} · ${shown ? number.format(step.count) : '…'}`);
      if (i === filterStep) item.setAttribute('aria-current', 'step');
      if (shown && step.count === 0) item.dataset.empty = 'true';
      return item;
    }));

    byId('filter-trace-details')?.remove();
    if (filterStep !== filterTrace.steps.length - 1) return;
    const details = element('section', undefined, 'filter-trace-details');
    details.id = 'filter-trace-details';
    const count = number.format(filterTrace.reference_count);
    details.append(element('h3', zh ? `同时符合三项 · ${count} 条原文` : `All three labels · ${count} citations`));
    if (!filterTrace.reference_count) {
      details.append(element('p', zh ? '没有片段同时标注这三项。' : 'No passage has all three labels.'));
    } else {
      const quotes = filterTrace.references.map(id => lines.get(id)).filter(Boolean);
      citationSection(details, t('original'), quotes);
    }
    byId('detail').prepend(details);
  }

  function playFilterTrace() {
    clearTimeout(filterTimer);
    filterStep = -1;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    function advance() {
      filterStep = reduced ? filterTrace.steps.length - 1 : filterStep + 1;
      const step = filterTrace.steps[filterStep];
      view.playWorkflowEffect(step.count ? 'query' : null, step.count ? [[step.id]] : []);
      renderFilterTrace();
      if (filterStep + 1 < filterTrace.steps.length) filterTimer = setTimeout(advance, 950);
    }
    advance();
  }

  function renderWorkflow() {
    if (!progress) return;
    const labels = workflowCopy[language];
    const search = progress.pipeline === 'context-search';
    const done = [...progress.stages].reverse().find(stage => stage.state === 'completed');
    const committed = progress.stages.find(stage => stage.name === 'commit');
    const status = progress.state === 'failed' ? labels.failed
      : search && done?.name === 'search' && !done.citations?.length ? labels.empty
      : committed?.changed === false ? labels.unchanged : labels[done?.name || 'pending'];
    byId('workflow-activity').hidden = false;
    const statusElement = byId('workflow-status');
    const message = labels[search ? 'query' : 'import'] + ' · ' + status;
    if (statusElement.textContent !== message) statusElement.textContent = message;
    const result = byId('workflow-result');
    const replay = byId('workflow-replay');
    const path = progress.change?.paths?.[0];
    replay.hidden = !path && !(search && done?.citations?.length);
    replay.textContent = labels.replay;
    const topic = path?.topics?.[0];
    const topicLink = byId('workflow-topic');
    const sourceLink = byId('workflow-source');
    const graph = { view: 'graph', lang: language, follow: progress.run_id };
    topicLink.hidden = !topic;
    sourceLink.hidden = !path;
    if (topic) {
      topicLink.href = '?' + new URLSearchParams({ ...graph, topic, mode: 'topic' });
      topicLink.textContent = labels.locateTopic;
    }
    if (path) {
      sourceLink.href = '?' + new URLSearchParams({
        ...graph, ...(topic ? { topic } : {}), mode: 'memory', node: 'source:' + path.checksum,
      });
      sourceLink.textContent = labels.importedPath;
    }
    const changed = progress.change?.source_changes?.[0];
    const target = progress.saved_search ? { view: 'graph', run: progress.saved_search, follow: progress.run_id }
      : changed?.has_graph ? { view: 'changes', event: progress.commit_version, checksum: changed.checksum }
      : progress.commit_version != null ? { view: 'activity', event: progress.commit_version } : null;
    result.hidden = !target;
    if (target) {
      result.href = '?' + new URLSearchParams({ ...target, lang: language });
      result.textContent = labels[search ? 'evidence' : 'changes'];
    }
  }

  function playWorkflowEffect() {
    if (!progress) return;
    if (progress.change?.paths?.length) {
      view.playWorkflowEffect('import', progress.change.paths.map(path => {
        const source = sources.get(path.checksum);
        return [
          'source:' + path.checksum,
          ...path.topics.map(code => topics.get(code)?.id).filter(Boolean),
          ...(source?.memories || []).map(memory => memory.id),
          ...(source?.memories || []).flatMap(memory => memory.evidence.map(line => line.id)),
          ...path.lines.map(line => 'line:' + line.line_id),
        ];
      }));
      return;
    }
    const citations = progress.stages.find(stage => stage.name === 'search')?.citations;
    if (!citations?.length) return;
    const paths = new Map();
    for (const citation of citations) {
      if (!paths.has(citation.checksum)) {
        paths.set(citation.checksum, new Set(['source:' + citation.checksum]));
      }
      const path = paths.get(citation.checksum);
      const line = lines.get(citation.line_id);
      for (const memory of line?.memories || []) {
        path.add(memory.id);
        for (const topic of memory.topics) path.add(topic.id);
      }
      path.add('line:' + citation.line_id);
    }
    view.playWorkflowEffect('query', [...paths.values()].slice(0, 3));
  }

  function watchWorkflow() {
    if (!progress || progress.state === 'completed') return;
    setTimeout(async () => {
      if (!document.hidden) {
        try {
          const params = new URLSearchParams({ progress: progress.run_id });
          const response = await fetch('?' + params, { cache: 'no-store' });
          if ([404, 410].includes(response.status)) {
            byId('workflow-status').textContent = workflowCopy[language].unavailable;
            for (const id of ['workflow-result', 'workflow-topic', 'workflow-source', 'workflow-replay']) {
              byId(id).hidden = true;
            }
            view.playWorkflowEffect(null, []);
            progress = undefined;
            return;
          }
          if (!response.ok) throw new Error('Could not read progress');
          const next = await response.json();
          const newResult = next.saved_search !== progress.saved_search || next.commit_version !== progress.commit_version;
          progress = next;
          renderWorkflow();
          if (newResult) playWorkflowEffect();
        } catch {
          // A local read can race the writer; retry without disturbing the graph.
        }
      }
      watchWorkflow();
    }, 800);
  }

  function render() {
    displayed = mode === 'all' ? allTopicScene() : mode === 'topic' ? topicScene(currentTopic) : memoryScene(selected);
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
    renderWorkflow();
    byId('title').textContent = t(searching ? 'searchTitle' : 'title');
    byId('graph-title').textContent = mode === 'all' ? t('allMode') : selected?.label || t('fallback');
    const scope = mode === 'all' ? rankedMemories()
      : selected?.kind === 'memory' ? [selected]
        : selected?.kind !== 'topic' && selected?.memories ? selected.memories : rankedMemories(currentTopic);
    const passageCount = scope.filter(node => !node.fact_id).length;
    const nodeCount = displayed.layers.reduce((count, layer) => count + layer.nodes.length, 0);
    byId('graph-summary').textContent = [
      number.format(groupedSearch ? snapshot.search_subquestion_branches.length : topics.size)
        + ' ' + t(searching ? (snapshot.search_question ? 'questions' : 'terms') : 'topics'),
      number.format(passageCount) + ' ' + t('record'),
      number.format(nodeCount) + ' ' + t('scope'),
    ].join(' · ');
    byId('empty').textContent = t('empty');
    byId('empty').hidden = nodeCount > 0;
    byId('search').hidden = !snapshot.search_available;
    byId('search-terms').placeholder = t('placeholder');
    byId('search-terms').setAttribute('aria-label', t('placeholder'));
    byId('search-submit').textContent = t('search');
    byId('recording-filter-label').textContent = t('recordingDates');
    byId('recording-start-label').textContent = t('recordingFrom');
    byId('recording-end-label').textContent = t('recordingThrough');
    byId('comparison-filter-label').textContent = t('compareQuestions');
    byId('comparison-question-label').textContent = t('secondQuestion');
    byId('comparison-version-label').textContent = t('baselineVersion');
    byId('comparison-recording-label').textContent = t('secondRecordingDates');
    byId('compare-recording-start-label').textContent = t('recordingFrom');
    byId('compare-recording-end-label').textContent = t('recordingThrough');
    byId('matching-lines-label').textContent = t('matchingLines');
    const versionFilter = byId('search-version');
    versionFilter.hidden = snapshot.history_origin == null;
    if (!versionFilter.hidden) {
      versionFilter.min = snapshot.history_origin;
      versionFilter.max = snapshot.version;
      versionFilter.placeholder = t('versionFilter');
      versionFilter.setAttribute('aria-label', t('versionFilterLabel'));
    }
    byId('comparison-version-label').hidden = byId('comparison-version').hidden = versionFilter.hidden;
    if (!versionFilter.hidden) {
      byId('comparison-version').min = snapshot.history_origin;
      byId('comparison-version').max = snapshot.version - 1;
    }
    const trace = byId('search-trace');
    trace.hidden = !snapshot.search_trace;
    if (snapshot.search_trace) {
      const stats = snapshot.search_trace;
      trace.replaceChildren(element('strong', snapshot.search_question ? t('compile') : t('terms')));
      if (comparison.length) {
        const branches = element('div', undefined, 'comparison-trace');
        for (const part of comparison) {
          const branch = element('div');
          const history = part.history;
          branch.append(button(history ? `${t('history')} v${history.version}` : part.question,
            () => choose(topics.get(part.key)), 'citation-link'));
          if (history) {
            branch.append(element('p', new Date(history.committed_at).toLocaleString(language)));
            if (history.later_deletions) branch.append(element('p', t('historyDeletes')));
            const changedLine = lines.get(part.version_evidence);
            if (changedLine) {
              branch.append(button(t('openVersionEvidence') + ' · ' + changedLine.segment_id,
                () => choose(changedLine), 'citation-link'));
            }
          }
          if (part.recording_bounds) {
            branch.append(element('p', recordingScope(part.recording_bounds) || t('allRecordingDates')));
          }
          const citedDates = [...(part.evidence_recording_dates || [])];
          if (part.has_undated_evidence) citedDates.push(t('noDate'));
          if (citedDates.length && (citedDates.length !== 1 || citedDates[0] !== part.recording_bounds?.start_date
            || citedDates[0] !== part.recording_bounds?.end_date)) {
            branch.append(element('p', t('evidenceRecordingDates') + ' · ' + citedDates.join(' / ')));
          }
          branch.append(
            termSummary(part.terms, part.term_coverage),
            element('p', `${number.format(part.passages)} ${t('record')} · ${number.format(part.quote_bytes)}/${number.format(part.quote_budget_bytes)} ${t('quoteBytes')}`));
          renderWordForms(branch, part.word_forms);
          renderRepeatedTerms(branch, part.repeated_terms);
          const phaseEvidence = part.phase_evidence || [];
          const unlinkedPhases = (part.change_phases || []).filter(phase => !phaseEvidence.some(pick => pick.term === phase));
          if (unlinkedPhases.length) {
            branch.append(element('p', t('changePhases') + ' · ' + unlinkedPhases.join(' / ')));
          }
          for (const pick of phaseEvidence) {
            const line = lines.get(pick.source_line_id);
            if (!line) continue;
            const label = ['dated_plan_followup', 'date_corroboration', 'role_corroboration', 'focused_date', 'focused_color'].includes(pick.reason)
              ? t('recordingSelection')[pick.reason] : '';
            branch.append(button([label, pick.term, line.conversation_id + '#' + line.segment_id].filter(Boolean).join(' · '),
              () => choose(line), 'citation-link'));
          }
          if (part.first_lines_prioritized) {
            branch.append(element('p', t(history ? 'versionLinesPrioritized' : 'firstLinesPrioritized')));
          }
          branches.append(branch);
        }
        trace.append(branches);
      } else {
        if (stats.implementation_search) {
          trace.append(element('p', `${t('implementationSearch')} · ${stats.implementation_search}`));
        }
        trace.append(termSummary(stats.terms, stats.term_coverage));
        renderWordForms(trace, stats.word_forms);
        renderRepeatedTerms(trace, stats.repeated_terms);
        if (stats.change_phases?.length) {
          trace.append(element('p', t('changePhases') + ' · ' + stats.change_phases.join(' / ')));
        }
        if (stats.subquestions?.length) {
          const list = element('ul');
          for (const part of stats.subquestions) {
            const labels = [...new Set((part.indexed_routes || []).flatMap(route => route.labels))];
            const clues = [...new Set([...part.anchors, ...part.terms])];
            const item = [part.question, clues.join(' / '), labels.length ? `${t('indexLabels')} ${labels.join(' / ')}` : ''];
            const row = element('li', item.filter(Boolean).join(' · '));
            for (const [id, question] of Object.entries(snapshot.search_branch_evidence || {})) {
              const line = lines.get(id);
              if (question === part.question && line) {
                row.append(' ', button(`${line.conversation_id}#${line.segment_id}`, () => choose(line), 'citation-link'));
              }
            }
            list.append(row);
          }
          trace.append(element('h4', t('subquestions')), list);
        }
      }
      trace.append(element('p', [number.format(stats.candidate_spans) + ' ' + t('candidates'),
          stats.candidate_sources === null ? null : number.format(stats.candidate_sources) + ' ' + t('candidateSources'),
          stats.anchor_sources ? number.format(stats.anchor_sources) + ' ' + t('anchorSources') : null,
          number.format(stats.sources) + ' ' + t('sourceCatalog'),
          number.format(stats.quotes) + ' ' + t('quotes'),
          stats.followup_quotes ? number.format(stats.followup_quotes) + ' ' + t('followupQuotes') : null,
          stats.shared_quotes ? number.format(stats.shared_quotes) + ' ' + t('sharedQuotes') : null,
          stats.trimmed_lines ? number.format(stats.trimmed_lines) + ' ' + t('trimmedLines') : null,
          number.format(stats.quote_bytes) + ' ' + t('quoteBytes')].filter(Boolean).join(' · ')));
      if (stats.packing?.length) {
        for (const packing of stats.packing) {
          trace.append(candidateTrace(packing, stats.packing.length > 1 ? packing.label : ''));
        }
      } else if (stats.candidate_spans) {
        trace.append(button(t('inspectCandidates'), () => {
          explainCandidates = true;
          byId('search').requestSubmit();
        }, 'citation-link'));
      }
      if (snapshot.search_matching_lines) trace.prepend(element('p', t('matchingLines')));
      if (sourceScope.source || sourceScope.conversation_id) {
        const scope = element('p', `${t('sourceScope')} · ${[sourceScope.source, sourceScope.conversation_id].filter(Boolean).join('/')}`);
        scope.append(' · ', button(t('allSources'), () => {
          searchAllSources = true;
          byId('search').requestSubmit();
          searchAllSources = false;
        }, 'citation-link'));
        trace.prepend(scope);
      }
      if (snapshot.search_history) {
        const history = snapshot.search_history;
        trace.prepend(element('p', `${t('history')} v${history.version} · ${new Date(history.committed_at).toLocaleString(language)}`));
        if (history.later_deletions) trace.append(element('p', t('historyDeletes')));
      }
      const scopeLabel = recordingScope(recordingBounds);
      if (scopeLabel && !comparison.some(part => part.recording_bounds)) {
        trace.prepend(element('p', scopeLabel));
        if (!stats.quotes) trace.append(element('p', t('noDatedResults')));
      }
      if (snapshot.saved_search) {
        const saved = snapshot.saved_search;
        const current = saved.version === snapshot.version ? '' : ` · ${t('currentWorkspace')} v${snapshot.version}`;
        trace.prepend(element('p', `${t('savedSearch')} v${saved.version} · ${new Date(saved.searched_at).toLocaleString(language)}${current}`));
      }
    }
    byId('refresh').textContent = t('refresh');
    byId('language').textContent = language === 'zh' ? 'English' : '中文';
    byId('gesture').textContent = t('gesture');
    byId('modes').setAttribute('aria-label', t('view'));
    byId('all-mode').textContent = t('allMode');
    byId('all-mode').hidden = searching || !topics.size;
    byId('all-mode').setAttribute('aria-pressed', String(mode === 'all'));
    byId('topic-mode').textContent = t('topicMode');
    byId('topic-mode').disabled = searching || !topics.size;
    byId('topic-mode').setAttribute('aria-pressed', String(mode === 'topic'));
    byId('memory-mode').textContent = t('memoryMode');
    byId('memory-mode').setAttribute('aria-pressed', String(mode === 'memory'));
    byId('zoom-controls').setAttribute('aria-label', t('zoom'));
    byId('zoom-out').setAttribute('aria-label', t('zoomOut'));
    byId('zoom-in').setAttribute('aria-label', t('zoomIn'));
    byId('zoom-reset').setAttribute('aria-label', t('reset'));
    canvas.setAttribute('aria-label', t('graph'));
    renderCatalogControls();
    renderViewLinks();
    renderNavigation();
    renderBreadcrumbs();
    renderLegend();
    renderDetail();
    view.show(displayed, selected, mode);
    renderFilterTrace();
  }

  byId('search-terms').value = snapshot.search_question || (snapshot.search_terms || []).join('，');
  const targetVersion = byId('search-version');
  targetVersion.value = query.get('at') ?? (versionComparison ? comparison[1]?.history.version : snapshot.search_history?.version) ?? '';
  const recordingStart = byId('recording-start');
  const recordingEnd = byId('recording-end');
  recordingStart.value = recordingBounds.start_date ?? query.get('start_date') ?? '';
  recordingEnd.value = recordingBounds.end_date ?? query.get('end_date') ?? '';
  const recordingFilter = byId('recording-filter');
  recordingFilter.toggleAttribute('data-active', Boolean(recordingStart.value || recordingEnd.value));
  const secondQuestion = byId('comparison-question');
  const earlierVersion = byId('comparison-version');
  earlierVersion.value = query.get('from') ?? (versionComparison ? comparison[0]?.history.version : '') ?? '';
  const compareStart = byId('compare-recording-start');
  const compareEnd = byId('compare-recording-end');
  compareStart.value = compareDateOverrides.start_date ?? query.get('compare_start_date') ?? '';
  compareEnd.value = compareDateOverrides.end_date ?? query.get('compare_end_date') ?? '';
  function clearDateErrors() {
    for (const input of [recordingEnd, compareStart, compareEnd]) input.setCustomValidity('');
  }
  recordingFilter.addEventListener('input', clearDateErrors);
  const matchingLines = byId('matching-lines');
  matchingLines.checked = Boolean(snapshot.search_matching_lines);
  secondQuestion.value = versionComparison ? '' : comparison[1]?.question ?? query.get('q2') ?? '';
  function enableComparisonControls() {
    secondQuestion.disabled = earlierVersion.value !== '';
    earlierVersion.disabled = Boolean(secondQuestion.value.trim());
    compareStart.disabled = compareEnd.disabled = !secondQuestion.value.trim();
    earlierVersion.setCustomValidity('');
  }
  enableComparisonControls();
  byId('comparison-filter').toggleAttribute('data-active', Boolean(secondQuestion.value || earlierVersion.value));
  if (earlierVersion.value && !snapshot.search_question) byId('comparison-filter').open = true;
  byId('comparison-filter').addEventListener('input', clearDateErrors);
  earlierVersion.addEventListener('input', () => {
    byId('search-terms').setCustomValidity('');
    enableComparisonControls();
  });
  targetVersion.addEventListener('input', () => earlierVersion.setCustomValidity(''));
  secondQuestion.addEventListener('input', () => {
    byId('search-terms').setCustomValidity('');
    enableComparisonControls();
  });
  byId('search-terms').addEventListener('input', event => event.target.setCustomValidity(''));
  byId('search').addEventListener('submit', event => {
    event.preventDefault();
    const input = byId('search-terms');
    const value = input.value.trim();
    const compareWith = secondQuestion.value.trim();
    const from = earlierVersion.value;
    if ((compareWith || from) && !value) {
      input.setCustomValidity(t(from ? 'comparisonQuestionRequired' : 'firstQuestionRequired'));
      input.reportValidity();
      return;
    }
    if (from && Number(from) >= Number(targetVersion.value || snapshot.version)) {
      earlierVersion.setCustomValidity(t('invalidVersionOrder'));
      earlierVersion.reportValidity();
      return;
    }
    if (recordingStart.value && recordingEnd.value && recordingStart.value > recordingEnd.value) {
      recordingEnd.setCustomValidity(t('invalidDateRange'));
      recordingEnd.reportValidity();
      return;
    }
    const secondStart = compareStart.value || recordingStart.value;
    const secondEnd = compareEnd.value || recordingEnd.value;
    if (compareWith && secondStart && secondEnd && secondStart > secondEnd) {
      const dateInput = compareEnd.value ? compareEnd : compareStart;
      dateInput.setCustomValidity(t('invalidDateRange'));
      dateInput.reportValidity();
      return;
    }
    const previousTerms = snapshot.search_question ? [] : (snapshot.search_terms || []);
    let terms = [];
    if (!compareWith && !from && value && value === previousTerms.join('，')) {
      terms = previousTerms;
    } else if (!compareWith && !from && value !== snapshot.search_question && /[,，、]/u.test(value) && !/[?？。]/u.test(value)) {
      terms = [...new Set(value.split(/[,，、]+/u).map(term => term.trim()).filter(Boolean))];
    }
    if (terms.length > (matchingLines.checked ? 12 : 3) || terms.some(term => term.length > 128)) {
      input.setCustomValidity(t(matchingLines.checked ? 'invalidMatching' : 'invalid'));
      input.reportValidity();
      return;
    }
    const params = new URLSearchParams({ lang: language });
    if (snapshot.comparison_available) params.set('view', 'graph');
    for (const term of terms) params.append('term', term);
    if (!terms.length && value) params.set('q', value);
    if (compareWith) {
      params.set('q2', compareWith);
      if (compareStart.value) params.set('compare_start_date', compareStart.value);
      if (compareEnd.value) params.set('compare_end_date', compareEnd.value);
    }
    if (from) params.set('from', from);
    if (value && matchingLines.checked) params.set('matching_lines', '1');
    if (value && explainCandidates) params.set('explain', '1');
    if (value && targetVersion.value) params.set('at', targetVersion.value);
    if (value && recordingStart.value) params.set('start_date', recordingStart.value);
    if (value && recordingEnd.value) params.set('end_date', recordingEnd.value);
    if (value && !searchAllSources) {
      for (const [key, scope] of Object.entries(sourceScope)) params.set(key, scope);
    }
    if (!value && currentTopic && !searching) params.set('topic', currentTopic.code);
    location.search = params.toString();
  });
  byId('overview').addEventListener('click', () => {
    const params = new URLSearchParams({ lang: language });
    if (snapshot.comparison_available) params.set('view', 'graph');
    location.search = params.toString();
  });
  byId('refresh').addEventListener('click', () => { location.search = graphQuery().toString(); });
  byId('language').addEventListener('click', () => { language = language === 'zh' ? 'en' : 'zh'; render(); });
  byId('all-mode').addEventListener('click', () => changeMode('all'));
  byId('topic-mode').addEventListener('click', () => changeMode('topic'));
  byId('memory-mode').addEventListener('click', () => changeMode('memory'));
  for (const kind of Object.keys(catalogs)) {
    byId(kind + '-catalog').addEventListener('click', () => {
      catalog = catalog === kind ? undefined : kind;
      catalogFilter.value = '';
      renderCatalogControls();
      renderCatalogNavigation();
      renderViewLinks();
    });
  }
  catalogFilter.addEventListener('input', renderCatalog);
  byId('zoom-out').addEventListener('click', () => view.scale(1 / 1.18));
  byId('zoom-in').addEventListener('click', () => view.scale(1.18));
  byId('zoom-reset').addEventListener('click', () => view.reset());
  byId('workflow-replay').addEventListener('click', filterTrace ? playFilterTrace : playWorkflowEffect);
  for (const [id, next] of [['workflow-topic', 'topic'], ['workflow-source', 'memory']]) {
    byId(id).addEventListener('click', event => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const path = progress?.change?.paths?.[0];
      const node = next === 'topic' ? topics.get(path?.topics?.[0]) : sources.get(path?.checksum);
      if (!node) return;
      event.preventDefault();
      mode = next;
      choose(node);
      playWorkflowEffect();
    });
  }
  render();
  if (filterTrace) playFilterTrace();
  else playWorkflowEffect();
  watchWorkflow();
  if (query.has('at') && !searching && snapshot.search_available) byId('search-terms').focus();
})();
