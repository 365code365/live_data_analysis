/* 后台管理端：系统概览、设备运维、代理池、定价、订单、事件日志。
   这里所有请求都带 X-Admin-Token；用户前台（/）看不到这些内容。 */
(() => {
  'use strict';

  const {
    $, esc, api, toast, modal, bindModal, fmtTime, fmtNum, fmtSize, yuan,
    empty, mountThemePicker, mountNav, getToken, setToken,
  } = window.LDM;

  const S = {
    view: 'overview', timer: null, auth: null,
    devices: [], proxies: [], plans: [], pullTimer: null,
  };

  bindModal();
  mountThemePicker($('#themePicker'));

  const adminApi = (path, opts = {}) => api(path, { ...opts, admin: true });

  // ── 令牌闸门 ────────────────────────────────────────────────────────
  function showGate(message) {
    $('#gate').hidden = false;
    $('#shell').hidden = true;
    const box = $('#gateErr');
    box.hidden = !message;
    if (message) box.innerHTML = `<div class="alert-body">${esc(message)}</div>`;
    $('#gateToken').focus();
  }

  async function boot() {
    let auth;
    try {
      auth = await api('/api/system/auth');
    } catch (err) {
      showGate(`无法连接控制器：${err.message}`);
      return;
    }
    S.auth = auth;
    $('#verText').textContent = `v${auth.version}`;

    if (auth.admin_required && !auth.admin_ok) {
      showGate(getToken() ? '令牌无效或已变更，请重新输入。' : '');
      return;
    }

    $('#gate').hidden = true;
    $('#shell').hidden = false;
    $('#noTokenWarn').hidden = auth.admin_required;
    $('#authBadge').className = auth.admin_required ? 'badge ok' : 'badge starting';
    $('#authBadge').textContent = auth.admin_required ? '已登录' : '未设密码';
    $('#logoutBtn').hidden = !auth.admin_required;

    // 令牌重试可能多次进 boot，导航只挂一次
    if (!S.mounted) {
      S.mounted = true;
      mountNav({ onChange: (view) => { S.view = view; refresh(); } });
      setupTimer();
    }
    refresh();
  }

  $('#gateForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    setToken($('#gateToken').value.trim());
    await boot();
  });

  $('#logoutBtn').addEventListener('click', () => {
    setToken('');
    location.reload();
  });

  $('#refreshBtn').addEventListener('click', () => refresh(true));
  $('#autoRefresh').addEventListener('change', setupTimer);
  function setupTimer() {
    clearInterval(S.timer);
    if ($('#autoRefresh').checked) S.timer = setInterval(() => refresh(), 15000);
  }

  // ── 系统概览 ────────────────────────────────────────────────────────
  async function loadOverview() {
    const [info, stats] = await Promise.all([adminApi('/api/system/info'), api('/api/stats')]);
    const d = info.docker || {};

    $('#statGrid').innerHTML = [
      ['云手机', stats.devices, `${stats.devices_running} 台运行中`],
      ['监控任务', stats.tasks, `${stats.tasks_enabled} 个启用`],
      ['直播快照', stats.snapshots, '累计'],
      ['商品记录', stats.products, '累计'],
      ['录像', stats.recordings, '累计'],
      ['受管容器', (d.managed_containers || []).length, d.ok ? 'Docker 正常' : 'Docker 异常'],
    ].map(([label, num, trend]) => `
      <div class="stat"><div class="num">${fmtNum(num)}</div>
        <div class="label">${esc(label)}</div><div class="trend">${esc(trend)}</div></div>`).join('');

    const tools = info.tools || {};
    const flag = (ok) => (ok ? '<span class="badge ok">可用</span>' : '<span class="badge error">缺失</span>');
    $('#envInfo').innerHTML = kv([
      ['控制器版本', esc(info.version)],
      ['Python', esc(info.python)],
      ['Docker', d.ok ? `<span class="badge ok">已连接</span> <span class="sub">${esc(d.server || '')}</span>`
        : `<span class="badge error">不可用</span><span class="cell-sub">${esc(d.error || '')}</span>`],
      ['adb', flag(tools.adb)],
      ['ffmpeg', flag(tools.ffmpeg)],
      ['ffprobe', flag(tools.ffprobe)],
      ['调度器', info.scheduler.running
        ? `<span class="badge ok">运行中</span> <span class="sub">${info.scheduler.jobs.length} 个作业</span>`
        : '<span class="badge error">未运行</span>'],
    ]);

    const st = info.settings || {};
    $('#settingsInfo').innerHTML = kv([
      ['安卓镜像', `<code>${esc(st.redroid_image)}</code>`],
      ['网关镜像', `<code>${esc(st.gateway_image)}</code>`],
      ['画面镜像', `<code>${esc(st.vnc_image)}</code>`],
      ['Docker 网络', `<code>${esc(st.docker_network)}</code>`],
      ['设备端口区间', `${(st.device_port_range || []).join(' - ')}`],
      ['默认采集间隔', `${st.default_interval_seconds}s`],
      ['并发采集上限', st.max_concurrent_tasks],
      ['录屏分段 / 码率', `${st.record_segment_seconds}s / ${fmtNum(st.record_bitrate)}`],
      ['选择器目录', st.selectors_dir ? `<code>${esc(st.selectors_dir)}</code>` : '<span class="sub">用内置</span>'],
      ['数据目录', `<code>${esc(st.data_dir)}</code>`],
    ]);

    renderImages(d.image_details || []);

    $('#jobTable tbody').innerHTML = info.scheduler.jobs.length
      ? info.scheduler.jobs.map((j) => `
        <tr><td>${esc(j.name || j.id)}<span class="cell-sub">${esc(j.id)}</span></td>
        <td>${fmtTime(j.next_run_time)}</td><td><span class="cell-sub">${esc(j.trigger)}</span></td></tr>`).join('')
      : '<tr><td colspan="3">没有作业</td></tr>';
  }

  function renderImages(items) {
    $('#imageList').innerHTML = items.map((i) => {
      const job = i.job;
      const pulling = job && job.state === 'pulling';
      return `
      <div class="img-row" style="padding:10px 0;border-bottom:1px solid var(--border)" data-target="${esc(i.target)}">
        <div style="min-width:190px">
          <strong>${esc(i.label)}</strong>
          <div class="img-name">${esc(i.image)}</div>
        </div>
        ${i.ready ? `<span class="badge ok">已就绪</span><span class="img-sub">${fmtSize(i.size_mb)}</span>`
          : '<span class="badge error">缺失</span>'}
        <span style="flex:1"></span>
        ${pulling
          ? `<div class="progress" style="width:220px"><div class="bar" style="width:${job.percent || 0}%"></div></div>
             <span class="img-sub">${(job.percent || 0).toFixed(0)}% · ${esc(String(job.message || '').slice(0, 40))}</span>`
          : (i.pullable ? `<button class="btn small ${i.ready ? '' : 'primary'}" data-act="pull">${i.ready ? '重新拉取' : '拉取镜像'}</button>`
            : '<span class="img-sub">本地构建</span>')}
        <button class="btn small ghost" data-act="how">怎么补齐</button>
        ${job && job.state === 'failed' ? `<div class="img-err" style="width:100%">${esc(job.error || '')}</div>` : ''}
        ${i.error ? `<div class="img-err" style="width:100%">${esc(i.error)}</div>` : ''}
      </div>`;
    }).join('') || empty('▣', '读不到镜像信息', 'Docker 可能不可用，先看上面的运行环境。');

    const anyPulling = items.some((i) => i.job && i.job.state === 'pulling');
    clearInterval(S.pullTimer);
    if (anyPulling) {
      S.pullTimer = setInterval(async () => {
        try {
          const r = await adminApi('/api/system/images');
          renderImages(r.items || []);
        } catch (_) { clearInterval(S.pullTimer); }
      }, 1500);
    }
  }

  $('#imageList').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const row = btn.closest('[data-target]');
    const target = row.dataset.target;
    if (btn.dataset.act === 'how') {
      const r = await adminApi('/api/system/images');
      const item = (r.items || []).find((i) => i.target === target) || {};
      modal(`补齐${item.label || target}`, `
        <p>需要在<strong>宿主机</strong>（不是控制器容器里）执行：</p>
        <pre class="log">${esc(item.hint || '')}</pre>
        <p class="hint">网关与画面镜像是本项目自带 Dockerfile 的本地镜像，必须本地构建；安卓镜像可以直接在这里点「拉取镜像」。</p>`);
      return;
    }
    if (btn.dataset.act === 'pull') {
      btn.disabled = true;
      try {
        await adminApi(`/api/system/images/${target}/pull`, { method: 'POST' });
        toast('已开始拉取，进度实时刷新', 'ok');
        renderImages((await adminApi('/api/system/images')).items || []);
      } catch (err) { toast(err.message, 'err'); btn.disabled = false; }
    }
  });

  // ── 设备运维 ────────────────────────────────────────────────────────
  async function loadContainers() {
    const [devices, containers] = await Promise.all([
      api('/api/devices'), adminApi('/api/system/containers'),
    ]);
    S.devices = devices;
    $('#navDevices').textContent = devices.length;

    $('#deviceTable tbody').innerHTML = devices.length ? devices.map((d) => {
      const cs = d.container_states || {};
      const chip = (role, label) => {
        const st = cs[role];
        return `<span class="badge ${st === 'running' ? 'ok' : (st ? 'error' : '')}" title="${esc(st || '不存在')}">${label}</span>`;
      };
      return `
      <tr data-id="${d.id}">
        <td>${esc(d.name)}<span class="cell-sub">#${d.id}</span></td>
        <td><span class="badge ${d.status}">${esc(d.status)}</span>${d.recording ? '<span class="badge recording">录屏</span>' : ''}</td>
        <td><div class="row-actions">${chip('gw', '网关')}${chip('android', '安卓')}${chip('vnc', '画面')}</div></td>
        <td><span class="cell-sub">${d.width}×${d.height} @${d.dpi}<br>${d.memory_mb || '∞'}MB · ${d.cpu_limit || '∞'} 核</span></td>
        <td><span class="cell-sub">adb ${d.adb_port}<br>画面 ${d.novnc_port}${d.audio_port ? `<br>音频 ${d.audio_port}` : ''}</span></td>
        <td>${esc(d.egress_ip || '-')}<span class="cell-sub">${esc(d.egress_region || d.proxy_name || '直连')}</span></td>
        <td class="wrap">${d.last_error ? `<span style="color:var(--err)" class="cell-sub">${esc(d.last_error.slice(0, 160))}</span>` : '-'}</td>
        <td><div class="row-actions">
          <button class="btn small" data-act="logs">日志</button>
          <button class="btn small" data-act="shell">shell</button>
          <button class="btn small" data-act="ui">UI 树</button>
          <button class="btn small" data-act="egress">测出口</button>
          ${d.status === 'running' ? '<button class="btn small" data-act="restart">重启</button>' : '<button class="btn small primary" data-act="start">开机</button>'}
        </div></td>
      </tr>`;
    }).join('')
      : `<tr><td colspan="8">${empty('▤', '还没有设备', '用户在前台创建云手机后会出现在这里。')}</td></tr>`;

    const items = containers.items || [];
    $('#containerTable tbody').innerHTML = items.length ? items.map((c) => `
      <tr>
        <td><span class="mono">${esc(c.name)}</span></td>
        <td>${esc(c.role || '-')}</td>
        <td>${esc(c.device_id || '-')}</td>
        <td><span class="badge ${c.status === 'running' ? 'ok' : 'error'}">${esc(c.status)}</span></td>
        <td><span class="cell-sub">${esc(c.image || '-')}</span></td>
      </tr>`).join('')
      : `<tr><td colspan="5">${esc(containers.error || '没有受管容器')}</td></tr>`;
  }

  $('#deviceTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = btn.closest('tr').dataset.id;
    const act = btn.dataset.act;
    btn.disabled = true;
    try {
      if (act === 'logs') await showLogs(id);
      else if (act === 'shell') showShell(id);
      else if (act === 'ui') {
        toast('正在抓取 UI 树…');
        const r = await adminApi(`/api/devices/${id}/ui`);
        modal(`设备 ${id} · UI 树`, `<pre class="log">${esc(JSON.stringify(r, null, 2))}</pre>`, { wide: true });
      } else if (act === 'egress') {
        const r = await api(`/api/devices/${id}/egress`);
        toast(r.ok ? `出口 IP ${r.ip || '-'} ${r.country || ''}` : `失败: ${r.error}`, r.ok ? 'ok' : 'err');
        await loadContainers();
      } else if (act === 'restart') {
        toast('正在重启…');
        await api(`/api/devices/${id}/restart`, { method: 'POST' });
        await loadContainers();
      } else if (act === 'start') {
        toast('正在开机…');
        await api(`/api/devices/${id}/start`, { method: 'POST' });
        await loadContainers();
      }
    } catch (err) { toast(err.message, 'err'); }
    finally { btn.disabled = false; }
  });

  async function showLogs(id, role = 'gw') {
    const r = await adminApi(`/api/devices/${id}/logs?role=${role}&tail=400`);
    modal(`设备 ${id} · 容器日志`, `
      <div class="form-row tight" style="margin-bottom:10px">
        ${['gw', 'android', 'vnc'].map((x) => `<button class="btn small ${x === role ? 'primary' : ''}" data-role="${x}">${{ gw: '网关', android: '安卓', vnc: '画面' }[x]}</button>`).join('')}
        <span class="sub mono">${esc(r.container)}</span>
      </div>
      <pre class="log" style="max-height:56vh">${esc(r.logs || '（没有输出）')}</pre>`, { wide: true });
    $('#modalBody').querySelectorAll('[data-role]').forEach((b) => {
      b.addEventListener('click', () => showLogs(id, b.dataset.role).catch((err) => toast(err.message, 'err')));
    });
  }

  function showShell(id) {
    modal(`设备 ${id} · adb shell`, `
      <div class="form-row tight">
        <label class="grow">命令<input id="shellCmd" placeholder="getprop sys.boot_completed" /></label>
        <button class="btn primary" id="shellRun">执行</button>
      </div>
      <p class="hint">直接在安卓里跑命令，属于调试能力，前台不开放。</p>
      <pre class="log" id="shellOut" style="margin-top:12px">（等待执行）</pre>`, { wide: true });
    const run = async () => {
      const command = $('#shellCmd').value.trim();
      if (!command) return;
      $('#shellOut').textContent = '执行中…';
      try {
        const r = await adminApi(`/api/devices/${id}/shell`, { method: 'POST', body: { command } });
        $('#shellOut').textContent = r.output || '（无输出）';
      } catch (err) { $('#shellOut').textContent = `错误: ${err.message}`; }
    };
    $('#shellRun').addEventListener('click', run);
    $('#shellCmd').addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
    $('#shellCmd').focus();
  }

  // ── 宿主自检 ────────────────────────────────────────────────────────
  async function loadHost() {
    const [caps, plat] = await Promise.all([
      adminApi('/api/system/host-check'), adminApi('/api/system/platforms'),
    ]);
    const yes = (ok) => (ok ? '<span class="badge ok">支持</span>' : '<span class="badge error">缺失</span>');
    $('#hostInfo').innerHTML = kv([
      ['内核版本', `<code>${esc(caps.kernel)}</code>`],
      ['binder（安卓容器）', yes(caps.binder)],
      ['tun（代理网关）', yes(caps.tun)],
      ['Docker Desktop 内核', caps.docker_desktop ? '<span class="badge starting">是</span>' : '否'],
      ['能跑安卓容器', caps.android_supported ? '<span class="badge ok">可以</span>' : '<span class="badge error">不行</span>'],
    ]);
    $('#hostAlerts').innerHTML = (caps.hints || []).map((h) => `
      <div class="alert"><div class="alert-title">宿主环境不满足条件</div>
      <div class="alert-body">${esc(h)}</div></div>`).join('')
      || '<div class="alert info"><div class="alert-title">宿主环境正常</div><div class="alert-body">binder 与 tun 都就绪，安卓容器和代理网关都能跑。</div></div>';

    $('#platformTable tbody').innerHTML = (plat.platforms || []).map((p) => `
      <tr><td>${esc(p.display_name)}<span class="cell-sub">${esc(p.key)}</span></td>
      <td><span class="cell-sub">包名 ${esc(p.package || '未配置')}</span></td>
      <td>${(p.config_files || []).length
        ? p.config_files.map((f) => `<span class="cell-sub mono">${esc(f)}</span>`).join('')
        : '<span class="sub">用内置默认</span>'}</td></tr>`).join('')
      || '<tr><td colspan="3">没有适配器</td></tr>';
  }

  $('#reloadSelectors').addEventListener('click', async (e) => {
    e.target.disabled = true;
    try {
      const r = await adminApi('/api/system/selectors/reload', { method: 'POST' });
      toast(r.message, 'ok');
      await loadHost();
    } catch (err) { toast(err.message, 'err'); }
    finally { e.target.disabled = false; }
  });

  // ── 事件日志 ────────────────────────────────────────────────────────
  async function loadEvents() {
    const level = $('#eventLevel').value;
    const limit = $('#eventLimit').value;
    const r = await adminApi(`/api/events?limit=${limit}${level ? `&level=${level}` : ''}`);
    $('#eventLog').innerHTML = r.items.length ? r.items.map((ev) => `
      <div class="line ${esc(ev.level)}"><span class="ts">${fmtTime(ev.created_at)}</span>
      [${esc(ev.level)}] [${esc(ev.source)}]${ev.device_id ? ` 设备${ev.device_id}` : ''}${ev.task_id ? ` 任务${ev.task_id}` : ''}
      ${esc(ev.message)}</div>`).join('')
      : '<div class="line info">暂无事件</div>';
  }
  $('#eventReload').addEventListener('click', () => loadEvents().catch((e) => toast(e.message, 'err')));
  $('#eventLevel').addEventListener('change', () => loadEvents().catch((e) => toast(e.message, 'err')));

  // ── 代理池 ──────────────────────────────────────────────────────────
  async function loadProxies() {
    const proxies = await adminApi('/api/proxies');
    S.proxies = proxies;
    $('#navProxies').textContent = proxies.length;
    $('#proxyTable tbody').innerHTML = proxies.length ? proxies.map((p) => `
      <tr data-id="${p.id}">
        <td>${esc(p.name)}<span class="cell-sub">#${p.id}${p.in_use ? ' · 使用中' : ''}</span></td>
        <td><span class="mono">${esc(p.url_masked)}</span></td>
        <td>${p.enabled ? '<span class="badge ok">启用</span>' : '<span class="badge">停用</span>'}
            ${p.last_status ? `<span class="cell-sub" style="color:${p.last_status === 'ok' ? 'var(--ok)' : 'var(--err)'}">${esc(p.last_status.slice(0, 60))}</span>` : ''}</td>
        <td>${esc(p.last_egress_ip || '-')}<span class="cell-sub">${esc(p.last_egress_region || '')}</span></td>
        <td><span class="cell-sub">${fmtTime(p.last_checked_at)}</span></td>
        <td class="wrap"><span class="cell-sub">${esc(p.remark || '')}</span></td>
        <td><div class="row-actions">
          <button class="btn small" data-act="test">验证</button>
          <button class="btn small" data-act="toggle">${p.enabled ? '停用' : '启用'}</button>
          <button class="btn small danger" data-act="del">删除</button>
        </div></td>
      </tr>`).join('')
      : `<tr><td colspan="7">${empty('⇄', '还没有代理', '加一条 socks5/http 代理，设备启动时会全局透明走它出网。')}</td></tr>`;
  }

  function proxyFormData() {
    const f = new FormData($('#proxyForm'));
    return {
      name: f.get('name'),
      scheme: f.get('scheme'),
      host: f.get('host'),
      port: Number(f.get('port')),
      username: f.get('username') || null,
      password: f.get('password') || null,
      remark: f.get('remark') || null,
    };
  }

  $('#proxyForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await adminApi('/api/proxies', { method: 'POST', body: proxyFormData() });
      e.target.reset();
      toast('已保存', 'ok');
      await loadProxies();
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#proxyProbe').addEventListener('click', async (e) => {
    const body = proxyFormData();
    if (!body.host || !body.port) { toast('先填主机和端口', 'err'); return; }
    e.target.disabled = true;
    toast('正在起临时网关验证，约 10-30 秒…');
    try {
      const r = await adminApi('/api/proxies/probe', { method: 'POST', body });
      toast(r.ok ? `通了：${r.ip || ''} ${r.country || ''} ${r.city || ''}` : `不通: ${r.error}`, r.ok ? 'ok' : 'err');
    } catch (err) { toast(err.message, 'err'); }
    finally { e.target.disabled = false; }
  });

  $('#proxyTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = btn.closest('tr').dataset.id;
    const act = btn.dataset.act;
    btn.disabled = true;
    try {
      if (act === 'test') {
        toast('正在验证，约 10-30 秒…');
        const r = await adminApi(`/api/proxies/${id}/test`, { method: 'POST' });
        toast(r.ok ? `通了：${r.ip || ''} ${r.country || ''}` : `不通: ${r.error}`, r.ok ? 'ok' : 'err');
      } else if (act === 'toggle') {
        const p = S.proxies.find((x) => String(x.id) === id);
        await adminApi(`/api/proxies/${id}`, { method: 'PATCH', body: { enabled: !p.enabled } });
      } else if (act === 'del') {
        if (!confirm('删除这条代理？')) return;
        await adminApi(`/api/proxies/${id}`, { method: 'DELETE' });
        toast('已删除', 'ok');
      }
      await loadProxies();
    } catch (err) { toast(err.message, 'err'); }
    finally { btn.disabled = false; }
  });

  // ── 定价 ────────────────────────────────────────────────────────────
  async function loadPricing() {
    const r = await api('/api/billing/plans?include_disabled=true');
    S.plans = r.items;
    $('#navPlans').textContent = r.items.length;

    const cell = (id, field, value, step) =>
      `<input class="cell" data-id="${id}" data-field="${field}" type="number"${step ? ` step="${step}"` : ''} value="${value ?? ''}" />`;

    $('#planTable tbody').innerHTML = r.items.length ? r.items.map((p) => {
      const s = p.spec;
      return `
      <tr data-id="${p.id}">
        <td><span class="mono">${esc(p.code)}</span></td>
        <td>${esc(p.name)}${p.badge ? `<span class="cell-sub">角标 ${esc(p.badge)}</span>` : ''}</td>
        <td>${cell(p.id, 'price_yuan', p.price_yuan, '0.01')}
            ${p.original_price_yuan ? `<span class="cell-sub">原价 ${p.original_price_yuan}</span>` : ''}</td>
        <td>${cell(p.id, 'duration_days', p.duration_days)}</td>
        <td>${cell(p.id, 'width', s.width)}×${cell(p.id, 'height', s.height)}</td>
        <td>${cell(p.id, 'dpi', s.dpi)}</td>
        <td>${cell(p.id, 'memory_mb', s.memory_mb)}</td>
        <td>${cell(p.id, 'cpu_limit', s.cpu_limit, '0.5')}</td>
        <td>${cell(p.id, 'max_devices', s.max_devices)}</td>
        <td>${cell(p.id, 'max_tasks', s.max_tasks)}</td>
        <td><div class="row-actions">
          ${['allow_proxy', 'allow_recording', 'allow_audio'].map((k) => `
            <label class="switch" style="font-size:11px"><input type="checkbox" data-id="${p.id}" data-field="${k}" ${s[k] ? 'checked' : ''} />
            ${{ allow_proxy: '代理', allow_recording: '录屏', allow_audio: '声音' }[k]}</label>`).join('')}
        </div></td>
        <td>${cell(p.id, 'sort_order', p.sort_order)}</td>
        <td><label class="switch" style="font-size:11px"><input type="checkbox" data-id="${p.id}" data-field="enabled" ${p.enabled ? 'checked' : ''} /> 上架</label></td>
        <td><div class="row-actions">
          <button class="btn small" data-act="edit">改文案</button>
          <button class="btn small danger" data-act="del">删除</button>
        </div></td>
      </tr>`;
    }).join('')
      : `<tr><td colspan="14">${empty('◈', '还没有套餐', '上面填一个，前台「套餐与账单」立即可见。')}</td></tr>`;
  }

  async function patchPlan(id, field, value) {
    const body = field === 'price_yuan'
      ? { price_cents: Math.round(Number(value) * 100) }
      : { [field]: field === 'cpu_limit' ? Number(value) : (typeof value === 'boolean' ? value : Number(value)) };
    await adminApi(`/api/billing/plans/${id}`, { method: 'PATCH', body });
  }

  $('#planTable').addEventListener('change', async (e) => {
    const input = e.target.closest('input[data-field]');
    if (!input) return;
    const { id, field } = input.dataset;
    try {
      await patchPlan(id, field, input.type === 'checkbox' ? input.checked : input.value);
      toast('已保存', 'ok');
      await loadPricing();
    } catch (err) { toast(err.message, 'err'); await loadPricing(); }
  });

  $('#planTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = btn.closest('tr').dataset.id;
    const plan = S.plans.find((p) => String(p.id) === id);
    if (btn.dataset.act === 'del') {
      if (!confirm(`删除套餐「${plan.name}」？有订单的会自动改为下架。`)) return;
      try {
        const r = await adminApi(`/api/billing/plans/${id}`, { method: 'DELETE' });
        toast(r.message, 'ok');
        await loadPricing();
      } catch (err) { toast(err.message, 'err'); }
      return;
    }
    modal(`编辑「${plan.name}」文案`, `
      <div class="form-row">
        <label class="grow">名称<input id="pfName" value="${esc(plan.name)}" /></label>
        <label>角标<input id="pfBadge" maxlength="10" value="${esc(plan.badge || '')}" placeholder="推荐" /></label>
        <label>原价(元)<input id="pfOrigin" type="number" step="0.01" value="${plan.original_price_yuan ?? ''}" placeholder="可空" /></label>
      </div>
      <div class="form-row" style="margin-top:12px">
        <label class="grow">卖点描述<textarea id="pfDesc" rows="3">${esc(plan.description || '')}</textarea></label>
      </div>
      <div class="btn-group" style="margin-top:14px"><button class="btn primary" id="pfSave">保存</button></div>`);
    $('#pfSave').addEventListener('click', async () => {
      const origin = $('#pfOrigin').value;
      try {
        await adminApi(`/api/billing/plans/${id}`, {
          method: 'PATCH',
          body: {
            name: $('#pfName').value.trim(),
            badge: $('#pfBadge').value.trim() || null,
            description: $('#pfDesc').value.trim() || null,
            original_price_cents: origin === '' ? null : Math.round(Number(origin) * 100),
          },
        });
        toast('已保存', 'ok');
        window.LDM.closeModal();
        await loadPricing();
      } catch (err) { toast(err.message, 'err'); }
    });
  });

  $('#planForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const origin = f.get('original');
    try {
      await adminApi('/api/billing/plans', {
        method: 'POST',
        body: {
          code: f.get('code'),
          name: f.get('name'),
          badge: f.get('badge') || null,
          description: f.get('description') || null,
          price_cents: Math.round(Number(f.get('price') || 0) * 100),
          original_price_cents: origin ? Math.round(Number(origin) * 100) : null,
          duration_days: Number(f.get('duration_days')),
          width: Number(f.get('width')),
          height: Number(f.get('height')),
          dpi: Number(f.get('dpi')),
          memory_mb: Number(f.get('memory_mb')),
          cpu_limit: Number(f.get('cpu_limit')),
          max_devices: Number(f.get('max_devices')),
          max_tasks: Number(f.get('max_tasks')),
          sort_order: Number(f.get('sort_order')),
          allow_proxy: f.get('allow_proxy') === 'on',
          allow_recording: f.get('allow_recording') === 'on',
          allow_audio: f.get('allow_audio') === 'on',
          enabled: f.get('enabled') === 'on',
        },
      });
      e.target.reset();
      toast('套餐已创建', 'ok');
      await loadPricing();
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 订单与权益 ──────────────────────────────────────────────────────
  async function loadOrders() {
    const status = $('#orderStatus').value;
    const [orders, ents, summary] = await Promise.all([
      api(`/api/billing/orders?limit=200${status ? `&status=${status}` : ''}`),
      api('/api/billing/entitlements'),
      api('/api/billing/summary'),
    ]);

    const paid = orders.items.filter((o) => o.status === 'paid');
    const revenue = paid.reduce((sum, o) => sum + o.amount_cents, 0);
    $('#orderStats').innerHTML = [
      ['订单总数', orders.items.length, status ? `筛选: ${status}` : '全部状态'],
      ['已支付', paid.length, `待支付 ${orders.items.filter((o) => o.status === 'pending').length} 笔`],
      ['已收金额', yuan(revenue), '按当前筛选结果统计'],
      ['生效权益', (summary.active_entitlements || []).length, `云手机额度 ${summary.device_used}/${summary.device_quota}`],
    ].map(([label, num, trend]) => `
      <div class="stat"><div class="num">${typeof num === 'number' ? fmtNum(num) : esc(num)}</div>
        <div class="label">${esc(label)}</div><div class="trend">${esc(trend)}</div></div>`).join('');

    $('#orderTable tbody').innerHTML = orders.items.length ? orders.items.map((o) => `
      <tr>
        <td><span class="mono">${esc(o.order_no)}</span>${o.remark ? `<span class="cell-sub">${esc(o.remark)}</span>` : ''}</td>
        <td>${esc(o.plan_name || '-')}<span class="cell-sub">${esc(o.plan_code || '')}</span></td>
        <td>${yuan(o.amount_cents)}</td>
        <td>${esc(o.channel)}</td>
        <td><span class="badge ${o.status}">${esc(o.status)}</span>
            ${o.error ? `<span class="cell-sub" style="color:var(--err)">${esc(o.error.slice(0, 60))}</span>` : ''}</td>
        <td><span class="cell-sub">${esc(o.trade_no || '-')}<br>${esc(o.buyer || '')}</span></td>
        <td><span class="cell-sub">${fmtTime(o.created_at)}</span></td>
        <td><span class="cell-sub">${fmtTime(o.paid_at)}</span></td>
      </tr>`).join('') : '<tr><td colspan="8">没有订单</td></tr>';

    $('#entTable tbody').innerHTML = ents.items.length ? ents.items.map((x) => `
      <tr>
        <td>${x.id}<span class="cell-sub mono">${esc(x.order_no || '')}</span></td>
        <td>${esc(x.plan_name)}<span class="cell-sub">${esc(x.plan_code || '')}</span></td>
        <td>${x.max_devices} 台 / ${x.max_tasks} 任务</td>
        <td>${x.used_devices} 台<span class="cell-sub">剩 ${x.remaining_devices}</span></td>
        <td>${x.days_left ?? '-'}</td>
        <td><span class="cell-sub">${fmtTime(x.started_at)}</span></td>
        <td><span class="cell-sub">${fmtTime(x.expires_at)}</span></td>
        <td><span class="badge ${x.status}">${esc(x.status)}</span></td>
      </tr>`).join('') : '<tr><td colspan="8">还没有发放权益</td></tr>';
  }
  $('#orderReload').addEventListener('click', () => loadOrders().catch((e) => toast(e.message, 'err')));
  $('#orderStatus').addEventListener('change', () => loadOrders().catch((e) => toast(e.message, 'err')));

  // ── 支付配置 ────────────────────────────────────────────────────────
  async function loadPayCfg() {
    const cfg = await adminApi('/api/billing/config');
    const yes = (ok) => (ok ? '<span class="badge ok">已配置</span>' : '<span class="badge error">未配置</span>');
    $('#payFlags').innerHTML = kv([
      ['计费功能', cfg.billing_enabled ? '<span class="badge ok">已启用</span>' : '<span class="badge">未启用</span>'],
      ['强制付费', cfg.enforce ? '<span class="badge starting">是</span> <span class="sub">没权益不能开设备</span>' : '否 <span class="sub">可免费试用</span>'],
      ['站点地址', `<code>${esc(cfg.site_base_url)}</code>`],
      ['订单有效期', `${cfg.order_ttl_minutes} 分钟`],
      ['后台令牌', cfg.admin_token_set ? '<span class="badge ok">已设置</span>' : '<span class="badge error">未设置</span>'],
      ['支付宝商户', yes(cfg.alipay_configured)],
      ['微信商户', yes(cfg.wechat_configured)],
    ]);

    $('#channelTable tbody').innerHTML = (cfg.channels || []).map((c) => `
      <tr><td><span class="mono">${esc(c.channel)}</span></td><td>${esc(c.label)}</td>
      <td>${c.ready ? '<span class="badge ok">可用</span>' : '<span class="badge error">不可用</span>'}</td>
      <td class="wrap"><span class="cell-sub">${esc(c.reason || c.display || '')}</span></td></tr>`).join('')
      || '<tr><td colspan="4">没有启用任何渠道</td></tr>';

    $('#notifyUrls').innerHTML = kv(
      Object.entries(cfg.notify_urls || {}).map(([k, v]) => [k, `<code>${esc(v)}</code>`]),
    );
  }

  // ── 工具 ────────────────────────────────────────────────────────────
  function kv(rows) {
    return rows.map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`).join('');
  }

  // ── 调度 ────────────────────────────────────────────────────────────
  const loaders = {
    overview: loadOverview, containers: loadContainers, host: loadHost, events: loadEvents,
    proxies: loadProxies, pricing: loadPricing, orders: loadOrders, paycfg: loadPayCfg,
  };

  let busy = false;
  async function refresh(manual = false) {
    if (busy) return;
    busy = true;
    try {
      await (loaders[S.view] || loadOverview)();
      if (manual) toast('已刷新', 'ok');
    } catch (err) {
      if (err.status === 401) { setToken(''); showGate('令牌失效，请重新登录。'); return; }
      if (manual) toast(err.message, 'err');
      else console.warn('刷新失败', err);
    } finally { busy = false; }
  }

  boot();
})();
