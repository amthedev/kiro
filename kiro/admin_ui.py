# -*- coding: utf-8 -*-
"""
Admin panel HTML — single-page app served at /admin.
No external CDN or JS framework dependencies.
"""

ADMIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kiro Gateway — Admin</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --card: #20232f;
    --border: #2e3347; --accent: #6c63ff; --accent2: #00d4aa;
    --text: #e8eaf0; --muted: #8b92a5; --danger: #ff4d6d;
    --warn: #ffa94d; --ok: #51cf66;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }
  a { color: var(--accent); text-decoration: none; }

  /* Layout */
  .layout { display: flex; min-height: 100vh; }
  .sidebar { width: 220px; background: var(--surface); border-right: 1px solid var(--border); padding: 0; flex-shrink: 0; display: flex; flex-direction: column; }
  .sidebar-logo { padding: 24px 20px 20px; border-bottom: 1px solid var(--border); }
  .sidebar-logo h1 { font-size: 16px; font-weight: 700; color: var(--accent); }
  .sidebar-logo span { font-size: 11px; color: var(--muted); }
  .sidebar-nav { padding: 12px 0; flex: 1; }
  .nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 20px; cursor: pointer; color: var(--muted); border-left: 3px solid transparent; transition: all .15s; font-size: 13px; }
  .nav-item:hover { background: var(--card); color: var(--text); }
  .nav-item.active { color: var(--accent); border-left-color: var(--accent); background: rgba(108,99,255,.08); }
  .nav-icon { width: 18px; text-align: center; }
  .main { flex: 1; padding: 28px; overflow-y: auto; }

  /* Cards */
  .page { display: none; }
  .page.active { display: block; }
  .page-title { font-size: 20px; font-weight: 700; margin-bottom: 24px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-bottom: 20px; }
  .card-title { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 16px; }

  /* Stats grid */
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }
  .stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }
  .stat-value { font-size: 28px; font-weight: 700; margin-top: 6px; }
  .stat-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }

  /* Tables */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,.02); }

  /* Badges */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .badge-ok { background: rgba(81,207,102,.15); color: var(--ok); }
  .badge-off { background: rgba(255,77,109,.15); color: var(--danger); }
  .badge-warn { background: rgba(255,169,77,.15); color: var(--warn); }

  /* Forms */
  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .form-group { display: flex; flex-direction: column; gap: 5px; }
  .form-group.full { grid-column: 1 / -1; }
  label { font-size: 12px; color: var(--muted); font-weight: 500; }
  input, textarea, select { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; color: var(--text); font-size: 13px; width: 100%; outline: none; font-family: inherit; }
  input:focus, textarea:focus, select:focus { border-color: var(--accent); }
  textarea { resize: vertical; min-height: 60px; font-family: monospace; font-size: 12px; }

  /* Buttons */
  .btn { padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px; font-weight: 600; transition: opacity .15s; }
  .btn:hover { opacity: .85; }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-success { background: var(--ok); color: #000; }
  .btn-danger { background: var(--danger); color: #fff; }
  .btn-ghost { background: var(--border); color: var(--text); }
  .btn-sm { padding: 4px 10px; font-size: 12px; }
  .btn-group { display: flex; gap: 8px; margin-top: 16px; }

  /* Key display */
  .key-box { font-family: monospace; font-size: 12px; background: var(--surface); padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border); word-break: break-all; color: var(--accent2); }

  /* Notifications */
  #toast { position: fixed; bottom: 24px; right: 24px; background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px 18px; font-size: 13px; opacity: 0; transition: opacity .2s; pointer-events: none; z-index: 999; }
  #toast.show { opacity: 1; }

  /* Login */
  .login-wrap { display: flex; align-items: center; justify-content: center; min-height: 100vh; background: var(--bg); }
  .login-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 36px; width: 360px; }
  .login-card h2 { font-size: 20px; margin-bottom: 24px; color: var(--accent); }
  .login-error { color: var(--danger); font-size: 12px; margin-top: 8px; min-height: 16px; }

  /* Charts */
  .bar-chart { display: flex; flex-direction: column; gap: 8px; }
  .bar-row { display: flex; align-items: center; gap: 10px; }
  .bar-label { width: 130px; font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: right; }
  .bar-track { flex: 1; height: 10px; background: var(--border); border-radius: 5px; overflow: hidden; }
  .bar-fill { height: 100%; background: var(--accent); border-radius: 5px; transition: width .4s; }
  .bar-val { font-size: 11px; color: var(--muted); width: 80px; }

  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  @media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } .form-grid { grid-template-columns: 1fr; } }

  .empty { color: var(--muted); font-size: 13px; text-align: center; padding: 24px; }
  .mono { font-family: monospace; font-size: 12px; }
</style>
</head>
<body>

<!-- Login screen -->
<div id="login-screen" class="login-wrap">
  <div class="login-card">
    <h2>&#x1F511; Kiro Gateway</h2>
    <div class="form-group">
      <label>Senha do Admin</label>
      <input type="password" id="login-password" placeholder="Digite a senha..." onkeydown="if(event.key==='Enter')doLogin()">
    </div>
    <div class="login-error" id="login-error"></div>
    <div class="btn-group">
      <button class="btn btn-primary" onclick="doLogin()">Entrar</button>
    </div>
  </div>
</div>

<!-- Main app -->
<div id="app" class="layout" style="display:none">
  <aside class="sidebar">
    <div class="sidebar-logo">
      <h1>Kiro Gateway</h1>
      <span>Admin Panel</span>
    </div>
    <nav class="sidebar-nav">
      <div class="nav-item active" onclick="navigate('dashboard')">
        <span class="nav-icon">&#x1F4CA;</span> Dashboard
      </div>
      <div class="nav-item" onclick="navigate('accounts')">
        <span class="nav-icon">&#x1F511;</span> Contas Kiro
      </div>
      <div class="nav-item" onclick="navigate('clients')">
        <span class="nav-icon">&#x1F465;</span> Clientes / API Keys
      </div>
      <div class="nav-item" onclick="navigate('logs')">
        <span class="nav-icon">&#x1F4DC;</span> Logs de Uso
      </div>
    </nav>
  </aside>

  <main class="main">
    <!-- Dashboard -->
    <div id="page-dashboard" class="page active">
      <div class="page-title">Dashboard</div>
      <div class="stats-grid" id="stats-grid">
        <div class="stat-card"><div class="stat-label">Total Requests</div><div class="stat-value" id="s-total-req">—</div><div class="stat-sub">desde sempre</div></div>
        <div class="stat-card"><div class="stat-label">Total Tokens</div><div class="stat-value" id="s-total-tok">—</div><div class="stat-sub">desde sempre</div></div>
        <div class="stat-card"><div class="stat-label">Requests Hoje</div><div class="stat-value" id="s-today-req">—</div><div class="stat-sub">últimas 24h</div></div>
        <div class="stat-card"><div class="stat-label">Tokens Hoje</div><div class="stat-value" id="s-today-tok">—</div><div class="stat-sub">últimas 24h</div></div>
        <div class="stat-card"><div class="stat-label">Requests Semana</div><div class="stat-value" id="s-week-req">—</div><div class="stat-sub">últimos 7 dias</div></div>
        <div class="stat-card"><div class="stat-label">Tokens Semana</div><div class="stat-value" id="s-week-tok">—</div><div class="stat-sub">últimos 7 dias</div></div>
      </div>
      <div class="two-col">
        <div class="card">
          <div class="card-title">Por Modelo</div>
          <div class="bar-chart" id="chart-model"></div>
        </div>
        <div class="card">
          <div class="card-title">Por Cliente</div>
          <div class="bar-chart" id="chart-client"></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Por Conta Kiro</div>
        <div class="bar-chart" id="chart-account"></div>
      </div>
    </div>

    <!-- Accounts -->
    <div id="page-accounts" class="page">
      <div class="page-title">Contas Kiro</div>
      <div class="card">
        <div class="card-title">Adicionar Conta</div>
        <div class="form-grid">
          <div class="form-group">
            <label>Label (nome identificador)</label>
            <input id="a-label" placeholder="ex: conta1@email.com">
          </div>
          <div class="form-group">
            <label>Região AWS</label>
            <select id="a-region">
              <option value="us-east-1">us-east-1</option>
              <option value="eu-central-1">eu-central-1</option>
              <option value="ap-southeast-1">ap-southeast-1</option>
              <option value="us-west-2">us-west-2</option>
            </select>
          </div>
          <div class="form-group full">
            <label>Refresh Token (do Kiro IDE)</label>
            <textarea id="a-token" placeholder="Cole aqui o refreshToken..."></textarea>
          </div>
          <div class="form-group full">
            <label>Profile ARN (opcional)</label>
            <input id="a-arn" placeholder="arn:aws:codewhisperer:us-east-1:...">
          </div>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" onclick="addAccount()">Adicionar Conta</button>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Contas Cadastradas</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Label</th><th>Região</th><th>Profile ARN</th><th>Status</th><th>Último Uso</th><th>Ações</th></tr></thead>
            <tbody id="accounts-table"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Clients -->
    <div id="page-clients" class="page">
      <div class="page-title">Clientes / API Keys</div>
      <div class="card">
        <div class="card-title">Criar Nova API Key</div>
        <div class="form-grid">
          <div class="form-group">
            <label>Nome do Cliente</label>
            <input id="c-name" placeholder="ex: João Silva">
          </div>
          <div class="form-group">
            <label>Observação (opcional)</label>
            <input id="c-note" placeholder="ex: Claude Desktop pessoal">
          </div>
        </div>
        <div class="btn-group">
          <button class="btn btn-success" onclick="createClient()">Gerar API Key</button>
        </div>
        <div id="new-key-box" style="margin-top:14px;display:none">
          <label style="font-size:12px;color:var(--ok);margin-bottom:6px;display:block">&#x2705; Nova API Key gerada — copie agora, não será exibida novamente:</label>
          <div class="key-box" id="new-key-value"></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Clientes Cadastrados</div>
        <div class="card" style="background:var(--surface);margin-bottom:16px">
          <div style="font-size:13px;color:var(--muted);margin-bottom:8px">Como configurar no cliente:</div>
          <div style="font-family:monospace;font-size:12px;color:var(--accent2)">
            Base URL: <span id="base-url-display" style="color:var(--text)">—</span><br>
            API Key: a chave gerada acima
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Nome</th><th>API Key (parcial)</th><th>Nota</th><th>Status</th><th>Criado em</th><th>Último Uso</th><th>Ações</th></tr></thead>
            <tbody id="clients-table"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Logs -->
    <div id="page-logs" class="page">
      <div class="page-title">Logs de Uso</div>
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <div class="card-title" style="margin:0">Últimas 200 requisições</div>
          <button class="btn btn-ghost btn-sm" onclick="loadLogs()">&#x1F504; Atualizar</button>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Horário</th><th>Cliente</th><th>Conta Kiro</th><th>Modelo</th><th>Tokens In</th><th>Tokens Out</th><th>Total</th><th>Status</th></tr></thead>
            <tbody id="logs-table"></tbody>
          </table>
        </div>
      </div>
    </div>
  </main>
</div>

<div id="toast"></div>

<script>
let AUTH = '';

function doLogin() {
  const pw = document.getElementById('login-password').value;
  fetch('/admin/api/login', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({password: pw})
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      AUTH = pw;
      document.getElementById('login-screen').style.display = 'none';
      document.getElementById('app').style.display = 'flex';
      document.getElementById('base-url-display').textContent = window.location.origin;
      loadDashboard(); loadAccounts(); loadClients();
    } else {
      document.getElementById('login-error').textContent = 'Senha incorreta';
    }
  });
}

function authHeader() {
  return {'Authorization': 'Bearer ' + AUTH, 'Content-Type': 'application/json'};
}

function toast(msg, isErr) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.borderColor = isErr ? 'var(--danger)' : 'var(--ok)';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

function navigate(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  event.currentTarget.classList.add('active');
  if (page === 'logs') loadLogs();
  if (page === 'dashboard') loadDashboard();
}

function fmt(n) {
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(n);
}

function tsToStr(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString('pt-BR');
}

// Dashboard
function loadDashboard() {
  fetch('/admin/api/stats', {headers: authHeader()})
    .then(r => r.json()).then(d => {
      document.getElementById('s-total-req').textContent = fmt(d.total.requests);
      document.getElementById('s-total-tok').textContent = fmt(d.total.tokens);
      document.getElementById('s-today-req').textContent = fmt(d.today.requests);
      document.getElementById('s-today-tok').textContent = fmt(d.today.tokens);
      document.getElementById('s-week-req').textContent = fmt(d.week.requests);
      document.getElementById('s-week-tok').textContent = fmt(d.week.tokens);
      renderBarChart('chart-model', d.by_model, 'model', 'tokens');
      renderBarChart('chart-client', d.by_client, 'client_name', 'tokens');
      renderBarChart('chart-account', d.by_account, 'account_label', 'tokens');
    });
}

function renderBarChart(elId, rows, labelKey, valKey) {
  const el = document.getElementById(elId);
  if (!rows || !rows.length) { el.innerHTML = '<div class="empty">Sem dados ainda</div>'; return; }
  const max = Math.max(...rows.map(r => r[valKey] || 0)) || 1;
  el.innerHTML = rows.map(r => `
    <div class="bar-row">
      <div class="bar-label" title="${r[labelKey]}">${r[labelKey] || '—'}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.round((r[valKey]/max)*100)}%"></div></div>
      <div class="bar-val">${fmt(r[valKey])} tok / ${r.reqs} req</div>
    </div>`).join('');
}

// Accounts
function loadAccounts() {
  fetch('/admin/api/kiro-accounts', {headers: authHeader()})
    .then(r => r.json()).then(accounts => {
      const tb = document.getElementById('accounts-table');
      if (!accounts.length) { tb.innerHTML = '<tr><td colspan="6" class="empty">Nenhuma conta ainda</td></tr>'; return; }
      tb.innerHTML = accounts.map(a => `
        <tr>
          <td><strong>${a.label}</strong></td>
          <td class="mono">${a.region}</td>
          <td class="mono" style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis" title="${a.profile_arn||''}">${a.profile_arn ? a.profile_arn.substring(0,40)+'…' : '—'}</td>
          <td><span class="badge ${a.enabled ? 'badge-ok':'badge-off'}">${a.enabled ? 'Ativa':'Inativa'}</span></td>
          <td>${tsToStr(a.last_used)}</td>
          <td>
            <button class="btn btn-ghost btn-sm" onclick="toggleAccount(${a.id}, ${a.enabled})">${a.enabled ? 'Desativar':'Ativar'}</button>
            <button class="btn btn-danger btn-sm" onclick="deleteAccount(${a.id})">Remover</button>
          </td>
        </tr>`).join('');
    });
}

function addAccount() {
  const label = document.getElementById('a-label').value.trim();
  const token = document.getElementById('a-token').value.trim();
  const arn   = document.getElementById('a-arn').value.trim();
  const region = document.getElementById('a-region').value;
  if (!label || !token) { toast('Label e Token são obrigatórios', true); return; }
  fetch('/admin/api/kiro-accounts', {
    method: 'POST', headers: authHeader(),
    body: JSON.stringify({label, refresh_token: token, profile_arn: arn||null, region})
  }).then(r => r.json()).then(d => {
    if (d.id) { toast('Conta adicionada!'); loadAccounts(); document.getElementById('a-label').value=''; document.getElementById('a-token').value=''; document.getElementById('a-arn').value=''; }
    else toast(d.detail || 'Erro', true);
  });
}

function toggleAccount(id, enabled) {
  fetch(`/admin/api/kiro-accounts/${id}/toggle`, {
    method: 'POST', headers: authHeader(),
    body: JSON.stringify({enabled: !enabled})
  }).then(() => { toast('Atualizado'); loadAccounts(); });
}

function deleteAccount(id) {
  if (!confirm('Remover esta conta?')) return;
  fetch(`/admin/api/kiro-accounts/${id}`, {method:'DELETE', headers: authHeader()})
    .then(() => { toast('Removida'); loadAccounts(); });
}

// Clients
function loadClients() {
  fetch('/admin/api/clients', {headers: authHeader()})
    .then(r => r.json()).then(clients => {
      const tb = document.getElementById('clients-table');
      if (!clients.length) { tb.innerHTML = '<tr><td colspan="7" class="empty">Nenhum cliente ainda</td></tr>'; return; }
      tb.innerHTML = clients.map(c => `
        <tr>
          <td><strong>${c.name}</strong></td>
          <td class="mono">${c.api_key.substring(0,16)}…</td>
          <td>${c.note||'—'}</td>
          <td><span class="badge ${c.enabled ? 'badge-ok':'badge-off'}">${c.enabled ? 'Ativo':'Inativo'}</span></td>
          <td>${tsToStr(c.created_at)}</td>
          <td>${tsToStr(c.last_used)}</td>
          <td>
            <button class="btn btn-ghost btn-sm" onclick="toggleClient(${c.id}, ${c.enabled})">${c.enabled ? 'Desativar':'Ativar'}</button>
            <button class="btn btn-danger btn-sm" onclick="deleteClient(${c.id})">Remover</button>
          </td>
        </tr>`).join('');
    });
}

function createClient() {
  const name = document.getElementById('c-name').value.trim();
  const note = document.getElementById('c-note').value.trim();
  if (!name) { toast('Nome é obrigatório', true); return; }
  fetch('/admin/api/clients', {
    method: 'POST', headers: authHeader(),
    body: JSON.stringify({name, note})
  }).then(r => r.json()).then(d => {
    if (d.api_key) {
      document.getElementById('new-key-value').textContent = d.api_key;
      document.getElementById('new-key-box').style.display = 'block';
      toast('API Key gerada!');
      loadClients();
      document.getElementById('c-name').value = '';
      document.getElementById('c-note').value = '';
    } else toast(d.detail || 'Erro', true);
  });
}

function toggleClient(id, enabled) {
  fetch(`/admin/api/clients/${id}/toggle`, {
    method: 'POST', headers: authHeader(),
    body: JSON.stringify({enabled: !enabled})
  }).then(() => { toast('Atualizado'); loadClients(); });
}

function deleteClient(id) {
  if (!confirm('Remover este cliente? Isto invalidará a API key dele.')) return;
  fetch(`/admin/api/clients/${id}`, {method:'DELETE', headers: authHeader()})
    .then(() => { toast('Removido'); loadClients(); });
}

// Logs
function loadLogs() {
  fetch('/admin/api/logs', {headers: authHeader()})
    .then(r => r.json()).then(logs => {
      const tb = document.getElementById('logs-table');
      if (!logs.length) { tb.innerHTML = '<tr><td colspan="8" class="empty">Sem logs ainda</td></tr>'; return; }
      tb.innerHTML = logs.map(l => `
        <tr>
          <td class="mono">${tsToStr(l.ts)}</td>
          <td>${l.client_name||'—'}</td>
          <td>${l.account_label||'—'}</td>
          <td class="mono">${l.model||'—'}</td>
          <td class="mono">${l.input_tokens}</td>
          <td class="mono">${l.output_tokens}</td>
          <td class="mono"><strong>${l.total_tokens}</strong></td>
          <td><span class="badge ${l.status==='ok'?'badge-ok':'badge-warn'}">${l.status}</span></td>
        </tr>`).join('');
    });
}

// Auto-refresh dashboard every 30s
setInterval(() => { if (document.getElementById('page-dashboard').classList.contains('active')) loadDashboard(); }, 30000);
</script>
</body>
</html>"""
