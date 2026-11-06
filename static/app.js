/* ==========================================================================
   ARBITER — document compliance review
   Vanilla-JS single-page client (no build step)
   ========================================================================== */

const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

let token = localStorage.getItem('arbiter_token') || '';
let me = null;
let apps = [];
let selectedApp = null;
let currentTab = 'cross';
let currentDocIndex = 0;
let currentFindingFilter = null;
let auditQuery = '';
let auditFilter = 'all';
let searchQuery = '';
let statusFilter = 'all';
let typeFilter = 'all';
let currentDocPage = 1;

// SVG Icons Pack
const ic = p => `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
const I = {
  dash: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  apps: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  chk: '<path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  auth: '<path d="M3 10l9-6 9 6M5 10v8M9 10v8M15 10v8M19 10v8M3 20h18"/>',
  aud: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  inf: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  arr: '<path d="M7 17L17 7M8 7h9v9"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  bell: '<path d="M6 17V11a6 6 0 0 1 12 0v6l1.5 2h-15zM10 21h4"/>',
  sea: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>'
};

const R = {
  dashboard: ['Overview', 'dash'],
  applications: ['Applications', 'apps'],
  rules: ['Rules & policies', 'chk'],
  verify: ['Document review', 'doc'],
  compliance: ['Assessment', 'chk'],
  authority: ['Source checks', 'auth'],
  audit: ['Audit trail', 'aud']
};

const SH = {
  dashboard: 'Overview',
  applications: 'Applications',
  rules: 'Rules',
  verify: 'Documents',
  compliance: 'Assessment',
  authority: 'Sources',
  audit: 'Audit trail'
};

const RT_LABELS = { licence_renewal: 'Renewal', scope_expansion: 'Scope expansion', new_registration: 'New registration' };
const rtLabel = key => RT_LABELS[key] || key;

const ST = {
  validated: ['Validated', 'ok'],
  pending: ['Verification pending', 'bl'],
  deficient: ['Deficiencies found', 'wn'],
  review: ['Needs official review', 'er']
};

const bdg = k => {
  const item = ST[k] || ['Active', 'bl'];
  return `<span class="b ${item[1]}">${item[0]}</span>`;
};

const ini = n => (n || 'AI').split(' ').slice(0, 2).map(w => w[0]).join('').toUpperCase();

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), ...(token ? { 'X-Session-Token': token } : {}) };
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    const e = await res.json().catch(() => ({ detail: 'Request failed' }));
    throw Error(e.detail || 'Request failed');
  }
  return res.json();
}

function toast(m) {
  const e = $('#ts');
  if (!e) return;
  e.innerHTML = `<div class="toast">${esc(m)}</div>`;
  clearTimeout(toast.h);
  toast.h = setTimeout(() => {
    const t = e.firstChild;
    if (t) t.classList.add('out');
    toast.h = setTimeout(() => (e.innerHTML = ''), 180);
  }, 2800);
}

function modal(title, html, saveAction) {
  const m = $('#modal');
  m.innerHTML = `<div class="modalbox"><h2>${title}</h2>${html}<div class="acts" style="justify-content:flex-end;margin-top:16px"><button class="btn o s" onclick="closeModal()">Cancel</button><button class="btn p s" id="saveModal">Confirm</button></div></div>`;
  m.classList.add('open');
  $('#saveModal').onclick = saveAction;
}

function closeModal() {
  $('#modal').classList.remove('open');
}

/* ==========================================================================
   Authentication & Initial Startup
   ========================================================================== */

async function quickLogin(email, password) {
  try {
    const res = await api('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    token = res.token;
    localStorage.setItem('arbiter_token', token);
    await start();
  } catch (err) {
    toast(err.message || 'Login failed');
  }
}

function loginView() {
  $('#app').innerHTML = `
    <div class="login-wrap">
      <div class="login-box">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
          <div class="brand pill" style="display:inline-flex;padding:6px 14px 6px 6px">
            <i>AR</i>Arbiter
          </div>
          <span class="tag">v1.0 · self-hosted</span>
        </div>
        <h1>Document compliance review</h1>
        <p>Extract fields, run your policy pack, and keep a human in the loop with an auditable trail.</p>
        <form id="loginForm">
          <div class="form-row">
            <label>Email address</label>
            <input type="email" name="email" placeholder="reviewer@arbiter.demo" required>
          </div>
          <div class="form-row">
            <label>Password</label>
            <input type="password" name="password" placeholder="••••••••••••" required>
          </div>
          <button class="btn p" style="width:100%;margin-top:12px">Sign in</button>
        </form>
        <div class="quick-creds">
          <b>One-Click Demo Logins</b>
          <div style="display:flex;flex-direction:column;gap:6px;margin-top:8px">
            <button type="button" class="btn alt s" style="text-align:left;width:100%" onclick="quickLogin('submitter@northwind.demo','Submitter@123')">Submitter — Northwind (clean case)</button>
            <button type="button" class="btn alt s" style="text-align:left;width:100%" onclick="quickLogin('submitter@cascade.demo','Submitter@123')">Submitter — Cascade (deficient case)</button>
            <button type="button" class="btn p s" style="text-align:left;width:100%" onclick="quickLogin('reviewer@arbiter.demo','Reviewer@123')">Reviewer queue</button>
            <button type="button" class="btn o s" style="text-align:left;width:100%" onclick="quickLogin('admin@arbiter.demo','Admin@123')">Administrator</button>
          </div>
        </div>
      </div>
    </div>`;

  $('#loginForm').onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await api('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.fromEntries(fd))
      });
      token = res.token;
      localStorage.setItem('arbiter_token', token);
      await start();
    } catch (err) {
      toast(err.message);
    }
  };
}

async function start() {
  try {
    const s = await api('/api/session');
    me = s.user;
  } catch (err) {
    token = '';
    localStorage.removeItem('arbiter_token');
    loginView();
    return;
  }

  try {
    await loadApps();
    renderAppShell();
    draw();
  } catch (err) {
    console.error('Error rendering app:', err);
    toast('Error loading workspace: ' + err.message);
  }
}

async function logout() {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch {}
  token = '';
  localStorage.removeItem('arbiter_token');
  loginView();
}

async function loadApps() {
  apps = await api('/api/applications');
  if (!selectedApp && apps.length > 0) {
    selectedApp = apps[0];
  } else if (selectedApp) {
    selectedApp = apps.find(a => a.id === selectedApp.id) || apps[0];
  }
}

/* ==========================================================================
   Shell Layout & Top Navigation
   ========================================================================== */

function renderAppShell() {
  const roleTitle = me.role === 'reviewer' ? 'Reviewer' : me.role === 'admin' ? 'Administrator' : 'Submitter';
  $('#app').innerHTML = `
    <header class="tb">
      <div class="pill brand"><i>AR</i>Arbiter <span style="font-size:11px;color:var(--mu);font-weight:400;margin-left:2px">| Review desk</span></div>
      <nav class="pill tn" id="nav" aria-label="Main"></nav>
      <div class="pill tr">
        <button class="ib" data-a="srch" aria-label="Search" id="i1">${ic(I.sea)}</button>
        <button class="ib dot" data-a="bell" aria-label="Alerts" id="i2">${ic(I.bell)}</button>
        <button class="ib" data-a="theme" aria-label="Switch theme" id="thm">${ic(document.documentElement.dataset.theme === 'dark' ? I.sun : I.moon)}</button>
      </div>
      <div class="pill me">
        <i>${ini(me.email)}</i>
        <span>${esc(me.email)}<small>${roleTitle}</small></span>
        <button class="btn ghost s" onclick="logout()" style="margin-left:8px">Sign out</button>
      </div>
    </header>
    <div class="shell">
      <nav class="pill rail" id="rail" aria-label="Quick links"></nav>
      <main id="main" tabindex="-1"></main>
    </div>`;
}

function ind(b, nt) {
  if (!b) return;
  const a = b.querySelector('[aria-current]');
  const i = b.querySelector('.ind');
  if (!a || !i) return;
  if (nt) i.classList.add('nt');
  i.style.width = a.offsetWidth + 'px';
  i.style.height = a.offsetHeight + 'px';
  i.style.transform = `translate(${a.offsetLeft}px,${a.offsetTop}px)`;
  if (nt) { void i.offsetWidth; i.classList.remove('nt'); }
}

let builtNav = false;
function buildNav(k) {
  const nav = $('#nav');
  const rail = $('#rail');
  if (!nav || !rail) return;
  let first = false;
  if (!builtNav) {
    builtNav = first = true;
    nav.innerHTML = '<i class="ind nt"></i>' + Object.keys(R).map(r => `<a class="nv" data-r="${r}" href="#${r}">${SH[r]}</a>`).join('');
    rail.innerHTML = '<i class="ind nt"></i>' + Object.entries(R).map(([r, v]) => `<a class="rn" data-r="${r}" href="#${r}" title="${v[0]}" aria-label="${v[0]}">${ic(I[v[1]])}</a>`).join('');
  }
  document.querySelectorAll('#nav a, #rail a').forEach(a => {
    if (a.dataset.r === k) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  ind(nav, first);
  ind(rail, first);
}

/* ==========================================================================
   Page Renderers
   ========================================================================== */

const adv = `<div class="adv">${ic(I.inf)}<div><b>Automated findings are advisory.</b> They are review prompts with page-level evidence, not proof of wrongdoing. Every decision rests with your reviewers and your own policy pack.</div></div>`;
const head = (t, s, x = '') => `<div class="top"><div><h1>${t}</h1><p class="sub">${s}</p></div><div class="acts">${x}</div></div>`;

async function renderDashboard() {
  const d = await api('/api/dashboard');
  const totalApps = apps.length;
  const defApps = apps.filter(a => (a.open_findings_count || 0) > 0).length;
  const readyApps = apps.filter(a => a.status === 'ready_for_review').length;
  const inWorkflow = apps.filter(a => ['submitted', 'awaiting_rectification', 'rectification_received'].includes(a.status)).length;
  const reviewedApps = apps.filter(a => a.status === 'reviewed').length;
  const notChecked = apps.filter(a => !a.checks_run).length;

  return head('Overview', 'Where applications stand today, and what needs a reviewer\'s attention first.',
    `<button class="btn p" data-a="kpi" data-v="review">${ic(I.plus)}Open review queue</button><button class="btn o" data-a="tn" data-export="dashboard">Export report</button>`
  ) + adv + `
  <div class="grid kpis">
    <button class="kpi hero" data-a="kpi" data-v="all">
      <div class="kh"><span>Total applications</span><i class="ar">${ic(I.arr)}</i></div>
      <strong>${d.applications || totalApps}</strong>
      <em>in the current review cycle</em>
    </button>
    <button class="kpi" data-a="kpi" data-v="ready">
      <div class="kh"><span>Ready for review</span><i class="ar">${ic(I.arr)}</i></div>
      <strong>${d.ready_for_review ?? readyApps}</strong>
      <em>Ready for review</em>
      <small>${d.in_review_workflow ?? inWorkflow} in review · ${d.reviewed_applications ?? reviewedApps} dispositioned</small>
    </button>
    <button class="kpi" data-a="kpi" data-v="deficient">
      <div class="kh"><span>With deficiencies</span><i class="ar">${ic(I.arr)}</i></div>
      <strong>${d.deficient_applications ?? defApps}</strong>
      <em>Cases with open findings (${d.open_findings || 0} flags total)</em>
    </button>
    <button class="kpi al" data-a="kpi" data-v="review">
      <div class="kh"><span>High-priority alerts</span><i class="ar">${ic(I.arr)}</i></div>
      <strong>${d.high_risk_findings || 0}</strong>
      <em>Open high/critical findings</em>
    </button>
  </div>
  <div class="grid two">
    <section class="card">
      <div class="ch"><h2>Review queue</h2><button class="btn s" data-a="kpi" data-v="all">View all</button></div>
      <ul class="tl">
        ${apps.map(a => {
          const openF = a.open_findings_count ?? (a.findings || []).filter(f => f.status === 'open').length;
          return `
          <li class="qi">
            <i class="av">${ini(a.organization_name)}</i>
            <div>
              <button class="lk" data-a="open" data-v="${a.id}">${esc(a.organization_name)}</button>
              <div class="sm mu">${esc(a.reference)} · ${esc(a.request_type_label || a.request_type || '—')}${a.escalated_at ? ' · <b>Escalated</b>' : ''}${a.assigned_to ? ` · ${esc(a.assigned_to)}` : ''}</div>
            </div>
            <span class="b ${openF > 0 ? 'er' : !a.checks_run ? 'mu' : 'ok'}">${openF > 0 ? openF + ' open flags' : !a.checks_run ? 'Not checked' : esc(a.status || 'No findings')}</span>
          </li>`;
        }).join('')}
      </ul>
    </section>
    <section class="dk">
      <h2>Sample scope <span class="tag">Configurable</span></h2>
      <div class="big"><span>${esc(d.ready_for_review ?? readyApps)}</span><small>ready for review</small></div>
      <p>Three sample request types and a sample policy pack ship with the demo. Every check is advisory and replaceable.</p><p class="sm">${notChecked} case(s) have not had a pre-check run; absence of findings is not a pass.</p><p class="sm">${d.escalated_applications ?? 0} escalated for official intervention · ${d.assigned_applications ?? 0} assigned to an reviewer.</p>
      <div class="pb"><i style="width:${totalApps ? Math.round((readyApps / totalApps) * 100) : 0}%"></i></div>
      <button class="btn lt" data-a="go" data-v="rules">View the policy pack</button>
    </section>
  </div>`;
}

async function renderRules() {
  const payload = await api('/api/rules');
  const rules = payload.rules || [];
  const versions = payload.versions || [];
  return head('Rules & policies', 'Versioned rule pack with source metadata. A second administrator must approve a change before it applies.',
    `${me.role === 'admin' ? '<button class="btn p" data-a="draftRulePack">Propose rule-pack change</button>' : ''}`
  ) + `<div class="adv"><div><b>${esc(payload.rule_pack.label)} · ${esc(payload.rule_pack.version)}</b><br>${esc(payload.rule_pack.notice)}<br><b>Scope:</b> ${esc((payload.rule_pack.review_cycles || []).join(', '))} · three sample request types.</div></div>
  <section class="card"><div class="ch"><h2>Rule metadata</h2><span class="tag">${rules.filter(r => r.enabled).length} executable checks enabled</span></div><div class="tw"><table><thead><tr><th>Rule</th><th>Scope / version</th><th>Applicability</th><th>Source quality / approval</th><th>Execution</th></tr></thead><tbody>${rules.map(rule => `<tr><td><b>${esc(rule.name)}</b><br><span class="tag">${esc(rule.id)}</span><p class="sm">${esc(rule.logic)}</p></td><td>${esc(rule.scope)}<br>${esc(rule.source_version || '—')} · ${esc(rule.clause || 'No clause')}</td><td>${esc((rule.review_cycles || []).join(', ') || 'Unscoped reference')}<br>${esc((rule.request_types || []).map(rtLabel).join(', ') || 'Multiple / reference')}</td><td>${esc(rule.source_quality || 'Unspecified')}<br>${esc(rule.approval_status || rule.verification_status)}</td><td><span class="b ${rule.enabled ? 'ok' : 'mu'}">${rule.enabled ? 'Enabled' : 'Reference / disabled'}</span></td></tr>`).join('')}</tbody></table></div><div class="fl"><b>Immutable rule-pack history</b>${versions.map(v => `<div class="evidence" style="margin-top:8px"><b>v${esc(v.version)} · ${esc(v.status)}</b><br>${esc(v.change_reason)} · proposed by ${esc(v.created_by)} · approved by ${esc(v.approved_by || 'pending')}<br>${esc(v.scope?.source_quality || 'unverified')} · ${esc(v.scope?.source_reference || 'No source reference supplied')} · cycles ${(v.scope?.review_cycles || []).map(esc).join(', ')} · request types ${(v.scope?.request_types || []).map(rtLabel).map(esc).join(', ')}<br>${v.published_at ? `Published ${new Date(v.published_at).toLocaleString()}` : ''}${me.role === 'admin' && v.status === 'draft' && v.created_by !== me.email ? `<button class="btn s p" data-a="publishRulePack" data-id="${v.id}">Approve & publish</button>` : ''}</div>`).join('') || '<p class="sm mu">No versions recorded.</p>'}</div></section>`;
}

function renderApplications() {
  const q = searchQuery.toLowerCase();
  const filtered = apps.filter(a => {
    const matchQ = (a.organization_name + a.reference + (a.scope_name || '')).toLowerCase().includes(q);
    const matchCat = typeFilter === 'all' || a.request_type === typeFilter;
    const openCount = a.open_findings_count || 0;
    const highRisk = a.high_risk_count || 0;
    const isReady = ['ready_for_review', 'submitted', 'reviewed'].includes(a.status);
    const matchSt = statusFilter === 'all' ||
      (statusFilter === 'validated' && a.checks_run && isReady && openCount === 0) ||
      (statusFilter === 'ready' && a.checks_run && a.status === 'ready_for_review' && openCount === 0) ||
      (statusFilter === 'deficient' && openCount > 0) ||
      (statusFilter === 'review' && (highRisk > 0 || ['submitted', 'awaiting_rectification', 'rectification_received'].includes(a.status))) ||
      (statusFilter === 'not_checked' && !a.checks_run);
    return matchQ && matchCat && matchSt;
  });

  return head('Applications', 'Search, filter and open any organizational case for compliance scrutiny.',
    `${me.role === 'submitter' ? '<button class="btn p" data-a="newCase">' + ic(I.plus) + 'New application</button>' : ''} <button class="btn o" data-a="tn" data-export="applications">Export list</button>`
  ) + `
  <section class="card">
    <div class="ch">
      <div class="tools" style="flex:1">
        <input type="search" id="q" placeholder="Search organization, application reference or course..." value="${esc(searchQuery)}">
        <select id="cat" aria-label="Request type">
          <option value="all" ${typeFilter === 'all' ? 'selected' : ''}>All request types</option>
          <option value="licence_renewal" ${typeFilter === 'licence_renewal' ? 'selected' : ''}>Renewal</option>
          <option value="scope_expansion" ${typeFilter === 'scope_expansion' ? 'selected' : ''}>Scope expansion</option>
          <option value="new_registration" ${typeFilter === 'new_registration' ? 'selected' : ''}>New registration</option>
        </select>
      </div>
    </div>
    <div class="ch" style="padding-top:0">
      <div class="chips" role="group" aria-label="Status">
        ${['all', 'validated', 'deficient', 'review', 'not_checked'].map(k => {
          const labels = { all: 'All cases', validated: 'No open findings*', deficient: 'Open findings', review: 'Needs review', not_checked: 'Not checked' };
          return `<button class="chip" aria-pressed="${statusFilter === k}" data-a="st" data-v="${k}">${labels[k]}</button>`;
        }).join('')}
      </div>
    </div>
    <div id="tbl">
      ${!filtered.length ? '<div class="empty"><b>No applications match these filters</b>Try a different search term.</div>' : `
      <div class="tw">
        <table>
          <thead><tr><th>Organization</th><th>Review cycle</th><th>Request type</th><th>Open Flags</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>
            ${filtered.map(a => {
              const openCount = a.open_findings_count ?? (a.findings || []).filter(f => f.status === 'open').length;
              const isDef = (a.open_findings_count || 0) > 0;
              const isReady = ['ready_for_review', 'submitted', 'reviewed'].includes(a.status);
              return `
              <tr>
                <td>
                  <button class="lk" data-a="open" data-v="${a.id}">${esc(a.organization_name)}</button>
                  <div class="sm mu">${esc(a.reference)} · Capacity: ${a.requested_capacity || 'N/A'}</div>
                </td>
                <td>${esc(a.review_cycle)}</td>
                <td><span class="tag">${esc(a.request_type_label || a.request_type || '—')}</span></td>
                <td>${openCount > 0 ? `<span class="b er">${openCount} open</span>` : !a.checks_run ? '<span class="b mu">Not checked</span>' : '<span class="b ok">0 open</span>'}</td>
                <td><span class="b ${isDef ? 'wn' : isReady ? 'ok' : 'mu'}">${isDef ? 'Open findings' : esc((a.status || 'draft').replace(/_/g, ' '))}</span></td>
                <td><button class="btn s alt" data-a="vf" data-v="verify" data-id="${a.id}">Inspect</button></td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>`}
    </div>
  </section>`;
}

async function renderVerify() {
  if (!selectedApp) return '<div class="empty"><b>No application selected</b>Select an application from the Applications workspace.</div>';

  const fullCase = await api(`/api/applications/${selectedApp.id}`);
  selectedApp = fullCase;
  const docs = fullCase.documents || [];
  const findings = fullCase.findings || [];
  const docChecklist = selectedApp.checklist || [];
  if (currentFindingFilter) {
    const targetFinding = findings.find(f => f.id === currentFindingFilter);
    if (targetFinding?.document_id) {
      const targetIndex = docs.findIndex(doc => doc.id === targetFinding.document_id);
      if (targetIndex >= 0) currentDocIndex = targetIndex;
    }
  }
  if (currentDocIndex >= docs.length) currentDocIndex = 0;
  const currentDoc = docs[currentDocIndex] || null;
  if (!currentFindingFilter && currentDoc) {
    const initialFinding = findings.find(f => f.document_id === currentDoc.id && f.status === 'open' && f.evidence_page);
    if (initialFinding) currentFindingFilter = initialFinding.id;
  }
  const selectedFinding = findings.find(f => f.id === currentFindingFilter);
  if (selectedFinding?.evidence_page) currentDocPage = selectedFinding.evidence_page;

  let docPageText = '';
  let docTotalPages = 1;
  if (currentDoc) {
    currentDocPage = Math.max(currentDocPage, 1);
    try {
      const pageInfo = await api(`/api/documents/${currentDoc.id}/pages/${currentDocPage}`);
      docPageText = pageInfo.text || '';
      docTotalPages = pageInfo.total_pages || 1;
      currentDocPage = pageInfo.page;
    } catch {
      docPageText = '';
    }
  }

  // Build Document Sheet HTML
  let paperContent = '<div class="empty"><b>No documents uploaded</b>Upload supporting certificates to start extraction.</div>';
  if (currentDoc) {
    const fields = currentDoc.fields || [];
    let highlightedBody = esc(docPageText);

    if (highlightedBody) {
      fields.forEach(f => {
        const val = (f.raw_value || '').trim();
        if (val.length > 2) {
          const re = new RegExp(`(${val.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&')})`, 'gi');
          highlightedBody = highlightedBody.replace(re, '<mark title="Extracted: ' + esc(f.field_name) + '">$1</mark>');
        }
      });
    } else {
      // Fallback structured field lines if raw page text is unavailable
      highlightedBody = fields.map(f => `${f.field_name.toUpperCase().padEnd(22)} : ${f.raw_value}`).join('\n');
    }

    const docFindings = findings.filter(f => f.document_id === currentDoc.id);
    const openDocFindings = docFindings.filter(f => f.status === 'open');
    const hasDiscrepancy = openDocFindings.length > 0;
    const statusLabel = !fullCase.checks_run ? 'NOT CHECKED' : hasDiscrepancy ? 'REVIEW REQUIRED' : docFindings.length ? 'REVIEWED' : 'NO FINDING RECORDED';
    const statusClass = hasDiscrepancy ? 'er' : !fullCase.checks_run ? 'mu' : 'ok';
    const digest = currentDoc.hash || '';
    const digestLabel = digest ? `SHA-256: ${digest}` : 'SHA-256 unavailable';    const docConfidence = currentDoc.confidence == null ? 'Not available' : `${Math.round(currentDoc.confidence * 100)}%`;

    paperContent = `
      <div class="record-sheet">
        <div class="record-header">
          <div class="record-emblem-wrap">
            <div class="record-emblem">AR</div>
            <div class="record-titles">
              <b>ARBITER · DOCUMENT REVIEW RECORD</b>
              <small>EXTRACTED TEXT, EVIDENCE LOCATIONS & INTEGRITY DIGEST</small>
            </div>
          </div>
          <div class="record-stamp">
            <span class="b ${statusClass}">${statusLabel}</span>
            <span class="sec-hash" title="${esc(digestLabel)}">${esc(digestLabel)}</span>
          </div>
        </div>

        <div class="record-meta-bar">
          <div class="meta-item">
            <b>Document File</b>
            <span>${esc(currentDoc.original_name)}</span>
          </div>
          <div class="meta-item">
            <b>Document type</b>
            <span>${esc(currentDoc.document_type.replace(/_/g, ' ').toUpperCase())}</span>
          </div>
          <div class="meta-item">
            <b>OCR & Engine Confidence</b>
            <span>${esc(docConfidence)} OCR/extraction confidence · ${esc(currentDoc.extraction_error ? 'manual review: ' + currentDoc.extraction_error : 'engine estimate; verify against original')}</span>
          </div>
        </div>

        <div class="record-body">${highlightedBody || 'Document content could not be displayed.'}</div>

        <div class="record-footer-bar">
          <span class="sm mu">Page ${currentDocPage} of ${docTotalPages} · Extracted by Arbiter · advisory record</span>
          <span class="page-nav"><button class="btn s alt" data-a="docPage" data-v="-1" ${currentDocPage <= 1 ? 'disabled' : ''}>Previous</button><button class="btn s alt" data-a="docPage" data-v="1" ${currentDocPage >= docTotalPages ? 'disabled' : ''}>Next</button></span>
          <div class="acts">
            <a href="/api/documents/${currentDoc.id}/download" class="btn s alt" target="_blank">Download original</a>
            ${docFindings.length ? `<button class="btn s o" onclick="inspectEvidence(${docFindings[0].id})">Launch Split-Screen Inspector</button>` : ''}
          </div>
        </div>
      </div>`;
  }

  // Cross-Check Comparison: Application Stated vs Extracted
  const fieldDict = {};
  if (currentDoc && currentDoc.fields) {
    currentDoc.fields.forEach(f => { fieldDict[f.field_name] = f.raw_value; });
  }

  const comparisons = [
    {
      param: 'Organization name',
      stated: selectedApp.organization_name,
      extracted: fieldDict['organization_name'] || '—',
      status: !fullCase.checks_run || !fieldDict['organization_name'] ? 'pending' :
        (fieldDict['organization_name'].toLowerCase().trim() === selectedApp.organization_name.toLowerCase().trim() ? 'match' : 'mismatch'),
      rule: 'NAME-MATCH-001'
    },
    {
      param: 'Scope item',
      stated: selectedApp.scope_name || 'Not specified',
      extracted: fieldDict['scope_name'] || '—',
      status: !fullCase.checks_run || !fieldDict['scope_name'] || !selectedApp.scope_name ? 'pending' :
        (fieldDict['scope_name'].toLowerCase().includes(selectedApp.scope_name.toLowerCase()) || selectedApp.scope_name.toLowerCase().includes(fieldDict['scope_name'].toLowerCase()) ? 'match' : 'mismatch'),
      rule: 'SCOPE-MATCH-001'
    },
    {
      param: 'Capacity',
      stated: selectedApp.requested_capacity != null ? selectedApp.requested_capacity : 'Not specified',
      extracted: fieldDict['capacity'] || '—',
      status: !fullCase.checks_run || !fieldDict['capacity'] || selectedApp.requested_capacity == null ? 'pending' :
        (Number(fieldDict['capacity']) === Number(selectedApp.requested_capacity) ? 'match' : 'mismatch'),
      rule: 'CAPACITY-MATCH-001'
    },
    {
      param: 'Validity / Expiry Date',
        stated: 'No expiry rule is inferred from the review cycle',
        extracted: fieldDict['valid_until'] || 'Not found in selected document',
        status: !fullCase.checks_run || !fieldDict['valid_until'] ? 'pending' :
          (fieldDict['valid_until'] >= new Date().toISOString().slice(0, 10) ? 'match' : 'mismatch'),
      rule: 'EXPIRY-001'
    }
  ];

  const tabContents = {
    cross: `
      <div style="padding:16px 20px">
        <h3 style="font-size:14px;margin:0 0 4px">Application vs. document cross-check</h3>
        <p class="sm mu" style="margin:0 0 12px">${fullCase.checks_run ? 'Advisory comparison against the selected synthetic case documents.' : 'Pre-check not run; values are shown for context only and no status is asserted.'}</p>
        <table class="comp-table">
          <thead>
            <tr><th>Parameter</th><th>Declared in Application</th><th>Found in Selected Document</th><th>Advisory Status</th></tr>
          </thead>
          <tbody>
            ${comparisons.map(c => `
              <tr>
                <td><b>${esc(c.param)}</b><br><small class="tag">${esc(c.rule)}</small></td>
                <td>${esc(c.stated)}</td>
                <td><b>${esc(c.extracted)}</b></td>
                <td>
                  ${c.status === 'match' ? '<span class="comp-match">✓ Conforming</span>' :
                    c.status === 'mismatch' ? '<span class="comp-mismatch">⚠️ Discrepancy</span>' :
                    '<span class="sm mu">Not found / not checked</span>'}
                </td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`,

    find: !fullCase.checks_run ? '<div class="empty"><b>Pre-check has not been run</b>No findings are available yet; this is not a clean or conforming result.</div>' : !findings.length ? '<div class="empty"><b>No findings recorded</b>No findings were generated by the current demo checks. This is advisory only.</div>' :
      findings.map(f => `
        <div class="fl">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
            <span class="b ${f.severity === 'critical' || f.severity === 'high' ? 'er' : 'wn'}">${esc(f.severity)}</span>
            <span class="tag">${esc(f.rule_id || 'RULE')}</span>
          </div>
          <p style="margin:8px 0 4px"><b>${esc(f.title)}</b></p>
          <p class="sm mu" style="margin:0">${esc(f.explanation)}</p>
          <div class="evidence" style="margin:6px 0 8px"><b>Evidence:</b> ${esc(f.evidence || 'N/A')}</div>
          <div class="acts" style="margin-top:8px">
            ${f.document_id ? `<button class="btn s alt" onclick="inspectEvidence(${f.id})">Launch Split Viewer</button>` : ''}
            ${me.role !== 'submitter' && f.status === 'open' ? `
              <button class="btn s p" onclick="handleDisposition(${f.id}, 'accepted')">Accept finding</button>
              <button class="btn s o" onclick="handleDisposition(${f.id}, 'dismissed')">Dismiss finding</button>` : ''}
          </div>
        </div>`).join(''),

    data: !currentDoc || !(currentDoc.fields || []).length ? '<div class="empty"><b>No extracted fields</b></div>' :
      currentDoc.fields.map(f => `
        <div class="fl">
          <div style="display:flex;justify-content:space-between">
            <span class="mu sm">${esc(f.field_name)}</span>
            <span class="sm tag">${f.page ? 'Page ' + f.page : 'Digital Layer'}</span>
          </div>
          <div style="font-weight:600;margin-top:2px;font-size:13px">${esc(f.raw_value)}</div>
          ${f.quote ? `<div class="evidence" style="margin-top:4px">"${esc(f.quote)}"</div>` : ''}
        </div>`).join('')
  };

  const openCount = findings.filter(f => f.status === 'open').length;

  return head('Document review', `${esc(selectedApp.organization_name)} · Ref: ${esc(selectedApp.reference)}`,
    `<button class="btn p" data-a="runChecks">${ic(I.plus)}Run pre-check</button>${me.role === 'submitter' ? ' <button class="btn o" data-a="submitCase">Submit for reviewer review</button>' : ''}`
  ) + `
  <div class="card" style="margin-bottom:18px;padding:16px 20px;background:var(--sf);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;border-left:5px solid var(--nv)">
    <div style="display:flex;align-items:center;gap:14px">
      <div style="width:40px;height:40px;border-radius:50%;background:var(--bls);color:var(--nv);display:grid;place-items:center;font-size:13px;font-weight:700;flex:none">${ini(selectedApp.organization_name)}</div>
      <div>
        <div style="font-weight:700;font-size:16px;color:var(--tx)">${esc(selectedApp.organization_name)}</div>
        <div class="sm mu">Application Reference: <b>${esc(selectedApp.reference)}</b> · Cycle: <b>${esc(selectedApp.review_cycle)}</b> · Request type: <span class="tag">${esc(selectedApp.request_type_label || selectedApp.request_type || 'Unmapped')}</span> · Capacity: <b>${selectedApp.requested_capacity || 'N/A'}</b> · Scope item: <b>${esc(selectedApp.scope_name || 'Not specified')}</b><br>Assigned reviewer: <b>${esc(selectedApp.assigned_to || 'Unassigned')}</b><br><b>Applied checklist:</b> ${esc((selectedApp.checklist || []).map(item => item.label).join(', ') || 'No checklist mapping')}</div>
      </div>
    </div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">        <span class="b ${!selectedApp.checks_run ? 'mu' : openCount > 0 ? 'er' : 'ok'}">${!selectedApp.checks_run ? 'Not checked yet' : openCount > 0 ? openCount + ' Open Findings' : 'No open findings recorded'}</span>
      ${me.role === 'submitter' ? `<button class="btn s alt" data-a="editCase">Edit application</button>` : ''}
      ${me.role === 'reviewer' ? `<button class="btn s o" data-a="requestClarification">Request clarification</button>` : ''}
      ${me.role === 'reviewer' || me.role === 'admin' ? `<button class="btn s alt" data-a="assignCase">Assign case</button><button class="btn s o" data-a="escalateCase">Escalate</button>${selectedApp.escalated_at ? `<button class="btn s p" data-a="resolveEscalation">Resolve escalation</button>` : ''}` : ''}
    </div>
  </div>` + (selectedApp.escalated_at ? `<div class="adv"><div><b>Escalated for ${esc((selectedApp.escalation_level || '').replace(/_/g, ' '))}</b><br>${esc(selectedApp.escalation_reason || '')}<br><span class="sm mu">Raised by ${esc(selectedApp.escalated_by || 'unknown')} · ${new Date(selectedApp.escalated_at).toLocaleString()} · listed in the reviewer alert queue as a case requiring official intervention.</span></div></div>` : '') + adv + `
  <div class="vf">
    <section class="card">
      <div class="ch">
        <h2>Supporting Documents (${docs.length})</h2>
      </div>
      <div class="dl">
        ${!docs.length ? '<div class="empty" style="padding:14px">No uploads yet.</div>' : docs.map((d, i) => {
          const docF = findings.filter(f => f.document_id === d.id);
          const hasFlags = docF.some(f => f.severity === 'high' || f.severity === 'critical');
          return `
          <button class="di" aria-current="${currentDocIndex === i}" data-a="doc" data-v="${i}">
            <div style="display:flex;justify-content:space-between;width:100%;align-items:center">
              <b>${esc(d.original_name)}</b>
              <span class="b ${hasFlags ? 'er' : !fullCase.checks_run ? 'mu' : 'ok'}">${hasFlags ? docF.filter(f => f.status === 'open').length + ' open' : !fullCase.checks_run ? 'Not checked' : docF.length ? 'Reviewed' : 'No finding'}</span>
            </div>
            <small>${esc(d.document_type.replace(/_/g, ' '))} · ${(d.hash || '').slice(0, 10)}${d.hash ? '…' : 'hash unavailable'}</small>
          </button>`;
        }).join('')}
      </div>
      ${me.role === 'submitter' ? `
      <div style="padding:14px 16px;border-top:1px solid var(--bd)">
        <p class="sm mu">Policy pack: ${esc(selectedApp.rule_pack?.version || 'No published pack for this scope')} · ${esc(selectedApp.rule_pack?.review_cycle || selectedApp.review_cycle)} · ${esc(selectedApp.request_type_label || selectedApp.request_type)}. Checklist below is the illustrative checklist for this case.</p>
        <form id="uploadDocForm">
          <label style="font-size:11px;font-weight:700;color:var(--mu);display:block;margin-bottom:4px">Upload checklist document</label>
          <select name="document_type" style="margin-bottom:8px">
            ${docChecklist.map(item => `<option value="${esc(item.type)}">${esc(item.label)}${item.required ? ' (required in demo)' : ''}</option>`).join('')}
          </select>
          ${['awaiting_rectification','rectification_received'].includes(selectedApp.status) ? `<label class="sm">Replace an existing document (optional)</label><select name="replaces_document_id" style="margin-bottom:8px"><option value="">Upload as a new versioned document</option>${docs.filter(doc => !doc.superseded).map(doc => `<option value="${doc.id}">${esc(doc.document_type)} · ${esc(doc.original_name)}</option>`).join('')}</select>` : ''}
          <input type="file" name="file" accept=".pdf,.txt,.csv,.png,.jpg,.jpeg,.mp4" required style="margin-bottom:8px">
          <button class="btn s p" style="width:100%">Upload & Extract</button>
        </form>
      </div>` : ''}
    </section>

    <section class="card">
      <div class="ch">
        <h2>${currentDoc ? esc(currentDoc.original_name) : 'Document Docket'}</h2>${currentDoc?.original_name?.toLowerCase().endsWith('.pdf') ? `<button class="btn s o" data-a="inspectIntegrity" data-id="${currentDoc.id}">Inspect PDF signature fields</button>` : ''}${currentDoc && /\.(png|jpe?g|mp4)$/i.test(currentDoc.original_name) ? `<button class="btn s o" data-a="previewImage" data-id="${currentDoc.id}">View facility evidence</button>` : ''} <span class="tag">Sample review record · not an official status</span>
      </div>
      ${paperContent}      ${selectedApp.clarifications?.length ? `<div class="fl"><b>Rectification / reviewer notes</b>${selectedApp.clarifications.map(c => `<div class="evidence" style="margin-top:8px"><b>${esc(c.status)} · ${esc(c.requested_by)}</b><br>${esc(c.message)}${c.response ? `<br><span class="mu">Submitter response: ${esc(c.response)}</span>` : ''}${me.role === 'submitter' && c.status === 'open' ? `<textarea id="clarification-${c.id}" placeholder="Describe your response or rectification"></textarea><button class="btn s p" data-a="respondClarification" data-id="${c.id}">Send response</button>` : ''}</div>`).join('')}</div>` : ''}
    </section>

    <section class="card">
      <div class="tabs" role="tablist">
        ${[['cross', 'Cross-Check'], ['find', `Findings (${findings.length})`], ['data', 'Fields']].map(t =>
          `<button role="tab" aria-selected="${(currentTab || 'cross') === t[0]}" data-a="tab" data-v="${t[0]}">${t[1]}</button>`
        ).join('')}
      </div>
      ${tabContents[currentTab || 'cross'] || tabContents.cross}
    </section>
  </div>`;
}

async function renderCompliance() {
  if (!selectedApp) return '<div class="empty"><b>No application selected</b></div>';
  const fullCase = await api(`/api/applications/${selectedApp.id}`);
  selectedApp = fullCase;
  const findings = fullCase.findings || [];
  const reqs = [
    ['Checklist completeness', 'DOCS-REQUIRED-001', findings.some(f => f.rule_id === 'DOCS-REQUIRED-001') ? 'gap' : 'met', 'Sample checklist'],
    ['Organization name consistency', 'NAME-MATCH-001', findings.some(f => f.rule_id === 'NAME-MATCH-001') ? 'gap' : 'met', 'Application record vs document'],
    ['Cross-document coherence', 'FIELD-CONFLICT-001', findings.some(f => f.rule_id === 'FIELD-CONFLICT-001') ? 'gap' : 'met', 'Pairwise document comparison'],
    ['Capacity check', 'CAPACITY-MATCH-001', findings.some(f => f.rule_id === 'CAPACITY-MATCH-001') ? 'gap' : 'met', 'Approved capacity vs requested capacity'],
    ['Scope item consistency', 'SCOPE-MATCH-001', findings.some(f => f.rule_id === 'SCOPE-MATCH-001') ? 'gap' : 'met', 'Scope item alignment'],
    ['Document validity & expiry', 'EXPIRY-001', findings.some(f => f.rule_id === 'EXPIRY-001') ? 'gap' : 'met', 'Printed validity date'],
    ['Duplicate file detection', 'DUPLICATE-001', 'met', 'SHA-256 digest comparison']
  ];

  const checked = fullCase.checks_run;
  const applicableReqs = reqs.filter(r => fullCase.rule_execution?.[r[1]] !== false);
  const metCount = checked ? applicableReqs.filter(r => !findings.some(f => f.rule_id === r[1] && f.status === 'open')).length : 0;
  const pct = checked && applicableReqs.length ? Math.round((metCount / applicableReqs.length) * 100) : null;

  return head('Assessment', `Policy checks · ${esc(selectedApp.organization_name)}`,
    `<button class="btn o" data-a="tn" data-export="compliance">Export case report</button>`
  ) + adv + `
  <section class="card">
    <div class="ch">
      <div>
        <b>${checked ? `${metCount} of ${applicableReqs.length} applicable illustrative checks have no open finding` : 'Illustrative checks have not been run'}</b>
        ${checked && applicableReqs.length ? `<div class="bar" style="width:260px;margin-top:8px"><i style="width:${pct}%"></i></div>` : ''}
      </div>
      <span class="tag">${checked ? 'Advisory summary' : 'Not checked'}</span>
    </div>
    <div class="tw">
      <table>
        <thead><tr><th>Rule Name & Scope</th><th>Rule ID</th><th>Assessment</th><th>Source Reference</th></tr></thead>
        <tbody>
          ${reqs.map(r => `
            <tr>
              <td><b>${r[0]}</b></td>
              <td><span class="tag">${r[1]}</span></td>
              <td><span class="b ${!checked || fullCase.rule_execution?.[r[1]] === false ? 'mu' : findings.some(f => f.rule_id === r[1] && f.status === 'open') ? 'er' : 'ok'}">${!checked ? 'Not checked' : fullCase.rule_execution?.[r[1]] === false ? 'Not applied by pack' : findings.some(f => f.rule_id === r[1] && f.status === 'open') ? 'Open finding' : 'No open finding'}</span></td>
              <td class="sm mu">${r[3]}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>
  </section>`;
}

async function renderAuthority() {
  if (!selectedApp) return '<div class="empty"><b>No application selected</b></div>';
  const fullCase = await api(`/api/applications/${selectedApp.id}`);
  const attempts = fullCase.verifications || [];

  return head('Source checks', 'External source connectors and the verification results recorded against this case.',
    `<button class="btn alt" onclick="recordVerification()">Record verification result</button><button class="btn p" onclick="simulateIssuer()">${ic(I.plus)}Simulate issuer check</button>`
  ) + `
  <section class="card">
    <div class="ch">
      <h2>Verification Activity Log</h2>
      <span class="tag">Simulated / Manual connectors</span>
    </div>
    <div class="tw">
      <table>
        <thead><tr><th>Method / Source</th><th>Mode</th><th>Result Status</th><th>Comparison & Audit Detail</th><th>Timestamp</th></tr></thead>
        <tbody>
          ${!attempts.length ? '<tr><td colspan="5"><div class="empty">No verification queries recorded yet. Record a result or click \"Simulate issuer check\" above.</div></td></tr>' :
            attempts.map(v => `
              <tr>
                <td><b>${esc(v.method)}</b><br><span class="sm mu">${esc(v.source || 'Source not recorded')}</span></td>
                <td><span class="tag">${esc(v.mode)}</span></td>
                <td><span class="b ${v.status === 'verified' ? 'ok' : (v.status === 'mismatch' || v.status === 'expired') ? 'er' : 'bl'}">${esc(v.label || v.status)}</span>${v.requires_manual_confirmation ? '<br><span class="sm mu">Routed for manual confirmation</span>' : ''}</td>
                <td class="sm">${esc(v.detail)}</td>
                <td class="sm mu">${new Date(v.checked_at).toLocaleString()}</td>
              </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="fl"><b>Verification statuses</b><p class="sm mu">Verified · Mismatch detected · Expired · Verification pending · Source unavailable · Manual review required. Pending, unavailable and manual-review results are routed into the reviewer alert queue for human confirmation. No external authority is contacted by this prototype.</p></div>
  </section>`;
}

async function recordVerification() {
  if (!selectedApp) return;
  const fullCase = await api(`/api/applications/${selectedApp.id}`);
  const docs = fullCase.documents || [];
  modal('Record verification result', `
    <div class="form-row"><label>Document (optional)</label><select id="verDocument"><option value="">Application-level result</option>${docs.map(d => `<option value="${d.id}">${esc(d.document_type)} · ${esc(d.original_name)}</option>`).join('')}</select></div>
    <div class="form-row"><label>Method / check performed</label><input id="verMethod" maxlength="120" placeholder="External reference lookup, QR code, portal record, digital signature…"></div>
    <div class="form-row"><label>Authorized source consulted</label><input id="verSource" maxlength="120" placeholder="Source name or reference"></div>
    <div class="form-row"><label>Result status</label><select id="verStatus">${[
      ['verified', 'Verified against source'], ['mismatch', 'Mismatch detected'], ['expired', 'Expired'],
      ['verification_pending', 'Verification pending'], ['source_unavailable', 'Source unavailable'], ['manual_review_required', 'Manual review required']
    ].map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select></div>
    <div class="form-row"><label>Value declared in application</label><input id="verDeclared" maxlength="300" placeholder="Required for verified / mismatch / expired"></div>
    <div class="form-row"><label>Value returned by source</label><input id="verReturned" maxlength="300" placeholder="Required for verified / mismatch / expired"></div>
    <div class="form-row"><label>Reviewer detail / observation</label><textarea id="verDetail" minlength="3" maxlength="500" placeholder="What was checked, and what remains to be confirmed"></textarea></div>
    <p class="sm mu">Manual entries are reviewer-reported; Arbiter does not authenticate the source. Comparison statuses require both values. Inconclusive or unavailable results are routed for human confirmation.</p>`, async () => {
    const payload = {
      document_id: $('#verDocument').value ? Number($('#verDocument').value) : null,
      method: $('#verMethod').value.trim(),
      source: $('#verSource').value.trim(),
      status: $('#verStatus').value,
      declared_value: $('#verDeclared').value.trim() || null,
      source_value: $('#verReturned').value.trim() || null,
      detail: $('#verDetail').value.trim(),
    };
    if (payload.method.length < 3) return toast('Describe the method checked');
    if (payload.source.length < 3) return toast('Name the authorized source consulted');
    if (payload.detail.length < 3) return toast('Add an reviewer observation');
    try {
      const result = await api(`/api/applications/${selectedApp.id}/verification`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      closeModal();
      toast(result.requires_manual_confirmation ? `${result.label} — routed for manual confirmation` : `${result.label} recorded`);
      await loadApps(); draw();
    } catch (err) { toast(err.message); }
  });
}

async function renderAudit() {
  if (!selectedApp) return '<div class="empty"><b>No application selected</b></div>';
  const fullCase = await api(`/api/applications/${selectedApp.id}`);
  const auditList = fullCase.audit || [];

  return head('Audit trail', `Immutable SHA-256 hash-chained event log for ${esc(selectedApp.reference)}`,
    `<button class="btn p" onclick="verifyAuditChain()">Verify cryptographic hash chain</button>`
  ) + `
  <section class="card">
    <div class="ch">
      <input type="search" id="aq" placeholder="Filter audit entries..." value="${esc(auditQuery)}">
      <span class="sm mu" id="chainStatus">Chain intact (${auditList.length} events)</span>
    </div>
    <div class="tw">
      <table>
        <thead><tr><th>Timestamp</th><th>Actor</th><th>Action</th><th>Detail & Chain Evidence</th></tr></thead>
        <tbody>
          ${!auditList.length ? '<tr><td colspan="4"><div class="empty">No audit events recorded.</div></td></tr>' :
            auditList.map(a => `
              <tr>
                <td class="sm mu">${new Date(a.created_at).toLocaleString()}</td>
                <td><b>${esc(a.actor)}</b></td>
                <td><span class="tag">${esc(a.action)}</span></td>
                <td class="sm">${esc(a.detail)}</td>
              </tr>`).join('')}
        </tbody>
      </table>
    </div>
  </section>`;
}

/* ==========================================================================
   Split Evidence Viewer Modal
   ========================================================================== */

async function inspectEvidence(findingId, pageOverride = null) {
  try {
    const finding = (selectedApp.findings || []).find(f => f.id === findingId);
    if (!finding || !finding.document_id) return toast('No document linked to this finding');

    const pageMatch = (finding.evidence || '').match(/(?:source\s+)?page\s*[:=]?\s*(\d+)|\bp\.(\d+)/i);
    const evidencePage = finding.evidence_page ?? (pageMatch ? Number(pageMatch[1] || pageMatch[2]) : null);
    const pageNum = pageOverride ?? evidencePage ?? 1;
    const pageData = await api(`/api/documents/${finding.document_id}/pages/${pageNum}`);

    let highlightedText = esc(pageData.text);
    if (finding.evidence) {
      const tokens = finding.evidence.match(/'([^']+)'/g) || [];
      tokens.forEach(tok => {
        const clean = tok.replace(/'/g, '');
        if (clean.length > 2) {
          const re = new RegExp(`(${clean.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&')})`, 'gi');
          highlightedText = highlightedText.replace(re, '<mark>$1</mark>');
        }
      });
    }

    const html = `
      <div class="split-viewer">
        <div class="viewer-left">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span class="b ${finding.severity === 'high' ? 'er' : 'wn'}">${esc(finding.severity)}</span>
            <span class="tag">${esc(finding.rule_id)}</span>
          </div>
          <h3>${esc(finding.title)}</h3>
          <p class="sm">${esc(finding.explanation)}</p>
          <div class="evidence"><b>Rule citation:</b> ${esc(finding.evidence)}</div>
          ${me.role !== 'submitter' && finding.status === 'open' ? `
          <div class="acts" style="margin-top:12px">
            <button class="btn s p" onclick="handleDisposition(${finding.id}, 'accepted')">Accept finding</button>
            <button class="btn s o" onclick="handleDisposition(${finding.id}, 'dismissed')">Dismiss finding</button>
          </div>` : ''}
        </div>
        <div class="viewer-right">
          <div class="viewer-doc">${highlightedText}</div>
          <div class="page-nav">
            <span class="sm mu">${esc(pageData.original_name)} — Page ${pageData.page} of ${pageData.total_pages}</span>
      <span class="acts"><button class="btn s alt" onclick="inspectEvidence(${finding.id}, ${Math.max(1, pageData.page - 1)})" ${pageData.page <= 1 ? 'disabled' : ''}>Previous page</button><button class="btn s alt" onclick="inspectEvidence(${finding.id}, ${Math.min(pageData.total_pages, pageData.page + 1)})" ${pageData.page >= pageData.total_pages ? 'disabled' : ''}>Next page</button></span>
          </div>
        </div>
      </div>`;

    const m = $('#modal');
    m.innerHTML = `<div class="modalbox wide"><h2>Evidence-to-Rule Split Inspection</h2>${html}<div class="acts" style="justify-content:flex-end;margin-top:14px"><button class="btn p s" onclick="closeModal()">Close viewer</button></div></div>`;
    m.classList.add('open');
  } catch (err) {
    toast(err.message);
  }
}

/* ==========================================================================
   Event Handlers & User Actions
   ========================================================================== */

async function handleDisposition(findingId, disposition) {
  modal(`Record reviewer review decision: ${disposition.toUpperCase()}`, `
    <div class="form-row">
      <label>Justification / Reason (Recorded in immutable audit trail)</label>
      <textarea id="dispReason" placeholder="State the regulatory justification for this decision..."></textarea>
    </div>`, async () => {
      const reason = $('#dispReason').value.trim();
      if (!reason) return toast('Please enter a review reason');
      try {
        await api(`/api/findings/${findingId}/review`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ outcome: disposition, reason })
        });
        currentFindingFilter = null;
        closeModal();
        toast(`Finding ${disposition}`);
        await loadApps();
        draw();
      } catch (err) {
        toast(err.message);
      }
    });
}

async function simulateIssuer() {
  try {
    const res = await api(`/api/applications/${selectedApp.id}/verification/simulate`, { method: 'POST' });
    toast(res.label || 'Verification simulated');
    await loadApps();
    draw();
  } catch (err) {
    toast(err.message);
  }
}

async function exportData(kind) {
  try {
    let filename, content, mime;
    if (kind === 'compliance' && selectedApp) {
      const report = await api(`/api/applications/${selectedApp.id}/report`);
      filename = `${selectedApp.reference}-decision-support.json`; content = JSON.stringify(report, null, 2); mime = 'application/json';
    } else if (kind === 'dashboard') {
      const [dashboard, reportData, alertData, rules] = await Promise.all([api('/api/dashboard'), api('/api/applications'), api('/api/alerts'), api('/api/rules')]);
      filename = 'arbiter-dashboard-summary.json'; content = JSON.stringify({ report_type: 'Arbiter dashboard decision-support summary', generated_at: new Date().toISOString(), scope: 'Current signed-in user permissions; sample data', dashboard, applications: reportData, open_alerts: alertData.items, rule_pack: rules.rule_pack, disclaimer: 'Sample data; advisory only, not an approval decision.' }, null, 2); mime = 'application/json';
    } else {
      const rows = apps.map(a => ({ reference: a.reference, organization: a.organization_name, review_cycle: a.review_cycle, request_type: a.request_type_label || a.request_type, status: a.status, checks_run: a.checks_run, rule_pack_version: a.rule_pack_version, open_findings: a.open_findings_count || 0, high_risk_findings: a.high_risk_count || 0, document_count: a.document_count || 0 }));          const keys = rows.length ? Object.keys(rows[0]) : ['reference','organization','review_cycle','request_type','status','checks_run','rule_pack_version','open_findings','high_risk_findings','document_count'];
          const cell = value => { let text = String(value ?? ''); if (/^[\s\u0000-\u001f]*[=+@\-]/.test(text)) text = `'${text}`; return `"${text.replace(/"/g, '""')}"`; };
      filename = 'arbiter-application-list.csv'; content = [keys.map(cell).join(','), ...rows.map(row => keys.map(key => cell(row[key])).join(','))].join('\r\n'); mime = 'text/csv;charset=utf-8';
    }
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`Downloaded ${filename}`);
  } catch (err) { toast(`Export failed: ${err.message}`); }
}

async function openAlerts() {
  try {
    const result = await api('/api/alerts');
    const items = result.items || [];
    const html = items.length ? items.map(item => `<div class="fl"><span class="b ${item.severity === 'critical' || item.severity === 'high' ? 'er' : 'wn'}">${esc(item.severity || item.status || item.type)}</span><p><b>${esc(item.organization_name)} · ${esc(item.reference)}</b></p><p class="sm">${esc(item.title || item.message)}</p>${item.explanation ? `<p class="sm mu">${esc(item.explanation)}</p>` : ''}<button class="btn s alt" data-a="alertOpen" data-id="${item.application_id}" data-finding="${item.finding_id || ''}">Open case</button></div>`).join('') : '<div class="empty"><b>No open alerts</b>No open findings or unanswered clarification requests are currently in scope.</div>';
    const box = $('#modal'); box.innerHTML = `<div class="modalbox wide"><h2>Open alerts (${result.count})</h2><p class="sm mu">Live open findings and pending clarifications for your authorized case scope.</p>${html}<div class="acts" style="justify-content:flex-end;margin-top:14px"><button class="btn p s" onclick="closeModal()">Close</button></div></div>`; box.classList.add('open');
  } catch (err) { toast(`Could not load alerts: ${err.message}`); }
}

async function requestClarification() {
  if (!selectedApp) return;
  modal('Request submitter clarification', '<div class="form-row"><label>Clarification request (stored in the case audit trail)</label><textarea id="clarificationRequest" minlength="3" maxlength="500" placeholder="Describe the information or replacement evidence requested"></textarea></div>', async () => {
    const message = $('#clarificationRequest').value.trim();
    if (message.length < 3) return toast('Enter a clarification request');
    try { await api(`/api/applications/${selectedApp.id}/clarifications`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message }) }); closeModal(); toast('Clarification sent to submitter'); await loadApps(); draw(); }
    catch (err) { toast(err.message); }
  });
}

async function assignCase() {
  if (!selectedApp) return;
  try {
    const roster = await api('/api/reviewers');
    modal(me.role === 'admin' ? 'Assign case to an reviewer' : 'Claim or release this case', `<div class="form-row"><label>${me.role === 'admin' ? 'Assigned reviewer' : 'Assignment'}</label><select id="assignReviewer"><option value="">${me.role === 'admin' ? 'Leave unassigned' : 'Leave in shared queue'}</option>${(roster.reviewers || []).map(o => `<option value="${esc(o.email)}" ${selectedApp.assigned_to === o.email ? 'selected' : ''}>${me.role === 'admin' ? esc(o.email) : 'Assign to me ('+esc(o.email)+')'}</option>`).join('')}</select></div><p class="sm mu">${esc(roster.scope || '')}</p>`, async () => {
      const reviewer_email = $('#assignReviewer').value || null;
      try { await api(`/api/applications/${selectedApp.id}/assign`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reviewer_email }) }); closeModal(); toast(reviewer_email ? `Case assigned to ${reviewer_email}` : 'Assignment cleared'); await loadApps(); draw(); }
      catch (err) { toast(err.message); }
    });
  } catch (err) { toast(err.message); }
}

async function escalateCase() {
  if (!selectedApp) return;
  modal('Escalate for official intervention', `<div class="form-row"><label>Escalation level</label><select id="escalationLevel"><option value="senior_review">Senior reviewer</option><option value="committee_review">Committee review</option><option value="inspection_required">Physical inspection required</option></select></div><div class="form-row"><label>Reason (why this needs official intervention)</label><textarea id="escalationReason" minlength="5" maxlength="500" placeholder="State what remains unresolved and who must decide"></textarea></div><p class="sm mu">Escalation routes the case into the reviewer alert queue. It is not a finding of non-compliance and does not decide the application.</p>`, async () => {
    const reason = $('#escalationReason').value.trim();
    if (reason.length < 5) return toast('Give a reason of at least 5 characters');
    try { await api(`/api/applications/${selectedApp.id}/escalate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ level: $('#escalationLevel').value, reason }) }); closeModal(); toast('Escalation recorded and queued for official review'); await loadApps(); draw(); }
    catch (err) { toast(err.message); }
  });
}

async function resolveEscalation() {
  if (!selectedApp) return;
  try { await api(`/api/applications/${selectedApp.id}/escalation/resolve`, { method: 'POST' }); toast('Escalation resolved'); await loadApps(); draw(); }
  catch (err) { toast(err.message); }
}

async function respondClarification(id) {
  const message = $(`#clarification-${id}`)?.value.trim();
  if (!message || message.length < 3) return toast('Enter a response of at least 3 characters');
  try { await api(`/api/clarifications/${id}/respond`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message }) }); toast('Response recorded; you may now upload permitted rectification evidence'); await loadApps(); draw(); }
  catch (err) { toast(err.message); }
}

function editCase() {
  if (!selectedApp) return;
  modal('Edit application details', `<form id="editAppForm"><div class="form-row"><label>Organization name</label><input name="organization_name" value="${esc(selectedApp.organization_name)}" required></div><div class="form-row"><label>Review cycle</label><select name="review_cycle">${['2024-25','2025-26','2026-27'].map(year => `<option value="${year}" ${year === selectedApp.review_cycle ? 'selected' : ''}>${year}</option>`).join('')}</select></div><div class="form-row"><label>Request type</label><select name="request_type">${[['licence_renewal','Renewal'],['scope_expansion','Scope expansion'],['new_registration','New registration']].map(([id,label]) => `<option value="${id}" ${id === selectedApp.request_type ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div class="form-row"><label>Scope item</label><input name="scope_name" value="${esc(selectedApp.scope_name || '')}"></div><div class="form-row"><label>Requested capacity</label><input type="number" min="1" name="requested_capacity" value="${esc(selectedApp.requested_capacity || '')}"></div></form>`, async () => {      const data = Object.fromEntries(new FormData($('#editAppForm'))); data.requested_capacity = data.requested_capacity ? Number(data.requested_capacity) : null;
      if (selectedApp.status === 'awaiting_rectification' && (data.organization_name !== selectedApp.organization_name || data.request_type !== selectedApp.request_type || data.review_cycle !== selectedApp.review_cycle)) return toast('Organization, request type and review cycle are locked during rectification; request an authorized reopening.');
    try { await api(`/api/applications/${selectedApp.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }); closeModal(); toast('Application details saved'); await loadApps(); draw(); }
    catch (err) { toast(err.message); }
  });
}

async function createRulePackDraft() {
  const payload = await api('/api/rules');
  const executable = payload.rules.filter(rule => rule.executable);
  modal('Propose a rule-pack revision', `<p class="sm mu">This creates an immutable draft. A different provisioned administrator must approve it; publication changes which checks run on new cases.</p><div class="form-row"><label>Rule to change</label><select id="draftRuleId">${executable.map(rule => `<option value="${esc(rule.id)}" data-enabled="${rule.enabled}">${esc(rule.id)} — currently ${rule.enabled ? 'enabled' : 'disabled'}</option>`).join('')}</select></div><div class="form-row"><label>New state</label><select id="draftRuleEnabled"><option value="true">Enabled</option><option value="false">Disabled</option></select></div><div class="form-row"><label>Effective from</label><select id="draftEffectiveFrom"><option>2024-25</option><option>2025-26</option><option selected>2026-27</option></select></div><div class="form-row"><label>Effective through (optional)</label><select id="draftEffectiveTo"><option value="">No end date set</option><option>2024-25</option><option>2025-26</option><option>2026-27</option></select></div><div class="form-row"><label>Applicable review cycles</label><select id="draftCycles" multiple>${['2024-25','2025-26','2026-27'].map(year => `<option value="${year}" ${year === '2026-27' ? 'selected' : ''}>${year}</option>`).join('')}</select></div><div class="form-row"><label>Applicable request types (empty means all three)</label><select id="draftTypes" multiple>${[['licence_renewal','Renewal'],['scope_expansion','Scope expansion'],['new_registration','New registration']].map(([id,label]) => `<option value="${id}">${label}</option>`).join('')}</select></div><div class="form-row"><label>Source quality</label><select id="draftSourceQuality"><option value="official_primary">Official primary source</option><option value="government_copy">Government-hosted copy</option><option value="secondary">Secondary source / mirror</option><option value="synthetic" selected>Sample logic in this repository</option><option value="unverified">Unverified</option></select></div><div class="form-row"><label>Source reference (URL, clause, or internal evidence)</label><input id="draftSourceReference" maxlength="500" placeholder="https://… or source title / clause"></div><div class="form-row"><label>Reason / evidence basis</label><textarea id="draftReason" minlength="8" placeholder="Explain the change and its source/approval basis"></textarea></div>`, async () => {      const rule_id = $('#draftRuleId').value, enabled = $('#draftRuleEnabled').value === 'true', reason = $('#draftReason').value.trim();
    const effective_from = $('#draftEffectiveFrom').value, effective_to = $('#draftEffectiveTo').value || null;
    const request_types = [...$('#draftTypes').selectedOptions].map(option => option.value);
    const review_cycles = [...$('#draftCycles').selectedOptions].map(option => option.value);
    const source_quality = $('#draftSourceQuality').value;
    const source_reference = $('#draftSourceReference').value.trim();
    if (effective_to && effective_to < effective_from) return toast('Effective through cannot be earlier than effective from');
    if (!review_cycles.length) return toast('Select at least one review cycle');
    if (reason.length < 8) return toast('Provide a reason of at least 8 characters');
    try { await api('/api/rule-packs/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ changes: [{ rule_id, enabled }], reason, effective_from, effective_to, review_cycles, request_types, source_quality, source_reference }) }); closeModal(); toast('Draft saved; independent administrator approval required'); draw(); }
    catch (err) { toast(err.message); }
  });
}

async function publishRulePack(id) {
  try { const result = await api(`/api/rule-packs/${id}/publish`, { method: 'POST' }); toast(`Published demo rule pack ${result.version}`); draw(); }
  catch (err) { toast(err.message); }
}

async function verifyAuditChain() {
  try {
    const res = await api(`/api/applications/${selectedApp.id}/audit/verify`);
    if (res.valid) {
      $('#chainStatus').innerHTML = `<span class="b ok">SHA-256 Chain Verified (${res.verified_events} events)</span>`;
      toast('Cryptographic audit chain intact.');
    } else {
      $('#chainStatus').innerHTML = `<span class="b er">Integrity Mismatch Detected</span>`;
      toast('Audit verification failure!');
    }
  } catch (err) {
    toast(err.message);
  }
}

const V = {
  dashboard: renderDashboard,
  applications: renderApplications,
  rules: renderRules,
  verify: renderVerify,
  compliance: renderCompliance,
  authority: renderAuthority,
  audit: renderAudit
};

async function draw() {
  const hash = location.hash.slice(1);
  const k = V[hash] ? hash : 'dashboard';
  buildNav(k);
  const main = $('#main');
  if (main) {
    main.innerHTML = await V[k]();
    wireFormEvents();
  }
  document.title = (R[k] ? R[k][0] : 'Overview') + ' · Arbiter';
}

function wireFormEvents() {
  const uploadForm = $('#uploadDocForm');
  if (uploadForm) {
    uploadForm.onsubmit = async e => {
      e.preventDefault();
      const fd = new FormData(uploadForm);
      try {
        const docType = fd.get('document_type');
        const replaceId = fd.get('replaces_document_id');
        fd.delete('replaces_document_id');
        const replaceQuery = replaceId ? `&replaces_document_id=${encodeURIComponent(replaceId)}` : '';
        const res = await fetch(`/api/applications/${selectedApp.id}/documents?document_type=${encodeURIComponent(docType)}${replaceQuery}`, {
          method: 'POST',
          headers: { 'X-Session-Token': token },
          body: fd
        });
        if (!res.ok) {
          const err = await res.json();
          throw Error(err.detail || 'Upload failed');
        }
        const uploadResult = await res.json();
        toast('Document uploaded & fields extracted');
        await loadApps();
        selectedApp = await api(`/api/applications/${selectedApp.id}`);
        const current = selectedApp.documents.findIndex(doc => doc.id === uploadResult.document_id);
        if (current >= 0) currentDocIndex = current;
        currentFindingFilter = null;
        currentDocPage = 1;
        draw();
      } catch (err) {
        toast(err.message);
      }
    };
  }
}

addEventListener('hashchange', () => { draw(); scrollTo(0, 0); });
addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

document.addEventListener('input', e => {
  if (e.target.id === 'q') { searchQuery = e.target.value; draw(); }
  if (e.target.id === 'aq') { auditQuery = e.target.value; draw(); }
});

document.addEventListener('change', e => {
  if (e.target.id === 'cat') { typeFilter = e.target.value; draw(); }
});

document.addEventListener('click', async e => {
  const t = e.target.closest('[data-a]');
  if (!t) return;
  const a = t.dataset.a;
  const v = t.dataset.v;

  if (a === 'st') { statusFilter = v; draw(); }
  else if (a === 'kpi') {
    statusFilter = v || 'all';
    location.hash = 'applications';
    if (location.hash === '#applications') draw();
  }
  else if (a === 'tn') { exportData(t.dataset.export || 'applications'); }
  else if (a === 'open' || a === 'vf') {
    const id = Number(t.dataset.id || v);
    selectedApp = apps.find(x => x.id === id) || selectedApp;
    location.hash = 'verify';
  }
  else if (a === 'go') { location.hash = v; }
  else if (a === 'doc') { currentDocIndex = Number(v); currentFindingFilter = null; currentDocPage = 1; draw(); }
  else if (a === 'inspectIntegrity') {
    try {
      const result = await api(`/api/documents/${Number(t.dataset.id)}/integrity`);
      modal('PDF signature-field inspection', `<div class="adv"><div><b>${esc(result.status.replace(/_/g, ' '))}</b><br>${esc(result.detail)}</div></div>${result.signature_fields?.length ? `<div class="fl"><b>Detected fields</b>${result.signature_fields.map(field => `<div class="evidence" style="margin-top:8px">${esc(field.field_name)} · page ${field.page} · ${field.filled ? 'field contains a value' : 'empty field'}</div>`).join('')}</div>` : ''}<p class="sm mu">Simulated structural inspection only. It does not establish document authenticity or signer identity.</p>`, closeModal);
    } catch (err) { toast(err.message); }
  }
  else if (a === 'previewImage') {
    try {
      const isVideo = /\.mp4$/i.test(selectedApp.documents.find(doc => doc.id === Number(t.dataset.id))?.original_name || '');
      const response = await fetch(`/api/documents/${Number(t.dataset.id)}/${isVideo ? 'video-preview' : 'image-preview'}`, { headers: { 'X-Session-Token': token } });
      if (!response.ok) throw Error((await response.json()).detail || 'Evidence preview failed');
      const objectUrl = URL.createObjectURL(await response.blob());
      const reviewData = ['reviewer', 'admin'].includes(me.role) ? await api(`/api/applications/${selectedApp.id}/facility-reviews`) : { items: [] };
      const outcomeLabel = { observed_no_issue: 'No visible concern observed', follow_up_required: 'Follow-up evidence required', inspection_recommended: 'Recommend physical inspection', unable_to_assess: 'Unable to assess image' };
      const box = $('#modal');
      box.innerHTML = `<div class="modalbox wide"><h2>Infrastructure evidence</h2><p class="sm mu">Original private upload · reviewer visual review only · no computer-vision assessment was performed.</p>${isVideo ? `<video controls playsinline style="display:block;max-width:100%;max-height:55vh;margin:auto" src="${objectUrl}">Your browser cannot play this MP4 evidence.</video>` : `<img src="${objectUrl}" alt="Uploaded facility evidence" style="display:block;max-width:100%;max-height:55vh;margin:auto;object-fit:contain">`}${['reviewer', 'admin'].includes(me.role) ? `<section class="fl"><b>Record human review</b><form id="facilityReviewForm"><select name="outcome" required><option value="follow_up_required">Follow-up evidence required</option><option value="observed_no_issue">No visible concern observed</option><option value="inspection_recommended">Recommend physical inspection</option><option value="unable_to_assess">Unable to assess evidence</option></select><textarea name="note" minlength="5" maxlength="1000" required placeholder="Describe what you observed and any limits of this evidence"></textarea><button class="btn s p" type="submit">Save review</button></form>${reviewData.items.map(item => `<div class="evidence" style="margin-top:8px"><b>${esc(outcomeLabel[item.outcome] || item.outcome)}</b> · ${esc(item.reviewer)} · ${esc(item.created_at)}<br>${esc(item.note)}</div>`).join('') || '<p class="sm mu">No reviewer review recorded for this application yet.</p>'}</section>` : ''}<div class="acts" style="justify-content:flex-end;margin-top:14px"><button class="btn p s" id="closeImagePreview">Close</button></div></div>`;
      box.classList.add('open');
      $('#closeImagePreview').onclick = () => { closeModal(); URL.revokeObjectURL(objectUrl); };
      $('#facilityReviewForm')?.addEventListener('submit', async event => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        try {
          const saved = await api(`/api/applications/${selectedApp.id}/facility-reviews`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_id: Number(t.dataset.id), outcome: form.get('outcome'), note: form.get('note') }) });
          toast(saved.inspection_routed ? 'Review saved; physical inspection recommendation added to reviewer alerts.' : 'Human facility review saved.');
          closeModal(); URL.revokeObjectURL(objectUrl); await loadApps(); draw();
        } catch (err) { toast(`Could not save review: ${err.message}`); }
      });
    } catch (err) { toast(err.message); }
  }
  else if (a === 'docPage') { currentDocPage = Math.max(1, currentDocPage + Number(v)); currentFindingFilter = null; draw(); }
  else if (a === 'alertOpen') { selectedApp = apps.find(x => x.id === Number(t.dataset.id)) || selectedApp; currentTab = 'find'; currentFindingFilter = t.dataset.finding ? Number(t.dataset.finding) : null; currentDocIndex = 0; currentDocPage = 1; closeModal(); location.hash = 'verify'; }
  else if (a === 'respondClarification') { respondClarification(Number(t.dataset.id)); }
  else if (a === 'tab') { currentTab = v; draw(); }
  else if (a === 'submitCase') {
    try {
      const res = await api(`/api/applications/${selectedApp.id}/submit`, { method: 'POST' });
      toast(`Submitted to reviewer queue (${res.finding_count} advisory findings · ${res.rule_pack_version})`);
      await loadApps(); draw();
    }
    catch (err) { toast(err.message); }
  }
  else if (a === 'requestClarification') { requestClarification(); }
  else if (a === 'assignCase') { assignCase(); }
  else if (a === 'escalateCase') { escalateCase(); }
  else if (a === 'resolveEscalation') { resolveEscalation(); }
  else if (a === 'editCase') { editCase(); }
  else if (a === 'draftRulePack') { createRulePackDraft(); }
  else if (a === 'publishRulePack') { publishRulePack(Number(t.dataset.id)); }
  else if (a === 'runChecks') {
    try {
      const res = await api(`/api/applications/${selectedApp.id}/precheck`, { method: 'POST' });
      toast(`Advisory pre-check complete: ${res.finding_count} findings · ${res.rule_pack_version}`);
      await loadApps();
      draw();
    } catch (err) {
      toast(err.message);
    }
  }
  else if (a === 'theme') {
    const root = document.documentElement;
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    $('#thm').innerHTML = ic(root.dataset.theme === 'dark' ? I.sun : I.moon);
  }
  else if (a === 'bell') { openAlerts(); }
  else if (a === 'srch') {
    location.hash = 'applications';
    setTimeout(() => { const q = $('#q'); if (q) q.focus(); }, 60);
  }
  else if (a === 'newCase') {
    modal('Create New Application', `
      <form id="newAppForm">
        <div class="form-row"><label>Organization name</label><input name="organization_name" required></div>
        <div class="form-row"><label>Review cycle</label><select name="review_cycle">${['2024-25', '2025-26', '2026-27'].map(year => `<option value="${year}" ${year === '2026-27' ? 'selected' : ''}>${year}</option>`).join('')}</select></div>
        <div class="form-row"><label>Request type</label><select name="request_type"><option value="licence_renewal">Renewal</option><option value="scope_expansion">Scope expansion</option><option value="new_registration">New registration</option></select><small class="mu">Sample request types bundled with the demo — replace them with your own.</small></div>
        <div class="form-row"><label>Scope item (product / service / category)</label><input name="scope_name" placeholder="Bulk Storage — Class A"></div>
        <div class="form-row"><label>Requested capacity</label><input type="number" name="requested_capacity" value="120"></div>
      </form>`, async () => {
        const data = Object.fromEntries(new FormData($('#newAppForm')));
        if (data.requested_capacity) data.requested_capacity = Number(data.requested_capacity);
        try {
          const res = await api('/api/applications', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
          });
          closeModal();
          toast('Application created');
          await loadApps();
          selectedApp = res;
          location.hash = 'verify';
        } catch (err) {
          toast(err.message);
        }
      });
  }
});

// Boot application
start();
