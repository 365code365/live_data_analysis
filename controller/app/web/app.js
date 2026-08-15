/* 直播间监控控制台 — 原生 JS，无构建步骤 */
(() => {
  'use strict';

  const state = {
    view: 'overview',
    devices: [],
    proxies: [],
    tasks: [],
    timer: null,
    dataTaskId: null,
    productKeys: [],
  };

  // ── 基础工具 ────────────────────────────────────────────────────────
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  async function api(path, { method = 'GET', body, raw = false } = {}) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = await res.json();
        detail = j.detail || j.message || JSON.stringify(j);
      } catch (_) { /* 忽略非 JSON 错误体 */ }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return raw ? res : res.json();
  }

  let toastTimer = null;
  function toast(msg, kind = '') {
    const el = $('#toast');
    el.textContent = msg;
    el.className = `toast ${kind}`;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, kind === 'err' ? 6000 : 3000);
  }

  const fmtTime = (v) => {
    if (!v) return '-';
    const d = new Date(v.endsWith?.('Z') || /[+-]\d\d:\d\d$/.test(v) ? v : v + 'Z');
    if (isNaN(d)) return v;
    return d.toLocaleString('zh-CN', { hour12: false });
  };
  const fmtNum = (v) => (v === null || v === undefined ? '-' : Number(v).toLocaleString('zh-CN'));
  const fmtDur = (s) => {
    if (!s) return '-';
    const t = Math.round(s);
    return `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor(t % 3600 / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
  };

  function modal(title, html) {
    $('#modalTitle').textContent = title;
    $('#modalBody').innerHTML = html;
    $('#modal').hidden = false;
  }
  function closeModal() {
    $('#modal').hidden = true;
    $('#modalBody').innerHTML = '';
  }
  $('#modalClose').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  // ── 视图切换 ────────────────────────────────────────────────────────
  $$('.tab').forEach((tab) => tab.addEventListener('click', () => {
    $$('.tab').forEach((t) => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
    tab.classList.add('active');
    tab.setAttribute('aria-selected', 'true');
    state.view = tab.dataset.view;
    $$('.view').forEach((v) => v.classList.remove('active'));
    $(`#view-${state.view}`).classList.add('active');
    refresh();
  }));

  $('#refreshBtn').addEventListener('click', () => refresh(true));
  $('#autoRefresh').addEventListener('change', setupTimer);

  function setupTimer() {
    clearInterval(state.timer);
    if ($('#autoRefresh').checked) state.timer = setInterval(() => refresh(), 8000);
  }

  // ── 概览 ────────────────────────────────────────────────────────────
  async function loadOverview() {
    const [stats, info, events] = await Promise.all([
      api('/api/stats'), api('/api/system/info'), api('/api/events?limit=30'),
    ]);
    const labels = {
      devices: '设备', devices_running: '运行中设备', tasks: '任务', tasks_enabled: '启用任务',
      snapshots: '直播快照', products: '商品记录', recordings: '录像',
    };
    $('#statGrid').innerHTML = Object.entries(labels)
      .map(([k, label]) => `<div class="stat"><div class="num">${fmtNum(stats[k])}</div><div class="label">${label}</div></div>`)
      .join('');

    $('#version').textContent = `v${info.version}`;
    const d = info.docker || {};
    const imgs = d.images || {};
    const rows = [
      ['Docker', d.ok ? esc(d.server) : `<span class="badge error">不可用: ${esc(d.error || '')}</span>`],
      ['网关镜像', imgs.gateway ? '<span class="badge ok">已就绪</span>' : '<span class="badge error">缺失 → make build-gateway</span>'],
      ['VNC 镜像', imgs.vnc ? '<span class="badge ok">已就绪</span>' : '<span class="badge error">缺失 → make build-vnc</span>'],
      ['安卓镜像', imgs.android ? '<span class="badge ok">已就绪</span>' : '<span class="badge error">缺失 → make pull-android</span>'],
      ['adb / ffmpeg', `${info.tools.adb ? 'adb ✓' : 'adb ✗'} · ${info.tools.ffmpeg ? 'ffmpeg ✓' : 'ffmpeg ✗'}`],
      ['调度器', info.scheduler.running ? `<span class="badge ok">运行中（${info.scheduler.jobs.length} 个 job）</span>` : '<span class="badge error">未运行</span>'],
      ['安卓镜像名', esc(info.settings.redroid_image)],
      ['设备端口区间', info.settings.device_port_range.join(' - ')],
      ['选择器外挂目录', esc(info.settings.selectors_dir || '（未设置，使用内置）')],
    ];
    $('#sysInfo').innerHTML = rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('');
    $('#overviewEvents').innerHTML = renderEvents(events.items);
  }

  function renderEvents(items) {
    if (!items?.length) return '<div class="line">暂无事件</div>';
    return items.map((e) => `<div class="line ${e.level}"><span class="ts">${fmtTime(e.created_at)}</span> [${esc(e.source)}] ${esc(e.message)}</div>`).join('');
  }

  // ── 设备 ────────────────────────────────────────────────────────────
  async function loadDevices() {
    const [devices, proxies] = await Promise.all([api('/api/devices'), api('/api/proxies')]);
    state.devices = devices;
    state.proxies = proxies;

    $('#deviceProxySelect').innerHTML = '<option value="">直连（不用代理）</option>' +
      proxies.filter((p) => p.enabled).map((p) => `<option value="${p.id}">${esc(p.name)} · ${esc(p.url_masked)}</option>`).join('');

    if (!devices.length) {
      $('#deviceList').innerHTML = '<div class="card">还没有设备。先在上面创建一台，再用 VNC 装 APK、登录账号。</div>';
      return;
    }

    $('#deviceList').innerHTML = devices.map((d) => {
      const vncUrl = `http://${location.hostname}:${d.novnc_port}/vnc.html?autoconnect=1&resize=scale&password=${encodeURIComponent(d.vnc_password || '')}`;
      return `
      <div class="device" data-id="${d.id}">
        <header>
          <h3>${esc(d.name)} <span class="badge ${d.status}">${d.status}</span></h3>
          ${d.recording ? '<span class="badge recording">录屏中</span>' : ''}
        </header>
        <div class="kv">
          <div class="k">分辨率</div><div class="v">${d.width}×${d.height} @${d.dpi}dpi</div>
          <div class="k">代理</div><div class="v">${d.proxy_name ? esc(d.proxy_name) + ' · ' + esc(d.proxy_url_masked) : '直连'}</div>
          <div class="k">出口 IP</div><div class="v">${d.egress_ip ? esc(d.egress_ip) + (d.egress_region ? ' (' + esc(d.egress_region) + ')' : '') : '未检测'}</div>
          <div class="k">adb</div><div class="v">${esc(d.adb_addr)}（宿主 ${d.adb_port}）</div>
          <div class="k">noVNC</div><div class="v">${d.novnc_port}</div>
          ${d.last_error ? `<div class="k">最近错误</div><div class="v" style="color:var(--err)">${esc(d.last_error)}</div>` : ''}
        </div>
        <div class="btns">
          ${d.status === 'running'
            ? `<button class="btn small" data-act="stop">停止</button><button class="btn small" data-act="restart">重启</button>`
            : `<button class="btn small primary" data-act="start">启动</button>`}
          <a class="btn small" href="${vncUrl}" target="_blank" rel="noopener">VNC</a>
          <button class="btn small" data-act="status">状态</button>
          <button class="btn small" data-act="egress">查出口IP</button>
          <button class="btn small" data-act="shot">截图</button>
          <button class="btn small" data-act="ui">UI Dump</button>
          <button class="btn small" data-act="apk">安装 APK</button>
          ${d.recording
            ? `<button class="btn small" data-act="recstop">停止录屏</button>`
            : `<button class="btn small" data-act="recstart">开始录屏</button>`}
          <button class="btn small" data-act="logs">日志</button>
          <button class="btn small danger" data-act="delete">删除</button>
        </div>
      </div>`;
    }).join('');
  }

  $('#deviceList').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = btn.closest('.device').dataset.id;
    const act = btn.dataset.act;
    btn.disabled = true;
    try {
      await deviceAction(id, act);
    } catch (err) {
      toast(err.message, 'err');
    } finally {
      btn.disabled = false;
    }
  });

  async function deviceAction(id, act) {
    switch (act) {
      case 'start':
        toast('正在启动，安卓首次开机 1-3 分钟…');
        await api(`/api/devices/${id}/start`, { method: 'POST' });
        break;
      case 'stop':
        await api(`/api/devices/${id}/stop`, { method: 'POST' });
        toast('已停止', 'ok');
        break;
      case 'restart':
        await api(`/api/devices/${id}/restart`, { method: 'POST' });
        toast('已重启', 'ok');
        break;
      case 'delete': {
        if (!confirm('删除设备？点「确定」后会再问是否一并删除安卓数据卷。')) return;
        const purge = confirm('同时删除安卓 /data 数据卷？（会丢失 App 登录态，一般选取消）');
        await api(`/api/devices/${id}?purge_data=${purge}`, { method: 'DELETE' });
        toast('已删除', 'ok');
        break;
      }
      case 'status': {
        const s = await api(`/api/devices/${id}/status`);
        modal('设备状态', `<pre class="log">${esc(JSON.stringify(s, null, 2))}</pre>`);
        return;
      }
      case 'egress': {
        toast('正在通过网关查询出口 IP…');
        const s = await api(`/api/devices/${id}/egress`);
        toast(s.ip ? `出口 IP: ${s.ip} ${s.country || ''}` : `失败: ${s.error}`, s.ip ? 'ok' : 'err');
        break;
      }
      case 'shot':
        modal('实时截图', `<img src="/api/devices/${id}/screenshot?t=${Date.now()}" alt="设备截图" />`);
        return;
      case 'ui': {
        const platform = prompt('要顺带跑一遍提取规则吗？填 douyin / xiaohongshu，留空只看控件树', '') || '';
        const q = platform ? `?platform=${encodeURIComponent(platform)}` : '';
        const s = await api(`/api/devices/${id}/ui${q}`);
        modal('当前界面控件', `<pre class="log">${esc(JSON.stringify(s, null, 2))}</pre>`);
        return;
      }
      case 'apk': {
        const apks = await api('/api/devices/apks');
        if (!apks.length) { toast('apks/ 目录里没有 apk 文件，先放进去', 'err'); return; }
        const name = prompt(`可安装：\n${apks.map((a) => `${a.filename} (${a.size_mb}MB)`).join('\n')}\n\n输入要安装的文件名：`, apks[0].filename);
        if (!name) return;
        toast('正在安装，大包可能要一两分钟…');
        const r = await api(`/api/devices/${id}/apk/install`, { method: 'POST', body: { filename: name } });
        toast(r.message || '安装完成', 'ok');
        return;
      }
      case 'recstart': {
        const r = await api(`/api/devices/${id}/record/start`, { method: 'POST', body: {} });
        toast(`已开始录屏（recording ${r.recording_id}）`, 'ok');
        break;
      }
      case 'recstop': {
        toast('正在收尾合并…');
        const r = await api(`/api/devices/${id}/record/stop`, { method: 'POST' });
        toast(`录屏已结束（recording ${r.recording_id}）`, 'ok');
        break;
      }
      case 'logs': {
        const role = prompt('看哪个容器的日志？gw / android / vnc', 'gw');
        if (!role) return;
        const s = await api(`/api/devices/${id}/logs?role=${encodeURIComponent(role)}&tail=300`);
        modal(`日志 · ${s.container}`, `<pre class="log">${esc(s.logs)}</pre>`);
        return;
      }
      default:
        return;
    }
    await loadDevices();
  }

  $('#deviceForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = {
      name: f.get('name'),
      width: f.get('width') ? Number(f.get('width')) : null,
      height: f.get('height') ? Number(f.get('height')) : null,
      dpi: f.get('dpi') ? Number(f.get('dpi')) : null,
      proxy_id: f.get('proxy_id') ? Number(f.get('proxy_id')) : null,
      autostart: f.get('autostart') === 'on',
    };
    try {
      await api('/api/devices', { method: 'POST', body });
      e.target.reset();
      toast('设备已创建', 'ok');
      await loadDevices();
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 代理 ────────────────────────────────────────────────────────────
  async function loadProxies() {
    const proxies = await api('/api/proxies');
    state.proxies = proxies;
    const tbody = $('#proxyTable tbody');
    tbody.innerHTML = proxies.map((p) => `
      <tr data-id="${p.id}">
        <td>${p.id}</td>
        <td>${esc(p.name)}${p.remark ? `<br><span class="label" style="color:var(--muted)">${esc(p.remark)}</span>` : ''}</td>
        <td>${esc(p.url_masked)}</td>
        <td><span class="badge ${p.last_status === 'ok' ? 'ok' : (p.last_status ? 'error' : '')}">${esc(p.last_status || '未检测')}</span>
            ${p.enabled ? '' : '<span class="badge error">已禁用</span>'}</td>
        <td>${esc(p.last_egress_ip || '-')}<br><span style="color:var(--muted)">${esc(p.last_egress_region || '')}</span></td>
        <td>${p.in_use ? '<span class="badge ok">使用中</span>' : '空闲'}</td>
        <td>${fmtTime(p.last_checked_at)}</td>
        <td>
          <button class="btn small" data-act="test">测试</button>
          <button class="btn small" data-act="toggle">${p.enabled ? '禁用' : '启用'}</button>
          <button class="btn small danger" data-act="delete">删除</button>
        </td>
      </tr>`).join('') || '<tr><td colspan="8">还没有代理。不加代理也能跑，出口就是宿主机 IP。</td></tr>';
  }

  $('#proxyTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = btn.closest('tr').dataset.id;
    btn.disabled = true;
    try {
      if (btn.dataset.act === 'test') {
        toast('正在起临时网关容器验证代理，约 10-25 秒…');
        const r = await api(`/api/proxies/${id}/test`, { method: 'POST' });
        toast(r.ok ? `代理可用，出口 ${r.ip} ${r.country || ''}` : `不可用: ${r.error}`, r.ok ? 'ok' : 'err');
        if (!r.ok && r.gateway_log) modal('网关日志', `<pre class="log">${esc(r.gateway_log)}</pre>`);
      } else if (btn.dataset.act === 'toggle') {
        const p = state.proxies.find((x) => String(x.id) === id);
        await api(`/api/proxies/${id}`, { method: 'PATCH', body: { enabled: !p.enabled } });
      } else if (btn.dataset.act === 'delete') {
        if (!confirm('删除该代理？')) return;
        await api(`/api/proxies/${id}`, { method: 'DELETE' });
        toast('已删除', 'ok');
      }
      await loadProxies();
    } catch (err) {
      toast(err.message, 'err');
    } finally { btn.disabled = false; }
  });

  $('#proxyForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = {
      name: f.get('name'), scheme: f.get('scheme'), host: f.get('host'),
      port: Number(f.get('port')),
      username: f.get('username') || null, password: f.get('password') || null,
      remark: f.get('remark') || null,
    };
    try {
      await api('/api/proxies', { method: 'POST', body });
      e.target.reset();
      toast('已保存，建议点「测试」确认出口 IP', 'ok');
      await loadProxies();
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 任务 ────────────────────────────────────────────────────────────
  async function loadTasks() {
    const [tasks, devices] = await Promise.all([api('/api/tasks'), api('/api/devices')]);
    state.tasks = tasks;
    state.devices = devices;

    const opts = '<option value="">自动挑一台运行中的</option>' +
      devices.map((d) => `<option value="${d.id}">${esc(d.name)} (${d.status})</option>`).join('');
    const sel = $('#taskDeviceSelect');
    if (sel.innerHTML !== opts) sel.innerHTML = opts;

    $('#taskTable tbody').innerHTML = tasks.map((t) => `
      <tr data-id="${t.id}">
        <td>${t.id}</td>
        <td>${esc(t.name)}</td>
        <td>${t.platform === 'douyin' ? '抖音' : '小红书'}</td>
        <td class="wrap">${esc(t.target)}</td>
        <td>${esc(t.device_name || '自动')}</td>
        <td>${t.interval_seconds}s</td>
        <td><span class="badge ${t.last_status || ''}">${esc(t.last_status || '未运行')}</span>
            ${t.enabled ? '' : '<span class="badge error">停用</span>'}
            ${t.last_error ? `<br><span style="color:var(--warn);font-size:11px">${esc(t.last_error.slice(0, 90))}</span>` : ''}</td>
        <td>${t.run_count}${t.fail_count ? ` / <span style="color:var(--err)">${t.fail_count}失败</span>` : ''}</td>
        <td>${fmtTime(t.next_run_at)}</td>
        <td>
          <button class="btn small" data-act="run">立即执行</button>
          <button class="btn small" data-act="toggle">${t.enabled ? '停用' : '启用'}</button>
          <button class="btn small" data-act="data">看数据</button>
          <button class="btn small danger" data-act="delete">删除</button>
        </td>
      </tr>`).join('') || '<tr><td colspan="10">还没有任务</td></tr>';
  }

  $('#taskTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = btn.closest('tr').dataset.id;
    btn.disabled = true;
    try {
      const act = btn.dataset.act;
      if (act === 'run') {
        toast('正在执行一次采集（同步等待，可能 20-60 秒）…');
        const r = await api(`/api/tasks/${id}/run?wait=true`, { method: 'POST' });
        if (r.ok) {
          toast(`采集完成：${r.is_live ? '直播中' : '未在直播'}，商品 ${r.products.length} 件`, 'ok');
          modal('本次采集结果', `<pre class="log">${esc(JSON.stringify(r, null, 2))}</pre>`);
        } else {
          toast(`失败: ${r.error}`, 'err');
        }
      } else if (act === 'toggle') {
        const t = state.tasks.find((x) => String(x.id) === id);
        await api(`/api/tasks/${id}`, { method: 'PATCH', body: { enabled: !t.enabled } });
      } else if (act === 'delete') {
        if (!confirm('删除任务？历史数据会保留。')) return;
        await api(`/api/tasks/${id}`, { method: 'DELETE' });
      } else if (act === 'data') {
        state.dataTaskId = Number(id);
        $$('.tab').find((t) => t.dataset.view === 'data').click();
        return;
      }
      await loadTasks();
    } catch (err) {
      toast(err.message, 'err');
    } finally { btn.disabled = false; }
  });

  $('#taskForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = {
      name: f.get('name'),
      platform: f.get('platform'),
      target: f.get('target'),
      device_id: f.get('device_id') ? Number(f.get('device_id')) : null,
      interval_seconds: Number(f.get('interval_seconds') || 60),
      collect_products: f.get('collect_products') === 'on',
      collect_comments: f.get('collect_comments') === 'on',
      record_video: f.get('record_video') === 'on',
      enabled: f.get('enabled') === 'on',
    };
    try {
      await api('/api/tasks', { method: 'POST', body });
      e.target.reset();
      toast('任务已创建，5 秒后开始第一次采集', 'ok');
      await loadTasks();
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 数据 ────────────────────────────────────────────────────────────
  async function loadData() {
    const tasks = state.tasks.length ? state.tasks : await api('/api/tasks');
    state.tasks = tasks;
    const sel = $('#dataTaskSelect');
    const opts = tasks.map((t) => `<option value="${t.id}">#${t.id} ${esc(t.name)}</option>`).join('');
    if (sel.innerHTML !== opts) sel.innerHTML = opts;
    if (state.dataTaskId) sel.value = String(state.dataTaskId);
    state.dataTaskId = Number(sel.value || tasks[0]?.id || 0);
    if (!state.dataTaskId) return;

    const [snaps, latest, keys] = await Promise.all([
      api(`/api/snapshots?task_id=${state.dataTaskId}&limit=40`),
      api(`/api/products/latest?task_id=${state.dataTaskId}`),
      api(`/api/products/keys?task_id=${state.dataTaskId}`),
    ]);

    $('#snapshotTable tbody').innerHTML = snaps.items.map((s) => `
      <tr>
        <td>${fmtTime(s.captured_at)}</td>
        <td><span class="badge ${s.is_live ? 'ok' : 'error'}">${s.is_live ? '直播中' : '未直播'}</span></td>
        <td class="wrap">${esc(s.room_title || '-')}</td>
        <td>${esc(s.anchor_name || '-')}</td>
        <td>${fmtNum(s.viewer_count)}</td>
        <td>${fmtNum(s.like_count)}</td>
        <td>${s.product_count}</td>
        <td>${s.screenshot_path ? `<a class="btn small" href="/api/media?path=${encodeURIComponent(relPath(s.screenshot_path))}" target="_blank" rel="noopener">查看</a>` : '-'}</td>
      </tr>`).join('') || '<tr><td colspan="8">暂无快照</td></tr>';

    $('#productTable tbody').innerHTML = latest.items.map((p) => `
      <tr>
        <td>${p.position ?? '-'}</td>
        <td class="wrap">${esc(p.title || '-')}</td>
        <td>${p.price ?? '-'}<br><span style="color:var(--muted);font-size:11px">${esc(p.price_text || '')}</span></td>
        <td>${p.origin_price ?? '-'}</td>
        <td>${esc(p.sales_text || '-')}</td>
        <td>${esc(p.stock_text || '-')}</td>
      </tr>`).join('') || '<tr><td colspan="6">最近一次没采到商品</td></tr>';

    state.productKeys = keys.items;
    const keySel = $('#productKeySelect');
    const keyOpts = keys.items.map((k) => `<option value="${esc(k.product_key)}">${esc((k.title || k.product_key).slice(0, 40))}（${k.samples}次）</option>`).join('');
    if (keySel.innerHTML !== keyOpts) keySel.innerHTML = keyOpts;
    if (keys.items.length) await loadSeries(keySel.value || keys.items[0].product_key);
    else { $('#seriesChart').innerHTML = ''; $('#seriesTable tbody').innerHTML = ''; }
  }

  const relPath = (p) => String(p).replace(/^.*?\/app\/data\//, '');

  async function loadSeries(key) {
    if (!key || !state.dataTaskId) return;
    const s = await api(`/api/products/series?task_id=${state.dataTaskId}&product_key=${encodeURIComponent(key)}`);
    $('#seriesTable tbody').innerHTML = s.points.slice(-40).reverse().map((p) => `
      <tr><td>${fmtTime(p.captured_at)}</td><td>${p.price ?? '-'}</td><td>${p.position ?? '-'}</td><td>${esc(p.sales_text || '-')}</td></tr>
    `).join('') || '<tr><td colspan="4">暂无数据</td></tr>';
    $('#seriesChart').innerHTML = sparkline(s.points.map((p) => p.price).filter((v) => v !== null && v !== undefined));
  }

  function sparkline(values) {
    if (values.length < 2) return '<div style="color:var(--muted);font-size:12px">数据点不足，采集两次以上才有走势</div>';
    const w = 600, h = 150, pad = 24;
    const min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const pts = values.map((v, i) => {
      const x = pad + (i * (w - pad * 2)) / (values.length - 1);
      const y = h - pad - ((v - min) / span) * (h - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="价格走势">
      <polyline points="${pts}" fill="none" stroke="#4ea1ff" stroke-width="2" />
      <text x="4" y="14" fill="#8b98a8" font-size="11">最高 ${max}</text>
      <text x="4" y="${h - 4}" fill="#8b98a8" font-size="11">最低 ${min}</text>
    </svg>`;
  }

  $('#dataReload').addEventListener('click', () => { state.dataTaskId = Number($('#dataTaskSelect').value); loadData().catch((e) => toast(e.message, 'err')); });
  $('#dataTaskSelect').addEventListener('change', () => { state.dataTaskId = Number($('#dataTaskSelect').value); loadData().catch((e) => toast(e.message, 'err')); });
  $('#productKeySelect').addEventListener('change', (e) => loadSeries(e.target.value).catch((err) => toast(err.message, 'err')));

  // ── 录像 ────────────────────────────────────────────────────────────
  async function loadRecordings() {
    const r = await api('/api/recordings?limit=100');
    $('#recordingTable tbody').innerHTML = r.items.map((x) => `
      <tr data-id="${x.id}">
        <td>${x.id}</td>
        <td>${x.task_id ?? '-'}</td>
        <td>${x.device_id ?? '-'}</td>
        <td><span class="badge ${x.status}">${x.status}</span>${x.error ? `<br><span style="color:var(--err);font-size:11px">${esc(x.error.slice(0, 80))}</span>` : ''}</td>
        <td>${fmtTime(x.started_at)}</td>
        <td>${fmtDur(x.duration_seconds)}</td>
        <td>${x.size_mb ? x.size_mb + ' MB' : '-'}</td>
        <td>${x.segment_count}</td>
        <td>
          ${x.downloadable ? `<a class="btn small" href="/api/recordings/${x.id}/download">下载</a>` : ''}
          <button class="btn small danger" data-act="delete">删除</button>
        </td>
      </tr>`).join('') || '<tr><td colspan="9">还没有录像</td></tr>';
  }

  $('#recordingTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act="delete"]');
    if (!btn) return;
    if (!confirm('删除该录像及文件？')) return;
    try {
      await api(`/api/recordings/${btn.closest('tr').dataset.id}`, { method: 'DELETE' });
      toast('已删除', 'ok');
      await loadRecordings();
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 事件 ────────────────────────────────────────────────────────────
  async function loadEvents() {
    const r = await api('/api/events?limit=200');
    $('#eventLog').innerHTML = renderEvents(r.items);
  }

  // ── 调度 ────────────────────────────────────────────────────────────
  const loaders = {
    overview: loadOverview,
    devices: loadDevices,
    proxies: loadProxies,
    tasks: loadTasks,
    data: loadData,
    recordings: loadRecordings,
    events: loadEvents,
  };

  let refreshing = false;
  async function refresh(manual = false) {
    if (refreshing) return;
    refreshing = true;
    try {
      await loaders[state.view]();
      if (manual) toast('已刷新', 'ok');
    } catch (err) {
      if (manual) toast(err.message, 'err');
      else console.warn('刷新失败', err);
    } finally {
      refreshing = false;
    }
  }

  setupTimer();
  refresh();
})();
