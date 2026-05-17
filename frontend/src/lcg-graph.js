// Renders the lcg producer output as a planet/orbit graph:
//   centre  = topic (extracted from the advisor H1)
//   planets = cited hypotheses, sized by novelty, distance ∝ inverse utility
//   colour  = scope tag (method=X, task=Y), one hue per unique scope
//   dashed  = graph bridges out to satellite labels
//
// Designed as a glance-able overview that sits above the existing
// hypothesis cards. Returns null if the input doesn't parse as lcg.

const SVG_W = 720;
const SVG_H = 360;
const CX = SVG_W / 2;
const CY = SVG_H / 2;

function _parseHypotheses(content) {
  const sep = content.indexOf('\n---\n');
  const dump = sep >= 0 ? content.slice(sep + 5) : content;
  // Split into hypothesis sections by ### h{digits}
  const sections = dump.split(/(?=^###\s+h\d{2,4}\s+—\s+)/m);
  const hyps = [];
  for (const sec of sections) {
    const head = sec.match(/^###\s+(h\d{2,4})\s+—\s+(.+?)$/m);
    if (!head) continue;
    const id = head[1];
    const title = head[2].trim();

    const scope = (sec.match(/\*\*Scope\.\*\*\s+([^\n]+)/) || [null, ''])[1].trim();
    const bridges = [];
    const brMatch = sec.match(/\*\*Graph bridge\.\*\*\s+([^\n]+)/);
    if (brMatch) {
      for (const part of brMatch[1].split(/[;,]\s*|\sand\s/)) {
        const m = part.match(/(.+?)\s*(?:→|->)\s*(.+)/);
        if (m) bridges.push({ from: m[1].trim(), to: m[2].trim() });
      }
    }

    // Utility table row — last cell is the rolled-up utility, col 3 is novelty.
    let utility = 0.5;
    let novelty = 0.5;
    const tableM = sec.match(/\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|\s*([\d.]+)\s*\|[\s\S]*?\|\s*([\d.]+)\s*\|\s*$/m);
    if (tableM) {
      novelty = parseFloat(tableM[1]);
      utility = parseFloat(tableM[2]);
    }

    hyps.push({ id, title, scope, bridges, utility, novelty });
  }
  return hyps;
}

function _parseTopic(content) {
  const m = content.match(/##\s+Advisor Answer[\s\S]*?#\s+Stage \d+:[^—\n]*—\s+([^\n]+)/);
  if (m) return m[1].trim();
  // Fallback: first H1 anywhere
  const h1 = content.match(/^#\s+([^\n]+)/m);
  return h1 ? h1[1].trim() : 'Topic';
}

function _escape(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Stable colour palette for scope tags (HSL hue rotation).
function _scopeColor(scope, scopeIndex) {
  // 9 distinct hues, rotated.
  const hue = (scopeIndex * 137) % 360;
  return `hsl(${hue}, 55%, 60%)`;
}

function _renderSvg(topic, hyps) {
  if (hyps.length === 0) return null;

  // Bucket by scope so similar hypotheses cluster nearby.
  const scopeOrder = [];
  const scopeIndex = new Map();
  for (const h of hyps) {
    if (!scopeIndex.has(h.scope)) {
      scopeIndex.set(h.scope, scopeOrder.length);
      scopeOrder.push(h.scope);
    }
  }
  // Sort hypotheses: first by scope bucket, then by descending utility (high utility first → closer to centre).
  hyps.sort((a, b) => {
    const sa = scopeIndex.get(a.scope);
    const sb = scopeIndex.get(b.scope);
    if (sa !== sb) return sa - sb;
    return b.utility - a.utility;
  });

  const N = hyps.length;
  const RING = Math.min(CX, CY) - 70;
  // Place each hypothesis on a near-circle, with radial offset proportional to (1 - utility):
  // higher utility ⇒ closer to centre.
  const positions = hyps.map((h, i) => {
    const angle = (i / N) * Math.PI * 2 - Math.PI / 2; // start at top
    const r = RING * (0.65 + 0.35 * (1 - h.utility));
    return {
      h,
      angle,
      r,
      x: CX + Math.cos(angle) * r,
      y: CY + Math.sin(angle) * r,
      radius: 12 + 14 * h.novelty,
      color: _scopeColor(h.scope, scopeIndex.get(h.scope)),
    };
  });

  // Compute bridge satellite positions.
  const bridgeNodes = [];
  positions.forEach(p => {
    p.h.bridges.forEach((br, j) => {
      const bridgeAngle = p.angle + 0.18 * (j + 1);
      const bridgeR = p.r + 50;
      bridgeNodes.push({
        x: CX + Math.cos(bridgeAngle) * bridgeR,
        y: CY + Math.sin(bridgeAngle) * bridgeR,
        label: br.to,
        fromX: p.x,
        fromY: p.y,
      });
    });
  });

  // Build SVG content.
  let svg = `<svg viewBox="0 0 ${SVG_W} ${SVG_H}" xmlns="http://www.w3.org/2000/svg" class="lcg-graph-svg" aria-label="hypothesis orbit graph">`;
  svg += `<defs><radialGradient id="lcgg-core" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="#1a1a1a" stop-opacity="1"/><stop offset="100%" stop-color="#1a1a1a" stop-opacity="0.6"/></radialGradient></defs>`;

  // Concentric guide rings (very faint).
  for (const rr of [RING * 0.65, RING]) {
    svg += `<circle cx="${CX}" cy="${CY}" r="${rr}" fill="none" stroke="var(--text4, #888)" stroke-opacity="0.15" stroke-dasharray="2 4"/>`;
  }

  // Bridge dashed lines (drawn behind planets).
  bridgeNodes.forEach(b => {
    svg += `<line x1="${b.fromX}" y1="${b.fromY}" x2="${b.x}" y2="${b.y}" stroke="var(--text4, #888)" stroke-opacity="0.4" stroke-dasharray="3 3"/>`;
  });

  // Bridge labels.
  bridgeNodes.forEach(b => {
    const t = _escape(b.label.length > 22 ? b.label.slice(0, 22) + '…' : b.label);
    svg += `<text x="${b.x}" y="${b.y}" fill="var(--text3, #aaa)" font-size="10" text-anchor="middle" dominant-baseline="middle">${t}</text>`;
  });

  // Centre: topic.
  const topicShort = _escape(topic.length > 28 ? topic.slice(0, 28) + '…' : topic);
  svg += `<circle cx="${CX}" cy="${CY}" r="42" fill="url(#lcgg-core)" stroke="var(--accent, #69f)" stroke-width="1.5"/>`;
  svg += `<text x="${CX}" y="${CY - 4}" fill="var(--text1, #fff)" font-size="11" font-weight="600" text-anchor="middle">TOPIC</text>`;
  svg += `<text x="${CX}" y="${CY + 12}" fill="var(--text2, #ddd)" font-size="10" text-anchor="middle">${topicShort}</text>`;

  // Planets.
  positions.forEach(p => {
    svg += `<g class="lcg-graph-planet" data-lcg-graph-id="${p.h.id}" style="cursor:pointer">`;
    svg += `<circle cx="${p.x}" cy="${p.y}" r="${p.radius}" fill="${p.color}" fill-opacity="0.85" stroke="${p.color}" stroke-width="1.5"/>`;
    svg += `<text x="${p.x}" y="${p.y + 3}" fill="#fff" font-size="11" font-weight="600" text-anchor="middle">${_escape(p.h.id)}</text>`;
    svg += `</g>`;
  });

  // Legend: list each unique scope with its colour, bottom-left.
  let lx = 12;
  const ly = SVG_H - 12;
  scopeOrder.forEach((sc, i) => {
    if (!sc) return;
    const color = _scopeColor(sc, i);
    const short = sc.length > 28 ? sc.slice(0, 28) + '…' : sc;
    svg += `<circle cx="${lx + 5}" cy="${ly - 4}" r="4" fill="${color}"/>`;
    svg += `<text x="${lx + 14}" y="${ly}" fill="var(--text3, #aaa)" font-size="10">${_escape(short)}</text>`;
    lx += 14 + short.length * 6;
  });

  svg += `</svg>`;
  return svg;
}

export function tryRenderLcgGraph(content) {
  if (!content || typeof content !== 'string') return null;
  // Only render when we can extract ≥ 2 hypotheses — anything less is not worth a graph.
  const hyps = _parseHypotheses(content);
  if (hyps.length < 2) return null;
  const topic = _parseTopic(content);
  return _renderSvg(topic, hyps);
}

// Click handler — wired by index.html. Scrolls to the corresponding hypothesis
// card and highlights it briefly.
export function setupLcgGraphClicks(rootEl) {
  if (!rootEl) return;
  rootEl.querySelectorAll('.lcg-graph-planet').forEach(g => {
    g.addEventListener('click', () => {
      const id = g.getAttribute('data-lcg-graph-id');
      if (!id) return;
      // The lcg-renderer namespaces card IDs with `lcg{n}__h202` — look for any
      // `<details>` whose summary contains the bare ID.
      const cards = document.querySelectorAll('details.lcg-card');
      for (const card of cards) {
        const sum = card.querySelector('summary');
        if (sum && sum.textContent && sum.textContent.indexOf(id) === 0) {
          card.open = true;
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
          card.style.transition = 'box-shadow 0.4s';
          card.style.boxShadow = '0 0 0 3px var(--accent, #69f)';
          setTimeout(() => { card.style.boxShadow = ''; }, 1200);
          break;
        }
      }
    });
  });
}

window._tryRenderLcgGraph = tryRenderLcgGraph;
window._setupLcgGraphClicks = setupLcgGraphClicks;
