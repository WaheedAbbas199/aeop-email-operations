(() => {
'use strict';
/* AEOP dashboard. All data is rendered with textContent (never innerHTML) so email content cannot inject markup. */
const S = { user: null, cfg: {}, pending: 0, notif: { items: [], unread: 0 }, tok: 0, timer: null, popOpen: false };
const Q = { inbox: { page: 1, q: '', category: '', status: '', priority: '' }, runs: { page: 1, state: '', workflow: '' },
  approvals: { tab: 'pending', page: 1 }, records: { kind: 'tickets', page: 1 }, jobs: { status: 'dead', page: 1 },
  audit: { page: 1, action: '', actor: '', resource_type: '' } };
const NS = 'http://www.w3.org/2000/svg';

/* ------------------------------------------------------------------ helpers */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false || k === 'value') continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'checked' || k === 'disabled' || k === 'selected') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat(Infinity)) { if (c == null || c === false) continue; el.append(c.nodeType ? c : document.createTextNode(String(c))); }
  if (attrs && attrs.value !== undefined && attrs.value !== null) el.value = attrs.value;
  return el;
}
const IC = {
  home: '<path d="M3 11l9-8 9 8v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>', inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/>',
  flow: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M6 9v3a3 3 0 0 0 3 3h6"/>', check: '<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
  bot: '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V4"/><circle cx="9" cy="14" r="1"/><circle cx="15" cy="14" r="1"/>',
  tool: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>',
  plug: '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/>', alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>', chart: '<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2 21c0-4 3-6 7-6s7 2 7 6"/><path d="M17 4a3.5 3.5 0 0 1 0 7M22 21c0-3-1.5-5-4-5.7"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8M10 21a2 2 0 0 0 4 0"/>', plus: '<path d="M12 5v14M5 12h14"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>', mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', cal: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>', file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>'
};
function ico(n, s = 18) {
  const d = document.createElementNS(NS, 'svg');
  for (const [k, v] of Object.entries({ width: s, height: s, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })) d.setAttribute(k, v);
  d.innerHTML = IC[n] || ''; // static trusted constants only
  return d;
}
function cubes(size = 300) {
  const cube = (x, y, s, a, b, c) => `<g transform="translate(${x} ${y})"><polygon points="0,${-s} ${s * .87},${-s / 2} 0,0 ${-s * .87},${-s / 2}" fill="${a}"/><polygon points="${-s * .87},${-s / 2} 0,0 0,${s} ${-s * .87},${s / 2}" fill="${b}"/><polygon points="${s * .87},${-s / 2} 0,0 0,${s} ${s * .87},${s / 2}" fill="${c}"/></g>`;
  const w = document.createElementNS(NS, 'svg');
  w.setAttribute('viewBox', '0 0 300 300'); w.setAttribute('width', size); w.setAttribute('height', size); w.setAttribute('aria-hidden', 'true');
  w.setAttribute('style', 'filter:drop-shadow(0 0 28px rgba(109,93,252,.65))');
  w.innerHTML = cube(150, 150, 60, '#a99bff', '#5b4bf5', '#3b2fb8') + cube(90, 200, 40, '#7dd3fc', '#2563eb', '#1e3a8a') + cube(215, 205, 40, '#f0abfc', '#9333ea', '#581c87') + cube(150, 80, 32, '#c4b5fd', '#6d5dfc', '#4338ca');
  return w;
}
const cookie = n => (document.cookie.split('; ').find(r => r.startsWith(n + '=')) || '').split('=')[1];
async function api(path, { method = 'GET', body } = {}) {
  const o = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
  if (method !== 'GET') o.headers['X-CSRF-Token'] = cookie('aeop_csrf') || '';
  const r = await fetch('/api/v1' + path, o);
  let data = null; try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) {
    const d = data && data.detail;
    const e = new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : `Request failed (${r.status})`);
    e.status = r.status;
    if (r.status === 401 && S.user && !path.startsWith('/auth/')) { S.user = null; location.hash = '#/login'; }
    throw e;
  }
  return data;
}
function toast(msg, kind = '') { const t = h('div', { class: 'toast ' + kind }, msg); document.getElementById('toasts').append(t); setTimeout(() => t.remove(), 4500); }
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, 'err'); } };
const can = p => !!S.user && S.user.permissions.includes(p);
const fmt = iso => iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
const STATE = { completed: 'ok', ignored: '', awaiting_approval: 'warn', awaiting_response: 'cy', needs_review: 'warn', failed: 'bad', rejected: 'bad', cancelled: '', processing: 'acc', executing: 'acc', approved: 'ok', queued: 'acc', retry_scheduled: 'warn', received: '', classified: 'acc', linked_reply: 'cy', pending: 'warn', expired: '', executed: 'ok', dead: 'bad', running: 'acc', done: 'ok', connected: 'ok', error: 'bad', reauth_required: 'warn', disconnected: '' };
const badge = (txt, cls) => h('span', { class: 'badge b-' + (cls ?? (STATE[txt] || 'x')) }, String(txt).replace(/_/g, ' '));
const prio = p => p ? h('span', { class: 'badge b-' + ({ P1: 'bad', P2: 'warn', P3: 'cy', P4: 'x' }[p] || 'x') }, p) : '—';
const risk = r => h('span', { class: 'badge b-' + ({ low: 'ok', medium: 'warn', high: 'bad', critical: 'bad' }[r] || 'x') }, r + ' risk');
const money = c => c == null ? '—' : (c / 100).toLocaleString(undefined, { minimumFractionDigits: 2 });
const card = (title, ...c) => h('div', { class: 'card' }, title ? h('h3', {}, title) : null, ...c);
const empty = (t, sub) => h('div', { class: 'empty' }, h('div', { style: 'font-size:15px;color:#cfcff0' }, t), sub ? h('div', { class: 'small' }, sub) : null);
const link = (href, t) => h('a', { href }, t);
const field = (label, input) => h('label', { class: 'f' }, label, input);
function table(cols, rows, onrow) {
  return h('div', { class: 'tw' }, h('table', {}, h('thead', {}, h('tr', {}, cols.map(c => h('th', {}, c)))),
    h('tbody', {}, rows.map(r => { const tr = h('tr', { class: onrow ? 'click' : '' }, r.cells.map(c => h('td', {}, c))); if (onrow) tr.addEventListener('click', () => onrow(r)); return tr; }))));
}
function pager(res, set) {
  const pages = Math.max(1, Math.ceil(res.total / res.page_size));
  return h('div', { class: 'row spread', style: 'margin-top:12px' }, h('span', { class: 'muted small' }, `${res.total} total · page ${res.page} of ${pages}`),
    h('div', { class: 'row' }, h('button', { class: 'btn sm', disabled: res.page <= 1, onclick: () => set(res.page - 1) }, 'Prev'), h('button', { class: 'btn sm', disabled: res.page >= pages, onclick: () => set(res.page + 1) }, 'Next')));
}
function modal(title, content, { small = false } = {}) {
  const ov = h('div', { class: 'ov', role: 'dialog', 'aria-modal': 'true' });
  const close = () => ov.remove();
  ov.addEventListener('click', e => { if (e.target === ov) close(); });
  document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
  ov.append(h('div', { class: 'modal' + (small ? ' sm' : '') }, title ? h('div', { class: 'row spread', style: 'margin-bottom:14px' }, h('h3', {}, title), h('button', { class: 'btn sm', onclick: close, 'aria-label': 'Close' }, '✕')) : null, content));
  document.body.append(ov);
  return { close };
}
const json = o => h('pre', { class: 'pre' }, JSON.stringify(o, null, 2));
const logo = () => h('div', { class: 'logo' }, h('div', { class: 'mark' }, ico('mail', 18)), h('span', {}, 'AEOP'));

/* ------------------------------------------------------------------ landing + auth */
function landing() {
  const wf = [['Customer complaint', 'Identify the customer, verify the order, open a ticket, draft a grounded reply and send it only after approval.', 'mail'],
    ['Invoice processing', 'Extract, validate and cross-check against purchase orders. Drafts only: nothing is ever paid automatically.', 'file'],
    ['Meeting scheduling', 'Parse requests, check the calendar, propose verified slots and book after confirmation and approval.', 'cal'],
    ['Sales leads', 'Capture lead details, avoid duplicate CRM records, create follow-ups and prepare replies.', 'users']];
  return h('div', { class: 'land' },
    h('div', { class: 'lnav' }, logo(), h('nav', {}, link('#/login', 'Features'), link('#/login', 'Workflows'), link('#/login', 'Security')),
      h('div', { class: 'row' }, h('a', { class: 'btn', href: '#/login' }, 'Sign In'), S.cfg.signup_enabled ? h('a', { class: 'btn primary', href: '#/signup' }, 'Sign Up') : null)),
    h('div', { class: 'hero' }, h('div', {},
      h('div', { class: 'kick' }, 'Ingest', h('b', {}, '•'), 'Decide', h('b', {}, '•'), 'Approve'),
      h('h1', {}, 'Turn Every Inbox Into ', h('span', { class: 'grad' }, 'Controlled Workflows')),
      h('p', {}, 'A multi-agent email operations platform: specialised AI agents propose, a deterministic policy layer decides, humans approve, and every action is audited.'),
      h('div', { class: 'row', style: 'margin-top:24px' }, h('a', { class: 'btn primary', href: S.cfg.signup_enabled ? '#/signup' : '#/login' }, 'Get started'), h('a', { class: 'btn', href: '#/login' }, 'Sign in'))),
      h('div', { style: 'display:grid;place-items:center' }, cubes(340))),
    h('div', { class: 'facts' }, [['4', 'end-to-end workflows'], ['7', 'agents & policy layer'], ['26', 'permission-checked tools'], ['100%', 'actions audited']].map(([a, b]) => h('div', {}, h('b', {}, a), h('span', { class: 'muted' }, b)))),
    h('h2', { style: 'margin:30px 0 6px' }, 'Workflows'), h('p', { class: 'muted', style: 'margin:0 0 16px' }, 'Predictable business processes run as deterministic workflows; LLMs only interpret and draft.'),
    h('div', { class: 'grid g4' }, wf.map(([t, d, i]) => h('div', { class: 'card wfc' }, h('div', { class: 'art' }, ico(i, 44)), h('div', { class: 'bd' }, h('h3', {}, t), h('p', { class: 'muted small', style: 'margin:0' }, d))))),
    h('div', { class: 'footer' }, logo(), h('span', {}, 'Agents propose · Policy decides · Humans approve · Everything is audited')));
}
function authView(mode) {
  const signup = mode === 'signup' && S.cfg.signup_enabled;
  const err = h('div', { class: 'banner bad', style: 'display:none' });
  const inp = (n, ph, t = 'text') => h('input', { name: n, type: t, placeholder: ph, autocomplete: t === 'password' ? (signup ? 'new-password' : 'current-password') : n === 'email' ? 'email' : 'off', required: true });
  const org = inp('org_name', 'Acme Inc.'), name = inp('name', 'Your name'), email = inp('email', 'you@company.com', 'email'), pw = inp('password', signup ? 'At least 10 characters, letters + digits' : 'Your password', 'password');
  const form = h('form', { class: 'col', onsubmit: async e => {
    e.preventDefault(); err.style.display = 'none';
    try {
      S.user = await api(signup ? '/auth/register' : '/auth/login', { method: 'POST', body: signup ? { org_name: org.value, name: name.value, email: email.value, password: pw.value } : { email: email.value, password: pw.value } });
      location.hash = '#/overview';
    } catch (x) { err.textContent = x.message; err.style.display = 'block'; }
  } }, signup ? [field('Organization', org), field('Full name', name)] : null, field('Email address', email), field('Password', pw), err,
  h('button', { class: 'btn primary', type: 'submit', style: 'justify-content:center;padding:11px' }, signup ? 'Create account' : 'Sign in'),
  h('div', { class: 'muted small', style: 'text-align:center' }, signup ? 'Already have an account? ' : S.cfg.signup_enabled ? 'No account? ' : 'Accounts are created by your administrator.', signup ? link('#/login', 'Sign in') : S.cfg.signup_enabled ? link('#/signup', 'Sign up') : null));
  return h('div', { class: 'authwrap' }, h('div', { class: 'card auth' },
    h('div', { class: 'brand' }, cubes(200), logo(), h('div', { class: 'muted' }, 'Controlled automation for business email')),
    h('div', { class: 'form' }, h('h2', {}, signup ? 'Create your account' : 'Welcome back'), h('p', { class: 'muted', style: 'margin:6px 0 20px' }, signup ? 'You become the administrator of a new, isolated organization.' : 'Sign in to continue to your dashboard.'), form,
      h('div', { style: 'margin-top:14px' }, link('#/', '← Back to home')))));
}

/* ------------------------------------------------------------------ shell */
const NAV = [['overview', 'Overview', 'home', null], ['inbox', 'Inbox', 'inbox', 'email.read'], ['runs', 'Workflow runs', 'flow', 'email.read'], ['approvals', 'Approval center', 'check', 'email.read'],
  ['records', 'Business records', 'grid', 'any'], ['agents', 'Agents', 'bot', 'email.read'], ['tools', 'Tools & permissions', 'tool', 'audit.read'], ['integrations', 'Integrations', 'plug', 'integration.manage'],
  ['jobs', 'Failed jobs', 'alert', 'job.retry'], ['audit', 'Audit logs', 'shield', 'audit.read'], ['analytics', 'Analytics', 'chart', null], ['users', 'Users & roles', 'users', 'user.manage'], ['settings', 'Settings', 'gear', 'email.read']];
const allowedNav = ([, , , p]) => !p || (p === 'any' ? ['ticket.read', 'crm.read', 'invoice.read', 'calendar.read', 'customer.read'].some(can) : can(p));
const TITLES = Object.fromEntries(NAV.map(n => [n[0], n[1]]));
function shell(page) {
  const side = h('aside', { class: 'side' }, h('div', { style: 'padding:6px 8px 16px' }, logo()),
    h('nav', { class: 'nav' }, NAV.filter(allowedNav).map(([k, t, i]) => h('a', { href: '#/' + k, class: (page === k || (page === 'email' && k === 'inbox') || (page === 'run' && k === 'runs')) ? 'on' : '' }, ico(i, 18), t, k === 'approvals' && S.pending ? h('span', { class: 'cnt' }, S.pending) : null))),
    h('div', { style: 'flex:1' }), h('a', { class: 'btn', href: '#', onclick: e => { e.preventDefault(); confirmSignOut(); } }, ico('logout', 16), 'Sign out'));
  const bell = h('button', { class: 'bell', 'aria-label': 'Notifications', onclick: guard(async e => { e.stopPropagation(); S.popOpen = !S.popOpen; if (S.popOpen && S.notif.unread) { await api('/notifications/read', { method: 'POST' }); S.notif.unread = 0; } route(true); }) }, ico('bell', 18), S.notif.unread ? h('i', {}, S.notif.unread) : null);
  const pop = S.popOpen ? h('div', { class: 'pop' }, S.notif.items.length ? S.notif.items.slice(0, 12).map(n => h('div', { class: 'it' }, h('div', { class: 'row spread' }, h('b', {}, n.title), badge(n.level, { info: 'cy', warning: 'warn', critical: 'bad' }[n.level])), h('div', { class: 'muted small' }, n.body), h('div', { class: 'muted small' }, fmt(n.created_at)))) : h('div', { class: 'empty' }, 'No notifications')) : null;
  const top = h('div', { class: 'top' }, h('div', {}, h('h2', {}, TITLES[page] || (page === 'email' ? 'Email' : page === 'run' ? 'Workflow run' : '')), h('div', { class: 'muted small' }, S.user.org.name + ' · engine: ' + S.user.llm_engine)),
    h('div', { class: 'row', style: 'position:relative' }, bell, pop, h('div', { class: 'userchip', onclick: profile }, h('div', { class: 'avatar' }, S.user.name[0].toUpperCase()), h('div', {}, h('div', { style: 'font-weight:600;line-height:1.1' }, S.user.name), h('div', { class: 'muted small' }, S.user.role.replace('_', ' '))))));
  return h('div', { class: 'shell' }, side, h('main', { class: 'main' }, top, h('div', { id: 'view' })));
}
function confirmSignOut() {
  const m = modal(null, h('div', { class: 'col', style: 'align-items:center' }, h('div', { style: 'color:var(--acc2)' }, ico('logout', 44)), h('h3', {}, 'Are you sure you want to sign out?'), h('div', { class: 'muted small' }, 'You will be redirected to the homepage.'),
    h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: guard(async () => { await api('/auth/logout', { method: 'POST' }); S.user = null; m.close(); location.hash = '#/'; }) }, 'Sign out'), h('button', { class: 'btn', onclick: () => m.close() }, 'Cancel'))), { small: true });
}
function profile() {
  modal('Profile', h('div', { class: 'col' }, h('div', {}, h('b', {}, S.user.name), h('div', { class: 'muted' }, S.user.email)), h('div', {}, badge(S.user.role, 'acc'), ' ', h('span', { class: 'muted' }, S.user.org.name)),
    h('div', { class: 'muted small' }, 'Permissions (enforced by the server, not by this UI):'), h('div', {}, S.user.permissions.map(p => h('span', { class: 'chip' }, p)))));
}

/* ------------------------------------------------------------------ pages */
async function pOverview() {
  const a = await api('/analytics');
  S.pending = a.pending_approvals;
  const stat = (i, v, l) => h('div', { class: 'card stat' }, h('div', { class: 'ic' }, ico(i, 20)), h('div', {}, h('b', {}, v), h('span', {}, l)));
  const out = [];
  if (!a.emails_total) out.push(h('div', { class: 'banner info', style: 'margin-bottom:16px' }, h('b', {}, 'No emails yet. '), 'Load synthetic demo data, add a test email, or connect a mailbox to see the workflows run.'));
  out.push(h('div', { class: 'grid g6', style: 'margin-bottom:16px' }, stat('mail', a.emails_processed, 'Emails processed'), stat('check', a.pending_approvals, 'Pending approvals'), stat('flow', a.active_workflows, 'Active workflows'),
    stat('alert', a.needs_review, 'Needs review'), stat('shield', a.failed_jobs, 'Failed jobs'), stat('clock', a.avg_latency_s == null ? '—' : a.avg_latency_s + 's', 'Avg completion latency')));
  const runs = a.recent_runs.length ? a.recent_runs.map(r => h('div', { class: 'row spread', style: 'padding:9px 0;border-bottom:1px solid var(--line)' }, h('div', { style: 'min-width:0' }, link('#/run/' + r.id, r.subject || '(no subject)'), h('div', { class: 'muted small' }, r.workflow + ' · ' + (r.summary || '').slice(0, 90))), badge(r.state))) : [empty('No workflow runs yet')];
  const qa = h('div', { class: 'col' },
    can('user.manage') && S.user.demo_enabled ? h('button', { class: 'btn', onclick: guard(async () => { const r = await api('/demo/load', { method: 'POST' }); toast(r.status === 'loaded' ? 'Demo data loaded: emails are being processed' : 'Demo data already loaded', 'ok'); route(); }) }, ico('plus', 16), 'Load demo data (synthetic)') : null,
    can('workflow.run') ? h('button', { class: 'btn', onclick: addEmailModal }, ico('mail', 16), 'Add test email') : null,
    can('integration.manage') ? h('a', { class: 'btn', href: '#/integrations' }, ico('plug', 16), 'Connect mailbox') : null, h('a', { class: 'btn', href: '#/approvals' }, ico('check', 16), 'Review approvals'));
  const integ = a.integrations.length ? a.integrations.map(i => h('div', { class: 'row spread', style: 'padding:6px 0' }, h('span', {}, i.provider + ' · ' + i.address), badge(i.status))) : [h('div', { class: 'muted' }, 'No mailbox connected')];
  const act = (a.recent_audit || []).map(x => h('div', { style: 'padding:6px 0;border-bottom:1px solid var(--line)' }, h('div', {}, x.action), h('div', { class: 'muted small' }, x.actor + ' · ' + fmt(x.ts))));
  out.push(h('div', { class: 'grid split' }, card('Recent workflow runs', ...runs, h('div', { style: 'margin-top:10px' }, link('#/runs', 'View all runs →'))), h('div', { class: 'col' }, card('Quick actions', qa), card('Integration health', ...integ))));
  if (act.length) out.push(h('div', { style: 'margin-top:16px' }, card('Latest activity', ...act, h('div', { style: 'margin-top:8px' }, link('#/audit', 'Open audit log →')))));
  return h('div', {}, out);
}
function nextWeekday() { const d = new Date(); d.setDate(d.getDate() + 3); while ([0, 6].includes(d.getDay())) d.setDate(d.getDate() + 1); return d.toISOString().slice(0, 10); }
function addEmailModal() {
  const P = {
    Complaint: { sender: 'customer@example.com', name: 'Jamie Customer', subject: 'My order ORD-20001 is late', body: 'Hi, my order ORD-20001 has not arrived and I am disappointed. Please help urgently.', att: null },
    Invoice: { sender: 'billing@vendor.example', name: 'Vendor Billing', subject: 'Invoice INV-5001 attached', body: 'Please find our invoice attached.', att: ['INV-5001.txt', 'INVOICE\nVendor: Vendor Co Ltd\nInvoice Number: INV-5001\nInvoice Date: 2026-10-20\nDue Date: 2026-11-19\nCurrency: USD\nSubtotal: 400.00\nTax: 40.00\nTotal: 440.00\n'] },
    Meeting: { sender: 'partner@example.com', name: 'Pat Partner', subject: 'Can we meet?', body: `Hi, could we have a call? How about ${nextWeekday()} at 14:00 UTC for 30 minutes?`, att: null },
    Sales: { sender: 'buyer@acme.example', name: 'Riley Buyer', subject: 'Pricing request', body: 'We are interested in your enterprise plan for 40 seats.\nCompany: Acme Corp\nOur budget is $15,000 and our timeline is within 3 months.', att: null } };
  const f = { sender: h('input', { type: 'email', value: '' }), name: h('input', {}), subject: h('input', {}), body: h('textarea', {}), an: h('input', { placeholder: 'optional, e.g. INV-1.txt' }), at: h('textarea', { placeholder: 'attachment text (txt/csv only)' }) };
  const fill = k => { const p = P[k]; f.sender.value = p.sender; f.name.value = p.name; f.subject.value = p.subject; f.body.value = p.body; f.an.value = p.att ? p.att[0] : ''; f.at.value = p.att ? p.att[1] : ''; };
  fill('Complaint');
  const m = modal('Add test email (goes through the real pipeline)', h('div', { class: 'col' }, h('div', { class: 'row wrap' }, Object.keys(P).map(k => h('button', { class: 'btn sm', onclick: () => fill(k) }, k))),
    field('From', f.sender), field('Sender name', f.name), field('Subject', f.subject), field('Body', f.body), field('Attachment name', f.an), field('Attachment text', f.at),
    h('button', { class: 'btn primary', onclick: guard(async () => { await api('/emails/manual', { method: 'POST', body: { sender: f.sender.value, sender_name: f.name.value, subject: f.subject.value, body: f.body.value, attachments: f.an.value ? [{ filename: f.an.value, text: f.at.value }] : [] } }); m.close(); toast('Email ingested and queued', 'ok'); route(); }) }, 'Ingest email')));
}
async function pInbox() {
  const q = Q.inbox, p = new URLSearchParams({ page: q.page, page_size: 15 });
  for (const k of ['q', 'category', 'status', 'priority']) if (q[k]) p.set(k, q[k]);
  const res = await api('/emails?' + p);
  const sel = (key, opts) => h('select', { value: q[key], onchange: e => { q[key] = e.target.value; q.page = 1; route(); } }, h('option', { value: '' }, 'All ' + key), opts.map(o => h('option', { value: o }, o.replace(/_/g, ' '))));
  const cats = ['customer_support', 'complaint', 'sales_lead', 'invoice', 'payment_reminder', 'meeting_request', 'security_alert', 'newsletter', 'spam_suspicious', 'unknown'];
  const sts = ['received', 'classified', 'processing', 'awaiting_approval', 'awaiting_response', 'completed', 'needs_review', 'failed', 'rejected', 'ignored'];
  const search = h('input', { placeholder: 'Search subject or sender…', value: q.q, onkeydown: e => { if (e.key === 'Enter') { q.q = e.target.value; q.page = 1; route(); } } });
  return card(null, h('div', { class: 'row wrap', style: 'margin-bottom:14px' }, h('div', { class: 'grow', style: 'min-width:220px' }, search), sel('category', cats), sel('status', sts), sel('priority', ['P1', 'P2', 'P3', 'P4']),
    can('workflow.run') ? h('button', { class: 'btn primary', onclick: addEmailModal }, ico('plus', 16), 'Add test email') : null),
  res.items.length ? table(['Received', 'Sender', 'Subject', 'Category', 'Priority', 'Status', 'Conf.'], res.items.map(e => ({ id: e.id, cells: [fmt(e.received_at), e.sender_name ? `${e.sender_name} <${e.sender}>` : e.sender, h('div', {}, h('b', {}, e.subject || '(no subject)'), h('div', { class: 'muted small' }, e.snippet)),
    e.category ? badge(e.category, 'acc') : '—', prio(e.priority), badge(e.status), e.confidence != null ? Math.round(e.confidence * 100) + '%' : '—'] })), r => location.hash = '#/email/' + r.id) : empty('No emails match', 'Load demo data from the Overview or add a test email.'),
  pager(res, pg => { q.page = pg; route(); }));
}
async function pEmail(id) {
  const d = await api('/emails/' + id), e = d.email, c = d.classification;
  const left = h('div', { class: 'col' },
    card(null, h('div', { class: 'row spread' }, h('h3', {}, e.subject || '(no subject)'), badge(e.status)), h('div', { class: 'muted small', style: 'margin-bottom:10px' }, `From ${e.sender_name || ''} <${e.sender}> · ${fmt(e.received_at)} · thread ${e.thread_id.slice(0, 12)}`),
      e.injection_flags.length ? h('div', { class: 'banner', style: 'margin-bottom:10px' }, 'Possible prompt-injection content detected. Automation is disabled for this email; its text is treated as untrusted data.') : null, h('div', { class: 'pre' }, e.body_text || '(empty)')),
    d.attachments.length ? card('Attachments', ...d.attachments.map(a => h('div', { style: 'padding:6px 0' }, h('div', { class: 'row spread' }, h('span', {}, ico('file', 14), ' ', a.filename, ' ', h('span', { class: 'muted small' }, `${a.size} B`)), badge(a.safety_status, { ok: 'ok', blocked: 'bad' }[a.safety_status] || 'warn')), a.safety_reason ? h('div', { class: 'muted small' }, a.safety_reason) : null,
      a.text_content ? h('details', {}, h('summary', {}, 'Extracted text'), h('div', { class: 'pre' }, a.text_content.slice(0, 3000))) : null))) : null);
  const ents = c ? Object.entries(c.entities || {}).filter(([, v]) => v && v.length).map(([k, v]) => h('div', {}, h('span', { class: 'muted small' }, k.replace(/_/g, ' ') + ': '), v.map(x => h('span', { class: 'chip' }, x)))) : [];
  const runSel = h('select', {}, ['reclassify', 'complaint', 'invoice', 'meeting', 'sales_lead'].map(w => h('option', { value: w }, w === 'reclassify' ? 'Re-classify & auto-route' : 'Force workflow: ' + w)));
  const right = h('div', { class: 'col' },
    card('Classification', c ? h('div', { class: 'col' }, h('div', { class: 'row wrap' }, badge(c.category, 'acc'), prio(c.priority), badge(c.urgency, 'cy'), badge(c.sentiment), badge(c.language)),
      h('div', {}, h('div', { class: 'row spread small' }, h('span', { class: 'muted' }, 'Confidence'), h('b', {}, Math.round(c.confidence * 100) + '%')), h('div', { class: 'bar' }, h('i', { style: `width:${Math.round(c.confidence * 100)}%` }))),
      h('div', {}, h('span', { class: 'muted small' }, 'Workflow: '), badge(c.recommended_workflow, 'cy')), h('div', { class: 'small' }, h('span', { class: 'muted' }, 'Intent: '), c.intent), h('div', { class: 'small' }, h('span', { class: 'muted' }, 'Evidence: '), c.evidence),
      c.missing_info.length ? h('div', { class: 'small' }, h('span', { class: 'muted' }, 'Missing: '), c.missing_info.join(', ')) : null, ...ents, h('div', { class: 'muted small' }, `engine ${c.engine} · prompt ${c.prompt_version}`)) : h('div', { class: 'muted' }, 'Not classified yet')),
    can('workflow.run') ? card('Actions', h('div', { class: 'col' }, runSel, h('button', { class: 'btn primary', onclick: guard(async () => { await api(`/emails/${id}/run`, { method: 'POST', body: { workflow: runSel.value } }); toast('Queued', 'ok'); setTimeout(() => route(), 800); }) }, ico('play', 16), 'Run'), h('div', { class: 'muted small' }, 'Human overrides are recorded in the audit log.'))) : null,
    card('Workflow runs', d.runs.length ? d.runs.map(r => h('div', { class: 'row spread', style: 'padding:5px 0' }, link('#/run/' + r.id, r.workflow), badge(r.state))) : h('div', { class: 'muted' }, 'None yet'),
      ...d.approvals.map(a => h('div', { class: 'row spread', style: 'padding:5px 0' }, h('span', { class: 'small' }, a.action_type.replace(/_/g, ' ')), badge(a.status)))),
    d.tickets.length || d.invoices.length ? card('Related records', d.tickets.map(t => h('div', {}, `Ticket ${t.ref} · `, prio(t.priority), ' ', badge(t.status))), d.invoices.map(i => h('div', {}, `Invoice ${i.invoice_number || '?'} · ${i.currency} ${money(i.total_cents)} · `, badge(i.status, { draft_created: 'ok', needs_review: 'warn' }[i.status] || 'acc')))) : null);
  return h('div', {}, h('div', { style: 'margin-bottom:12px' }, link('#/inbox', '← Back to inbox')), h('div', { class: 'grid split' }, left, right));
}
async function pRuns() {
  const q = Q.runs, p = new URLSearchParams({ page: q.page, page_size: 15 });
  if (q.state) p.set('state', q.state); if (q.workflow) p.set('workflow', q.workflow);
  const res = await api('/runs?' + p);
  const sel = (k, opts) => h('select', { value: q[k], onchange: e => { q[k] = e.target.value; q.page = 1; route(); } }, h('option', { value: '' }, 'All ' + k), opts.map(o => h('option', { value: o }, o.replace(/_/g, ' '))));
  return card(null, h('div', { class: 'row wrap', style: 'margin-bottom:14px' }, sel('state', ['processing', 'awaiting_approval', 'awaiting_response', 'executing', 'completed', 'needs_review', 'rejected', 'failed', 'retry_scheduled', 'cancelled']), sel('workflow', ['complaint', 'invoice', 'meeting', 'sales_lead', 'manual_review', 'none'])),
    res.items.length ? table(['Started', 'Email', 'Workflow', 'State', 'Steps/Tools', 'Tokens', 'Summary'], res.items.map(r => ({ id: r.id, cells: [fmt(r.started_at), r.subject, badge(r.workflow, 'cy'), badge(r.state), `${r.step_count}/${r.tool_calls}`, r.tokens_in + r.tokens_out || '—', h('span', { class: 'small muted' }, (r.summary || r.error || '').slice(0, 110))] })), r => location.hash = '#/run/' + r.id) : empty('No workflow runs'),
    pager(res, pg => { q.page = pg; route(); }));
}
async function pRun(id) {
  const d = await api('/runs/' + id), r = d.run, ctx = r.context || {};
  const steps = d.steps.map(s => h('div', { class: 'st ' + (s.kind === 'tool' ? 'tool ' : '') + (s.status === 'error' ? 'err' : '') },
    h('div', { class: 'row wrap' }, h('b', {}, s.name), badge(s.kind, s.kind === 'tool' ? 'cy' : 'acc'), h('span', { class: 'muted small' }, s.agent), badge(s.status, { ok: 'ok', error: 'bad', review: 'warn', pending_approval: 'warn' }[s.status] || 'x'), h('span', { class: 'muted small' }, fmt(s.started_at))),
    s.error ? h('div', { class: 'small', style: 'color:#ff9a9a' }, s.error) : null,
    Object.keys(s.input || {}).length || Object.keys(s.output || {}).length ? h('details', {}, h('summary', {}, 'Details'), json({ input: s.input, output: s.output })) : null));
  const slots = r.state === 'awaiting_response' && ctx.proposed_slots ? card('Recipient confirmation', h('div', { class: 'muted small', style: 'margin-bottom:8px' }, 'The proposal was sent. When the recipient confirms a slot, record it here; availability is re-checked and booking requires approval.'),
    ...ctx.proposed_slots.map((s, i) => h('div', { class: 'row spread', style: 'padding:5px 0' }, h('span', {}, `Option ${i + 1}: ${new Date(s.start).toLocaleString()} – ${new Date(s.end).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`), can('calendar.write') ? h('button', { class: 'btn sm primary', onclick: guard(async () => { const x = await api(`/runs/${id}/confirm-slot`, { method: 'POST', body: { slot_index: i } }); toast('Slot recorded → ' + x.state, 'ok'); route(); }) }, 'Recipient confirmed') : null))) : null;
  const terminal = ['completed', 'rejected', 'failed', 'cancelled', 'needs_review'].includes(r.state);
  return h('div', {}, h('div', { style: 'margin-bottom:12px' }, link('#/runs', '← Back to runs')), h('div', { class: 'grid split' },
    h('div', { class: 'col' }, card(null, h('div', { class: 'row spread' }, h('h3', {}, d.subject), badge(r.state)), h('div', { class: 'muted small' }, `workflow ${r.workflow} · correlation ${r.correlation_id} · ${r.step_count} rows · ${r.tool_calls} tool calls · ${r.model_calls} model calls`),
      r.summary ? h('div', { style: 'margin:10px 0' }, r.summary) : null, r.error ? h('div', { class: 'banner bad' }, r.error) : null, h('div', { class: 'row' }, link('#/email/' + r.email_id, 'Open email'), can('audit.read') ? link('#/audit?run_id=' + r.id, 'Audit trail') : null)),
      card('Execution timeline', h('div', { class: 'tl' }, steps.length ? steps : h('div', { class: 'muted' }, 'No steps yet')))),
    h('div', { class: 'col' }, slots, card('Approvals', d.approvals.length ? d.approvals.map(a => h('div', { style: 'padding:6px 0' }, h('div', { class: 'row spread' }, h('b', {}, a.action_type.replace(/_/g, ' ')), badge(a.status)), h('div', { class: 'muted small' }, a.reason))) : h('div', { class: 'muted' }, 'No approvals for this run'),
      d.approvals.some(a => a.status === 'pending') ? h('div', { style: 'margin-top:8px' }, link('#/approvals', 'Go to Approval Center →')) : null),
    can('workflow.run') && !terminal || (can('workflow.run') && r.state === 'needs_review') ? card('Controls', h('button', { class: 'btn bad', onclick: guard(async () => { await api(`/runs/${id}/cancel`, { method: 'POST' }); toast('Run cancelled', 'ok'); route(); }) }, 'Cancel run')) : null)));
}
function payloadView(a) {
  const p = a.payload || {};
  if (a.action_type === 'send_email') return h('div', { class: 'col' }, h('div', { class: 'small' }, h('span', { class: 'muted' }, 'To: '), p.to), h('div', { class: 'small' }, h('span', { class: 'muted' }, 'Subject: '), p.subject), h('div', { class: 'pre' }, p.body));
  if (a.action_type === 'create_calendar_event') return h('div', { class: 'small' }, `${p.title} · ${new Date(p.start).toLocaleString()} – ${new Date(p.end).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · attendees: ${(p.attendees || []).join(', ')}`);
  return json(p);
}
async function pApprovals() {
  const q = Q.approvals, res = await api(`/approvals?status=${q.tab === 'pending' ? 'pending' : 'all'}&page=${q.page}&page_size=10`);
  S.pending = q.tab === 'pending' ? res.total : S.pending;
  const tabs = h('div', { class: 'tabs' }, ['pending', 'history'].map(t => h('button', { class: q.tab === t ? 'on' : '', onclick: () => { q.tab = t; q.page = 1; route(); } }, t === 'pending' ? 'Pending' : 'All / history')));
  const items = res.items.map(a => {
    const reason = h('input', { placeholder: 'Reason (required for rejection)' });
    const decide = guard(async d => { if (d === 'reject' && !reason.value.trim()) { toast('Please give a rejection reason', 'err'); return; } await api(`/approvals/${a.id}/decision`, { method: 'POST', body: { decision: d, reason: reason.value } }); toast(d === 'approve' ? 'Approved: execution queued' : 'Rejected', 'ok'); route(); });
    return card(null, h('div', { class: 'row spread wrap' }, h('div', { class: 'row wrap' }, h('h3', { style: 'margin:0' }, a.action_type.replace(/_/g, ' ')), risk(a.risk), badge(a.status)), h('span', { class: 'muted small' }, `requested by ${a.requested_by} · ${fmt(a.created_at)}${a.expires_at ? ' · expires ' + fmt(a.expires_at) : ''}`)),
      h('div', { class: 'small', style: 'margin:6px 0' }, h('span', { class: 'muted' }, 'Source email: '), a.email_id ? link('#/email/' + a.email_id, a.subject || 'open') : '—', h('span', { class: 'muted' }, ' · requires permission '), a.required_permission),
      a.reason ? h('div', { class: 'small muted', style: 'margin-bottom:8px' }, 'Why: ' + a.reason) : null, payloadView(a), h('div', { class: 'muted small mono', style: 'margin-top:6px' }, 'payload hash ' + a.payload_hash.slice(0, 16) + '… (approval is bound to exactly this payload)'),
      a.status === 'pending' ? (a.can_decide ? h('div', { class: 'row wrap', style: 'margin-top:10px' }, h('div', { class: 'grow' }, reason), h('button', { class: 'btn ok', onclick: () => decide('approve') }, 'Approve'), h('button', { class: 'btn bad', onclick: () => decide('reject') }, 'Reject')) : h('div', { class: 'banner info', style: 'margin-top:10px' }, 'You do not have the permissions required to decide this approval.')) : h('div', { class: 'small muted', style: 'margin-top:8px' }, `${a.status} by ${a.decided_by || '—'} ${a.decision_reason ? '· ' + a.decision_reason : ''} · result: ${JSON.stringify(a.result || {})}`));
  });
  return h('div', {}, tabs, h('div', { class: 'col' }, items.length ? items : empty(q.tab === 'pending' ? 'Nothing waiting for approval' : 'No approvals yet')), pager(res, pg => { q.page = pg; route(); }));
}
async function pRecords() {
  const q = Q.records, kinds = [['tickets', 'Tickets', 'ticket.read'], ['crm', 'CRM', 'crm.read'], ['invoices', 'Invoices', 'invoice.read'], ['events', 'Calendar', 'calendar.read'], ['customers', 'Customers', 'customer.read']].filter(k => can(k[2]));
  if (!kinds.find(k => k[0] === q.kind)) q.kind = kinds[0][0];
  const res = await api(`/records/${q.kind}?page=${q.page}&page_size=15`);
  const cols = { tickets: [['Ref', t => t.ref], ['Subject', t => t.subject], ['Priority', t => prio(t.priority)], ['Team', t => t.team], ['Status', t => badge(t.status)], ['SLA due', t => fmt(t.sla_due_at)], ['Esc.', t => 'L' + t.escalation_level]],
    crm: [['Type', t => badge(t.kind, 'acc')], ['Name', t => t.name], ['Email', t => t.email], ['Company', t => t.company], ['Status', t => badge(t.status)], ['Data', t => h('span', { class: 'small muted' }, JSON.stringify(t.data || {}).slice(0, 100))]],
    invoices: [['Vendor', t => t.vendor], ['Number', t => t.invoice_number], ['Total', t => `${t.currency} ${money(t.total_cents)}`], ['PO', t => t.po_number || '—'], ['Status', t => badge(t.status, { draft_created: 'ok', needs_review: 'warn' }[t.status] || 'acc')], ['Validation', t => h('details', {}, h('summary', {}, ((t.validation || {}).issues || []).length + ' finding(s)'), h('div', { class: 'small' }, ((t.validation || {}).issues || []).map(i => h('div', {}, `[${i.severity}] ${i.msg}`)))) ], ['Accounting', t => t.accounting_ref ? h('span', { class: 'mono' }, t.accounting_ref) : '—']],
    events: [['Title', t => t.title], ['Start', t => fmt(t.start)], ['End', t => fmt(t.end)], ['Attendees', t => (t.attendees || []).join(', ')], ['Ref', t => h('span', { class: 'mono' }, t.external_ref || '')], ['Adapter', t => t.adapter]],
    customers: [['Name', t => t.name], ['Email', t => t.email], ['Company', t => t.company], ['Tier', t => badge(t.tier, t.tier === 'vip' ? 'acc' : 'x')]] }[q.kind];
  return h('div', {}, h('div', { class: 'tabs' }, kinds.map(k => h('button', { class: q.kind === k[0] ? 'on' : '', onclick: () => { q.kind = k[0]; q.page = 1; route(); } }, k[1]))),
    card(null, res.items.length ? table(cols.map(c => c[0]), res.items.map(t => ({ cells: cols.map(c => c[1](t)) }))) : empty('No records'), pager(res, pg => { q.page = pg; route(); }),
      h('div', { class: 'muted small', style: 'margin-top:8px' }, 'Records are stored by the bundled local test adapters (not an external SaaS).')));
}
async function pAgents() {
  const list = await api('/agents');
  return h('div', { class: 'grid g2' }, list.map(a => card(null, h('div', { class: 'row spread' }, h('h3', { style: 'margin:0' }, a.name.replace(/_/g, ' ')), a.toggleable && can('agent.manage') ? h('label', { class: 'switch' }, h('input', { type: 'checkbox', checked: a.enabled, onchange: guard(async e => { await api('/agents/' + a.name, { method: 'PATCH', body: { enabled: e.target.checked } }); toast(a.name + (e.target.checked ? ' enabled' : ' disabled'), 'ok'); }) }), h('s')) : badge(a.enabled ? 'always on' : 'off', a.enabled ? 'ok' : 'x')),
    h('p', { class: 'muted', style: 'margin:8px 0' }, a.description), h('div', { class: 'small' }, h('span', { class: 'muted' }, 'version '), a.version, ' · workflows: ', a.workflows.join(', ')),
    h('div', { style: 'margin-top:8px' }, h('div', { class: 'muted small' }, 'Allow-listed tools'), a.tools.length ? a.tools.map(t => h('span', { class: 'chip' }, t)) : h('span', { class: 'muted small' }, 'none (deterministic service)')),
    h('div', { style: 'margin-top:6px' }, h('div', { class: 'muted small' }, 'Agent permissions'), a.permissions.map(t => h('span', { class: 'chip', style: 'background:rgba(109,93,252,.12);border-color:var(--line2)' }, t))))));
}
async function pTools() {
  const t = await api('/tools');
  return card(null, table(['Tool', 'Permissions', 'Risk', 'Side effect', 'Approval', 'Idempotency', 'Agents'], t.map(x => ({ cells: [h('div', {}, h('b', { class: 'mono' }, x.name), h('div', { class: 'muted small' }, x.description)), x.permissions.map(p => h('span', { class: 'chip' }, p)), risk(x.risk), x.side_effect ? badge('yes', 'warn') : badge('read-only', 'ok'), x.approval, h('span', { class: 'small' }, x.idempotency), h('span', { class: 'small muted' }, x.agents.join(', '))] }))),
    h('div', { class: 'muted small', style: 'margin-top:10px' }, 'Only registered tools can run. No shell, arbitrary code or unrestricted HTTP is exposed to any agent.'));
}
async function pIntegrations() {
  const d = await api('/integrations'), pv = Object.fromEntries(d.providers.map(p => [p.name, p]));
  const connect = guard(async p => { const r = await api(`/oauth/${p}/start`); location.href = r.url; });
  const meta = { gmail: ['Gmail', 'Gmail API via OAuth 2.0 (read + send scopes only)'], outlook: ['Microsoft Outlook', 'Microsoft Graph via OAuth 2.0 (Mail.Read, Mail.Send)'] };
  const provCards = ['gmail', 'outlook'].map(p => card(null, h('div', { class: 'row spread' }, h('h3', { style: 'margin:0' }, meta[p][0]), badge(pv[p].configured ? 'credentials set' : 'not configured', pv[p].configured ? 'ok' : 'warn')), h('p', { class: 'muted small' }, meta[p][1]),
    h('button', { class: 'btn primary', disabled: !pv[p].configured, title: pv[p].configured ? '' : 'Set the OAuth client credentials in .env first (docs/INTEGRATIONS.md)', onclick: () => connect(p) }, ico('plug', 16), 'Connect ' + meta[p][0]),
    !pv[p].configured ? h('div', { class: 'muted small', style: 'margin-top:8px' }, 'Server OAuth credentials missing. See docs/INTEGRATIONS.md.') : null));
  const accs = d.accounts.length ? d.accounts.map(a => card(null, h('div', { class: 'row spread wrap' }, h('div', {}, h('b', {}, a.label), h('div', { class: 'muted small' }, a.address + ' · scopes: ' + (a.scopes || []).map(s => s.split('/').pop()).join(', '))), badge(a.status)),
    h('div', { class: 'muted small', style: 'margin:6px 0' }, `last sync ${fmt(a.last_sync_at)}${a.last_error ? ' · error: ' + a.last_error : ''}${a.provider === 'demo' ? ' · SYNTHETIC: replies are simulated and no real email is ever sent' : ''}`),
    h('div', { class: 'row' }, a.provider !== 'demo' ? h('button', { class: 'btn sm', onclick: guard(async () => { const r = await api(`/integrations/${a.id}/sync`, { method: 'POST' }); toast(r.queued ? 'Sync queued' : r.note, 'ok'); }) }, 'Sync now') : null,
      a.status === 'reauth_required' ? h('button', { class: 'btn sm primary', onclick: () => connect(a.provider) }, 'Re-authorize') : null, a.status !== 'disconnected' ? h('button', { class: 'btn sm bad', onclick: guard(async () => { await api('/integrations/' + a.id, { method: 'DELETE' }); toast('Disconnected', 'ok'); route(); }) }, 'Disconnect') : null))) : [empty('No mailbox connected')];
  return h('div', { class: 'col' }, h('div', { class: 'grid g3' }, ...provCards, card(null, h('div', { class: 'row spread' }, h('h3', { style: 'margin:0' }, 'Demo mailbox'), badge('synthetic', 'cy')), h('p', { class: 'muted small' }, 'Local mailbox for testing. No provider is contacted.'), h('button', { class: 'btn', onclick: guard(async () => { await api('/integrations/demo', { method: 'POST' }); route(); }) }, 'Enable demo mailbox'))),
    h('h3', {}, 'Connected mailboxes'), ...accs, h('h3', {}, 'Business adapters'), card(null, table(['Integration', 'Adapter', 'Note'], d.adapters.map(a => ({ cells: [a.kind, badge(a.adapter, 'warn'), a.note] }))), h('div', { class: 'muted small', style: 'margin-top:8px' }, 'Local test adapters are not connected to any external CRM, ticketing, calendar or accounting SaaS. Implement the adapter interface in app/integrations.py for your provider.')));
}
async function pJobs() {
  const q = Q.jobs, res = await api(`/jobs?status=${q.status}&page=${q.page}&page_size=15`);
  return card(null, h('div', { class: 'row', style: 'margin-bottom:12px' }, h('select', { value: q.status, onchange: e => { q.status = e.target.value; q.page = 1; route(); } }, ['dead', 'queued', 'running', 'done', 'all'].map(s => h('option', { value: s }, s === 'dead' ? 'dead-lettered (failed)' : s)))),
    res.items.length ? table(['Created', 'Kind', 'Status', 'Attempts', 'Error class', 'Last error', ''], res.items.map(j => ({ cells: [fmt(j.created_at), j.kind, badge(j.status), `${j.attempts}/${j.max_attempts}`, j.error_class || '—', h('span', { class: 'small', style: 'color:#ff9a9a' }, (j.last_error || '').slice(0, 140)),
      j.status === 'dead' ? (j.error_class === 'ambiguous' ? h('span', { class: 'muted small' }, 'verify manually') : h('button', { class: 'btn sm', onclick: guard(async () => { await api(`/jobs/${j.id}/retry`, { method: 'POST' }); toast('Re-queued', 'ok'); route(); }) }, 'Retry')) : ''] }))) : empty('No jobs in this state'), pager(res, pg => { q.page = pg; route(); }));
}
async function pAudit(params) {
  const q = Q.audit; if (params.get('run_id')) q.run_id = params.get('run_id');
  const p = new URLSearchParams({ page: q.page, page_size: 25 }); for (const k of ['action', 'actor', 'resource_type', 'run_id']) if (q[k]) p.set(k, q[k]);
  const res = await api('/audit?' + p), vr = h('span', {});
  const f = (k, ph) => h('input', { placeholder: ph, value: q[k] || '', style: 'max-width:200px', onkeydown: e => { if (e.key === 'Enter') { q[k] = e.target.value; q.page = 1; route(); } } });
  return card(null, h('div', { class: 'row wrap', style: 'margin-bottom:12px' }, f('action', 'action contains…'), f('actor', 'actor contains…'), f('resource_type', 'resource type'), q.run_id ? h('button', { class: 'btn sm', onclick: () => { delete q.run_id; location.hash = '#/audit'; } }, 'Clear run filter ✕') : null,
    h('div', { class: 'grow' }), vr, h('button', { class: 'btn', onclick: guard(async () => { const v = await api('/audit/verify'); vr.replaceChildren(badge(v.ok ? `chain intact (${v.checked} events)` : `TAMPERING at #${v.broken_at_id}`, v.ok ? 'ok' : 'bad')); }) }, ico('shield', 16), 'Verify integrity')),
    res.items.length ? table(['Time', 'Actor', 'Action', 'Resource', 'Authz', 'Result'], res.items.map(a => ({ a, cells: [fmt(a.ts), `${a.actor_type}:${a.actor_id.slice(0, 14)}`, h('b', {}, a.action), `${a.resource_type || ''} ${(a.resource_id || '').slice(0, 10)}`, badge(a.authorization, a.authorization === 'allowed' ? 'ok' : 'bad'), badge(a.result, { ok: 'ok', error: 'bad', denied: 'bad', blocked: 'bad' }[a.result] || 'warn')] })), r => modal('Audit event #' + r.a.id, json(r.a))) : empty('No audit events'), pager(res, pg => { q.page = pg; route(); }));
}
const barRows = (obj, max) => Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => h('div', { style: 'margin:7px 0' }, h('div', { class: 'row spread small' }, h('span', {}, k.replace(/_/g, ' ')), h('b', {}, v)), h('div', { class: 'bar' }, h('i', { style: `width:${Math.max(4, v / max * 100)}%` }))));
async function pAnalytics() {
  const a = await api('/analytics'); let ev = { items: [] }; try { ev = await api('/evaluations'); } catch (e) { /* needs audit.read */ }
  const mx = o => Math.max(1, ...Object.values(o || { x: 1 }));
  const last = ev.items[0];
  const runEval = guard(async () => { await api('/evaluations/run', { method: 'POST' }); toast('Evaluation complete', 'ok'); route(); });
  return h('div', { class: 'col' }, h('div', { class: 'grid g4' }, [['Emails', a.emails_total], ['Completion rate', a.completion_rate == null ? '—' : a.completion_rate + '%'], ['Avg latency', a.avg_latency_s == null ? '—' : a.avg_latency_s + 's'], ['LLM tokens', a.tokens.in + a.tokens.out]].map(([l, v]) => card(null, h('div', { class: 'muted small' }, l), h('b', { style: 'font-size:26px' }, v)))),
    h('div', { class: 'grid g3' }, card('Runs by state', ...barRows(a.runs_by_state, mx(a.runs_by_state))), card('Runs by workflow', ...barRows(a.runs_by_workflow, mx(a.runs_by_workflow))), card('Email categories', ...barRows(a.categories, mx(a.categories)))),
    h('div', { class: 'grid g2' }, card('Approvals by status', ...barRows(a.approvals_by_status, mx(a.approvals_by_status))), card('LLM usage', h('div', {}, 'Engine: ', h('b', {}, a.engine)), h('div', { class: 'muted small' }, `tokens in ${a.tokens.in} · out ${a.tokens.out} · estimated cost ${a.tokens.estimated_cost} (set LLM_PRICE_* in .env)`))),
    card('Classification evaluation', h('div', { class: 'row spread', style: 'margin-bottom:8px' }, h('div', { class: 'muted small' }, 'Runs the current engine over a small synthetic dataset (evaluation/dataset.jsonl). A regression signal, not production accuracy.'), can('policy.manage') ? h('button', { class: 'btn', onclick: runEval }, 'Run evaluation') : null),
      last ? h('div', { class: 'col' }, h('div', { class: 'row wrap' }, badge('engine ' + last.engine, 'cy'), badge('prompt ' + last.prompt_version, 'acc'), badge('accuracy ' + last.metrics.accuracy, 'ok'), badge('injection detection ' + last.metrics.injection_detection_rate, 'ok'), badge('manual review rate ' + last.metrics.manual_review_rate, 'warn'), h('span', { class: 'muted small' }, fmt(last.created_at))), h('div', { class: 'banner info small' }, last.metrics.note)) : h('div', { class: 'muted' }, 'No evaluation runs yet')));
}
async function pUsers() {
  const d = await api('/users');
  const create = () => {
    const f = { email: h('input', { type: 'email' }), name: h('input', {}), pw: h('input', { type: 'password', placeholder: '10+ chars, letters + digits' }), role: h('select', {}, Object.keys(d.roles).map(r => h('option', { value: r }, r))) };
    const m = modal('Create user', h('div', { class: 'col' }, field('Email', f.email), field('Name', f.name), field('Role', f.role), field('Initial password', f.pw), h('button', { class: 'btn primary', onclick: guard(async () => { await api('/users', { method: 'POST', body: { email: f.email.value, name: f.name.value, role: f.role.value, password: f.pw.value } }); m.close(); toast('User created', 'ok'); route(); }) }, 'Create')));
  };
  return h('div', { class: 'col' }, card(null, h('div', { class: 'row spread', style: 'margin-bottom:12px' }, h('span', { class: 'muted' }, 'Roles are enforced on every API call.'), h('button', { class: 'btn primary', onclick: create }, ico('plus', 16), 'Create user')),
    table(['Name', 'Email', 'Role', 'Active', 'Created'], d.items.map(u => ({ cells: [u.name, u.email, h('select', { value: u.role, disabled: u.id === S.user.id, onchange: guard(async e => { await api('/users/' + u.id, { method: 'PATCH', body: { role: e.target.value } }); toast('Role updated', 'ok'); }) }, Object.keys(d.roles).map(r => h('option', { value: r }, r))),
      h('label', { class: 'switch' }, h('input', { type: 'checkbox', checked: u.is_active, disabled: u.id === S.user.id, onchange: guard(async e => { await api('/users/' + u.id, { method: 'PATCH', body: { is_active: e.target.checked } }); toast('Updated', 'ok'); }) }), h('s')), fmt(u.created_at)] })))),
    card('Role permissions', h('div', { class: 'tw' }, table(['Role', 'Permissions'], Object.entries(d.roles).map(([r, p]) => ({ cells: [badge(r, 'acc'), p.map(x => h('span', { class: 'chip' }, x))] }))))));
}
async function pSettings() {
  const d = await api('/settings'), s = d.settings, ed = d.can_edit, F = {};
  const num = (k, v, st = 'any') => F[k] = h('input', { type: 'number', step: st, value: v, disabled: !ed });
  const chk = (k, v) => F[k] = h('input', { type: 'checkbox', checked: v, disabled: !ed });
  F.org = h('input', { value: d.org_name, disabled: !ed }); F.tz = h('input', { value: s.timezone, disabled: !ed });
  num('conf', s.confidence_threshold, '0.05'); num('inv', s.invoice_approval_threshold); num('exp', s.approval_expiry_hours); num('mult', s.unusual_amount_multiplier); num('tol', s.po_total_tolerance_pct); num('poll', s.poll_interval_seconds);
  num('sh', s.working_hours.start_hour); num('eh', s.working_hours.end_hour); chk('sod', s.separation_of_duties);
  ['send_email', 'create_calendar_event', 'update_crm_sensitive'].forEach(k => chk('ar_' + k, s.approval_required[k])); ['P1', 'P2', 'P3', 'P4'].forEach(k => num('sla_' + k, s.sla_hours[k]));
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((n, i) => ({ n, i, el: h('input', { type: 'checkbox', checked: s.working_hours.days.includes(i), disabled: !ed }) }));
  const save = guard(async () => {
    await api('/settings', { method: 'PUT', body: { org_name: F.org.value, timezone: F.tz.value, confidence_threshold: +F.conf.value, invoice_approval_threshold: +F.inv.value, approval_expiry_hours: +F.exp.value, unusual_amount_multiplier: +F.mult.value, po_total_tolerance_pct: +F.tol.value, poll_interval_seconds: +F.poll.value, separation_of_duties: F.sod.checked,
      working_hours: { start_hour: +F.sh.value, end_hour: +F.eh.value, days: days.filter(x => x.el.checked).map(x => x.i) }, approval_required: Object.fromEntries(['send_email', 'create_calendar_event', 'update_crm_sensitive'].map(k => [k, F['ar_' + k].checked])), sla_hours: Object.fromEntries(['P1', 'P2', 'P3', 'P4'].map(k => [k, +F['sla_' + k].value])) } });
    toast('Settings saved', 'ok'); S.user = await api('/auth/me');
  });
  const row = (l, el, hint) => h('label', { class: 'f' }, l, el, hint ? h('span', { class: 'small' }, hint) : null);
  return h('div', { class: 'col' }, !ed ? h('div', { class: 'banner info' }, 'Read-only: you need the policy.manage permission to change settings.') : null,
    h('div', { class: 'grid g2' }, card('Organization & scheduling', h('div', { class: 'col' }, row('Organization name', F.org), row('Timezone (IANA)', F.tz, 'e.g. UTC, Asia/Karachi, America/New_York'), h('div', { class: 'row' }, row('Work start hour', F.sh), row('Work end hour', F.eh)), h('div', { class: 'row wrap' }, days.map(x => h('label', { class: 'row small' }, x.el, x.n))))),
      card('Policy', h('div', { class: 'col' }, row('Classification confidence threshold', F.conf, 'Below this, emails go to manual review'), row('Invoice approval threshold (amount)', F.inv, 'Accounting drafts above this need approval'), row('Unusual-amount multiplier', F.mult), row('PO total tolerance %', F.tol), row('Approval expiry (hours)', F.exp), row('Mailbox polling interval (s)', F.poll), h('label', { class: 'row' }, F.sod, 'Separation of duties for high-risk actions'))),
      card('Approval required for', h('div', { class: 'col' }, ['send_email', 'create_calendar_event', 'update_crm_sensitive'].map(k => h('label', { class: 'row' }, F['ar_' + k], k.replace(/_/g, ' '))), h('div', { class: 'muted small' }, 'Payments and vendor bank-detail changes are not supported by this platform and cannot be enabled.'))),
      card('SLA hours by priority', h('div', { class: 'row' }, ['P1', 'P2', 'P3', 'P4'].map(k => row(k, F['sla_' + k]))))),
    ed ? h('div', {}, h('button', { class: 'btn primary', onclick: save }, 'Save settings')) : null, h('div', { class: 'muted small' }, `AI engine: ${d.engine}${d.llm_configured ? '' : ' (no LLM key configured: deterministic heuristics are used; set GEMINI_API_KEY to enable the LLM)'}`));
}

/* ------------------------------------------------------------------ router */
const PAGES = { overview: pOverview, inbox: pInbox, email: pEmail, runs: pRuns, run: pRun, approvals: pApprovals, records: pRecords, agents: pAgents, tools: pTools, integrations: pIntegrations, jobs: pJobs, audit: pAudit, analytics: pAnalytics, users: pUsers, settings: pSettings };
async function refreshChrome() {
  try { const [n, a] = await Promise.all([api('/notifications'), api('/analytics')]); S.notif = n; S.pending = a.pending_approvals; } catch (e) { /* ignore */ }
}
async function route(silent = false) {
  clearTimeout(S.timer);
  const raw = location.hash.replace(/^#\/?/, ''), [pathq, qs] = raw.split('?'), [page, id] = (pathq || '').split('/'), params = new URLSearchParams(qs || '');
  const app = document.getElementById('app');
  if (!S.user && !['login', 'signup', ''].includes(page)) { location.hash = '#/login'; return; }
  if (!S.user) { app.replaceChildren(page === '' ? landing() : authView(page)); return; }
  if (['login', 'signup', ''].includes(page)) { location.hash = '#/overview'; return; }
  const key = PAGES[page] ? page : 'overview', my = ++S.tok;
  if (!silent) await refreshChrome();
  if (my !== S.tok) return;
  if (!allowedNav(NAV.find(n => n[0] === key) || [null, null, null, null]) && NAV.find(n => n[0] === key)) { toast('You do not have access to that page', 'err'); location.hash = '#/overview'; return; }
  let view;
  try { view = await PAGES[key](key === 'audit' ? params : id); } catch (e) { view = h('div', { class: 'banner bad' }, e.message); }
  if (my !== S.tok) return;
  const sh = shell(key), keep = document.getElementById('view');
  if (silent && keep) { keep.replaceChildren(view); document.querySelector('.side').replaceWith(sh.querySelector('.side')); document.querySelector('.top').replaceWith(sh.querySelector('.top')); }
  else { sh.querySelector('#view').append(view); app.replaceChildren(sh); }
  S.timer = setTimeout(() => { const a = document.activeElement; if (!document.querySelector('.ov') && !(a && ['INPUT', 'TEXTAREA', 'SELECT'].includes(a.tagName))) refreshChrome().then(() => route(true)); else S.timer = setTimeout(() => route(true), 6000); }, 7000);
}
window.addEventListener('hashchange', () => route());
document.addEventListener('click', () => { if (S.popOpen) { S.popOpen = false; const p = document.querySelector('.pop'); if (p) p.remove(); } });
(async () => {
  try { S.cfg = await api('/config'); } catch (e) { S.cfg = {}; }
  try { S.user = await api('/auth/me'); } catch (e) { S.user = null; }
  route();
})();
})();
