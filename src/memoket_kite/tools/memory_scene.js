function renderViewerNavigation(language, activeView, parameters, comparisonAvailable = false) {
  const zh = language === 'zh';
  const nav = document.getElementById('workspace-views');
  nav.hidden = false;
  nav.setAttribute('aria-label', zh ? '工作区视图' : 'Workspace views');
  document.getElementById('title').textContent = zh ? '记忆工作区' : 'Memory workspace';
  const views = [
    ['graph', zh ? '记忆图谱' : 'Memory graph'],
    ['activity', zh ? '工作流' : 'Workflows'],
    ['changes', zh ? '变更对照' : 'Compare changes'],
  ];
  for (const [view, label] of views) {
    const link = document.getElementById('workspace-' + view);
    link.hidden = view === 'changes' && !comparisonAvailable;
    const query = new URLSearchParams(parameters);
    if (activeView === 'revision') {
      query.delete('before');
      query.delete('cursor');
    }
    query.set('view', view);
    query.set('lang', language);
    link.href = '?' + query.toString();
    link.textContent = label;
    if (view === activeView) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
}

class MemoryScene {
  static entityQuotes(entity, evidence) {
    const normalize = text => text.toLowerCase().replaceAll('_', ' ');
    const names = [entity.label, ...(entity.aliases || [])].map(normalize);
    return [...new Set(evidence)].filter(quote => {
      const text = normalize(quote.text);
      return names.some(name => text.includes(name));
    });
  }

  constructor(canvas, labels, select, reportZoom) {
    this.canvas = canvas;
    this.labels = labels;
    this.select = select;
    this.reportZoom = reportZoom;
    this.context = canvas.getContext('2d');
    this.scene = { layers: [], edges: [] };
    this.positions = new Map();
    this.projected = [];
    this.tags = new Map();
    this.mode = 'topic';
    this.camera = { yaw: -.20, pitch: .48, zoom: 1, x: 0, y: 0 };
    this.palette = {
      topic: { top: '#8ce0c7', front: '#3e9485', side: '#276a63', glow: '#8bf0d6' },
      memory: { top: '#f7c389', front: '#bc7750', side: '#85513e', glow: '#ffd5a8' },
      facet: { top: '#84c9ef', front: '#4d8eb4', side: '#315a79', glow: '#ade0fc' },
      line: { top: '#d3c0f4', front: '#9378be', side: '#6e58a1', glow: '#e3d0ff' },
      source: { top: '#abd9ff', front: '#547fac', side: '#3b608a', glow: '#bddfff' },
      state: { top: '#ebb6d1', front: '#aa6d90', side: '#815375', glow: '#ffc7e1' },
      entity: { top: '#f4da8a', front: '#a07e3d', side: '#705926', glow: '#ffe5a6' },
    };
    this.changeColors = { added: '#63edb3', removed: '#ff9ca1', historical: '#f5c578', changed: '#a9c8ff' };
    this.bind();
    new ResizeObserver(() => this.resize()).observe(canvas);
  }

  bind() {
    this.canvas.addEventListener('pointerdown', event => {
      if (!event.isPrimary || (event.button !== 0 && event.button !== 1)) return;
      this.drag = {
        x: event.clientX, y: event.clientY, pan: event.shiftKey || event.button === 1,
        moved: false, camera: { ...this.camera },
      };
      this.canvas.setPointerCapture(event.pointerId);
    });
    this.canvas.addEventListener('pointermove', event => {
      if (!this.drag) return;
      const dx = event.clientX - this.drag.x;
      const dy = event.clientY - this.drag.y;
      if (!this.drag.moved && Math.hypot(dx, dy) < 4) return;
      this.drag.moved = true;
      const base = this.drag.camera;
      if (this.drag.pan) {
        this.camera.x = base.x + dx;
        this.camera.y = base.y + dy;
      } else {
        this.camera.yaw = base.yaw + dx * .009;
        this.camera.pitch = Math.max(-.2, Math.min(1.25, base.pitch + dy * .008));
      }
      this.cameraMoved();
    });
    this.canvas.addEventListener('pointerup', event => {
      if (this.drag && !this.drag.moved && event.button === 0) {
        const { left, top } = this.canvas.getBoundingClientRect();
        const point = [...this.projected].reverse().find(item => !item.node.ghost &&
          Math.hypot(event.clientX - left - item.screen.x, event.clientY - top - item.screen.y) <= 19 * item.screen.scale);
        if (point) this.select(point.node);
      }
      this.drag = null;
    });
    this.canvas.addEventListener('pointercancel', () => { this.drag = null; });
    this.canvas.addEventListener('wheel', event => {
      event.preventDefault();
      const dy = event.deltaY * (event.deltaMode === 1 ? 16 : 1);
      this.scale(Math.exp(-dy * .0015));
    }, { passive: false });
    this.canvas.addEventListener('keydown', event => {
      const horizontal = { ArrowLeft: -.12, ArrowRight: .12 }[event.key];
      const vertical = { ArrowUp: .10, ArrowDown: -.10 }[event.key];
      if (horizontal) this.camera.yaw += horizontal;
      else if (vertical) this.camera.pitch = Math.max(-.2, Math.min(1.25, this.camera.pitch + vertical));
      else if (event.key === '+' || event.key === '=') this.scale(1.18);
      else if (event.key === '-') this.scale(1 / 1.18);
      else if (event.key === '0') this.reset();
      else return;
      event.preventDefault();
      this.cameraMoved();
    });
  }

  resize() {
    if (!this.context) return;
    const bounds = this.canvas.getBoundingClientRect();
    this.width = bounds.width;
    this.height = bounds.height;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(this.width * ratio);
    this.canvas.height = Math.round(this.height * ratio);
    this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.requestDraw();
  }

  reset() {
    this.camera = { yaw: -.20, pitch: .48, zoom: 1, x: 0, y: 0 };
    this.reportZoom(this.camera.zoom);
    this.cameraMoved();
  }

  scale(factor) {
    this.camera.zoom = Math.max(.65, Math.min(2.5, this.camera.zoom * factor));
    this.reportZoom(this.camera.zoom);
    this.cameraMoved();
  }

  cameraMoved() {
    this.requestDraw();
    this.onCameraChange?.(this.camera);
  }

  show(scene, selected, mode) {
    this.scene = scene;
    this.selected = selected;
    this.mode = mode;
    this.layout();
    const oldFocus = document.activeElement;
    const next = new Map();
    const fragment = document.createDocumentFragment();
    for (const layer of scene.layers) for (const node of layer.nodes) {
      if (node.ghost) continue;
      const tag = this.tags.get(node.id) || document.createElement('button');
      tag.className = 'graph-label';
      tag.type = 'button';
      tag.title = node.label;
      tag.textContent = node.label;
      if (node.change) tag.dataset.change = node.change;
      else delete tag.dataset.change;
      tag.setAttribute('aria-current', String(node === selected));
      tag.onclick = () => this.select(node);
      fragment.append(tag);
      next.set(node.id, tag);
    }
    this.labels.replaceChildren(fragment);
    this.tags = next;
    if ([...next.values()].includes(oldFocus)) oldFocus.focus({ preventScroll: true });
    this.requestDraw();
  }

  layout() {
    this.positions.clear();
    this.surfaces = [];
    if (this.scene.coordinates) {
      this.positions = new Map(this.scene.coordinates);
      const extent = Math.max(1.2, ...[...this.positions.values()].map(([x, , z]) => Math.hypot(x, z)));
      const floor = Math.min(-2.35, ...[...this.positions.values()].map(([, y]) => y - .15));
      this.bounds = { x: extent, y: Math.max(3.1, extent * .58, -floor) };
      this.surfaces = [{ x: extent, y: floor, z: extent, kind: 'topic' }];
      return;
    }
    let bound = 1;
    const height = this.mode === 'topic' ? 2.25 : this.mode === 'memory' ? 1.58 : 1.92;
    const count = this.scene.layers.length;
    this.scene.layers.forEach((layer, rank) => {
      const ordered = [...layer.nodes];
      const anchor = layer.kind === 'source' && this.selected?.kind === 'memory'
        ? this.selected.sources?.[0] : this.selected;
      const columns = ordered.length > 6 ? Math.ceil(ordered.length / 2) : ordered.length;
      const selection = ordered.indexOf(anchor);
      if (selection >= 0 && this.mode !== 'comparison') {
        ordered.splice(selection, 1);
        ordered.splice(Math.floor((columns - 1) / 2), 0, anchor);
      }
      const spacing = ordered.length > 8 ? 1.34 : 1.61;
      const depth = ordered.length > columns ? 1.36 : .64;
      const y = ((count - 1) / 2 - rank) * height;
      for (let index = 0; index < ordered.length; index += columns) {
        const row = ordered.slice(index, index + columns);
        const z = ordered.length > columns ? (index === 0 ? -depth : depth) : 0;
        for (const [column, node] of row.entries()) {
          const x = (column - (row.length - 1) / 2) * spacing;
          this.positions.set(node, [x, y, z + (column % 2 ? .18 : -.12)]);
        }
      }
      const extent = Math.max(1.2, columns * spacing / 2 + .36);
      bound = Math.max(bound, extent);
      this.surfaces.push({ y: y - .13, x: extent, z: depth + .69, kind: layer.kind });
    });
    this.bounds = { x: bound, y: Math.max(2.3, (count - 1) * height / 2 + .65) };
  }

  requestDraw() {
    if (this.frame !== undefined) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = undefined;
      this.draw();
    });
  }

  project([x, y, z]) {
    const { yaw, pitch, zoom, x: panX, y: panY } = this.camera;
    const sinYaw = Math.sin(yaw);
    const cosYaw = Math.cos(yaw);
    const sinPitch = Math.sin(pitch);
    const cosPitch = Math.cos(pitch);
    const across = x * cosYaw + z * sinYaw;
    const inward = z * cosYaw - x * sinYaw;
    const up = y * cosPitch - inward * sinPitch;
    const depth = inward * cosPitch + y * sinPitch;
    const scale = 17 / Math.max(6, 17 - depth);
    const unit = Math.min((this.width - 28) / (this.bounds.x * 2 + 2.55),
      (this.height - 112) / (this.bounds.y * 2 + 1.55)) * zoom;
    return {
      x: this.width / 2 + across * unit * scale + panX,
      y: this.height / 2 - up * unit * scale + panY,
      depth, scale,
    };
  }

  polygon(vertices, fill, stroke, width = 1) {
    const context = this.context;
    const points = vertices.map(point => this.project(point));
    context.beginPath();
    context.moveTo(points[0].x, points[0].y);
    for (const point of points.slice(1)) context.lineTo(point.x, point.y);
    context.closePath();
    if (fill) {
      context.fillStyle = fill;
      context.fill();
    }
    if (stroke) {
      context.strokeStyle = stroke;
      context.lineWidth = width;
      context.stroke();
    }
  }

  segment(start, end, stroke, width = 1) {
    const context = this.context;
    const from = this.project(start);
    const to = this.project(end);
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.lineTo(to.x, to.y);
    context.strokeStyle = stroke;
    context.lineWidth = width;
    context.stroke();
  }

  deck(surface) {
    const { x, y, z, kind } = surface;
    const color = this.palette[kind].top;
    const inset = .45;
    const perimeter = [
      [-x + inset, y, -z], [x - inset, y, -z], [x, y, -z + inset], [x, y, z - inset],
      [x - inset, y, z], [-x + inset, y, z], [-x, y, z - inset], [-x, y, -z + inset],
    ];
    this.polygon(perimeter.map(([x, y, z]) => [x, y - .14, z]), '#080f1a60', color + '21');
    this.polygon(perimeter, color + '0d', color + '4b');
    this.segment([-x + inset, y, z], [x - inset, y, z], color + '70');
    for (let column = Math.ceil(-x + .6); column < x - .4; column += .7) {
      this.segment([column, y, -z + .15], [column, y, z - .15], color + '12');
    }
    for (let row = -z + .35; row < z; row += .65) {
      this.segment([-x + .35, y, row], [x - .35, y, row], color + '12');
    }
  }

  drawEdge(edge) {
    const { from, to, kind } = edge;
    const a = this.positions.get(from);
    const b = this.positions.get(to);
    if (!a || !b) return;
    const start = this.project([a[0], a[1] - .03, a[2]]);
    const end = this.project([b[0], b[1] + .04, b[2]]);
    const active = from === this.selected || to === this.selected;
    const shared = kind === 'shared';
    const correction = kind === 'correction';
    const cooccurrence = kind === 'cooccurrence';
    const context = this.context;
    const middle = (a[1] + b[1]) / 2;
    const left = this.project([a[0], middle, a[2]]);
    const right = this.project([b[0], middle, b[2]]);
    const curve = shared || cooccurrence || Math.abs(a[1] - b[1]) < .1;
    const color = this.changeColors[edge.change] || (shared ? '#c2d3ec' : correction ? '#efafd2' : cooccurrence ? '#f5cb83'
      : kind === 'mention' || kind === 'facet' ? this.palette[from.kind].top : this.palette[to.kind].top);
    context.beginPath();
    context.moveTo(start.x, start.y);
    context.bezierCurveTo(
      left.x, left.y - (curve ? 28 : 0),
      right.x, right.y - (curve ? 28 : 0),
      end.x, end.y,
    );
    if (active) {
      context.strokeStyle = color + '30';
      context.lineWidth = 5;
      context.stroke();
    }
    context.strokeStyle = color + (active || edge.change ? 'ed' : shared ? '59' : '80');
    context.lineWidth = active || edge.change ? 1.9 : 1.1;
    if (shared || correction || cooccurrence || edge.change === 'removed') context.setLineDash([4, 4]);
    context.stroke();
    context.setLineDash([]);
  }

  block(node, at) {
    const [x, y, z] = at;
    const { top, front, side, glow } = this.palette[node.kind];
    const chosen = node === this.selected;
    const dimensions = {
      topic: [.34, .25, .18], memory: [.34, .23, .115], facet: [.31, .22, .19], line: [.20, .16, .08],
      source: [.31, .25, .16], state: [.24, .20, .15], entity: [.25, .20, .14],
    };
    const [dx, dz, dy] = dimensions[node.kind];
    const floor = y - .05;
    const roof = floor + dy;
    const rectangle = [
      [x - dx, roof, z - dz], [x + dx, roof, z - dz],
      [x + dx, roof, z + dz], [x - dx, roof, z + dz],
    ];
    const ring = node.kind === 'entity'
      ? [[x - dx, roof, z], [x - dx / 2, roof, z - dz], [x + dx / 2, roof, z - dz],
        [x + dx, roof, z], [x + dx / 2, roof, z + dz], [x - dx / 2, roof, z + dz]]
      : node.kind === 'facet'
        ? [[x - dx, roof, z], [x, roof, z - dz], [x + dx, roof, z], [x, roof, z + dz]]
        : rectangle;
    const base = ring.map(([x, , z]) => [x, floor, z]);
    const context = this.context;
    if (node.ghost) {
      context.setLineDash([3, 3]);
      this.polygon(ring, null, '#c1d6e64d');
      context.setLineDash([]);
      return;
    }
    if (chosen) {
      context.shadowColor = glow + '99';
      context.shadowBlur = 19;
    }
    this.polygon(base, '#030a1490');
    context.shadowBlur = 0;
    const faces = ring.map((point, i) => {
      const j = (i + 1) % ring.length;
      const corners = [point, ring[j], base[j], base[i]];
      const depth = corners.reduce((sum, point) => sum + this.project(point).depth, 0);
      return { corners, depth, color: i % 2 ? side : front };
    });
    for (const face of faces.sort((a, b) => a.depth - b.depth)) this.polygon(face.corners, face.color);
    this.polygon(ring, top, chosen ? '#ffffff' : top + 'bb', chosen ? 1.8 : 1);
    if (node.kind === 'memory' || node.kind === 'line') {
      this.segment([x - dx * .54, roof + .004, z - dz * .28],
        [x + dx * .51, roof + .004, z - dz * .28], '#271a393f');
      this.segment([x - dx * .54, roof + .004, z + dz * .25],
        [x + dx * .31, roof + .004, z + dz * .25], '#271a393f');
    }
    if (chosen || node.change) {
      const border = ring.map(([px, py, pz]) => [x + (px - x) * 1.29, py, z + (pz - z) * 1.30]);
      this.polygon(border, null, this.changeColors[node.change] || glow + 'a0', node.change ? 1.5 : 1);
    }
  }

  positionLabels() {
    const allTopics = this.mode === 'all';
    const connected = new Set();
    if (allTopics) for (const { from, to } of this.scene.edges) {
      if (from === this.selected) connected.add(to);
      if (to === this.selected) connected.add(from);
    }
    const prioritized = [...this.projected].sort((a, b) =>
      Number(b.node === this.selected) - Number(a.node === this.selected)
      || Number(allTopics && Boolean(b.node.overviewRoot)) - Number(allTopics && Boolean(a.node.overviewRoot))
      || Number(connected.has(b.node)) - Number(connected.has(a.node))
      || Number(b.node.kind === 'topic') - Number(a.node.kind === 'topic')
      || b.screen.depth - a.screen.depth
    );
    const occupied = [];
    for (const { node, screen } of prioritized) {
      const tag = this.tags.get(node.id);
      if (!tag) continue;
      if (allTopics && !node.overviewRoot && !connected.has(node) && node !== this.selected && this.camera.zoom < 1.35) {
        tag.hidden = true;
        continue;
      }
      tag.hidden = false;
      const width = tag.offsetWidth;
      const height = tag.offsetHeight;
      const choices = [
        [screen.x - width / 2, screen.y + 9],
        [screen.x - width / 2, screen.y - height - 12],
        [screen.x + 11, screen.y - height / 2],
        [screen.x - width - 11, screen.y - height / 2],
      ];
      const anchor = choices.find(([x, y]) => x >= 4 && y >= 36
        && x + width <= this.width - 4 && y + height <= this.height - 41
        && !occupied.some(box => x < box.x + box.width + 3 && x + width + 3 > box.x
          && y < box.y + box.height + 2 && y + height + 2 > box.y));
      if (!anchor && node !== this.selected) {
        tag.hidden = true;
        continue;
      }
      const [x, y] = anchor || choices[0];
      tag.style.left = Math.round(x) + 'px';
      tag.style.top = Math.round(y) + 'px';
      occupied.push({ x, y, width, height });
    }
  }

  draw() {
    if (!this.context || !this.width || !this.height || !this.bounds) return;
    this.context.clearRect(0, 0, this.width, this.height);
    if (!this.positions.size) return;
    const floor = this.surfaces.at(-1).y - .57;
    const extent = Math.ceil(this.bounds.x + 1.1);
    for (let i = -extent; i <= extent; i++) {
      this.segment([i, floor, -3.4], [i, floor, 3.4], '#a7c8d320');
    }
    for (let i = -3; i <= 3; i++) {
      this.segment([-extent, floor, i], [extent, floor, i], '#a7c8d320');
    }
    for (const surface of [...this.surfaces].reverse()) this.deck(surface);
    for (const edge of this.scene.edges) {
      if (edge.from !== this.selected && edge.to !== this.selected) this.drawEdge(edge);
    }
    for (const edge of this.scene.edges) {
      if (edge.from === this.selected || edge.to === this.selected) this.drawEdge(edge);
    }
    this.projected = [...this.positions].map(([node, at]) => ({ node, at, screen: this.project(at) }))
      .sort((a, b) => a.screen.depth - b.screen.depth);
    for (const { node, at } of this.projected) this.block(node, at);
    this.positionLabels();
  }
}
