/* 设备控制台：屏幕(noVNC 精简页) + 安卓快捷操作 + 声音 + 粘贴 + 应用管理 */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const deviceId = Number(new URLSearchParams(location.search).get('device') || 0);
  const state = { device: null, timer: null, jobTimer: null, recording: false };

  // ── 基础 ────────────────────────────────────────────────────────────
  async function api(path, { method = 'GET', body, form } = {}) {
    const opts = { method, headers: {} };
    if (form) {
      opts.body = form;
    } else if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = j.detail || j.message || detail; } catch (_) {}
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return res.status === 204 ? null : res.json();
  }

  let toastTimer = null;
  function toast(msg, kind = '') {
    const el = $('#toast');
    el.textContent = msg;
    el.className = `toast ${kind}`;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, kind === 'err' ? 6000 : 2600);
  }

  function modal(title, html) {
    $('#modalTitle').textContent = title;
    $('#modalBody').innerHTML = html;
    $('#modal').hidden = false;
    $('#modal').classList.add('open');
  }
  function closeModal() {
    $('#modal').hidden = true;
    $('#modal').classList.remove('open');
    $('#modalBody').innerHTML = '';
  }
  $('#modalClose').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  function mask(title, body) {
    $('#maskTitle').textContent = title;
    $('#maskBody').textContent = body || '';
    $('#screenMask').hidden = false;
  }
  const unmask = () => { $('#screenMask').hidden = true; };
  $('#maskRetry').addEventListener('click', () => location.reload());

  // ── 加载设备 ────────────────────────────────────────────────────────
  async function loadDevice() {
    const d = await api(`/api/devices/${deviceId}`);
    state.device = d;
    $('#devName').textContent = d.name;
    $('#devMeta').textContent = `${d.width}×${d.height} @${d.dpi}dpi · ${d.proxy_name || '直连'}`;
    document.title = `${d.name} · 设备控制台`;

    const frame = $('#screenFrame');
    frame.style.aspectRatio = `${d.width} / ${d.height}`;

    const cs = d.container_states || {};
    const ok = cs.vnc === 'running' && cs.android === 'running';
    $('#liveDot').style.background = ok ? 'var(--ok)' : 'var(--err)';
    $('#liveDot').style.boxShadow = `0 0 8px ${ok ? 'var(--ok)' : 'var(--err)'}`;

    $('#devStatus').innerHTML = [
      ['状态', `<span class="badge ${d.status}">${esc(d.status)}</span>`],
      ['容器', ['gw', 'android', 'vnc'].map((r) => {
        const st = cs[r];
        return `<span class="badge ${st === 'running' ? 'ok' : (st ? 'error' : '')}">${r} ${st || '-'}</span>`;
      }).join(' ')],
      ['出口 IP', esc(d.egress_ip || '未检测')],
      ['规格', `${d.memory_mb ? d.memory_mb + 'MB' : '内存不限'} · ${d.cpu_limit ? d.cpu_limit + ' 核' : 'CPU 不限'}`],
      ['声音', d.enable_audio ? `已开启（端口 ${d.audio_port || '-'}）` : '已关闭'],
    ].map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('');

    $('#btnRecord').textContent = d.recording ? '停止录屏' : '开始录屏';
    state.recording = !!d.recording;

    if (!ok) {
      const info = await api(`/api/devices/${deviceId}/vnc`).catch(() => null);
      mask('设备还没准备好', (info && info.problem) || '容器未全部就绪，启动后自动恢复');
      return;
    }

    // vnc_lite.html 是 noVNC 自带的极简页：只有画面，没有工具栏/设置面板
    const url = `http://${location.hostname}:${d.novnc_port}/vnc_lite.html`
      + `?password=${encodeURIComponent(d.vnc_password || '')}&resize=scale&reconnect=1&show_dot=1`;
    const frameEl = $('#vncFrame');
    if (frameEl.dataset.url !== url) {
      frameEl.dataset.url = url;
      frameEl.src = url;
    }
    unmask();
    setupAudio(d);
  }

  // ── 声音 ────────────────────────────────────────────────────────────
  function setupAudio(d) {
    const el = $('#audioEl');
    if (!d.enable_audio || !d.audio_port) {
      $('#audioOn').disabled = true;
      $('#audioOn').title = '该设备未开启声音';
      return;
    }
    const src = `http://${location.hostname}:${d.audio_port}/`;
    if (el.dataset.src !== src) el.dataset.src = src;
    el.volume = Number($('#webVolume').value) / 100;
  }

  $('#audioOn').addEventListener('change', async (e) => {
    const el = $('#audioEl');
    if (e.target.checked) {
      // 每次开都换一个 src，避免浏览器复用已结束的连续流
      el.src = `${el.dataset.src}?t=${Date.now()}`;
      try {
        await el.play();
        toast('已开始播放设备声音', 'ok');
      } catch (err) {
        e.target.checked = false;
        toast(`播放失败: ${err.message}（浏览器可能拦截了自动播放，再点一次）`, 'err');
      }
    } else {
      el.pause();
      el.removeAttribute('src');
      el.load();
    }
  });

  $('#webVolume').addEventListener('input', (e) => {
    const v = Number(e.target.value);
    $('#audioEl').volume = v / 100;
    $('#webVolumeLabel').textContent = `${v}%`;
  });

  // ── 按键 / 快捷操作 ─────────────────────────────────────────────────
  document.addEventListener('click', async (e) => {
    const keyBtn = e.target.closest('[data-key]');
    if (keyBtn) {
      try {
        await api(`/api/devices/${deviceId}/key/${keyBtn.dataset.key}`, { method: 'POST' });
      } catch (err) { toast(err.message, 'err'); }
      return;
    }
    const volBtn = e.target.closest('[data-vol]');
    if (volBtn) {
      try {
        const r = await api(`/api/devices/${deviceId}/volume`, { method: 'POST', body: { action: volBtn.dataset.vol } });
        applyVolume(r);
      } catch (err) { toast(err.message, 'err'); }
    }
  });

  function applyVolume(info) {
    if (!info) return;
    if (typeof info.max === 'number' && info.max > 0) $('#devVolume').max = info.max;
    if (typeof info.volume === 'number') $('#devVolume').value = info.volume;
    $('#devVolumeHint').textContent = `设备媒体音量 ${info.volume ?? '-'} / ${info.max ?? '-'}`;
  }

  let volTimer = null;
  $('#devVolume').addEventListener('input', () => {
    clearTimeout(volTimer);
    volTimer = setTimeout(async () => {
      try {
        const r = await api(`/api/devices/${deviceId}/volume`, {
          method: 'POST', body: { action: 'set', value: Number($('#devVolume').value) },
        });
        applyVolume(r);
      } catch (err) { toast(err.message, 'err'); }
    }, 220);
  });

  $('#btnRotate').addEventListener('click', async () => {
    try {
      const r = await api(`/api/devices/${deviceId}/rotate`, { method: 'POST', body: {} });
      toast(`已旋转到方向 ${r.orientation}`, 'ok');
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#btnWake').addEventListener('click', async () => {
    try {
      await api(`/api/devices/${deviceId}/key/KEYCODE_WAKEUP`, { method: 'POST' });
      toast('已唤醒', 'ok');
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#btnShot').addEventListener('click', async () => {
    try {
      const res = await fetch(`/api/devices/${deviceId}/screenshot?t=${Date.now()}`);
      if (!res.ok) {
        let d = res.statusText;
        try { d = (await res.json()).detail || d; } catch (_) {}
        modal('截图失败', `<div class="alert" style="margin:0"><div class="alert-body">${esc(d)}</div></div>`);
        return;
      }
      const url = URL.createObjectURL(await res.blob());
      modal('实时截图', `<img src="${url}" alt="设备截图" />`);
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#btnRecord').addEventListener('click', async () => {
    const btn = $('#btnRecord');
    btn.disabled = true;
    try {
      if (state.recording) {
        toast('正在收尾合并…');
        const r = await api(`/api/devices/${deviceId}/record/stop`, { method: 'POST' });
        toast(`录屏已结束（#${r.recording_id}）`, 'ok');
      } else {
        const r = await api(`/api/devices/${deviceId}/record/start`, { method: 'POST', body: {} });
        toast(`已开始录屏（#${r.recording_id}）`, 'ok');
      }
      await loadDevice();
    } catch (err) { toast(err.message, 'err'); } finally { btn.disabled = false; }
  });

  // ── 粘贴 ────────────────────────────────────────────────────────────
  async function doPaste(submit) {
    const text = $('#pasteText').value;
    if (!text.trim()) { toast('先填点内容', 'err'); return; }
    try {
      const r = await api(`/api/devices/${deviceId}/paste`, { method: 'POST', body: { text, submit } });
      const how = { clipboard: '原生剪贴板', adbkeyboard: 'ADBKeyboard', 'input-text': 'input text' }[r.method] || r.method;
      toast(`已粘贴（${how}）`, 'ok');
      $('#pasteHint').textContent = `上次使用通道：${how}`;
    } catch (err) {
      modal('粘贴失败', `<div class="alert" style="margin:0"><div class="alert-body">${esc(err.message)}</div></div>`);
    }
  }
  $('#btnPaste').addEventListener('click', () => doPaste(false));
  $('#btnPasteEnter').addEventListener('click', () => doPaste(true));

  // ── 应用 ────────────────────────────────────────────────────────────
  async function loadCatalog() {
    const r = await api('/api/apps/catalog');
    const local = await api('/api/apps/local').catch(() => ({ items: [] }));
    const groups = [];
    const installable = r.items.filter((i) => i.installable);
    if (installable.length) {
      groups.push(`<optgroup label="应用目录">${installable
        .map((i) => `<option value="catalog:${esc(i.key)}">${esc(i.name)}</option>`).join('')}</optgroup>`);
    }
    const pending = r.items.filter((i) => !i.installable);
    if (pending.length) {
      groups.push(`<optgroup label="需自备安装包">${pending
        .map((i) => `<option value="page:${esc(i.key)}">${esc(i.name)}（无直链）</option>`).join('')}</optgroup>`);
    }
    if (local.items.length) {
      groups.push(`<optgroup label="已上传">${local.items
        .map((i) => `<option value="local:${esc(i.filename)}">${esc(i.filename)} (${i.size_mb}MB)</option>`).join('')}</optgroup>`);
    }
    $('#catalogSelect').innerHTML = groups.join('') || '<option value="">（目录为空）</option>';
    state.catalog = r.items;
  }

  async function loadApps() {
    try {
      const r = await api(`/api/devices/${deviceId}/apps`);
      $('#appTable tbody').innerHTML = r.items.length
        ? r.items.map((a) => `
          <tr>
            <td>${esc(a.name || a.package)}<br><span class="img-sub">${esc(a.package)}</span></td>
            <td>
              <button class="btn small" data-launch="${esc(a.package)}">启动</button>
              <button class="btn small danger" data-uninstall="${esc(a.package)}">卸载</button>
            </td>
          </tr>`).join('')
        : '<tr><td colspan="2" class="img-sub">还没装第三方应用</td></tr>';
      renderJob(r.job);
    } catch (err) {
      $('#appTable tbody').innerHTML = `<tr><td colspan="2" class="img-sub">${esc(err.message)}</td></tr>`;
    }
  }

  function renderJob(job) {
    const box = $('#appJob');
    if (!job || ['done', 'failed'].includes(job.state)) {
      if (job && job.state === 'failed') {
        box.hidden = false;
        $('#appJobBar').style.width = '100%';
        $('#appJobMsg').innerHTML = `<span style="color:var(--err)">${esc(job.error || '失败')}</span>`;
      } else if (job && job.state === 'done') {
        box.hidden = false;
        $('#appJobBar').style.width = '100%';
        $('#appJobMsg').textContent = `${job.name}：${job.message}`;
      } else {
        box.hidden = true;
      }
      if (state.jobTimer) { clearInterval(state.jobTimer); state.jobTimer = null; }
      return;
    }
    box.hidden = false;
    $('#appJobBar').style.width = `${job.percent || 0}%`;
    $('#appJobMsg').textContent = `${job.state === 'downloading' ? '下载' : '安装'} ${job.name} — ${job.message}`;
    if (!state.jobTimer) {
      state.jobTimer = setInterval(async () => {
        try {
          const r = await api(`/api/devices/${deviceId}/apps/job`);
          renderJob(r.job);
          if (r.job && r.job.state === 'done') loadApps();
        } catch (_) { /* 忽略轮询抖动 */ }
      }, 1200);
    }
  }

  async function startInstall(body) {
    try {
      const job = await api(`/api/devices/${deviceId}/apps/install`, { method: 'POST', body });
      renderJob(job);
      toast('已提交安装任务', 'ok');
    } catch (err) {
      modal('安装失败', `<div class="alert" style="margin:0"><div class="alert-body">${esc(err.message)}</div></div>`);
    }
  }

  $('#btnInstallCatalog').addEventListener('click', () => {
    const val = $('#catalogSelect').value || '';
    const [kind, rest] = val.split(':');
    if (kind === 'catalog') startInstall({ source: 'catalog', key: rest });
    else if (kind === 'local') startInstall({ source: 'local', filename: rest });
    else if (kind === 'page') {
      const item = (state.catalog || []).find((i) => i.key === rest);
      modal('需要自备安装包', `<div class="alert" style="margin:0">
        <div class="alert-title">${esc(item?.name || rest)} 没有稳定直链</div>
        <div class="alert-body">${esc(item?.note || '')}</div>
        ${item?.page ? `<p class="hint">官方下载页：<a href="${esc(item.page)}" target="_blank" rel="noopener">${esc(item.page)}</a></p>` : ''}
        <p class="hint">下载到本地后用下面的「上传并安装」，或把直链填进「apk 直链」。</p></div>`);
    } else toast('先选一个应用', 'err');
  });

  $('#btnInstallUrl').addEventListener('click', () => {
    const url = $('#apkUrl').value.trim();
    if (!/^https?:\/\//.test(url)) { toast('请填 http(s) 开头的 apk 直链', 'err'); return; }
    startInstall({ source: 'url', url });
  });

  $('#btnUpload').addEventListener('click', async () => {
    const f = $('#apkFile').files[0];
    if (!f) { toast('先选一个 apk 文件', 'err'); return; }
    const btn = $('#btnUpload');
    btn.disabled = true;
    try {
      const form = new FormData();
      form.append('file', f);
      toast(`上传中 ${(f.size / 1048576).toFixed(1)}MB …`);
      const up = await api('/api/apps/upload', { method: 'POST', form });
      toast(`已上传 ${up.filename}，开始安装`, 'ok');
      await startInstall({ source: 'local', filename: up.filename });
      await loadCatalog();
    } catch (err) {
      modal('上传失败', `<div class="alert" style="margin:0"><div class="alert-body">${esc(err.message)}</div></div>`);
    } finally { btn.disabled = false; }
  });

  $('#appTable').addEventListener('click', async (e) => {
    const launch = e.target.closest('[data-launch]');
    const un = e.target.closest('[data-uninstall]');
    try {
      if (launch) {
        await api(`/api/devices/${deviceId}/launch/${launch.dataset.launch}`, { method: 'POST' });
        toast('已启动', 'ok');
      } else if (un) {
        if (!confirm(`卸载 ${un.dataset.uninstall}？`)) return;
        await api(`/api/devices/${deviceId}/apps/${un.dataset.uninstall}`, { method: 'DELETE' });
        toast('已卸载', 'ok');
        loadApps();
      }
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 启动 ────────────────────────────────────────────────────────────
  async function boot() {
    if (!deviceId) { mask('缺少参数', '请从设备列表进入控制台'); return; }
    try {
      await loadDevice();
      await loadCatalog();
      await loadApps();
      api(`/api/devices/${deviceId}/volume`).then(applyVolume).catch(() => {});
    } catch (err) {
      mask('加载失败', err.message);
    }
    state.timer = setInterval(() => loadDevice().catch(() => {}), 10000);
  }

  boot();
})();
