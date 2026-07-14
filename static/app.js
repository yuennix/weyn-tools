(() => {
  let selectedMethod = '1';
  let evtSource      = null;
  let knownHits      = new Set();
  let totalHits      = 0;
  let _stopping      = false;
  let _starting      = false;

  // ── DOM refs ──
  const startBtn       = document.getElementById('startBtn');
  const stopBtn        = document.getElementById('stopBtn');
  const findChatBtn    = document.getElementById('findChatBtn');
  const chatResults    = document.getElementById('chatResults');
  const statusBar      = document.getElementById('statusBar');
  const methodBadge    = document.getElementById('methodBadge');
  const hitsFeed       = document.getElementById('hitsFeed');
  const hitsCount      = document.getElementById('hitsCount');
  const methodBtn1     = document.getElementById('methodBtn1');
  const methodBtn2     = document.getElementById('methodBtn2');
  const methodBtn3     = document.getElementById('methodBtn3');
  const yearRangeGroup = document.getElementById('yearRangeGroup');
  const minLabel       = document.getElementById('minLabel');
  const minInput       = document.getElementById('min_followers');

  const statIds = ['hits','good','bad_insta','bad_email','scanned','total'];
  const timerEl = document.getElementById('s-timer');

  function formatElapsed(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = String(Math.floor(sec / 3600)).padStart(2, '0');
    const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0');
    const s = String(sec % 60).padStart(2, '0');
    return `${h}:${m}:${s}`;
  }

  // ── Method selector ──
  function selectMethod(m) {
    selectedMethod = m;
    methodBtn1.classList.toggle('active', m === '1');
    methodBtn2.classList.toggle('active', m === '2');
    methodBtn3.classList.toggle('active', m === '3');
    yearRangeGroup.style.display = m === '1' ? '' : 'none';
    if (m === '2') {
      minLabel.textContent = 'WORKERS (fixed 500)';
      minInput.min = '0';
      minInput.value = 500;
      minInput.disabled = true;
    } else if (m === '3') {
      minLabel.textContent = 'THREADS (fixed 200)';
      minInput.min = '0';
      minInput.value = 200;
      minInput.disabled = true;
    } else {
      minLabel.textContent = 'MIN FOLLOWERS';
      minInput.min = '0';
      minInput.disabled = false;
    }
  }
  methodBtn1.addEventListener('click', () => { if (!startBtn.disabled) selectMethod('1'); });
  methodBtn2.addEventListener('click', () => { if (!startBtn.disabled) selectMethod('2'); });
  methodBtn3.addEventListener('click', () => { if (!startBtn.disabled) selectMethod('3'); });

  // ── Strip iOS smart-punctuation from token/chat_id strings ──
  function sanitizeInput(str) {
    return String(str)
      .replace(/[\u2013\u2014\u2015]/g, '-')   // en-dash / em-dash → hyphen
      .replace(/[\u2018\u2019]/g, "'")          // curly single quotes → straight
      .replace(/[\u201C\u201D]/g, '"')          // curly double quotes → straight
      .replace(/\u00A0/g, ' ')                  // non-breaking space → space
      .trim();
  }

  // ── Persist token & chat_id across refreshes ──
  const tokenEl  = document.getElementById('token');
  const chatIdEl = document.getElementById('chat_id');

  if (localStorage.getItem('tg_token'))   tokenEl.value  = localStorage.getItem('tg_token');
  if (localStorage.getItem('tg_chat_id')) chatIdEl.value = localStorage.getItem('tg_chat_id');

  tokenEl.addEventListener('input',  () => localStorage.setItem('tg_token',   tokenEl.value));
  chatIdEl.addEventListener('input', () => localStorage.setItem('tg_chat_id', chatIdEl.value));

  // ── SSE stream ──
  let _sseRetryTimer = null;

  function startSSE() {
    if (evtSource) { evtSource.close(); evtSource = null; }
    if (_sseRetryTimer) { clearTimeout(_sseRetryTimer); _sseRetryTimer = null; }
    evtSource = new EventSource('/api/stats');
    evtSource.onmessage = (e) => {
      const d = JSON.parse(e.data);
      updateStats(d);
      updateHits(d.recent_hits || []);
      // Suppress button flips during transitions:
      // • _stopping: stop clicked but backend thread still shutting down
      // • _starting: start clicked but backend thread not yet confirmed running
      if (_stopping && d.running)  return;
      if (_starting && !d.running) return;
      if (d.running)  _starting = false;
      if (!d.running) _stopping = false;
      setRunning(d.running);
    };
    evtSource.addEventListener('expired', () => {
      evtSource.close();
      window.location.href = '/gate';
    });
    evtSource.onerror = () => {
      if (evtSource) { evtSource.close(); evtSource = null; }
      _sseRetryTimer = setTimeout(startSSE, 3000);
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
    if (timerEl) timerEl.textContent = formatElapsed(d.elapsed);
    if (d.method && !_starting) {
      const labels = { '1': 'M1 · STANDARD', '2': 'M2 · HI2 ALT', '3': 'M3 · HI2.IN/TELEGMAIL' };
      const classes = { '1': 'badge', '2': 'badge badge-m2', '3': 'badge badge-m3' };
      methodBadge.textContent = labels[d.method] || ('M' + d.method);
      methodBadge.className   = classes[d.method] || 'badge';
    }
  }

  function updateHits(hits) {
    if (!hits || hits.length === 0) return;
    hits.forEach(raw => {
      // M2: {"e":email,"p":posts}  M3: {"e":email,"m":method,"u":igUsername}  M1: plain string
      let email, posts = null, methodLabel = null, igUsername = null;
      try {
        const obj = JSON.parse(raw);
        email       = obj.e;
        posts       = obj.p !== undefined ? obj.p : null;
        methodLabel = obj.m !== undefined ? obj.m : null;
        igUsername  = (obj.u && obj.u.length) ? obj.u : null;
      } catch (_) {
        email = raw;
      }

      if (knownHits.has(email)) return;
      knownHits.add(email);
      totalHits++;
      hitsCount.textContent = totalHits;

      const emailUser = email.includes('@') ? email.split('@')[0] : email;
      const profileHandle = igUsername || emailUser;
      const empty = hitsFeed.querySelector('.hits-empty');
      if (empty) empty.remove();

      const postsBadge = posts !== null
        ? `<span class="hit-posts-badge">${escHtml(String(posts))} posts</span>`
        : methodLabel !== null
          ? `<span class="hit-posts-badge" style="color:#f59e0b;border-color:#f59e0b">${escHtml(methodLabel)}</span>`
          : '';

      const card = document.createElement('div');
      card.className = 'hit-card';
      card.innerHTML = `
        <div class="hit-username">@${escHtml(profileHandle)}${postsBadge}</div>
        <div class="hit-email">${escHtml(email)}</div>
        <div class="hit-meta">${new Date().toLocaleTimeString()}</div>`;
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
    const token     = sanitizeInput(document.getElementById('token').value);
    const chat_id   = sanitizeInput(document.getElementById('chat_id').value);
    const min_value = minInput.value;

    if (!token || !chat_id) {
      alert('Bot Token and Chat ID are required.');
      return;
    }

    _starting = true;
    _stopping = false;
    setRunning(true);
    const _badgeLabels  = { '1': 'M1 · STANDARD', '2': 'M2 · HI2 ALT', '3': 'M3 · HI2.IN/TELEGMAIL' };
    const _badgeClasses = { '1': 'badge', '2': 'badge badge-m2', '3': 'badge badge-m3' };
    methodBadge.textContent = _badgeLabels[selectedMethod]  || ('M' + selectedMethod);
    methodBadge.className   = _badgeClasses[selectedMethod] || 'badge';

    knownHits.clear();
    totalHits = 0;
    hitsCount.textContent = '0';
    hitsFeed.innerHTML = '<div class="hits-empty">Scanning… hits will appear here.</div>';
    statIds.forEach(k => {
      const el = document.getElementById('s-' + k);
      if (el) el.textContent = '0';
    });
    if (timerEl) timerEl.textContent = '00:00:00';

    try {
      const yearEl       = document.getElementById('year_choice');
      const year_choice  = yearEl && yearEl.value ? yearEl.value : null;
      const res = await fetch('/api/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ method: selectedMethod, token, chat_id, min_followers: min_value, year_choice })
      });
      const data = await res.json();
      if (!res.ok) {
        _starting = false;
        setRunning(false);
        alert(data.error || 'Failed to start.');
        return;
      }
    } catch (err) {
      _starting = false;
      setRunning(false);
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

  // ── Stop button ──
  stopBtn.addEventListener('click', async () => {
    _stopping = true;
    setRunning(false);
    try {
      await fetch('/api/stop', { method: 'POST' });
    } catch (err) {
      console.error(err);
    }
  });

  // ── Init ──
  startSSE();
})();
