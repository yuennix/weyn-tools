(() => {
  let selectedMethod = '1';
  let evtSource      = null;
  let knownHits      = new Set();
  let totalHits      = 0;

  // ── DOM refs ──
  const startBtn    = document.getElementById('startBtn');
  const stopBtn     = document.getElementById('stopBtn');
  const testBtn     = document.getElementById('testBtn');
  const testStatus  = document.getElementById('testStatus');
  const findChatBtn = document.getElementById('findChatBtn');
  const chatResults = document.getElementById('chatResults');
  const statusBar   = document.getElementById('statusBar');
  const methodBadge = document.getElementById('methodBadge');
  const hitsFeed    = document.getElementById('hitsFeed');
  const hitsCount   = document.getElementById('hitsCount');

  const statIds = ['hits','good','bad_insta','bad_email','taken','limit','total','verified'];

  // ── Method tabs ──
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      selectedMethod = tab.dataset.method;
    });
  });

  // ── SSE stream ──
  function startSSE() {
    if (evtSource) { evtSource.close(); }
    evtSource = new EventSource('/api/stats');
    evtSource.onmessage = (e) => {
      const d = JSON.parse(e.data);
      updateStats(d);
      updateHits(d.recent_hits || []);
      setRunning(d.running);
    };
    evtSource.onerror = () => {
      // reconnect handled automatically by browser
    };
  }

  const tgStatusEl = document.getElementById('tgStatus');

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
             style="color:var(--cyan);text-decoration:none;">
            instagram.com/${escHtml(username)}
          </a>
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
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;');
  }

  // ── Start button ──
  startBtn.addEventListener('click', async () => {
    const token        = document.getElementById('token').value.trim();
    const chat_id      = document.getElementById('chat_id').value.trim();
    const min_followers= document.getElementById('min_followers').value;

    if (!token || !chat_id) {
      alert('Bot Token and Chat ID are required.');
      return;
    }

    knownHits.clear();
    totalHits = 0;
    hitsCount.textContent = '0';
    hitsFeed.innerHTML = '<div class="hits-empty">Scanning… hits will appear here.</div>';
    statIds.forEach(k => {
      const el = document.getElementById('s-' + k);
      if (el) el.textContent = '0';
    });

    try {
      const res = await fetch('/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ method: selectedMethod, token, chat_id, min_followers })
      });
      const data = await res.json();
      if (!res.ok) { alert(data.error || 'Failed to start.'); return; }
      setRunning(true);
    } catch (err) {
      alert('Connection error: ' + err.message);
    }
  });

  // ── Find Chat ID button ──
  findChatBtn.addEventListener('click', async () => {
    const token = document.getElementById('token').value.trim();
    if (!token) {
      chatResults.innerHTML = '<div class="chat-result-err">Enter your Bot Token first.</div>';
      return;
    }
    findChatBtn.disabled = true;
    chatResults.innerHTML = '<div class="chat-result-info">Scanning for groups… send any message in your group first if nothing appears.</div>';
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
        chatResults.innerHTML = '<div class="chat-result-info">No groups found. Send any message in your group, then try again.</div>';
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

  // ── Test Telegram button ──
  testBtn.addEventListener('click', async () => {
    const token   = document.getElementById('token').value.trim();
    const chat_id = document.getElementById('chat_id').value.trim();
    if (!token || !chat_id) {
      testStatus.textContent = '✗ Enter Bot Token and Chat ID first.';
      testStatus.className = 'test-status fail';
      return;
    }
    testBtn.disabled = true;
    testStatus.textContent = 'Sending test message…';
    testStatus.className = 'test-status';
    try {
      const res  = await fetch('/api/test_telegram', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, chat_id })
      });
      const data = await res.json();
      if (data.ok) {
        testStatus.textContent = '✓ Message sent! Check your group.';
        testStatus.className = 'test-status ok';
      } else {
        testStatus.textContent = '✗ ' + (data.error || 'Failed');
        testStatus.className = 'test-status fail';
      }
    } catch (err) {
      testStatus.textContent = '✗ ' + err.message;
      testStatus.className = 'test-status fail';
    }
    testBtn.disabled = false;
  });

  // ── Stop button ──
  stopBtn.addEventListener('click', async () => {
    try {
      await fetch('/api/stop', { method: 'POST' });
      setRunning(false);
    } catch (err) {
      console.error(err);
    }
  });

  // ── Init ──
  startSSE();
})();
