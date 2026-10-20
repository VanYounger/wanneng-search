/* 万能搜索 · 前端逻辑 */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };

  const scroll = $('#scroll');
  const stream = $('#stream');
  const input = $('#input');
  const sendBtn = $('#send');
  const deepBox = $('#deep');
  const hint = $('#hint');
  const toastEl = $('#toast');

  let mode = 'ask';
  let busy = false;
  let controller = null;
  let pendingMarkdown = '';
  let renderScheduled = false;

  const EXAMPLES = [
    '最近 OpenAI 发布了什么新模型？',
    '2026 年诺贝尔物理学奖给了谁',
    '国内能用哪些免费的大模型 API',
    '帮我找几篇关于 RAG 的最新论文',
    'iPhone 17 在国外的评测怎么样',
    '上海公积金贷款额度怎么算',
  ];

  marked.setOptions({ gfm: true, breaks: true });

  /* ---------------- 小工具 ---------------- */
  let toastTimer = null;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('show'), 1800);
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function sanitize(html) {
    return html
      .replace(/<script[\s\S]*?<\/script>/gi, '')
      .replace(/<iframe[\s\S]*?<\/iframe>/gi, '')
      .replace(/\son\w+\s*=\s*"[^"]*"/gi, '')
      .replace(/\son\w+\s*=\s*'[^']*'/gi, '');
  }

  function mdToHtml(md, maxCite) {
    let html;
    try {
      html = marked.parse(md);
    } catch (e) {
      html = escapeHtml(md).replace(/\n/g, '<br>');
    }
    html = sanitize(html);
    // 把 [1] 这种引用编号变成小标签
    if (maxCite) {
      html = html.replace(/\[(\d{1,2})\](?!\()/g, (m, n) => {
        const k = parseInt(n, 10);
        return k >= 1 && k <= maxCite ? `<span class="cite" data-n="${k}">${k}</span>` : m;
      });
    }
    return html;
  }

  function hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return url; }
  }

  function nearBottom() {
    return scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 160;
  }

  function autoScroll(force) {
    if (force || nearBottom()) {
      requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
    }
  }

  /* ---------------- 历史记录 ---------------- */
  function loadHistory() {
    try { return JSON.parse(localStorage.getItem('wss_history') || '[]'); } catch (e) { return []; }
  }
  function saveHistory(q) {
    if (!q) return;
    let h = loadHistory().filter(x => x !== q);
    h.unshift(q);
    h = h.slice(0, 30);
    localStorage.setItem('wss_history', JSON.stringify(h));
    renderHistory();
  }
  function renderHistory() {
    const box = $('#history');
    box.innerHTML = '';
    const h = loadHistory();
    if (!h.length) { box.appendChild(el('div', 'hist-empty', '还没有提问记录')); return; }
    h.forEach(q => {
      const item = el('div', 'hist-item', escapeHtml(q));
      item.title = q;
      item.onclick = () => { input.value = q; setMode('ask'); autoGrow(); input.focus(); };
      box.appendChild(item);
    });
  }

  /* ---------------- 消息渲染 ---------------- */
  function addAsk(text) {
    const wrap = el('div', 'msg ask');
    wrap.appendChild(el('div', 'bubble', escapeHtml(text)));
    stream.appendChild(wrap);
    autoScroll(true);
  }

  function addAnswer() {
    const wrap = el('div', 'msg answer');
    wrap.appendChild(el('div', 'avatar', '搜'));
    const body = el('div', 'body');
    const status = el('div', 'status', '<span class="spinner"></span><span class="stext">准备中…</span>');
    const prose = el('div', 'prose');
    const sources = el('div', 'sources');
    const actions = el('div', 'answer-actions');
    body.append(status, prose, sources, actions);
    wrap.appendChild(body);
    stream.appendChild(wrap);
    autoScroll(true);
    return { wrap, status, prose, sources, actions };
  }

  function setStatus(node, text, spinning = true) {
    node.innerHTML = '';
    if (spinning) node.appendChild(el('span', 'spinner'));
    node.appendChild(el('span', 'stext', escapeHtml(text)));
  }

  function renderSources(box, items) {
    if (!items || !items.length) { box.innerHTML = ''; return; }
    const readN = items.filter(i => i.read).length;
    const collapsed = box.dataset.collapsed === '1';
    box.innerHTML = '';
    box.classList.toggle('collapsed', collapsed);

    const head = el('div', 'sources-head');
    head.innerHTML = `<span class="arrow">▼</span> <b>${items.length} 条来源</b>
      <span style="color:var(--ink-3)">· 已精读 ${readN} 篇</span>`;
    head.onclick = () => {
      const c = box.classList.toggle('collapsed');
      box.dataset.collapsed = c ? '1' : '0';
    };
    box.appendChild(head);

    const list = el('div', 'src-list');
    items.forEach(it => {
      const row = el('div', 'src');
      row.appendChild(el('div', 'num', String(it.idx)));
      const meta = el('div', 'meta');
      const a = el('a', 't', escapeHtml(it.title || hostOf(it.url)));
      a.href = it.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      meta.append(a, el('div', 'u', escapeHtml(hostOf(it.url))));
      row.appendChild(meta);
      const chars = it.chars || 0;
      const sizeTxt = chars >= 1000 ? `已读 ${(chars / 1000).toFixed(1)}k字` : `已读 ${chars}字`;
      row.appendChild(el('span', 'badge' + (it.read ? '' : ' snip'), it.read ? sizeTxt : '仅摘要'));
      list.appendChild(row);
    });
    box.appendChild(list);
  }

  function bindCite(prose, sources) {
    prose.addEventListener('click', e => {
      const t = e.target.closest('.cite');
      if (!t) return;
      const n = parseInt(t.dataset.n, 10);
      const item = (sources || []).find(s => s.idx === n);
      if (item) window.open(item.url, '_blank', 'noopener');
    });
  }

  /* ---------------- 发送 ---------------- */
  function autoGrow() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 180) + 'px';
  }

  function setMode(m) {
    mode = m;
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.mode === m));
    input.placeholder = m === 'read'
      ? '把网页链接粘贴到这里，我帮你读完并总结'
      : '例如：最近 OpenAI 发布了什么新模型？';
    hint.textContent = m === 'read' ? 'Enter 读取 · Shift+Enter 换行' : 'Enter 发送 · Shift+Enter 换行';
    sendBtn.textContent = m === 'read' ? '读取' : '搜索';
  }

  async function streamRequest(url, payload, handlers) {
    controller = new AbortController();
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!res.ok || !res.body) throw new Error('服务返回 ' + res.status);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, i);
        buf = buf.slice(i + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          try { handlers(JSON.parse(raw)); } catch (e) { /* 忽略坏包 */ }
        }
      }
    }
  }

  function setBusy(v) {
    busy = v;
    sendBtn.disabled = false;
    if (v) {
      sendBtn.textContent = '停止';
      sendBtn.classList.add('stopping');
    } else {
      sendBtn.textContent = mode === 'read' ? '读取' : '搜索';
      sendBtn.classList.remove('stopping');
    }
  }

  async function submit() {
    if (busy) {                       // 再点一次 = 停止
      if (controller) controller.abort();
      return;
    }
    const text = input.value.trim();
    if (!text) { input.focus(); return; }

    if (mode === 'read' && !/^https?:\/\//i.test(text)) {
      toast('读链接模式请粘贴以 http 开头的网址');
      return;
    }

    const hero = $('#hero');
    if (hero) hero.remove();
    addAsk(text);
    input.value = '';
    autoGrow();
    saveHistory(text);

    const a = addAnswer();
    let md = '';
    let sources = [];
    let errMsg = '';
    setBusy(true);

    const scheduleRender = () => {
      if (renderScheduled) return;
      renderScheduled = true;
      requestAnimationFrame(() => {
        renderScheduled = false;
        a.prose.innerHTML = mdToHtml(md, sources.length) + '<span class="cursor"></span>';
        autoScroll();
      });
    };

    const finish = () => {
      a.prose.innerHTML = mdToHtml(md, sources.length);
      setBusy(false);
      controller = null;
    };

    try {
      const endpoint = mode === 'read' ? '/api/read' : '/api/ask';
      const payload = mode === 'read' ? { url: text } : { question: text, deep: deepBox.checked };

      await streamRequest(endpoint, payload, ev => {
        switch (ev.type) {
          case 'status':
            setStatus(a.status, ev.text, true);
            break;
          case 'sources':
            sources = ev.items || [];
            renderSources(a.sources, sources);
            break;
          case 'delta':
            md += ev.text;
            setStatus(a.status, '正在写答案…', false);
            scheduleRender();
            break;
          case 'error':
            errMsg = ev.message || '出错了';
            a.prose.innerHTML = `<div class="err">${escapeHtml(errMsg)}</div>`;
            setStatus(a.status, '出错了', false);
            a.status.querySelector('.stext').style.color = 'var(--danger)';
            break;
          case 'done':
            setStatus(a.status, `完成 · 用时 ${ev.elapsed}s · 精读 ${ev.read} 篇`, false);
            break;
        }
      });

      finish();
      if (errMsg) {
        a.prose.innerHTML = `<div class="err">${escapeHtml(errMsg)}</div>`;
      } else if (!md) {
        a.prose.innerHTML = '<div class="err">没有拿到内容，请重试一次。</div>';
      } else {
        bindCite(a.prose, sources);
        const copy = el('button', 'mini', '复制答案');
        copy.onclick = async () => {
          try { await navigator.clipboard.writeText(md); toast('已复制'); }
          catch (e) { toast('复制失败'); }
        };
        const again = el('button', 'mini', '重搜一次');
        again.onclick = () => { input.value = text; submit(); };
        a.actions.append(copy, again);
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        setStatus(a.status, '已停止', false);
      } else {
        a.prose.innerHTML = `<div class="err">${escapeHtml(err.message || String(err))}</div>`;
        setStatus(a.status, '出错了', false);
      }
      finish();
    }
  }

  /* ---------------- 自检 ---------------- */
  async function checkHealth() {
    const dot = $('#healthDot');
    const txt = $('#healthTxt');
    dot.className = 'dot';
    txt.textContent = '自检中…';
    try {
      const r = await fetch('/api/ping', { method: 'GET' });
      const j = await r.json();
      if (j.ok) { dot.className = 'dot ok'; txt.textContent = '模型已连接'; }
      else { dot.className = 'dot bad'; txt.textContent = '连接异常'; }
    } catch (e) {
      dot.className = 'dot bad';
      txt.textContent = '服务未启动';
    }
  }

  /* ---------------- 初始化 ---------------- */
  EXAMPLES.forEach(q => {
    const c = el('button', 'chip', escapeHtml(q));
    c.onclick = () => { input.value = q; setMode('ask'); autoGrow(); submit(); };
    $('#chips').appendChild(c);
  });

  document.querySelectorAll('.tab').forEach(t => t.onclick = () => setMode(t.dataset.mode));
  sendBtn.onclick = submit;
  $('#newBtn').onclick = () => {
    if (busy && controller) controller.abort();
    stream.innerHTML = '';
    const hero = el('section', 'hero');
    hero.id = 'hero';
    hero.innerHTML = `<h1>你想查什么？</h1>
      <p>国内外的网站、新闻、论文、教程、价格、政策……直接问，我用中文给你讲明白。</p>
      <div class="chips" id="chips"></div>`;
    stream.appendChild(hero);
    EXAMPLES.forEach(q => {
      const c = el('button', 'chip', escapeHtml(q));
      c.onclick = () => { input.value = q; setMode('ask'); autoGrow(); submit(); };
      hero.querySelector('#chips').appendChild(c);
    });
    input.value = '';
    input.focus();
    autoGrow();
  };
  $('#healthBtn').onclick = checkHealth;
  $('#helpBtn').onclick = () => $('#modalMask').classList.add('show');
  $('#modalClose').onclick = () => $('#modalMask').classList.remove('show');
  $('#modalMask').onclick = e => { if (e.target.id === 'modalMask') e.currentTarget.classList.remove('show'); };
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') $('#modalMask').classList.remove('show');
  });

  input.addEventListener('input', autoGrow);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit();
    }
  });

  renderHistory();
  setMode('ask');
  input.focus();
  checkHealth();
  setInterval(() => { if (!document.hidden) checkHealth(); }, 60000);
})();
