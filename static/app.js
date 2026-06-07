(() => {
  const selectedMethod = '1';
  let evtSource      = null;
  let knownHits      = new Set();
  let totalHits      = 0;
  let countdownTimer = null;
  let _myKey         = '';

  // ── DOM refs ──
  const startBtn    = document.getElementById('startBtn');
  const stopBtn     = document.getElementById('stopBtn');
  const findChatBtn = document.getElementById('findChatBtn');
  const chatResults = document.getElementById('chatResults');
  const statusBar   = document.getElementById('statusBar');
  const methodBadge = document.getElementById('methodBadge');
  const hitsFeed    = document.getElementById('hitsFeed');
  const hitsCount   = document.getElementById('hitsCount');
  const tgStatusEl  = document.getElementById('tgStatus');
  const countdownEl = document.getElementById('keyCountdown');

  const statIds = ['hits','good','bad_insta','bad_email','taken','limit','total','verified'];

  // ── Persist token & chat_id ──
  const tokenEl  = document.getElementById('token');
  const chatIdEl = document.getElementById('chat_id');
  if (localStorage.getItem('tg_token'))   tokenEl.value  = localStorage.getItem('tg_token');
  if (localStorage.getItem('tg_chat_id')) chatIdEl.value = localStorage.getItem('tg_chat_id');
  function saveToken()  { localStorage.setItem('tg_token',   tokenEl.value); }
  function saveChatId() { localStorage.setItem('tg_chat_id', chatIdEl.value); }
  tokenEl.addEventListener('input',  saveToken);
  tokenEl.addEventListener('change', saveToken);
  tokenEl.addEventListener('paste',  () => setTimeout(saveToken,  50));
  chatIdEl.addEventListener('input',  saveChatId);
  chatIdEl.addEventListener('change', saveChatId);
  chatIdEl.addEventListener('paste',  () => setTimeout(saveChatId, 50));

  // ── Tab switching ──
  window.switchTab = function(tab) {
    document.getElementById('paneConfig').classList.toggle('active', tab === 'config');
    document.getElementById('paneStats').classList.toggle('active', tab === 'stats');
    document.getElementById('tabConfig').classList.toggle('active', tab === 'config');
    document.getElementById('tabStats').classList.toggle('active', tab === 'stats');
  };

  // ── Key info + countdown ──
  async function loadKeyInfo() {
    try {
      const res  = await fetch('/api/key_info');
      const data = await res.json();
      _myKey = data.key || '';
      if (document.getElementById('keyName'))  document.getElementById('keyName').textContent  = data.name || '—';
      if (document.getElementById('keyValue')) document.getElementById('keyValue').textContent = _myKey || '—';
      if (data.expires_at) startCountdown(new Date(data.expires_at + 'Z'));
    } catch (_) {}
  }

  function startCountdown(expiresAt) {
    if (countdownTimer) clearInterval(countdownTimer);
    function tick() {
      const diff = expiresAt - new Date();
      if (diff <= 0) {
        countdownEl.textContent = '⚠ Key expired — redirecting…';
        countdownEl.className = 'key-countdown expired';
        clearInterval(countdownTimer);
        localStorage.removeItem('weyn_auth_key');
        setTimeout(() => { window.location.href = '/gate'; }, 3000);
        return;
      }
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      const label = h > 0 ? `${h}h ${m}m ${s}s` : m > 0 ? `${m}m ${s}s` : `${s}s`;
      countdownEl.textContent = `⏱ Key expires in: ${label}`;
      countdownEl.className = 'key-countdown' + (diff < 600000 ? ' warning' : '');
    }
    tick();
    countdownTimer = setInterval(tick, 1000);
  }

  // ── Copy key ──
  window.copyKey = function() {
    if (!_myKey) return;
    const btn = document.getElementById('copyKeyBtn');
    navigator.clipboard.writeText(_myKey).then(() => {
      btn.textContent = '✓  COPIED!';
      btn.classList.add('copied');
      setTimeout(() => { btn.textContent = '📋 \u00a0COPY KEY'; btn.classList.remove('copied'); }, 2000);
    }).catch(() => {
      btn.textContent = '✗  Copy failed';
      setTimeout(() => { btn.textContent = '📋 \u00a0COPY KEY'; }, 2000);
    });
  };

  // ── Revoke device ──
  window.revokeDevice = async function() {
    const btn = document.getElementById('revokeBtn');
    if (!confirm('This will unbind your key from this device. You will be logged out and can log in from a different device. Continue?')) return;
    btn.disabled = true;
    btn.textContent = 'Revoking…';
    try {
      const res = await fetch('/api/revoke_device', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        localStorage.removeItem('weyn_auth_key');
        localStorage.removeItem('weyn_device_id');
        window.location.href = '/gate';
      } else {
        alert(data.error || 'Failed to revoke device.');
        btn.disabled = false;
        btn.textContent = '🔓 \u00a0REVOKE THIS DEVICE';
      }
    } catch (e) {
      alert('Connection error: ' + e.message);
      btn.disabled = false;
      btn.textContent = '🔓 \u00a0REVOKE THIS DEVICE';
    }
  };

  // ── Logout ──
  window.logout = async function() {
    localStorage.removeItem('weyn_auth_key');
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/gate';
  };

  // ── SSE stream (always running — updates DOM regardless of active tab) ──
  function startSSE() {
    if (evtSource) evtSource.close();
    evtSource = new EventSource('/api/stats');
    evtSource.onmessage = (e) => {
      const d = JSON.parse(e.data);
      updateStats(d);
      updateHits(d.recent_hits || []);
      setRunning(d.running);
    };
    evtSource.onerror = () => {
      // browser handles reconnect automatically
    };
  }

  function updateStats(d) {
    if (d.tg_status === 'error' && d.tg_error) {
      tgStatusEl.textContent = '⚠ TG: ' + d.tg_error;
      tgStatusEl.className = 'tg-status-bar tg-err';
    } else if (d.tg_status === 'ok') {
      tgStatusEl.textContent = '✓ TG: Message sent';
      tgStatusEl.className = 'tg-status-bar tg-ok';
    } else {
      tgStatusEl.textContent = '';
      tgStatusEl.className = 'tg-status-bar';
    }
    statIds.forEach(key => {
      const el = document.getElementById('s-' + key);
      if (!el) return;
      const newVal = d[key] ?? 0;
      const oldVal = parseInt(el.textContent) || 0;
      el.textContent = newVal;
      if (newVal > oldVal) {
        const card = el.closest('.stat-card');
        card.classList.add('bump');
        setTimeout(() => card.classList.remove('bump'), 300);
      }
    });
    if (d.method) {
      const labels = { '1':'M1','2':'M2','3':'M3','4':'M4 ★' };
      methodBadge.textContent = labels[d.method] || d.method;
    }
    // Flash the LIVE STATS tab when running and stats change
    const statsTab = document.getElementById('tabStats');
    if (d.running && statsTab && !statsTab.classList.contains('active')) {
      statsTab.classList.add('live');
    } else {
      statsTab && statsTab.classList.remove('live');
    }
  }

  function updateHits(hits) {
    if (!hits || hits.length === 0) return;
    hits.forEach(email => {
      if (knownHits.has(email)) return;
      knownHits.add(email);
      totalHits++;
      hitsCount.textContent = totalHits;
      const username = email.includes('@') ? email.split('@')[0] : email;
      const empty = hitsFeed.querySelector('.hits-empty');
      if (empty) empty.remove();
      const card = document.createElement('div');
      card.className = 'hit-card';
      card.innerHTML = `
        <div class="hit-username">@${escHtml(username)}</div>
        <div class="hit-email">${escHtml(email)}</div>
        <div class="hit-meta">
          <a href="https://www.instagram.com/${escHtml(username)}" target="_blank"
             style="color:var(--cyan);text-decoration:none;">instagram.com/${escHtml(username)}</a>
          &nbsp;·&nbsp;${new Date().toLocaleTimeString()}
        </div>`;
      hitsFeed.insertBefore(card, hitsFeed.firstChild);
    });
  }

  function setRunning(running) {
    startBtn.disabled = running;
    stopBtn.disabled  = !running;
    if (running) {
      statusBar.textContent = '● SCANNING...';
      statusBar.classList.add('running');
    } else {
      statusBar.textContent = 'IDLE';
      statusBar.classList.remove('running');
    }
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Start ──
  startBtn.addEventListener('click', async () => {
    const token         = tokenEl.value.trim();
    const chat_id       = chatIdEl.value.trim();
    const min_followers = document.getElementById('min_followers').value;
    if (!token || !chat_id) { alert('Bot Token and Chat ID are required.'); return; }

    knownHits.clear(); totalHits = 0;
    hitsCount.textContent = '0';
    hitsFeed.innerHTML = '<div class="hits-empty">Scanning… hits will appear here.</div>';
    statIds.forEach(k => { const el = document.getElementById('s-'+k); if (el) el.textContent='0'; });

    try {
      const res  = await fetch('/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ method: selectedMethod, token, chat_id, min_followers })
      });
      const data = await res.json();
      if (!res.ok) { alert(data.error || 'Failed to start.'); return; }
      setRunning(true);
    } catch (err) { alert('Connection error: ' + err.message); }
  });

  // ── Stop ──
  stopBtn.addEventListener('click', async () => {
    try { await fetch('/api/stop', { method: 'POST' }); setRunning(false); }
    catch (err) { console.error(err); }
  });

  // ── Find Chat ID ──
  findChatBtn.addEventListener('click', async () => {
    const token = tokenEl.value.trim();
    if (!token) {
      chatResults.innerHTML = '<div class="chat-result-err">Enter your Bot Token first.</div>';
      return;
    }
    findChatBtn.disabled = true;
    chatResults.innerHTML = '<div class="chat-result-info">Scanning for groups…</div>';
    try {
      const res  = await fetch('/api/find_chat_id', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token })
      });
      const data = await res.json();
      if (!data.ok) {
        chatResults.innerHTML = `<div class="chat-result-err">✗ ${escHtml(data.error)}</div>`;
      } else if (!data.chats.length) {
        chatResults.innerHTML = '<div class="chat-result-info">No groups found. Send a message in your group then try again.</div>';
      } else {
        chatResults.innerHTML = data.chats.map(c => `
          <div class="chat-result-item" data-id="${c.id}">
            <span class="chat-result-name">${escHtml(c.name)}</span>
            <span class="chat-result-type">${escHtml(c.type)}</span>
            <span class="chat-result-id">${c.id}</span>
            <button class="chat-use-btn" onclick="document.getElementById('chat_id').value='${c.id}';document.getElementById('chatResults').innerHTML='';">USE</button>
          </div>`).join('');
      }
    } catch (err) {
      chatResults.innerHTML = `<div class="chat-result-err">✗ ${escHtml(err.message)}</div>`;
    }
    findChatBtn.disabled = false;
  });

  // ── Load revoke device visibility ──
  async function loadRevokeVisibility() {
    try {
      const res  = await fetch('/api/settings');
      const data = await res.json();
      const wrap = document.getElementById('revokeDeviceWrap');
      if (wrap) wrap.style.display = data.revoke_device_enabled ? '' : 'none';
    } catch (_) {}
  }

  // ── Init ──
  startSSE();
  loadKeyInfo();
  loadRevokeVisibility();
})();
