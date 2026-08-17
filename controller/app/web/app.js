/* 直播间监控控制台 — 原生 JS，无构建步骤 */
(() => {
  'use strict';

  const state = {
    view: 'overview',
    devices: [],
    proxies: [],
    tasks: [],
    timer: null,
    pullTimer: null,
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
    $('#modal').classList.add('open');
  }
  function closeModal() {
    const el = $('#modal');
    el.hidden = true;
    el.classList.remove('open');
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
    const [stats, info, events, hostCheck] = await Promise.all([
      api('/api/stats'), api('/api/system/info'), api('/api/events?limit=30'),
      api('/api/system/host-check').catch(() => null),
    ]);

    const alertBox = $('#hostAlert');
    if (hostCheck && hostCheck.hints && hostCheck.hints.length) {
      alertBox.hidden = false;
      alertBox.className = 'alert';
      alertBox.innerHTML = `
        <div class="alert-title">宿主环境不满足运行安卓容器的条件</div>
        <div class="alert-body">${hostCheck.hints.map((h) => esc(h)).join('\n\n')}</div>
        <div class="alert-meta">内核 ${esc(hostCheck.kernel)} · binder ${hostCheck.binder ? '✓' : '✗'} · tun ${hostCheck.tun ? '✓' : '✗'}</div>`;
    } else {
      alertBox.hidden = true;
      alertBox.innerHTML = '';
    }
    const labels = {
      devices: '设备', devices_running: '运行中设备', tasks: '任务', tasks_enabled: '启用任务',
      snapshots: '直播快照', products: '商品记录', recordings: '录像',
    };
    $('#statGrid').innerHTML = Object.entries(labels)
      .map(([k, label]) => `<div class="stat"><div class="num">${fmtNum(stats[k])}</div><div class="label">${label}</div></div>`)
      .join('');

    $('#version').textContent = `v${info.version}`;
    const d = info.docker || {};
    const details = d.image_details || [];
    const rows = [
      ['Docker', d.ok ? esc(d.server) : `<span class="badge error">不可用: ${esc(d.error || '')}</span>`],
      ...details.map((img) => [img.label, renderImage(img)]),
      ['adb / ffmpeg', `${info.tools.adb ? 'adb ✓' : 'adb ✗'} · ${info.tools.ffmpeg ? 'ffmpeg ✓' : 'ffmpeg ✗'}`],
      ['调度器', info.scheduler.running ? `<span class="badge ok">运行中（${info.scheduler.jobs.length} 个 job）</span>` : '<span class="badge error">未运行</span>'],
      ['设备端口区间', info.settings.device_port_range.join(' - ')],
      ['选择器外挂目录', esc(info.settings.selectors_dir || '（未设置，使用内置）')],
    ];
    $('#sysInfo').innerHTML = rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('');
    $('#overviewEvents').innerHTML = renderEvents(events.items);

    // 有拉取在进行时加快刷新，让进度条动起来
    const pulling = details.some((i) => i.job && i.job.state === 'pulling');
    if (pulling && !state.pullTimer) {
      state.pullTimer = setInterval(() => { if (state.view === 'overview') loadOverview().catch(() => {}); }, 1500);
    } else if (!pulling && state.pullTimer) {
      clearInterval(state.pullTimer);
      state.pullTimer = null;
    }
  }

  function renderImage(img) {
    const job = img.job;
    const name = `<div class="img-name">${esc(img.image)}</div>`;

    if (job && job.state === 'pulling') {
      const pct = job.percent || 0;
      return `
        <div class="img-row">
          <span class="badge starting">拉取中 ${pct}%</span>
          <div class="progress"><div class="bar" style="width:${pct}%"></div></div>
          <span class="img-sub">${job.downloaded_mb || 0} / ${job.total_mb || 0} MB · ${esc((job.message || '').slice(0, 40))}</span>
        </div>${name}`;
    }

    if (img.ready) {
      return `<div class="img-row"><span class="badge ok">已就绪</span>
        <span class="img-sub">${img.size_mb ? img.size_mb + ' MB' : ''}</span></div>${name}`;
    }

    const failed = job && job.state === 'failed'
      ? `<div class="img-err">${esc((job.error || '').slice(0, 200))}</div>` : '';

    if (img.pullable) {
      return `
        <div class="img-row">
          <span class="badge error">缺失</span>
          <button class="btn small primary" data-pull="${img.target}">
            ${job && job.state === 'failed' ? '重试拉取' : '立即拉取'}
          </button>
          <span class="img-sub">约 600MB-1.5GB，会在后台拉，可以关页面</span>
        </div>${name}${failed}`;
    }

    // 网关 / VNC 是本项目自带 Dockerfile 的本地镜像，只能在宿主机构建
    return `
      <div class="img-row">
        <span class="badge error">缺失</span>
        <code class="cmd">${esc(img.hint)}</code>
        <button class="btn small" data-copy="${esc(img.hint)}">复制命令</button>
        <span class="img-sub">本地构建镜像，需在宿主机项目目录执行</span>
      </div>${name}${failed}`;
  }

  $('#sysInfo').addEventListener('click', async (e) => {
    const pullBtn = e.target.closest('button[data-pull]');
    const copyBtn = e.target.closest('button[data-copy]');
    if (copyBtn) {
      const text = copyBtn.dataset.copy;
      try {
        await navigator.clipboard.writeText(text);
        toast('命令已复制', 'ok');
      } catch (_) {
        toast(`手动复制： ${text}`);
      }
      return;
    }
    if (!pullBtn) return;
    pullBtn.disabled = true;
    try {
      await api(`/api/system/images/${pullBtn.dataset.pull}/pull`, { method: 'POST' });
      toast('已开始拉取，进度会实时显示', 'ok');
      await loadOverview();
    } catch (err) {
      toast(err.message, 'err');
      pullBtn.disabled = false;
    }
  });

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

    // 已购权益对应的套餐可以直接用来开设备（value 是 plan_id）
    try {
      const [summary, plans] = await Promise.all([
        api('/api/billing/summary'),
        api('/api/billing/plans?include_disabled=true'),
      ]);
      const idByCode = Object.fromEntries(plans.items.map((p) => [p.code, p.id]));
      const usable = (summary.active_entitlements || []).filter((e) => e.remaining_devices > 0);
      $('#devicePlanSelect').innerHTML = '<option value="">不绑定（自用）</option>' + usable
        .map((e) => `<option value="${idByCode[e.plan_code] || ''}">${esc(e.plan_name)}（剩 ${e.remaining_devices} 台）</option>`)
        .join('');
    } catch (_) { /* 计费不可用时忽略，设备仍可自由创建 */ }

    if (!devices.length) {
      $('#deviceList').innerHTML = '<div class="card">还没有设备。先在上面创建一台，再用 VNC 装 APK、登录账号。</div>';
      return;
    }

    $('#deviceList').innerHTML = devices.map((d) => {
      const cs = d.container_states || {};
      const dot = (role, label) => {
        const st = cs[role];
        const cls = st === 'running' ? 'ok' : (st ? 'error' : '');
        return `<span class="badge ${cls}">${label} ${st || '未创建'}</span>`;
      };
      return `
      <div class="device" data-id="${d.id}">
        <header>
          <h3>${esc(d.name)} <span class="badge ${d.status}">${d.status}</span></h3>
          ${d.recording ? '<span class="badge recording">录屏中</span>' : ''}
        </header>
        <div class="kv">
          <div class="k">容器</div><div class="v">${dot('gw', '网关')} ${dot('android', '安卓')} ${dot('vnc', '画面')}</div>
          <div class="k">分辨率</div><div class="v">${d.width}×${d.height} @${d.dpi}dpi</div>
          <div class="k">规格</div><div class="v">${d.memory_mb ? d.memory_mb + 'MB' : '内存不限'} · ${d.cpu_limit ? d.cpu_limit + ' 核' : 'CPU 不限'}${d.entitlement_id ? ` · <span class="badge ok">套餐 #${d.entitlement_id}</span>` : ''}</div>
          <div class="k">代理</div><div class="v">${d.proxy_name ? esc(d.proxy_name) + ' · ' + esc(d.proxy_url_masked) : '直连'}</div>
          <div class="k">出口 IP</div><div class="v">${d.egress_ip ? esc(d.egress_ip) + (d.egress_region ? ' (' + esc(d.egress_region) + ')' : '') : '未检测'}</div>
          <div class="k">端口</div><div class="v">adb ${d.adb_port} · noVNC ${d.novnc_port} · 声音 ${d.audio_port || '未分配'}</div>
          ${d.last_error ? `<div class="k">最近错误</div><div class="v" style="color:var(--err)">${esc(d.last_error)}</div>` : ''}
        </div>
        <div class="btns">
          <a class="btn small primary" href="/console?device=${d.id}" target="_blank" rel="noopener">打开控制台</a>
          ${d.status === 'running'
            ? `<button class="btn small" data-act="stop">停止</button><button class="btn small" data-act="restart">重启</button>`
            : `<button class="btn small primary" data-act="start">启动</button>`}
          <button class="btn small" data-act="vnc">裸 VNC</button>
          <button class="btn small" data-act="status">状态</button>
          <button class="btn small" data-act="egress">查出口IP</button>
          <button class="btn small" data-act="shot">截图</button>
          <button class="btn small" data-act="ui">UI Dump</button>
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
      case 'vnc': {
        const info = await api(`/api/devices/${id}/vnc`);
        const url = `http://${location.hostname}:${info.novnc_port}${info.path}`;
        if (!info.ready) {
          modal('VNC 还不能用', `
            <div class="alert" style="margin:0"><div class="alert-title">打开会白屏或报连接重置</div>
            <div class="alert-body">${esc(info.problem || '画面容器未就绪')}</div>
            <div class="alert-meta">容器状态: ${esc(JSON.stringify(info.container_states))}</div></div>
            <p class="hint">确实要打开： <a href="${url}" target="_blank" rel="noopener">${esc(url)}</a></p>`);
          return;
        }
        window.open(url, '_blank', 'noopener');
        return;
      }
      case 'shot': {
        // 先取数据再渲染：直接塞 <img src> 的话失败只会看到一个坏图标
        const res = await fetch(`/api/devices/${id}/screenshot?t=${Date.now()}`);
        if (!res.ok) {
          let detail = res.statusText;
          try { detail = (await res.json()).detail || detail; } catch (_) { /* 非 JSON */ }
          modal('截图失败', `<div class="alert" style="margin:0">
            <div class="alert-title">拿不到设备画面</div>
            <div class="alert-body">${esc(detail)}</div></div>`);
          return;
        }
        const blob = await res.blob();
        const objUrl = URL.createObjectURL(blob);
        modal('实时截图', `<img src="${objUrl}" alt="设备截图" />`);
        return;
      }
      case 'ui': {
        const platform = prompt('要顺带跑一遍提取规则吗？填 douyin / xiaohongshu，留空只看控件树', '') || '';
        const q = platform ? `?platform=${encodeURIComponent(platform)}` : '';
        const s = await api(`/api/devices/${id}/ui${q}`);
        modal('当前界面控件', `<pre class="log">${esc(JSON.stringify(s, null, 2))}</pre>`);
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
      plan_id: f.get('plan_id') ? Number(f.get('plan_id')) : null,
      enable_audio: f.get('enable_audio') === 'on',
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
        <td>${x.task_id ? '任务' + x.task_id : '手动'}<br><span class="img-sub">设备 ${x.device_id ?? '-'}</span></td>
        <td><span class="badge ${x.status}">${x.status}</span>${x.error ? `<br><span style="color:var(--err);font-size:11px">${esc(x.error.slice(0, 60))}</span>` : ''}</td>
        <td>${fmtTime(x.started_at)}</td>
        <td>${fmtDur(x.duration_seconds)}</td>
        <td>${x.size_mb ? x.size_mb + ' MB' : '-'}<br><span class="img-sub">${x.segment_count} 段</span></td>
        <td>
          ${x.downloadable ? `<button class="btn small primary" data-act="play">播放</button>` : ''}
          ${x.downloadable ? `<a class="btn small" href="/api/recordings/${x.id}/download">下载</a>` : ''}
          <button class="btn small danger" data-act="delete">删除</button>
        </td>
      </tr>`).join('') || '<tr><td colspan="7">还没有录像</td></tr>';
  }

  function playRecording(id) {
    const player = $('#player');
    // 用 stream 接口，服务端支持 Range，拖进度不用先下完整个文件
    player.src = `/api/recordings/${id}/stream`;
    $('#playerTitle').textContent = `#${id}`;
    player.play().catch(() => { /* 需要用户手动点一下播放，正常 */ });
    player.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  $('#recordingTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = btn.closest('tr').dataset.id;
    if (btn.dataset.act === 'play') { playRecording(id); return; }
    if (!confirm('删除该录像及文件？')) return;
    try {
      await api(`/api/recordings/${id}`, { method: 'DELETE' });
      if ($('#player').src.includes(`/${id}/stream`)) { $('#player').removeAttribute('src'); $('#player').load(); }
      toast('已删除', 'ok');
      await loadRecordings();
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 应用 ────────────────────────────────────────────────────────────
  async function loadAppsPage() {
    const [cat, local, devices] = await Promise.all([
      api('/api/apps/catalog'), api('/api/apps/local'), api('/api/devices'),
    ]);
    state.devices = devices;

    const opts = devices.map((d) => `<option value="${d.id}">${esc(d.name)} (${d.status})</option>`).join('');
    const sel = $('#apkTargetDevice');
    if (sel.innerHTML !== opts) sel.innerHTML = opts || '<option value="">没有设备</option>';

    const devSelect = (cls) => `<select class="${cls}">${devices
      .filter((d) => d.status === 'running')
      .map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join('') || '<option value="">无运行设备</option>'}</select>`;

    $('#catalogTable tbody').innerHTML = cat.items.map((a) => `
      <tr data-key="${esc(a.key)}">
        <td>${esc(a.name)}<br><span class="img-sub">${esc(a.category)}</span></td>
        <td class="wrap"><span class="img-sub">${esc(a.package || '-')}</span></td>
        <td>${a.installable
          ? '<span class="badge ok">有直链</span>'
          : `<span class="badge error">需自备</span>${a.page ? `<br><a class="img-sub" href="${esc(a.page)}" target="_blank" rel="noopener">官方下载页</a>` : ''}`}</td>
        <td>${a.installable
          ? `${devSelect('cat-dev')} <button class="btn small primary" data-act="install">安装</button>`
          : `<button class="btn small" data-act="why">说明</button>`}</td>
      </tr>`).join('') || '<tr><td colspan="4">目录为空</td></tr>';
    $('#catalogFileHint').textContent = `目录文件：${cat.catalog_file}（改完调用 /api/system/selectors/reload 无需重启）`;
    state.catalog = cat.items;

    $('#localApkTable tbody').innerHTML = local.items.map((f) => `
      <tr data-file="${esc(f.filename)}">
        <td class="wrap">${esc(f.filename)}</td>
        <td>${f.size_mb} MB</td>
        <td>${devSelect('local-dev')} <button class="btn small primary" data-act="install">安装</button>
            <button class="btn small danger" data-act="delete">删除</button></td>
      </tr>`).join('') || '<tr><td colspan="3">还没有上传安装包</td></tr>';
  }

  $('#catalogTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const key = tr.dataset.key;
    if (btn.dataset.act === 'why') {
      const item = (state.catalog || []).find((i) => i.key === key);
      modal(`${item?.name || key}`, `<div class="alert" style="margin:0">
        <div class="alert-title">这个应用没有稳定直链</div>
        <div class="alert-body">${esc(item?.note || '')}</div>
        ${item?.page ? `<p class="hint">官方下载页：<a href="${esc(item.page)}" target="_blank" rel="noopener">${esc(item.page)}</a></p>` : ''}
        <p class="hint">下载到本地后用上方「上传安装包」，或把直链写进目录文件的 url 字段。</p></div>`);
      return;
    }
    const devId = tr.querySelector('.cat-dev')?.value;
    if (!devId) { toast('没有处于运行状态的设备', 'err'); return; }
    try {
      await api(`/api/devices/${devId}/apps/install`, { method: 'POST', body: { source: 'catalog', key } });
      toast('已提交安装任务，进度在设备控制台里看', 'ok');
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#localApkTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const filename = tr.dataset.file;
    try {
      if (btn.dataset.act === 'delete') {
        if (!confirm(`删除 ${filename}？`)) return;
        await api(`/api/apps/local/${encodeURIComponent(filename)}`, { method: 'DELETE' });
        toast('已删除', 'ok');
        await loadAppsPage();
        return;
      }
      const devId = tr.querySelector('.local-dev')?.value;
      if (!devId) { toast('没有处于运行状态的设备', 'err'); return; }
      await api(`/api/devices/${devId}/apps/install`, { method: 'POST', body: { source: 'local', filename } });
      toast('已提交安装任务', 'ok');
    } catch (err) { toast(err.message, 'err'); }
  });

  async function uploadApk(install) {
    const f = $('#apkFileGlobal').files[0];
    if (!f) { toast('先选一个 apk 文件', 'err'); return; }
    const btn = install ? $('#btnUploadGlobal') : $('#btnUploadOnly');
    btn.disabled = true;
    try {
      const form = new FormData();
      form.append('file', f);
      toast(`上传中 ${(f.size / 1048576).toFixed(1)}MB …`);
      const up = await api('/api/apps/upload', { method: 'POST', form });
      toast(`已上传 ${up.filename}`, 'ok');
      if (install) {
        const devId = $('#apkTargetDevice').value;
        if (!devId) { toast('没有可安装的设备，文件已保留在 apks/', 'err'); }
        else {
          await api(`/api/devices/${devId}/apps/install`, { method: 'POST', body: { source: 'local', filename: up.filename } });
          toast('已提交安装任务', 'ok');
        }
      }
      await loadAppsPage();
    } catch (err) {
      modal('上传失败', `<div class="alert" style="margin:0"><div class="alert-body">${esc(err.message)}</div></div>`);
    } finally { btn.disabled = false; }
  }
  $('#btnUploadGlobal').addEventListener('click', () => uploadApk(true));
  $('#btnUploadOnly').addEventListener('click', () => uploadApk(false));

  // ── 套餐 / 订单 ─────────────────────────────────────────────────────
  const yuan = (cents) => `¥${(cents / 100).toFixed(2)}`;

  async function loadPlans() {
    const [plans, summary, orders] = await Promise.all([
      api('/api/billing/plans'), api('/api/billing/summary'), api('/api/billing/orders?limit=20'),
    ]);
    state.channels = plans.channels.filter((c) => c.ready);

    const ents = summary.active_entitlements || [];
    $('#quotaInfo').innerHTML = [
      ['计费', summary.billing_enabled
        ? `<span class="badge ok">已开启</span>${summary.enforce ? ' <span class="badge starting">强制</span>' : ' <span class="sub">未强制，可自由开设备</span>'}`
        : '<span class="badge">未开启</span>'],
      ['设备额度', `${summary.device_used} / ${summary.device_quota}（剩 ${summary.device_remaining}）`],
      ['生效套餐', ents.length
        ? ents.map((e) => `${esc(e.plan_name)} · 剩 ${e.days_left ?? '-'} 天 · 设备 ${e.used_devices}/${e.max_devices}`).join('<br>')
        : '<span class="sub">还没有已购套餐</span>'],
    ].map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('');

    $('#planGrid').innerHTML = plans.items.map((p) => `
      <div class="plan" data-id="${p.id}">
        ${p.badge ? `<div class="plan-badge">${esc(p.badge)}</div>` : ''}
        <h3>${esc(p.name)}</h3>
        <div class="plan-price">${yuan(p.price_cents)}
          <span class="plan-cycle">/ ${p.duration_days} 天</span></div>
        ${p.original_price_cents ? `<div class="plan-origin">${yuan(p.original_price_cents)}</div>` : ''}
        <p class="plan-desc">${esc(p.description || '')}</p>
        <ul class="plan-spec">
          <li>分辨率 ${p.spec.width}×${p.spec.height} @${p.spec.dpi}dpi</li>
          <li>${p.spec.memory_mb ? p.spec.memory_mb + 'MB 内存' : '内存不限'} · ${p.spec.cpu_limit ? p.spec.cpu_limit + ' 核' : 'CPU 不限'}</li>
          <li>${p.spec.max_devices} 台设备 · ${p.spec.max_tasks} 个监控任务</li>
          <li>${p.spec.allow_proxy ? '独立出口 IP' : '不含代理'} · ${p.spec.allow_recording ? '录屏' : '无录屏'} · ${p.spec.allow_audio ? '声音' : '无声音'}</li>
        </ul>
        <div class="form-row tight">
          <select class="pay-channel">${state.channels
            .map((c) => `<option value="${c.channel}">${esc(c.label)}</option>`).join('') || '<option value="">无可用通道</option>'}</select>
          <button class="btn primary" data-act="buy">立即购买</button>
        </div>
      </div>`).join('') || '<div class="card">还没有配置套餐，去「后台」新增</div>';

    $('#orderTable tbody').innerHTML = orders.items.map((o) => `
      <tr data-no="${esc(o.order_no)}">
        <td class="wrap"><span class="img-sub">${esc(o.order_no)}</span></td>
        <td>${esc(o.plan_name || '-')}</td>
        <td>${yuan(o.amount_cents)}</td>
        <td>${esc(o.channel)}</td>
        <td><span class="badge ${o.status === 'paid' ? 'ok' : (o.status === 'pending' ? 'starting' : 'error')}">${esc(o.status)}</span></td>
        <td>${fmtTime(o.created_at)}</td>
        <td>${o.status === 'pending' ? '<button class="btn small primary" data-act="pay">继续支付</button>' : ''}
            ${o.status === 'pending' ? '<button class="btn small danger" data-act="cancel">取消</button>' : ''}</td>
      </tr>`).join('') || '<tr><td colspan="7">还没有订单</td></tr>';
  }

  let payPoll = null;
  function showPayModal(order) {
    clearInterval(payPoll);
    const isMock = order.channel === 'mock';
    modal('扫码支付', `
      <div class="pay-box">
        <img class="pay-qr" src="/api/billing/orders/${order.order_no}/qr.png?t=${Date.now()}" alt="支付二维码" />
        <div class="pay-info">
          <div class="pay-amount">${yuan(order.amount_cents)}</div>
          <div>${esc(order.plan_name || '')}</div>
          <div class="img-sub">订单 ${esc(order.order_no)}</div>
          <div class="img-sub">通道 ${esc(order.channel)}</div>
          <div id="payStatus" class="badge starting">等待支付…</div>
          ${isMock ? `<p class="hint">本地联调通道：<a href="${esc(order.pay_url)}" target="_blank" rel="noopener">点这里模拟付款</a></p>` : '<p class="hint">用手机扫码完成付款，页面会自动刷新状态</p>'}
        </div>
      </div>`);
    payPoll = setInterval(async () => {
      try {
        const o = await api(`/api/billing/orders/${order.order_no}`);
        const st = $('#payStatus');
        if (!st) { clearInterval(payPoll); return; }
        if (o.status === 'paid') {
          clearInterval(payPoll);
          st.className = 'badge ok';
          st.textContent = '支付成功，权益已发放';
          toast('支付成功', 'ok');
          loadPlans().catch(() => {});
        } else if (o.status !== 'pending') {
          clearInterval(payPoll);
          st.className = 'badge error';
          st.textContent = `订单${o.status === 'closed' ? '已关闭' : o.status}`;
        }
      } catch (_) { /* 轮询抖动忽略 */ }
    }, 2000);
  }
  document.addEventListener('click', (e) => {
    if (e.target.id === 'modalClose' || e.target.id === 'modal') clearInterval(payPoll);
  });

  $('#planGrid').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act="buy"]');
    if (!btn) return;
    const card = btn.closest('.plan');
    const channel = card.querySelector('.pay-channel')?.value;
    if (!channel) { toast('没有可用的支付通道，先在 .env 里配置', 'err'); return; }
    btn.disabled = true;
    try {
      const order = await api('/api/billing/orders', { method: 'POST', body: { plan_id: Number(card.dataset.id), channel } });
      showPayModal(order);
      await loadPlans();
    } catch (err) { toast(err.message, 'err'); } finally { btn.disabled = false; }
  });

  $('#orderTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const no = btn.closest('tr').dataset.no;
    try {
      if (btn.dataset.act === 'pay') {
        showPayModal(await api(`/api/billing/orders/${no}`));
      } else {
        await api(`/api/billing/orders/${no}/cancel`, { method: 'POST' });
        toast('已取消', 'ok');
        await loadPlans();
      }
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 后台 ────────────────────────────────────────────────────────────
  const adminToken = () => localStorage.getItem('ldm_admin_token') || '';
  function adminHeaders() {
    const t = adminToken();
    return t ? { 'X-Admin-Token': t } : {};
  }
  async function adminApi(path, { method = 'GET', body } = {}) {
    const opts = { method, headers: { ...adminHeaders() } };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return res.json();
  }

  $('#btnSaveToken').addEventListener('click', () => {
    localStorage.setItem('ldm_admin_token', $('#adminToken').value.trim());
    toast('已记住 token（存在本地浏览器）', 'ok');
    loadAdmin().catch((e) => toast(e.message, 'err'));
  });

  async function loadAdmin() {
    $('#adminToken').value = adminToken();
    let cfg = null;
    try { cfg = await adminApi('/api/billing/config'); } catch (err) {
      $('#billingConfig').innerHTML = `<div class="k">读取失败</div><div class="v" style="color:var(--err)">${esc(err.message)}</div>`;
    }
    if (cfg) {
      $('#billingConfig').innerHTML = [
        ['计费开关', cfg.billing_enabled ? '<span class="badge ok">开启</span>' : '<span class="badge">关闭</span>'],
        ['强制付费', cfg.enforce ? '<span class="badge starting">开启（无权益不能开设备）</span>' : '<span class="badge">关闭</span>'],
        ['站点地址', `<code class="cmd">${esc(cfg.site_base_url)}</code>`],
        ['支付通道', (cfg.channels || []).map((c) => `<span class="badge ${c.ready ? 'ok' : 'error'}">${esc(c.label)}${c.ready ? '' : ' 未配置'}</span>`).join(' ')],
        ['支付宝', cfg.alipay_configured ? '<span class="badge ok">已配置</span>' : '<span class="badge">未配置</span>'],
        ['微信支付', cfg.wechat_configured ? '<span class="badge ok">已配置</span>' : '<span class="badge">未配置</span>'],
        ['回调地址', `支付宝 <code class="cmd">${esc(cfg.notify_urls.alipay)}</code><br>微信 <code class="cmd">${esc(cfg.notify_urls.wechat)}</code>`],
        ['订单有效期', `${cfg.order_ttl_minutes} 分钟`],
        ['Admin Token', cfg.admin_token_set ? '<span class="badge ok">已设置</span>' : '<span class="badge">未设置（接口无保护）</span>'],
      ].map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('');
    }

    const plans = await api('/api/billing/plans?include_disabled=true');
    const num = (v, field, id) => `<input class="cell" type="number" step="${field === 'cpu_limit' ? '0.5' : '1'}" value="${v ?? ''}" data-id="${id}" data-field="${field}" />`;
    $('#planAdminTable tbody').innerHTML = plans.items.map((p) => `
      <tr data-id="${p.id}">
        <td>${p.id}</td>
        <td><span class="img-sub">${esc(p.code)}</span></td>
        <td>${esc(p.name)}</td>
        <td>${num(p.spec.width, 'width', p.id)}×${num(p.spec.height, 'height', p.id)}<br>
            dpi ${num(p.spec.dpi, 'dpi', p.id)}</td>
        <td>内存 ${num(p.spec.memory_mb, 'memory_mb', p.id)}<br>
            CPU ${num(p.spec.cpu_limit, 'cpu_limit', p.id)}<br>
            设备 ${num(p.spec.max_devices, 'max_devices', p.id)} 任务 ${num(p.spec.max_tasks, 'max_tasks', p.id)}</td>
        <td>${num(p.duration_days, 'duration_days', p.id)} 天</td>
        <td>${num(p.price_cents, 'price_cents', p.id)} 分<br><span class="img-sub">${yuan(p.price_cents)}</span></td>
        <td><input type="checkbox" data-id="${p.id}" data-field="enabled" ${p.enabled ? 'checked' : ''} /></td>
        <td><button class="btn small danger" data-act="delete">删除</button></td>
      </tr>`).join('') || '<tr><td colspan="9">还没有套餐</td></tr>';
  }

  $('#planAdminTable').addEventListener('change', async (e) => {
    const el = e.target.closest('[data-field]');
    if (!el) return;
    const field = el.dataset.field;
    const value = el.type === 'checkbox' ? el.checked : Number(el.value);
    try {
      await adminApi(`/api/billing/plans/${el.dataset.id}`, { method: 'PATCH', body: { [field]: value } });
      toast(`已保存 ${field}`, 'ok');
      await loadAdmin();
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#planAdminTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act="delete"]');
    if (!btn) return;
    if (!confirm('删除该套餐？有历史订单时会自动改为下架。')) return;
    try {
      const r = await adminApi(`/api/billing/plans/${btn.closest('tr').dataset.id}`, { method: 'DELETE' });
      toast(r.message || '已删除', 'ok');
      await loadAdmin();
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#planForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = {
      code: f.get('code'), name: f.get('name'),
      width: Number(f.get('width')), height: Number(f.get('height')), dpi: Number(f.get('dpi')),
      memory_mb: Number(f.get('memory_mb') || 0), cpu_limit: Number(f.get('cpu_limit') || 0),
      max_devices: Number(f.get('max_devices') || 1), max_tasks: Number(f.get('max_tasks') || 5),
      duration_days: Number(f.get('duration_days') || 30),
      price_cents: Math.round(Number(f.get('price_yuan') || 0) * 100),
      original_price_cents: f.get('original_price_yuan') ? Math.round(Number(f.get('original_price_yuan')) * 100) : null,
      allow_proxy: f.get('allow_proxy') === 'on',
      allow_recording: f.get('allow_recording') === 'on',
      allow_audio: f.get('allow_audio') === 'on',
    };
    try {
      await adminApi('/api/billing/plans', { method: 'POST', body });
      e.target.reset();
      toast('套餐已创建', 'ok');
      await loadAdmin();
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
    apps: loadAppsPage,
    proxies: loadProxies,
    tasks: loadTasks,
    data: loadData,
    recordings: loadRecordings,
    plans: loadPlans,
    admin: loadAdmin,
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
