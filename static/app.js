/* Item Hunter front-end: polls /api/state and renders the cards + feed. */
const $ = (sel, el = document) => el.querySelector(sel);
const ui = { data: null, expanded: new Set(), showHidden: new Set(), lastEventTs: undefined, editing: null };
const ROWS_COLLAPSED = 8;
const CHANNEL_NAMES = { email: 'Email', push: 'Phone push' };

async function api(method, url, body) {
  const r = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try { msg = (await r.json()).error || msg; } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return r.json();
}

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function money(p, cur) {
  if (p === null || p === undefined) return 'no price';
  const s = '$' + Number(p).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return cur ? `${s} <small>${esc(cur)}</small>` : s;
}
function ago(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
function flash(msg, good = false) {
  const el = document.createElement('div');
  el.className = 'toast' + (good ? ' ok' : ''); el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}
function sourceName(key) {
  const src = (ui.data.sources || []).find(s => s.key === key);
  return src ? src.name : key;
}
// The store badge column is narrow; the full name goes in the tooltip and everywhere else.
const SHORT_SOURCE = {
  facebook: 'Facebook', canadacomputers: 'Canada Comp.', bestbuy_ca: 'Best Buy',
  amazon: 'Amazon', newegg: 'Newegg', ebay: 'eBay',
};
function shortSource(r) {
  const short = SHORT_SOURCE[r.source];
  if (!short) return r.source_name;
  const suffix = /\.(ca|com)\b/.exec(r.source_name);   // keep Amazon.ca vs Amazon.com apart
  return suffix ? short + suffix[0] : short;
}

/* ---------- polling ---------- */
let lastSignature = '';
async function refresh(force = false) {
  let data;
  try {
    data = await api('GET', '/api/state');
  } catch (e) {
    $('#status').textContent = 'Server unreachable. Is app.py still running?';
    $('#status').classList.remove('busy');
    lastSignature = '';
    return;
  }
  ui.data = data;
  // Redrawing the cards every 4 seconds replaces the buttons under the pointer and can swallow a click,
  // so only redraw when something changed. The clock-driven text ("2 min ago") is refreshed separately.
  const { now, ...rest } = data;
  const signature = JSON.stringify(rest);
  if (force || signature !== lastSignature) {
    lastSignature = signature;
    render();
  } else {
    renderStatus(data);
  }
}

function render() {
  const d = ui.data;
  renderStatus(d);
  renderErrors(d);
  renderFeed(d);
  renderItems(d);
  if ($('#settingsDlg').open) renderLog(d);
}

function renderStatus(d) {
  const st = d.status, el = $('#status');
  if (st.running) {
    el.textContent = `Checking ${st.current}`;
    el.classList.add('busy');
    return;
  }
  el.classList.remove('busy');
  const parts = [st.last_run ? `Last check ${ago(st.last_run)}` : 'No check yet'];
  const here = new Set((d.sources || []).filter(s => s.regions.includes(d.settings.region)).map(s => s.key));
  const fastest = Math.min(...Object.entries(d.cadence || {})
    .filter(([k]) => here.has(k) && d.settings.sources[k] !== false).map(([, v]) => v).concat([Infinity]));
  if (Number.isFinite(fastest)) parts.push(`fastest store every ${fastest < 90 ? fastest + ' s' : Math.round(fastest / 60) + ' min'}`);
  parts.push(`${st.workers || 1} checking at once`);
  if (st.browser) parts.push(`via ${st.browser}`);
  el.textContent = parts.join(' · ');
}

function renderErrors(d) {
  const other = $('#otherEdition');
  other.hidden = !d.other_edition;
  if (d.other_edition) {
    other.innerHTML = `This edition of Item Hunter is for Canada and the United States, and this computer is set to
      somewhere else. For every other country, use <a href="${esc(d.other_edition.url)}" target="_blank" rel="noopener">Item
      Hunter International</a>. To watch Canadian or US prices anyway, pick the country in Settings.`;
  }
  const box = $('#errors');
  const names = Object.assign({}, CHANNEL_NAMES, Object.fromEntries(d.sources.map(s => [s.key, s.name])));
  const errs = Object.entries(d.status.errors || {});
  box.hidden = errs.length === 0;
  box.innerHTML = errs.map(([k, e]) =>
    `<span class="chip" title="${esc(e.item)} · ${ago(e.ts)}">${esc(names[k] || k)}: ${esc(e.msg)}</span>`).join('');
}

function renderFeed(d) {
  const evs = d.events || [];
  $('#feedwrap').hidden = evs.length === 0;
  $('#feed').innerHTML = evs.slice(0, 40).map(ev => {
    const badge = ev.type === 'drop'
      ? `<span class="badge drop">Down ${money(ev.prev_price - ev.price)}</span>`
      : `<span class="badge new">${ev.type === 'restock' ? 'Back in stock' : 'New'}</span>`;
    const where = ev.location ? ` · ${esc(ev.location)}` : '';
    return `<a class="ev" href="${esc(ev.url)}" target="_blank" rel="noopener">
      <div class="l1">${badge}<span class="item">${esc(ev.item_name)}</span><span class="price">${money(ev.price)}</span></div>
      <div class="t">${esc(ev.source_name)} · ${esc(ev.title)}</div>
      <div class="when">${ago(ev.ts)}${where}${ev.prev_price ? ` · was ${money(ev.prev_price)}` : ''}</div>
    </a>`;
  }).join('');

  // Browser notifications for events that arrived since the last poll. Judged by time, not by the id of
  // the last event seen: that event can disappear (its card removed), and then every event in the feed
  // would look new at once.
  const newestTs = evs.length ? evs[0].ts : 0;
  if (ui.lastEventTs !== undefined && 'Notification' in window && Notification.permission === 'granted') {
    const fresh = evs.filter(ev => ev.ts > ui.lastEventTs).slice(0, 3);
    for (const ev of fresh) {
      const kind = ev.type === 'drop' ? 'price drop' : ev.type === 'restock' ? 'back in stock' : 'new listing';
      const n = new Notification(`${ev.item_name}: ${kind} on ${ev.source_name}`,
        { body: `$${Number(ev.price).toFixed(2)} · ${ev.title}` });
      n.onclick = () => window.open(ev.url, '_blank');
    }
  }
  ui.lastEventTs = Math.max(ui.lastEventTs || 0, newestTs);
}

function listingRow(item, r, bestKey, isHiddenView) {
  const cls = ['lrow'];
  if (r.key === bestKey) cls.push('best');
  if (!r.active) cls.push('inactive');
  if (r.suspect) cls.push('oos');
  if (isHiddenView) cls.push('hiddenrow');
  const recentDrop = r.prev_price && r.price < r.prev_price && r.changed_at && (Date.now() / 1000 - r.changed_at) < 86400;
  if (recentDrop) cls.push('dropped');

  let sub = '';
  if (r.prev_price && r.prev_price !== r.price) {
    const down = r.price < r.prev_price;
    sub = `<small class="${down ? 'down' : 'up'}">${down ? 'down from' : 'up from'} ${money(r.prev_price)}</small>`;
  } else if (r.was_price && r.was_price > r.price) {
    sub = `<small>list ${money(r.was_price)}</small>`;
  }
  const tags = [];
  if (r.key === bestKey) tags.push('<span class="tag low">best</span>');
  if (r.suspect) tags.push('<span class="tag oos" title="Far below every retail price; probably a placeholder">price?</span>');
  if (!r.active) tags.push('<span class="tag">gone</span>');
  if (r.sponsored) tags.push('<span class="tag">ad</span>');
  if (/^used/i.test(r.condition || '')) tags.push('<span class="tag used">used</span>');
  if (r.lowest !== undefined && r.price === r.lowest && (r.history || []).length > 3) tags.push('<span class="tag low">lowest seen</span>');
  const meta = [r.condition, r.location, r.seller].filter(Boolean).join(' · ');
  const hideBtn = isHiddenView
    ? `<button class="quiet" data-action="unhide" data-key="${esc(r.key)}" title="Show this listing again">Unhide</button>`
    : `<button class="quiet" data-action="hide" data-key="${esc(r.key)}" title="Hide this listing">Hide</button>`;
  return `<div class="${cls.join(' ')}">
    <a class="cells" href="${esc(r.url)}" target="_blank" rel="noopener">
      <span class="src ${esc(r.source)}" title="${esc(r.source_name)}">${esc(shortSource(r))}</span>
      <span class="pr">${money(r.price)}${sub}</span>
      <span class="ttl">${esc(r.title)}${tags.join('')}<span class="meta">${esc(meta)}&nbsp;</span></span>
    </a>
    ${hideBtn}
  </div>`;
}

function renderItems(d) {
  const items = d.items || [];
  $('#empty').hidden = items.length > 0;
  $('#items').innerHTML = items.map(item => {
    const bestKey = item.best ? item.best.key : null;
    const showHidden = ui.showHidden.has(item.id);
    const expanded = ui.expanded.has(item.id);
    const visible = item.listings.filter(r => r.active && !r.hidden);
    const hiddenRows = item.listings.filter(r => r.hidden);
    const goneRows = item.listings.filter(r => !r.active && !r.hidden);
    const rows = expanded ? visible.concat(goneRows) : visible.slice(0, ROWS_COLLAPSED);
    let html = rows.map(r => listingRow(item, r, bestKey, false)).join('');
    if (showHidden) html += hiddenRows.map(r => listingRow(item, r, bestKey, true)).join('');
    if (!html) {
      html = `<div class="nothing">${item.last_checked ? 'No in-stock listing matches yet. Loosen the filters under Edit if that seems wrong.' : 'Searching every store now.'}</div>`;
    }
    const more = [];
    if (visible.length > ROWS_COLLAPSED || goneRows.length) {
      more.push(`<button class="ghost small" data-action="expand">${expanded ? 'Show fewer' : `Show all (${visible.length + goneRows.length})`}</button>`);
    }
    if (hiddenRows.length) {
      more.push(`<button class="ghost small" data-action="togglehidden">${showHidden ? 'Hide hidden' : `Show hidden (${hiddenRows.length})`}</button>`);
    }
    const best = item.best
      ? `<div class="p">${money(item.best.price)}</div><div class="s">${esc(item.best.source_name)} · ${esc(item.best.currency || '')}</div>`
      : `<div class="p none">No price</div><div class="s">no in-stock match yet</div>`;
    const target = item.target_price ? ` · target ${money(item.target_price)}` : '';
    let stores = '';
    if (item.sources && item.sources.length) {
      stores = item.sources.length === 1 ? ` · ${esc(sourceName(item.sources[0]))} only` : ` · ${item.sources.map(sourceName).map(esc).join(', ')}`;
    }
    return `<div class="card ${item.paused ? 'paused' : ''}" data-id="${esc(item.id)}">
      <div class="head">
        <div><div class="name">${esc(item.name)}</div>
          <div class="sub">"${esc(item.query)}" · ${visible.length} listing${visible.length === 1 ? '' : 's'} · checked ${ago(item.last_checked)}${target}${stores}${item.paused ? ' · paused' : ''}</div></div>
        <div class="best">${best}</div>
      </div>
      <div class="tools">
        <button class="quiet" data-action="check" title="Check this item now">Check now</button>
        <button class="quiet" data-action="edit" title="Edit filters and stores">Edit</button>
        <button class="quiet" data-action="pause" title="${item.paused ? 'Resume checking' : 'Pause checking'}">${item.paused ? 'Resume' : 'Pause'}</button>
        <button class="quiet" data-action="delete" title="Remove item">Remove</button>
      </div>
      <div class="rows">${html}</div>
      <div class="more">${more.join('')}</div>
    </div>`;
  }).join('');
}

function renderLog(d) {
  $('#log').textContent = (d.status.log || []).map(l => {
    const t = new Date(l.ts * 1000);
    return `${t.toLocaleTimeString()}  ${l.msg}`;
  }).join('\n') || '(nothing yet)';
}

/* ---------- actions ---------- */
$('#items').addEventListener('click', async e => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  e.preventDefault();
  const card = btn.closest('.card');
  const id = card.dataset.id;
  const item = ui.data.items.find(i => i.id === id);
  const action = btn.dataset.action;
  try {
    if (action === 'check') { await api('POST', `/api/items/${id}/check`); }
    else if (action === 'pause') { await api('PATCH', `/api/items/${id}`, { paused: !item.paused }); }
    else if (action === 'delete') {
      if (confirm(`Stop hunting "${item.name}"? Its price history goes with it.`)) await api('DELETE', `/api/items/${id}`);
    }
    else if (action === 'edit') { openEdit(item); return; }
    else if (action === 'hide') { await api('POST', `/api/items/${id}/hide`, { key: btn.dataset.key, hidden: true }); }
    else if (action === 'unhide') { await api('POST', `/api/items/${id}/hide`, { key: btn.dataset.key, hidden: false }); }
    else if (action === 'expand') { ui.expanded.has(id) ? ui.expanded.delete(id) : ui.expanded.add(id); render(); return; }
    else if (action === 'togglehidden') { ui.showHidden.has(id) ? ui.showHidden.delete(id) : ui.showHidden.add(id); render(); return; }
    await refresh(true);
  } catch (err) { flash(err.message); }
});

$('#addForm').addEventListener('submit', async e => {
  e.preventDefault();
  const q = $('#addQuery').value.trim();
  if (!q) return;
  try {
    await api('POST', '/api/items', { query: q });
    $('#addQuery').value = '';
    await refresh(true);
  } catch (err) { flash(err.message); }
});

$('#checkAll').addEventListener('click', async () => {
  try {
    await api('POST', '/api/check');
    flash('Check queued. Several stores are read at once, with a pause between requests to the same store, so a full pass takes a minute or two.', true);
    await refresh(true);
  } catch (err) { flash(err.message); }
});
$('#clearFeed').addEventListener('click', async () => { try { await api('POST', '/api/events/clear'); await refresh(true); } catch (err) { flash(err.message); } });

/* ---------- store checkbox helpers (shared by both dialogs) ---------- */
function everyText(seconds) {
  if (!seconds) return '';
  return seconds < 90 ? `every ${seconds} s` : `every ${Math.round(seconds / 60)} min`;
}
function storeToggles(container, prefix, isChecked, note) {
  container.innerHTML = ui.data.sources.map(src => {
    const extra = note ? note(src) : everyText((ui.data.cadence || {})[src.key]);
    return `<label class="inline${note && extra ? ' off' : ''}"><input type="checkbox" name="${prefix}${esc(src.key)}" ${isChecked(src) ? 'checked' : ''}> ${esc(src.name)} <span class="hint" style="margin:0">${extra ? esc(extra) : src.regions.join('/')}</span></label>`;
  }).join('');
}
function setStores(form, prefix, keep) {
  for (const src of ui.data.sources) form[`${prefix}${src.key}`].checked = keep(src.key);
}
function checkedStores(form, prefix) {
  return ui.data.sources.filter(src => form[`${prefix}${src.key}`].checked).map(src => src.key);
}

/* ---------- edit dialog ---------- */
function openEdit(item) {
  ui.editing = item.id;
  const f = $('#editForm');
  f.name.value = item.name; f.query.value = item.query;
  f.must.value = (item.must || []).join(', '); f.exclude.value = (item.exclude || []).join(', ');
  f.min_price.value = item.min_price ?? ''; f.max_price.value = item.max_price ?? ''; f.target_price.value = item.target_price ?? '';
  storeToggles($('#itemSources'), 'isrc_', src => !item.sources || item.sources.includes(src.key),
    src => (ui.data.settings.sources[src.key] === false ? 'switched off in Settings, so not checked' : ''));
  $('#editDlg').showModal();
}
$('#itemAmazonOnly').addEventListener('click', () => setStores($('#editForm'), 'isrc_', k => k === 'amazon'));
$('#itemAllStores').addEventListener('click', () => setStores($('#editForm'), 'isrc_', () => true));
$('#editForm').addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target;
  const num = v => (v === '' ? null : Number(v));
  const picked = checkedStores(f, 'isrc_');
  if (!picked.length) return flash('Pick at least one store for this item.');
  const all = picked.length === ui.data.sources.length;
  try {
    await api('PATCH', `/api/items/${ui.editing}`, {
      name: f.name.value, query: f.query.value, must: f.must.value, exclude: f.exclude.value,
      min_price: num(f.min_price.value), max_price: num(f.max_price.value), target_price: num(f.target_price.value),
      sources: all ? null : picked,
    });
    $('#editDlg').close();
    await refresh(true);
  } catch (err) { flash(err.message); }
});

/* ---------- settings dialog ---------- */
function openSettings() {
  const d = ui.data, s = d.settings, f = $('#settingsForm');
  f.region.value = s.region; f.speed.value = s.speed || 'normal'; f.workers.value = s.workers || 2;
  dialogRegion = s.region;
  f.fb_city.value = s.fb_city; f.notify.checked = !!s.notify;
  f.include_used.checked = s.include_used !== false;
  storeToggles($('#sourceToggles'), 'src_', src => s.sources[src.key] !== false);
  const em = s.email || {}, pu = s.push || {};
  f.email_enabled.checked = !!em.enabled; f.email_to.value = em.to || ''; f.email_smtp_host.value = em.smtp_host || '';
  f.email_smtp_port.value = em.smtp_port || 587; f.email_username.value = em.username || ''; f.email_security.value = em.security || 'starttls';
  f.email_password.value = ''; f.email_password.placeholder = em.password_set ? 'saved, leave blank to keep' : '';
  f.push_enabled.checked = !!pu.enabled; f.push_topic.value = pu.topic || ''; f.push_server.value = pu.server || 'https://ntfy.sh';
  renderLog(d);
  $('#settingsDlg').showModal();
}
function settingsPayload(f) {
  const sources = {};
  for (const src of ui.data.sources) sources[src.key] = f[`src_${src.key}`].checked;
  const email = {
    enabled: f.email_enabled.checked, to: f.email_to.value, smtp_host: f.email_smtp_host.value,
    smtp_port: Number(f.email_smtp_port.value) || 587, username: f.email_username.value, security: f.email_security.value,
  };
  if (f.email_password.value) email.password = f.email_password.value;
  const push = { enabled: f.push_enabled.checked, topic: f.push_topic.value, server: f.push_server.value };
  return { region: f.region.value, speed: f.speed.value, workers: Number(f.workers.value) || 2,
           fb_city: f.fb_city.value, notify: f.notify.checked, include_used: f.include_used.checked,
           sources, email, push };
}
$('#openSettings').addEventListener('click', openSettings);
// switching country brings that country's Facebook city along, unless the user typed their own
let dialogRegion = null;
$('#settingsForm').region.addEventListener('focus', e => { dialogRegion = e.target.value; });
$('#settingsForm').region.addEventListener('change', e => {
  const f = $('#settingsForm'), defaults = ui.data.fb_defaults || {};
  if (!f.fb_city.value || f.fb_city.value === defaults[dialogRegion]) f.fb_city.value = defaults[e.target.value] || '';
  dialogRegion = e.target.value;
});
$('#storesAmazonOnly').addEventListener('click', () => setStores($('#settingsForm'), 'src_', k => k === 'amazon'));
$('#storesAll').addEventListener('click', () => setStores($('#settingsForm'), 'src_', () => true));
$('#settingsForm').addEventListener('submit', async e => {
  e.preventDefault();
  const payload = settingsPayload(e.target);
  if (!Object.values(payload.sources).some(Boolean)) return flash('Switch on at least one store.');
  try {
    const saved = await api('PATCH', '/api/settings', payload);
    if (saved.workers !== payload.workers) flash(`Between 1 and 4 stores can be checked at once; using ${saved.workers}.`);
    else if (payload.workers !== (ui.data.settings.workers || 2)) flash('Restart Item Hunter for the new number of checkers to take effect.', true);
    $('#settingsDlg').close();
    await refresh(true);
  } catch (err) { flash(err.message); }
});
$('#testNotify').addEventListener('click', async () => {
  try {
    const r = await api('POST', '/api/test-notify');
    r.ok ? flash('Windows notification sent.', true) : flash('Notification failed: ' + (r.error || 'unknown error'));
  } catch (err) { flash(err.message); }
});
function showTestResult(el, good, text) {
  el.hidden = false;
  el.className = 'teststatus ' + (good ? 'good' : 'bad');
  el.textContent = text;
}
$('#testEmail').addEventListener('click', async () => {
  const btn = $('#testEmail'), st = $('#emailStatus');
  btn.disabled = true; btn.textContent = 'Sending';
  showTestResult(st, true, 'Sending a test email');
  try {
    const r = await api('POST', '/api/test-email', settingsPayload($('#settingsForm')).email);
    if (r.ok) showTestResult(st, true, `Sent to ${r.to}. Check that inbox (and its spam folder), then press Save.`);
    else showTestResult(st, false, 'Failed: ' + r.error);
  } catch (err) { showTestResult(st, false, 'Failed: ' + err.message); }
  btn.disabled = false; btn.textContent = 'Send test email';
});
$('#testPush').addEventListener('click', async () => {
  const btn = $('#testPush'), st = $('#pushStatus');
  btn.disabled = true; btn.textContent = 'Sending';
  showTestResult(st, true, 'Sending a test push');
  try {
    const r = await api('POST', '/api/test-push', settingsPayload($('#settingsForm')).push);
    if (r.ok) showTestResult(st, true, `Sent to topic ${r.topic}. It should be on your phone now; then press Save.`);
    else showTestResult(st, false, 'Failed: ' + r.error);
  } catch (err) { showTestResult(st, false, 'Failed: ' + err.message); }
  btn.disabled = false; btn.textContent = 'Send test push';
});
$('#genTopic').addEventListener('click', () => {
  const alphabet = 'abcdefghijkmnpqrstuvwxyz23456789';
  const bytes = new Uint8Array(10); crypto.getRandomValues(bytes);
  $('#settingsForm').push_topic.value = 'item-hunter-' + Array.from(bytes, b => alphabet[b % alphabet.length]).join('');
});
$('#browserAlerts').addEventListener('click', async () => {
  if (!('Notification' in window)) return flash('This browser does not support notifications.');
  const p = await Notification.requestPermission();
  flash(p === 'granted' ? 'Browser alerts enabled for this tab.' : 'Browser alerts not allowed.', p === 'granted');
});
document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));

/* ---------- go ---------- */
refresh();
setInterval(refresh, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
