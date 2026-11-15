/* Present for Kids: Phase 1 viewer.
 * Reads the static JSON that the collector commits (data/), shows events on a
 * world map and a daily timeline, and lets people filter them.
 * No build step and no framework: plain browser JavaScript + MapLibre GL JS.
 */
(() => {
  'use strict';

  // ---------- vocabulary ----------
  // Display order = slot order of the validated categorical palette (--c1..--c8).
  // Color follows the category, never its rank or count.
  const CATS = [
    { id: 'disaster_climate', slot: 'c1', ja: '災害・気候', en: 'Disasters & climate' },
    { id: 'conflict_security', slot: 'c2', ja: '紛争・安全保障', en: 'Conflict & security' },
    { id: 'economy_inequality', slot: 'c3', ja: '経済・格差', en: 'Economy & inequality' },
    { id: 'politics_governance', slot: 'c4', ja: '政治・ガバナンス', en: 'Politics & governance' },
    { id: 'public_health', slot: 'c5', ja: '公衆衛生', en: 'Public health' },
    { id: 'culture_society', slot: 'c6', ja: '文化・社会', en: 'Culture & society' },
    { id: 'ai_technology', slot: 'c7', ja: 'AI・技術', en: 'AI & technology' },
    { id: 'human_rights', slot: 'c8', ja: '人権', en: 'Human rights' },
  ];
  const CAT = Object.fromEntries(CATS.map((c) => [c.id, c]));

  const DIRS = [
    { id: 'worsening', icon: '▼', ja: '悪化', en: 'Worsening' },
    { id: 'improving', icon: '▲', ja: '改善', en: 'Improving' },
    { id: 'turning_point', icon: '◆', ja: '転換点', en: 'Turning point' },
    { id: 'neutral', icon: '●', ja: '中立', en: 'Neutral' },
  ];
  const DIR = Object.fromEntries(DIRS.map((d) => [d.id, d]));

  const TYPES = {
    earthquake: { ja: '地震', en: 'Earthquake' },
    tropical_cyclone: { ja: '台風・熱帯低気圧', en: 'Tropical cyclone' },
    flood: { ja: '洪水', en: 'Flood' },
    volcano: { ja: '火山', en: 'Volcano' },
    drought: { ja: '干ばつ', en: 'Drought' },
    wildfire: { ja: '山火事', en: 'Wildfire' },
    tsunami: { ja: '津波', en: 'Tsunami' },
  };
  // GDELT titles start with these English labels (collector/sources/gdelt.py).
  const GDELT_JA = {
    'Material cooperation': '実務・物資の協力',
    'Aid provided': '援助',
    'Concession / easing': '譲歩・緩和',
    'Protest': '抗議',
    'Coercion / repression': '強制・弾圧',
    'Assault': '襲撃',
    'Armed clash': '武力衝突',
    'Mass violence': '大規模な暴力',
    'Military de-escalation / ceasefire': '軍事的緊張の緩和・停戦',
    'Humanitarian aid': '人道支援',
    'Protection granted': '保護の付与',
    'Political reform accepted': '政治改革の受け入れ',
  };
  const SOURCES = {
    usgs: { name: 'USGS', url: 'https://earthquake.usgs.gov/' },
    gdacs: { name: 'GDACS', url: 'https://www.gdacs.org/' },
    gdelt: { name: 'GDELT', url: 'https://www.gdeltproject.org/' },
    reliefweb: { name: 'ReliefWeb', url: 'https://reliefweb.int/' },
    unhcr: { name: 'UNHCR', url: 'https://www.unhcr.org/refugee-statistics/' },
  };
  const CONF = {
    high: { ja: '公的機関の一次情報', en: 'Official primary source' },
    medium: { ja: '編集済みの二次情報', en: 'Curated secondary source' },
    low: { ja: '報道からの機械抽出', en: 'Machine-coded from news' },
  };
  const SEV_RADIUS = { low: 4, medium: 5.5, high: 7.5, critical: 9.5 };
  // UNHCR: people forced to flee, by country of origin (returnees excluded).
  const DISPLACED = ['refugees_by_origin', 'asylum_seekers_by_origin', 'internally_displaced_by_origin',
    'stateless_by_origin', 'other_in_need_of_protection_by_origin'];
  const CHORO_STEPS = [10000, 100000, 500000, 1000000, 5000000];

  const T = {
    ja: {
      tagline: '未来の子供たちのために、世界でいま起きていることを俯瞰する',
      categories: '分野', direction: '向き', layers: '表示',
      showNews: '報道から抽出した出来事も表示する',
      newsHint: 'GDELT が世界の報道から機械的に抽出したものです。誤りを含むことがあるため、輪郭だけの点で示しています。',
      showChoro: '避難を強いられた人の数（出身国別）を色で示す',
      listTitle: '出来事の一覧', timeline: '日ごとの件数（UTC）',
      r1: '24時間', r7: '7日', r30: '30日', rAll: 'すべて',
      timelineHint: 'グラフをドラッグすると期間を選べます。1日をクリックするとその日だけを表示します。',
      about: 'このサイトは「未来の子供たちを幸せにするための理解を得ること」を目指しています。危機だけでなく改善の兆しも同じ重さで集めています。いまは公開データを自動で集めて並べる段階で、日本語の要約や、危機どうしのつながりの分析はこれから加えていきます。',
      credits: 'データ: <a href="https://earthquake.usgs.gov/" rel="noopener">USGS</a>、<a href="https://www.gdacs.org/" rel="noopener">GDACS</a>、<a href="https://www.gdeltproject.org/" rel="noopener">The GDELT Project</a>、<a href="https://www.unhcr.org/refugee-statistics/" rel="noopener">UNHCR</a>。地図: <a href="https://www.naturalearthdata.com/" rel="noopener">Natural Earth</a>、<a href="https://maplibre.org/" rel="noopener">MapLibre</a>。記事の本文は保存せず、見出しとリンクだけを載せています。',
      count: '件', period: '期間', items: (n) => `${n.toLocaleString()} 件`,
      more: (n) => `ほか ${n.toLocaleString()} 件。期間や分野を絞ると表示されます。`,
      popupMore: (n) => `ほか ${n} 件（地図を拡大すると分かれます）`,
      empty: 'この期間と条件に当てはまる出来事はありません。',
      loadError: 'データを読み込めませんでした。時間をおいて再読み込みしてください。',
      source: '出典', updated: '最終取得', never: '未取得', skipped: '準備中', failed: '取得失敗',
      choroTitle: '避難を強いられた人（出身国別、{p}年、UNHCR）',
      people: (n) => `${n.toLocaleString()} 人`, noData: 'データなし',
      ago: (m) => (m < 60 ? `${m}分前` : m < 1440 ? `${Math.round(m / 60)}時間前` : `${Math.round(m / 1440)}日前`),
      articles: (n) => `報道 ${n} 件`, total: '合計',
    },
    en: {
      tagline: 'Seeing what is happening in the world, for the children of the future',
      categories: 'Categories', direction: 'Direction', layers: 'Layers',
      showNews: 'Include events extracted from news',
      newsHint: 'Machine-coded by GDELT from world news. They can be wrong, so they are drawn as outlined dots.',
      showChoro: 'Shade countries by people forced to flee (by origin)',
      listTitle: 'Events', timeline: 'Events per day (UTC)',
      r1: '24 h', r7: '7 days', r30: '30 days', rAll: 'All',
      timelineHint: 'Drag across the chart to pick a period. Click a day to show only that day.',
      about: 'This site aims at “understanding that helps make the future better for children.” It collects signs of improvement with the same weight as crises. Right now it gathers and shows open data automatically; Japanese summaries and analysis of how crises connect come next.',
      credits: 'Data: <a href="https://earthquake.usgs.gov/" rel="noopener">USGS</a>, <a href="https://www.gdacs.org/" rel="noopener">GDACS</a>, <a href="https://www.gdeltproject.org/" rel="noopener">The GDELT Project</a>, <a href="https://www.unhcr.org/refugee-statistics/" rel="noopener">UNHCR</a>. Map: <a href="https://www.naturalearthdata.com/" rel="noopener">Natural Earth</a>, <a href="https://maplibre.org/" rel="noopener">MapLibre</a>. We never store article text, only headlines and links.',
      count: 'events', period: 'Period', items: (n) => `${n.toLocaleString()} events`,
      more: (n) => `${n.toLocaleString()} more. Narrow the period or categories to see them.`,
      popupMore: (n) => `${n} more here (zoom in to separate)`,
      empty: 'No events match this period and these filters.',
      loadError: 'Could not load the data. Please reload in a while.',
      source: 'Source', updated: 'Last fetched', never: 'never', skipped: 'coming soon', failed: 'failed',
      choroTitle: 'People forced to flee (by origin, {p}, UNHCR)',
      people: (n) => `${n.toLocaleString()} people`, noData: 'no data',
      ago: (m) => (m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`),
      articles: (n) => `${n} articles`, total: 'Total',
    },
  };

  const DAY = 86400000;
  const LOAD_DAYS = 92; // months of event files to load, counted back from the newest data

  // ---------- state ----------
  const state = {
    lang: 'ja',
    cats: new Set(CATS.map((c) => c.id)),
    dirs: new Set(DIRS.map((d) => d.id)),
    showNews: true,
    showChoro: false,
    range: null, // [startMs, endMs) in UTC days
    rangeDays: 7,
  };
  let events = [];
  let countries = null; // iso3 -> {ja, en}
  let displaced = null; // {period, byIso3}
  let status = null;
  let domain = null; // [firstDayMs, lastDayMs]
  let map = null;
  let mapReady = false;
  let popup = null;

  const $ = (id) => document.getElementById(id);
  const t = (k) => T[state.lang][k];
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const dayOf = (ms) => Math.floor(ms / DAY) * DAY;
  const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : null);

  // ---------- language ----------
  function initLang() {
    const q = new URLSearchParams(location.search).get('lang');
    let saved = null;
    try { saved = localStorage.getItem('pfk-lang'); } catch (_) { /* storage blocked */ }
    const nav = (navigator.language || '').toLowerCase().startsWith('ja') ? 'ja' : 'en';
    state.lang = ['ja', 'en'].includes(q) ? q : ['ja', 'en'].includes(saved) ? saved : nav;
    document.querySelectorAll('.lang button').forEach((b) => {
      b.addEventListener('click', () => {
        state.lang = b.dataset.lang;
        try { localStorage.setItem('pfk-lang', state.lang); } catch (_) { /* ignore */ }
        applyLang();
        renderAll();
      });
    });
    applyLang();
  }
  function applyLang() {
    document.documentElement.lang = state.lang;
    document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll('[data-i18n-html]').forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
    document.querySelectorAll('.lang button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.lang === state.lang)));
  }

  // ---------- labels ----------
  function kindLabel(e) {
    if (e.source === 'gdelt') {
      const en = (e.title.en || '').split(':')[0];
      return state.lang === 'ja' ? (GDELT_JA[en] || en) : en;
    }
    const ty = TYPES[e.type];
    return ty ? ty[state.lang] : e.type;
  }
  function placeLabel(e) {
    const iso = e.location.country_iso3;
    if (state.lang === 'ja' && iso && countries && countries[iso] && countries[iso].ja) return countries[iso].ja;
    return e.location.place || '';
  }
  function title(e) {
    if (e.title[state.lang]) return e.title[state.lang];
    const place = placeLabel(e);
    return place ? `${kindLabel(e)}（${place}）` : kindLabel(e);
  }
  function originalTitle(e) {
    // In Japanese mode, show the source's own headline until the AI stage translates it.
    return state.lang === 'ja' && !e.title.ja ? e.title.en : null;
  }
  function when(e) {
    const d = new Date(e._t);
    const dateOnly = e.source === 'gdelt' && e.occurred_at.endsWith('T00:00:00Z');
    const loc = state.lang === 'ja' ? 'ja-JP' : 'en-GB';
    if (dateOnly) return d.toLocaleDateString(loc, { timeZone: 'UTC', month: 'short', day: 'numeric' });
    return d.toLocaleString(loc, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  function severityText(e) {
    const s = e.severity || {};
    if (s.value == null || s.value === 0) return '';
    if (s.unit === 'articles') return t('articles')(s.value);
    if (s.unit === 'magnitude' || s.unit === 'M') return `M${s.value}`;
    return `${s.value}${s.unit ? ' ' + s.unit : ''}`;
  }
  function metaLine(e) {
    const dir = DIR[e.direction];
    return [when(e), dir ? `${dir.icon} ${dir[state.lang]}` : '', severityText(e),
      `${(SOURCES[e.source] || {}).name || e.source}・${(CONF[e.confidence] || {})[state.lang] || ''}`]
      .filter(Boolean).join(' / ');
  }
  const catColor = (id) => css('--' + (CAT[id] || CATS[0]).slot);
  const dot = (cat, low) => `<span class="dot" style="color:${catColor(cat)};background:${low ? 'transparent' : catColor(cat)}"></span>`;

  // ---------- data ----------
  async function getJSON(url) {
    const r = await fetch(url, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  }
  async function loadData() {
    const manifest = await getJSON('data/manifest.json');
    const allMonths = Object.values(manifest.events || {}).flat().sort();
    const newest = allMonths.length ? allMonths[allMonths.length - 1] : null;
    let cutoff = '';
    if (newest) {
      const d = new Date(Date.UTC(+newest.slice(0, 4), +newest.slice(5, 7) - 1, 1) - LOAD_DAYS * DAY);
      cutoff = d.toISOString().slice(0, 7);
    }
    const files = [];
    for (const [src, months] of Object.entries(manifest.events || {})) {
      for (const m of months) if (m >= cutoff) files.push(`data/events/${src}/${m}.json`);
    }
    const unhcr = (manifest.indicators || {}).unhcr || [];
    const latestPeriod = unhcr.length ? unhcr[unhcr.length - 1] : null;

    const [lists, geo, ind, st] = await Promise.all([
      Promise.all(files.map((f) => getJSON(f).catch((err) => { console.warn(err); return []; }))),
      getJSON('assets/countries.geojson'),
      latestPeriod ? getJSON(`data/indicators/unhcr/${latestPeriod}.json`).catch(() => []) : [],
      getJSON('data/status.json').catch(() => null),
    ]);

    const seen = new Map();
    for (const list of lists) for (const e of list) {
      e._t = Date.parse(e.occurred_at);
      if (Number.isFinite(e._t)) seen.set(e.id, e);
    }
    events = [...seen.values()].sort((a, b) => b._t - a._t);
    events.forEach((e, i) => { e._i = i; });

    countries = {};
    for (const f of geo.features) countries[f.properties.iso3] = f.properties;

    const byIso3 = {};
    for (const r of ind) {
      if (!DISPLACED.includes(r.indicator) || !r.area || !r.area.iso3 || typeof r.value !== 'number') continue;
      byIso3[r.area.iso3] = (byIso3[r.area.iso3] || 0) + r.value;
    }
    displaced = latestPeriod ? { period: latestPeriod, byIso3 } : null;
    status = st;

    if (events.length) {
      const first = dayOf(events[events.length - 1]._t);
      const last = Math.max(dayOf(events[0]._t), dayOf(Date.now()));
      domain = [Math.max(first, last - LOAD_DAYS * DAY), last];
    } else {
      const today = dayOf(Date.now());
      domain = [today - 6 * DAY, today];
    }
    return geo;
  }

  // ---------- filtering ----------
  function passes(e, ignoreRange) {
    if (!state.cats.has(e.category)) return false;
    if (!state.dirs.has(e.direction)) return false;
    if (!state.showNews && e.confidence === 'low') return false;
    if (!ignoreRange && state.range && (e._t < state.range[0] || e._t >= state.range[1])) return false;
    return true;
  }
  function setRangeDays(days) {
    state.rangeDays = days;
    const end = domain[1] + DAY;
    state.range = days ? [Math.max(domain[0], end - days * DAY), end] : [domain[0], end];
  }

  // ---------- render: panel ----------
  function renderSummary(list) {
    const counts = Object.fromEntries(DIRS.map((d) => [d.id, 0]));
    list.forEach((e) => { counts[e.direction] = (counts[e.direction] || 0) + 1; });
    const fmt = (ms) => new Date(ms).toLocaleDateString(state.lang === 'ja' ? 'ja-JP' : 'en-GB', { timeZone: 'UTC', month: 'short', day: 'numeric' });
    const [a, b] = state.range;
    const span = b - a <= DAY ? fmt(a) : `${fmt(a)} – ${fmt(b - DAY)}`;
    const dirs = DIRS.filter((d) => counts[d.id]).map((d) => `${d.icon} ${d[state.lang]} ${counts[d.id]}`).join('　');
    $('summary').innerHTML = `<div><strong>${list.length.toLocaleString()}</strong>${esc(t('count'))}<span class="muted">　${esc(t('period'))}: ${esc(span)} (UTC)</span></div><div class="muted">${esc(dirs)}</div>`;
  }

  function renderChips() {
    const inRange = events.filter((e) => passesExcept(e, 'cat'));
    const catCount = {};
    inRange.forEach((e) => { catCount[e.category] = (catCount[e.category] || 0) + 1; });
    $('cat-filters').innerHTML = CATS.map((c) => `<button type="button" class="chip" data-cat="${c.id}" aria-pressed="${state.cats.has(c.id)}">${dot(c.id)}${esc(c[state.lang])} <span class="n">${catCount[c.id] || 0}</span></button>`).join('');
    const inRange2 = events.filter((e) => passesExcept(e, 'dir'));
    const dirCount = {};
    inRange2.forEach((e) => { dirCount[e.direction] = (dirCount[e.direction] || 0) + 1; });
    $('dir-filters').innerHTML = DIRS.map((d) => `<button type="button" class="chip" data-dir="${d.id}" aria-pressed="${state.dirs.has(d.id)}"><span aria-hidden="true">${d.icon}</span>${esc(d[state.lang])} <span class="n">${dirCount[d.id] || 0}</span></button>`).join('');
  }
  function passesExcept(e, which) {
    const cats = state.cats; const dirs = state.dirs;
    if (which === 'cat') state.cats = new Set(CATS.map((c) => c.id));
    if (which === 'dir') state.dirs = new Set(DIRS.map((d) => d.id));
    const ok = passes(e, false);
    state.cats = cats; state.dirs = dirs;
    return ok;
  }

  const LIST_MAX = 150;
  function renderList(list) {
    $('list-count').textContent = t('items')(list.length);
    const items = list.slice(0, LIST_MAX).map((e) => {
      const orig = originalTitle(e);
      return `<li><button type="button" data-i="${e._i}">${dot(e.category, e.confidence === 'low')}<span class="ev-title">${esc(title(e))}</span>${orig ? `<span class="ev-orig">${esc(orig)}</span>` : ''}<span class="ev-meta">${esc(metaLine(e))}</span></button></li>`;
    });
    if (list.length > LIST_MAX) items.push(`<li class="more">${esc(t('more')(list.length - LIST_MAX))}</li>`);
    $('event-list').innerHTML = items.join('');
  }

  function renderStatus() {
    const el = $('status');
    if (!status) { el.innerHTML = ''; return; }
    const now = Date.now();
    el.innerHTML = Object.keys(SOURCES).filter((s) => status[s]).map((s) => {
      const st = status[s];
      let txt;
      if (st.skipped && !st.last_success) txt = t('skipped');
      else if (st.last_success) txt = `${t('updated')} ${t('ago')(Math.max(1, Math.round((now - Date.parse(st.last_success)) / 60000)))}`;
      else txt = t('never');
      const bad = st.ok === false && !st.skipped;
      return `<span class="${bad ? 'bad' : ''}">${esc(SOURCES[s].name)}: ${esc(txt)}${bad ? ` (${esc(t('failed'))})` : ''}</span>`;
    }).join('');
  }

  // ---------- render: map ----------
  function eventFeatures(list) {
    return {
      type: 'FeatureCollection',
      features: list.filter((e) => e.location && e.location.lat != null && e.location.lon != null).map((e) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [e.location.lon, e.location.lat] },
        properties: { i: e._i, cat: e.category, r: SEV_RADIUS[e.severity && e.severity.level] || 4, low: e.confidence === 'low' ? 1 : 0, t: e._t },
      })),
    };
  }
  function catMatch() {
    const m = ['match', ['get', 'cat']];
    CATS.forEach((c) => m.push(c.id, catColor(c.id)));
    m.push(css('--text-muted'));
    return m;
  }
  function choroColor() {
    if (!state.showChoro || !displaced) return css('--land');
    const steps = ['--choro-1', '--choro-2', '--choro-3', '--choro-4', '--choro-5'].map(css);
    const m = ['match', ['get', 'iso3']];
    let any = false;
    for (const [iso, v] of Object.entries(displaced.byIso3)) {
      let k = -1;
      CHORO_STEPS.forEach((s, j) => { if (v >= s) k = j; });
      if (k >= 0) { m.push(iso, steps[k]); any = true; }
    }
    m.push(css('--land'));
    return any ? m : css('--land');
  }
  function paintMap() {
    if (!mapReady) return;
    map.setPaintProperty('bg', 'background-color', css('--ocean'));
    map.setPaintProperty('land', 'fill-color', choroColor());
    map.setPaintProperty('borders', 'line-color', css('--land-line'));
    map.setPaintProperty('events', 'circle-color', catMatch());
    map.setPaintProperty('events', 'circle-stroke-color', ['case', ['==', ['get', 'low'], 1], catMatch(), css('--surface-1')]);
    renderChoroLegend();
  }
  function renderChoroLegend() {
    const el = $('choro-legend');
    if (!state.showChoro || !displaced) { el.hidden = true; return; }
    const steps = ['--choro-1', '--choro-2', '--choro-3', '--choro-4', '--choro-5'].map(css);
    const lab = (n) => (n >= 1e6 ? `${n / 1e6}M` : `${n / 1e3}k`);
    el.innerHTML = `${esc(t('choroTitle').replace('{p}', displaced.period))}<div class="ramp">${steps.map((c, j) => `<span><i style="background:${c}"></i>${lab(CHORO_STEPS[j])}+</span>`).join('')}</div>`;
    el.hidden = false;
  }
  function initMap(geo) {
    map = new maplibregl.Map({
      container: 'map',
      style: {
        version: 8,
        sources: {
          countries: { type: 'geojson', data: geo },
          events: { type: 'geojson', data: eventFeatures([]) },
        },
        layers: [
          { id: 'bg', type: 'background', paint: { 'background-color': css('--ocean') } },
          { id: 'land', type: 'fill', source: 'countries', paint: { 'fill-color': css('--land') } },
          { id: 'borders', type: 'line', source: 'countries', paint: { 'line-color': css('--land-line'), 'line-width': 0.6 } },
          {
            id: 'events', type: 'circle', source: 'events',
            layout: { 'circle-sort-key': ['-', 1, ['get', 'low']] },
            paint: {
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, ['get', 'r'], 6, ['*', 1.8, ['get', 'r']]],
              'circle-color': css('--c1'),
              'circle-opacity': ['case', ['==', ['get', 'low'], 1], 0.12, 0.9],
              'circle-stroke-width': ['case', ['==', ['get', 'low'], 1], 1.5, 1.5],
              'circle-stroke-color': css('--surface-1'),
            },
          },
        ],
      },
      center: [20, 18],
      zoom: 1.1,
      minZoom: 0.6,
      maxZoom: 9,
      dragRotate: false,
      attributionControl: { compact: true, customAttribution: 'Natural Earth' },
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.on('load', () => {
      mapReady = true;
      map.fitBounds([[-165, -50], [175, 70]], { padding: 8, duration: 0 });
      paintMap();
      renderMapData();
    });

    const tip = $('tooltip');
    map.on('mousemove', (ev) => {
      const hits = eventsAt(ev.point, 5);
      map.getCanvas().style.cursor = hits.length ? 'pointer' : '';
      if (hits.length) {
        showTip(ev.originalEvent, `${dot(hits[0].category, hits[0].confidence === 'low')} ${esc(title(hits[0]))}${hits.length > 1 ? `<div class="muted">+${hits.length - 1}</div>` : ''}`);
        return;
      }
      if (state.showChoro && displaced) {
        const f = map.queryRenderedFeatures(ev.point, { layers: ['land'] })[0];
        if (f) {
          const iso = f.properties.iso3;
          const v = displaced.byIso3[iso];
          showTip(ev.originalEvent, `<strong>${esc(f.properties[state.lang] || f.properties.en)}</strong><div>${esc(v ? t('people')(v) : t('noData'))}</div>`);
          return;
        }
      }
      tip.hidden = true;
    });
    map.on('mouseout', () => { tip.hidden = true; });
    map.on('click', (ev) => {
      const hits = eventsAt(ev.point, 6);
      if (hits.length) openPopup(hits, ev.lngLat);
    });
  }
  function eventsAt(point, pad) {
    if (!mapReady) return [];
    const box = [[point.x - pad, point.y - pad], [point.x + pad, point.y + pad]];
    const ids = new Set(map.queryRenderedFeatures(box, { layers: ['events'] }).map((f) => f.properties.i));
    return [...ids].map((i) => events[i]).filter(Boolean).sort((a, b) => (a.confidence === 'low') - (b.confidence === 'low') || b._t - a._t);
  }
  const POPUP_MAX = 6;
  function openPopup(list, lngLat) {
    const shown = list.slice(0, POPUP_MAX);
    const html = `<div class="popup">${shown.map((e) => {
      const orig = originalTitle(e);
      const url = safeUrl(e.urls && e.urls[0]);
      return `<div class="pi"><div class="pt">${dot(e.category, e.confidence === 'low')}${esc(title(e))}</div>${orig ? `<div class="po">${esc(orig)}</div>` : ''}<div class="pm">${esc(metaLine(e))}</div>${url ? `<div class="pm"><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(t('source'))} ↗</a></div>` : ''}</div>`;
    }).join('')}${list.length > POPUP_MAX ? `<div class="pm">${esc(t('popupMore')(list.length - POPUP_MAX))}</div>` : ''}</div>`;
    if (popup) popup.remove();
    popup = new maplibregl.Popup({ maxWidth: '320px', focusAfterOpen: false }).setLngLat(lngLat).setHTML(html).addTo(map);
  }
  function renderMapData(list) {
    if (!mapReady) return;
    map.getSource('events').setData(eventFeatures(list || events.filter((e) => passes(e))));
  }

  function showTip(mouse, html) {
    const tip = $('tooltip');
    tip.innerHTML = html;
    tip.hidden = false;
    const w = tip.offsetWidth; const h = tip.offsetHeight;
    let x = mouse.clientX + 14; let y = mouse.clientY + 14;
    if (x + w > innerWidth - 8) x = mouse.clientX - w - 14;
    if (y + h > innerHeight - 8) y = mouse.clientY - h - 14;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, y)}px`;
  }

  // ---------- render: timeline ----------
  const M = { l: 34, r: 8, t: 8, b: 22 };
  let tl = null; // geometry of the last draw, for pointer handling
  function renderTimeline() {
    if (!domain) return;
    const box = $('timeline');
    const W = box.clientWidth || 600; const H = box.clientHeight || 150;
    const nDays = Math.round((domain[1] - domain[0]) / DAY) + 1;
    const counts = Array.from({ length: nDays }, () => ({}));
    for (const e of events) {
      if (!passes(e, true)) continue;
      const k = Math.round((dayOf(e._t) - domain[0]) / DAY);
      if (k < 0 || k >= nDays) continue;
      counts[k][e.category] = (counts[k][e.category] || 0) + 1;
    }
    const totals = counts.map((c) => Object.values(c).reduce((a, b) => a + b, 0));
    const max = Math.max(1, ...totals);
    const nice = niceMax(max);
    const iw = W - M.l - M.r; const ih = H - M.t - M.b;
    const step = iw / nDays;
    const bw = Math.max(1, Math.min(28, step - 2));
    const y = (v) => M.t + ih - (v / nice) * ih;
    tl = { W, H, nDays, step, iw, counts, totals };

    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t('timeline'))}">`;
    s += '<g class="grid">';
    [0, nice / 2, nice].forEach((v) => { s += `<line x1="${M.l}" x2="${W - M.r}" y1="${y(v)}" y2="${y(v)}" /> `; });
    s += '</g><g class="axis">';
    [0, nice / 2, nice].forEach((v) => { s += `<text x="${M.l - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`; });
    const every = Math.max(1, Math.ceil(nDays / Math.max(2, Math.floor(iw / 70))));
    const fmt = (ms) => new Date(ms).toLocaleDateString(state.lang === 'ja' ? 'ja-JP' : 'en-GB', { timeZone: 'UTC', month: 'numeric', day: 'numeric' });
    for (let k = nDays - 1; k >= 0; k -= every) {
      s += `<text x="${M.l + k * step + step / 2}" y="${H - 6}" text-anchor="middle">${fmt(domain[0] + k * DAY)}</text>`;
    }
    s += '</g>';
    if (state.range) {
      const a = Math.max(0, (state.range[0] - domain[0]) / DAY); const b = Math.min(nDays, (state.range[1] - domain[0]) / DAY);
      s += `<rect class="brush" x="${M.l + a * step}" y="${M.t - 4}" width="${Math.max(2, (b - a) * step)}" height="${ih + 4}" rx="3" />`;
    }
    const surf = css('--surface-1');
    counts.forEach((c, k) => {
      let base = 0;
      const x = M.l + k * step + (step - bw) / 2;
      const present = CATS.filter((cat) => c[cat.id]);
      present.forEach((cat, j) => {
        const v = c[cat.id];
        const y0 = y(base); const y1 = y(base + v);
        const top = j === present.length - 1;
        s += barSeg(x, y1, bw, y0 - y1, top ? Math.min(4, bw / 2) : 0, catColor(cat.id), surf);
        base += v;
      });
    });
    s += `<rect class="hover-col" id="tl-hover" x="0" y="${M.t}" width="${step}" height="${ih}" visibility="hidden" />`;
    s += '</svg>';
    box.innerHTML = s;
  }
  function barSeg(x, y, w, h, r, fill, gap) {
    if (h <= 0) return '';
    r = Math.min(r, h);
    const d = r
      ? `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
      : `M${x},${y + h}V${y}H${x + w}V${y + h}Z`;
    // A 1px stroke in the surface color leaves a 2px gap between stacked segments.
    return `<path d="${d}" fill="${fill}" stroke="${gap}" stroke-width="1" />`;
  }
  function niceMax(v) {
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 4, 5, 10]) if (m * p >= v) return Math.max(2, m * p);
    return v;
  }
  function initTimeline() {
    const box = $('timeline');
    let drag = null;
    const dayAt = (ev) => {
      const r = box.getBoundingClientRect();
      const x = (ev.clientX - r.left) * (tl.W / r.width) - M.l;
      return Math.min(tl.nDays - 1, Math.max(0, Math.floor(x / tl.step)));
    };
    box.addEventListener('pointerdown', (ev) => {
      if (!tl) return;
      drag = { from: dayAt(ev), moved: false };
      box.setPointerCapture(ev.pointerId);
    });
    box.addEventListener('pointermove', (ev) => {
      if (!tl) return;
      const k = dayAt(ev);
      if (drag) {
        if (k !== drag.from) drag.moved = true;
        const a = Math.min(drag.from, k); const b = Math.max(drag.from, k) + 1;
        state.range = [domain[0] + a * DAY, domain[0] + b * DAY];
        state.rangeDays = null;
        renderAll(true);
        return;
      }
      const hover = document.getElementById('tl-hover');
      if (hover) { hover.setAttribute('x', M.l + k * tl.step); hover.setAttribute('visibility', 'visible'); }
      const c = tl.counts[k];
      const rows = CATS.filter((cat) => c[cat.id]).map((cat) => `<div class="row"><span>${dot(cat.id)}${esc(cat[state.lang])}</span><span>${c[cat.id]}</span></div>`).join('');
      const d = new Date(domain[0] + k * DAY).toLocaleDateString(state.lang === 'ja' ? 'ja-JP' : 'en-GB', { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric' });
      showTip(ev, `<strong>${esc(d)}</strong>${rows}<div class="row muted"><span>${esc(t('total'))}</span><span>${tl.totals[k]}</span></div>`);
    });
    const end = (ev) => {
      if (!drag) return;
      if (!drag.moved) {
        const k = drag.from;
        state.range = [domain[0] + k * DAY, domain[0] + (k + 1) * DAY];
        state.rangeDays = null;
      }
      drag = null;
      try { box.releasePointerCapture(ev.pointerId); } catch (_) { /* ignore */ }
      renderAll();
    };
    box.addEventListener('pointerup', end);
    box.addEventListener('pointercancel', end);
    box.addEventListener('pointerleave', () => {
      $('tooltip').hidden = true;
      const hover = document.getElementById('tl-hover');
      if (hover) hover.setAttribute('visibility', 'hidden');
    });
    let raf = 0;
    new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(renderTimeline); }).observe(box);
  }

  // ---------- wiring ----------
  function renderAll(fast) {
    if (!domain) return;
    const list = events.filter((e) => passes(e));
    renderTimeline();
    renderMapData(list);
    renderSummary(list);
    document.querySelectorAll('#ranges button').forEach((b) => b.setAttribute('aria-pressed', String(Number(b.dataset.days) === state.rangeDays)));
    const note = $('map-note');
    if (!list.length) { note.textContent = t('empty'); note.hidden = false; } else note.hidden = true;
    if (fast) return; // while dragging the brush, skip the heavier panel work
    renderChips();
    renderList(list);
    renderStatus();
    paintMap();
  }

  function initControls() {
    $('cat-filters').addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-cat]');
      if (!b) return;
      const id = b.dataset.cat;
      if (state.cats.has(id)) state.cats.delete(id); else state.cats.add(id);
      renderAll();
    });
    $('dir-filters').addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-dir]');
      if (!b) return;
      const id = b.dataset.dir;
      if (state.dirs.has(id)) state.dirs.delete(id); else state.dirs.add(id);
      renderAll();
    });
    $('toggle-news').addEventListener('change', (ev) => { state.showNews = ev.target.checked; renderAll(); });
    $('toggle-choro').addEventListener('change', (ev) => { state.showChoro = ev.target.checked; paintMap(); });
    $('ranges').addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-days]');
      if (!b) return;
      setRangeDays(Number(b.dataset.days));
      renderAll();
    });
    $('event-list').addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-i]');
      if (!b || !mapReady) return;
      const e = events[Number(b.dataset.i)];
      if (!e || e.location.lat == null) return;
      const ll = [e.location.lon, e.location.lat];
      map.flyTo({ center: ll, zoom: Math.max(map.getZoom(), 4), speed: 1.4 });
      openPopup([e], ll);
    });
    const mq = matchMedia('(prefers-color-scheme: dark)');
    (mq.addEventListener ? mq.addEventListener.bind(mq, 'change') : mq.addListener.bind(mq))(() => renderAll());
  }

  async function main() {
    initLang();
    initControls();
    initTimeline();
    let geo;
    try {
      geo = await loadData();
    } catch (err) {
      console.error(err);
      const note = $('map-note');
      note.textContent = t('loadError');
      note.hidden = false;
      return;
    }
    setRangeDays(7);
    initMap(geo);
    renderAll();
  }

  main();
})();
