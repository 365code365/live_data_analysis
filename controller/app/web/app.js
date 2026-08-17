/* 用户前台：云手机、应用市场、监控任务、数据、录像、套餐。
   后台内容（代理、定价、系统信息、全量日志）不在这里，见 /admin。 */
(() => {
  'use strict';

  const {
    $, $$, esc, api, toast, modal, closeModal, onModalClose, bindModal,
    fmtTime, fmtNum, fmtDur, fmtSize, yuan, empty, mountThemePicker, mountNav,
  } = window.LDM;

  const S = {
    view: 'home', timer: null, devices: [], tasks: [], catalog: [], plans: [],
    channels: [], dataTaskId: null, storeJobTimer: null, payPoll: null, quota: null,
  };

  bindModal();
  mountThemePicker($('#themePicker'));

  // ── 导航 ────────────────────────────────────────────────────────────
  const goto = mountNav({
    onChange: (view) => { S.view = view; refresh(); },
  });
  document.addEventListener('click', (e) => {
    const g = e.target.closest('[data-goto]');
    if (g) goto(g.dataset.goto);
  });
  $('#refreshBtn').addEventListener('click', () => refresh(true));
  $('#autoRefresh').addEventListener('change', setupTimer);
  function setupTimer() {
    clearInterval(S.timer);
    if ($('#autoRefresh').checked) S.timer = setInterval(() => refresh(), 15000);
  }

  // ── 后台入口只在验明身份后显示 ──────────────────────────────────────
  (async () => {
    try {
      const auth = await api('/api/system/auth');
      $('#verText').textContent = `v${auth.version}`;
      // 没设 ADMIN_TOKEN（自用模式）或已持有有效令牌，才露出后台入口
      $('#adminEntry').hidden = !(auth.admin_ok || !auth.admin_required);
    } catch (_) { /* 忽略 */ }
  })();

  // ── 总览 ────────────────────────────────────────────────────────────
  async function loadHome() {
    const [stats, devices, quota] = await Promise.all([
      api('/api/stats'), api('/api/devices'), api('/api/billing/summary').catch(() => null),
    ]);
    S.devices = devices;
    S.quota = quota;
    updateBadges();

    const cards = [
      ['云手机', stats.devices, `${stats.devices_running} 台运行中`],
      ['监控任务', stats.tasks, `${stats.tasks_enabled} 个已启用`],
      ['直播快照', stats.snapshots, '累计采集'],
      ['商品记录', stats.products, '累计采集'],
      ['录像', stats.recordings, '可在线回放'],
    ];
    $('#statGrid').innerHTML = cards.map(([label, num, trend]) => `
      <div class="stat"><div class="num">${fmtNum(num)}</div>
        <div class="label">${label}</div><div class="trend">${esc(trend)}</div></div>`).join('');

    const alertBox = $('#quotaAlert');
    if (quota && quota.billing_enabled && quota.device_quota === 0) {
      alertBox.hidden = false;
      alertBox.className = 'alert info';
      alertBox.innerHTML = `<div class="alert-title">还没有开通套餐</div>
        <div class="alert-body">当前${quota.enforce ? '需要先购买套餐才能创建云手机' : '可以免费试用，购买套餐后按规格分配资源'}。</div>
        <div style="margin-top:10px"><button class="btn primary small" data-goto="plans">查看套餐</button></div>`;
    } else {
      alertBox.hidden = true;
    }

    $('#homeDevices').innerHTML = devices.length
      ? devices.slice(0, 5).map((d) => `
        <div class="img-row" style="padding:8px 0;border-bottom:1px solid var(--border)">
          <span class="badge ${d.status}">${esc(d.status)}</span>
          <strong>${esc(d.name)}</strong>
          <span class="img-sub">${d.width}×${d.height}</span>
          <span class="spacer" style="flex:1"></span>
          <a class="btn small primary" href="/console?device=${d.id}" target="_blank" rel="noopener">进入</a>
        </div>`).join('')
      : empty('▤', '还没有云手机', '创建一台安卓实例，装上抖音/小红书就能开始监控。',
        '<button class="btn primary" data-goto="devices">新建云手机</button>');
  }

  function updateBadges() {
    $('#navDevices').textContent = S.devices.length;
    $('#navTasks').textContent = S.tasks.length;
  }

  // ── 云手机 ──────────────────────────────────────────────────────────
  async function loadDevices() {
    const [devices, quota, plans] = await Promise.all([
      api('/api/devices'),
      api('/api/billing/summary').catch(() => null),
      api('/api/billing/plans').catch(() => ({ items: [] })),
    ]);
    S.devices = devices;
    S.quota = quota;
    S.plans = plans.items || [];
    updateBadges();

    // 套餐下拉：只列还有名额的权益
    const idByCode = Object.fromEntries(S.plans.map((p) => [p.code, p.id]));
    const usable = (quota?.active_entitlements || []).filter((e) => e.remaining_devices > 0);
    $('#devicePlanSelect').innerHTML =
      (quota?.enforce ? '' : '<option value="">不绑定（试用）</option>')
      + usable.map((e) => `<option value="${idByCode[e.plan_code] || ''}">${esc(e.plan_name)}（剩 ${e.remaining_devices} 台）</option>`).join('');
    $('#deviceFormHint').textContent = quota && quota.enforce && !usable.length
      ? '当前为强制付费模式，且没有可用名额，请先购买套餐。'
      : '选了套餐时，分辨率与内存按套餐规格生效；不绑定则按下面填的参数创建。';

    $('#deviceList').innerHTML = devices.length ? devices.map(deviceCard).join('')
      : empty('▤', '还没有云手机', '点上面的「创建」开一台，安卓开机约 1-2 分钟。');
    devices.forEach((d) => { if (d.screen_ready) loadThumb(d.id); });
  }

  function deviceCard(d) {
    const cs = d.container_states || {};
    const chip = (role, label) => {
      const st = cs[role];
      return `<span class="badge ${st === 'running' ? 'ok' : (st ? 'error' : '')}">${label}</span>`;
    };
    return `
    <div class="device" data-id="${d.id}">
      <header>
        <h3>${esc(d.name)}</h3>
        <span class="badge ${d.status}">${esc(d.status)}</span>
        ${d.recording ? '<span class="badge recording">录屏中</span>' : ''}
        <span style="flex:1"></span>
        ${chip('gw', '网络')}${chip('android', '安卓')}${chip('vnc', '画面')}
      </header>

      <div class="thumb" data-act="open">
        <div class="ph" id="thumb-ph-${d.id}">${d.screen_ready ? '加载预览…' : '未开机'}</div>
        <img id="thumb-${d.id}" alt="" hidden />
        <div class="play">点击进入控制台</div>
      </div>

      <div class="kv">
        <div class="k">规格</div><div class="v">${d.width}×${d.height} @${d.dpi}dpi ·
          ${d.memory_mb ? d.memory_mb + 'MB' : '内存不限'} · ${d.cpu_limit ? d.cpu_limit + ' 核' : 'CPU 不限'}</div>
        <div class="k">出口 IP</div><div class="v">${esc(d.egress_ip || '未检测')}</div>
        <div class="k">声音</div><div class="v">${d.enable_audio ? '已开启' : '已关闭'}</div>
        ${d.last_error ? `<div class="k">最近错误</div><div class="v" style="color:var(--err)">${esc(d.last_error.slice(0, 120))}</div>` : ''}
      </div>

      <div class="btn-group">
        <a class="btn small primary" href="/console?device=${d.id}" target="_blank" rel="noopener">控制台</a>
        ${d.status === 'running'
          ? '<button class="btn small" data-act="stop">关机</button><button class="btn small" data-act="restart">重启</button>'
          : '<button class="btn small primary" data-act="start">开机</button>'}
        <button class="btn small" data-act="apps">装应用</button>
        ${d.recording ? '<button class="btn small" data-act="recstop">停止录屏</button>'
                      : '<button class="btn small" data-act="recstart">录屏</button>'}
        <button class="btn small danger" data-act="delete">删除</button>
      </div>
    </div>`;
  }

  async function loadThumb(id) {
    try {
      const res = await fetch(`/api/devices/${id}/screenshot?t=${Date.now()}`);
      if (!res.ok) return;
      const img = $(`#thumb-${id}`);
      const ph = $(`#thumb-ph-${id}`);
      if (!img) return;
      img.src = URL.createObjectURL(await res.blob());
      img.hidden = false;
      if (ph) ph.hidden = true;
    } catch (_) { /* 预览失败不影响其它功能 */ }
  }

  $('#deviceList').addEventListener('click', async (e) => {
    const card = e.target.closest('.device');
    if (!card) return;
    const id = card.dataset.id;
    const act = (e.target.closest('[data-act]') || {}).dataset?.act;
    if (!act) return;
    if (act === 'open') { window.open(`/console?device=${id}`, '_blank', 'noopener'); return; }
    if (act === 'apps') { goto('store'); $('#storeDevice').value = id; return; }

    const btn = e.target.closest('button');
    if (btn) btn.disabled = true;
    try {
      if (act === 'start') { toast('正在开机，安卓约 1-2 分钟…'); await api(`/api/devices/${id}/start`, { method: 'POST' }); }
      else if (act === 'stop') { await api(`/api/devices/${id}/stop`, { method: 'POST' }); toast('已关机', 'ok'); }
      else if (act === 'restart') { await api(`/api/devices/${id}/restart`, { method: 'POST' }); toast('已重启', 'ok'); }
      else if (act === 'recstart') { const r = await api(`/api/devices/${id}/record/start`, { method: 'POST', body: {} }); toast(`开始录屏 #${r.recording_id}`, 'ok'); }
      else if (act === 'recstop') { toast('正在合并…'); const r = await api(`/api/devices/${id}/record/stop`, { method: 'POST' }); toast(`录屏完成 #${r.recording_id}`, 'ok'); }
      else if (act === 'delete') {
        if (!confirm('删除这台云手机？')) return;
        const purge = confirm('同时清空它的安卓数据？（会丢失 App 登录态，一般选取消）');
        await api(`/api/devices/${id}?purge_data=${purge}`, { method: 'DELETE' });
        toast('已删除', 'ok');
      }
      await loadDevices();
    } catch (err) { toast(err.message, 'err'); }
    finally { if (btn) btn.disabled = false; }
  });

  $('#deviceForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api('/api/devices', {
        method: 'POST',
        body: {
          name: f.get('name'),
          plan_id: f.get('plan_id') ? Number(f.get('plan_id')) : null,
          width: f.get('width') ? Number(f.get('width')) : null,
          height: f.get('height') ? Number(f.get('height')) : null,
          dpi: f.get('dpi') ? Number(f.get('dpi')) : null,
          enable_audio: f.get('enable_audio') === 'on',
          autostart: f.get('autostart') === 'on',
        },
      });
      e.target.reset();
      toast('已创建，正在开机', 'ok');
      await loadDevices();
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 应用市场 ────────────────────────────────────────────────────────
  const runningDevices = () => S.devices.filter((d) => d.status === 'running');

  function deviceOptions(selected) {
    const list = runningDevices();
    if (!list.length) return '<option value="">没有运行中的云手机</option>';
    return list.map((d) => `<option value="${d.id}" ${String(d.id) === String(selected) ? 'selected' : ''}>${esc(d.name)}</option>`).join('');
  }

  async function loadStore() {
    const [cat, local, devices] = await Promise.all([
      api('/api/apps/catalog'), api('/api/apps/local'), api('/api/devices'),
    ]);
    S.devices = devices;
    S.catalog = cat.items;
    updateBadges();

    const cur = $('#storeDevice').value;
    $('#storeDevice').innerHTML = deviceOptions(cur);
    const target = $('#storeDevice').value;

    // 已装应用（拿第一台运行中的设备做参考）
    let installed = [];
    if (target) {
      try { installed = (await api(`/api/devices/${target}/apps`)).items || []; } catch (_) {}
    }
    const installedPkgs = new Set(installed.map((a) => a.package));

    const initial = (name) => (name || '?').trim().charAt(0).toUpperCase();
    $('#storeGrid').innerHTML = cat.items.map((a) => {
      const done = a.package && installedPkgs.has(a.package);
      return `
      <div class="app-card" data-key="${esc(a.key)}" data-pkg="${esc(a.package || '')}">
        <div class="app-top">
          <div class="app-icon">${esc(initial(a.name))}</div>
          <div style="min-width:0">
            <div class="app-name">${esc(a.name)}</div>
            <div class="app-meta">${esc(a.category)} · ${esc(a.package || '包名未知')}</div>
          </div>
        </div>
        <div class="app-note">${esc(a.note || '')}</div>
        <div class="app-actions">
          ${done ? '<span class="badge ok">已安装</span>' : (a.installable ? '<span class="app-tag">可一键装</span>' : '<span class="badge error">需自备安装包</span>')}
          <span style="flex:1"></span>
          ${done ? '<button class="btn small primary" data-act="open">打开</button>' : ''}
          ${a.installable ? `<button class="btn small ${done ? '' : 'primary'}" data-act="install">${done ? '重装' : '安装'}</button>` : ''}
          ${!a.installable ? '<button class="btn small" data-act="why">怎么装</button>' : ''}
        </div>
      </div>`;
    }).join('');

    $('#installedHint').textContent = target
      ? `云手机「${(runningDevices().find((d) => String(d.id) === target) || {}).name || ''}」上的第三方应用`
      : '先开机一台云手机';
    $('#installedGrid').innerHTML = installed.length ? installed.map((a) => `
      <div class="app-card" data-pkg="${esc(a.package)}">
        <div class="app-top">
          <div class="app-icon">${esc((a.name || a.package).charAt(0).toUpperCase())}</div>
          <div style="min-width:0">
            <div class="app-name">${esc(a.name || a.package.split('.').pop())}</div>
            <div class="app-meta">${esc(a.package)}</div>
          </div>
        </div>
        <div class="app-actions">
          <button class="btn small primary" data-act="open">打开</button>
          <span style="flex:1"></span>
          <button class="btn small danger" data-act="uninstall">卸载</button>
        </div>
      </div>`).join('')
      : empty('⊞', '这台云手机还没装第三方应用', '在上面的应用市场里点「安装」，或上传自己的 apk。');

    local.items.length && ($('#installedHint').textContent += ` · 已上传 ${local.items.length} 个安装包`);
    renderStoreJob(target);
  }

  // 装完直接在本页打开：启动 App 并弹出实时画面
  async function openAppOnDevice(pkg) {
    const id = $('#storeDevice').value;
    if (!id) { toast('先开机一台云手机', 'err'); return; }
    const dev = S.devices.find((d) => String(d.id) === String(id));
    try {
      if (pkg) await api(`/api/devices/${id}/launch/${pkg}`, { method: 'POST' });
      showScreenModal(dev, pkg);
    } catch (err) { toast(err.message, 'err'); }
  }

  function showScreenModal(dev, pkg) {
    if (!dev) return;
    const scale = Math.min(1, (window.innerHeight * 0.62) / dev.height);
    const w = Math.round(dev.width * scale);
    const h = Math.round(dev.height * scale);
    const url = `http://${location.hostname}:${dev.novnc_port}/screen.html`
      + `?password=${encodeURIComponent(dev.vnc_password || '')}&scale=1&reconnect=1`;
    modal(`${dev.name}${pkg ? ' · ' + pkg : ''}`, `
      <div style="display:flex;flex-direction:column;align-items:center;gap:12px">
        <iframe class="screen-modal-frame" src="${url}" style="width:${w}px;height:${h}px"
                title="云手机画面" allow="clipboard-read; clipboard-write"></iframe>
        <div class="btn-group">
          <button class="btn small" data-key="KEYCODE_BACK">返回</button>
          <button class="btn small" data-key="KEYCODE_HOME">主页</button>
          <button class="btn small" data-key="KEYCODE_APP_SWITCH">任务</button>
          <a class="btn small primary" href="/console?device=${dev.id}" target="_blank" rel="noopener">完整控制台</a>
        </div>
        <div class="sub">可以直接在画面里点、划、输入；需要粘贴中文或听声音请用完整控制台。</div>
      </div>`, { wide: true });

    $('#modalBody').addEventListener('click', async (e) => {
      const k = e.target.closest('[data-key]');
      if (!k) return;
      try { await api(`/api/devices/${dev.id}/key/${k.dataset.key}`, { method: 'POST' }); }
      catch (err) { toast(err.message, 'err'); }
    });
  }

  $('#storeGrid').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const card = btn.closest('.app-card');
    const key = card.dataset.key;
    const pkg = card.dataset.pkg;
    const item = S.catalog.find((i) => i.key === key) || {};

    if (btn.dataset.act === 'why') {
      modal(item.name || key, `<div class="alert warn" style="margin:0">
        <div class="alert-title">这个应用没有官方稳定直链</div>
        <div class="alert-body">${esc(item.note || '')}</div>
        ${item.page ? `<p class="hint">官方下载页：<a href="${esc(item.page)}" target="_blank" rel="noopener">${esc(item.page)}</a></p>` : ''}
        <p class="hint">下载 apk 后用上方「自有安装包 → 上传并安装」，或把直链粘到「apk 直链」。</p></div>`);
      return;
    }
    if (btn.dataset.act === 'open') { openAppOnDevice(pkg); return; }
    if (btn.dataset.act === 'install') {
      const id = $('#storeDevice').value;
      if (!id) { toast('先开机一台云手机', 'err'); return; }
      try {
        await api(`/api/devices/${id}/apps/install`, { method: 'POST', body: { source: 'catalog', key } });
        toast('已开始安装', 'ok');
        pollStoreJob(id);
      } catch (err) { toast(err.message, 'err'); }
    }
  });

  $('#installedGrid').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const pkg = btn.closest('.app-card').dataset.pkg;
    const id = $('#storeDevice').value;
    if (btn.dataset.act === 'open') { openAppOnDevice(pkg); return; }
    if (btn.dataset.act === 'uninstall') {
      if (!confirm(`卸载 ${pkg}？`)) return;
      try {
        await api(`/api/devices/${id}/apps/${pkg}`, { method: 'DELETE' });
        toast('已卸载', 'ok');
        await loadStore();
      } catch (err) { toast(err.message, 'err'); }
    }
  });

  $('#storeDevice').addEventListener('change', () => loadStore().catch((e) => toast(e.message, 'err')));

  $('#storeUpload').addEventListener('click', async () => {
    const f = $('#storeFile').files[0];
    const id = $('#storeDevice').value;
    if (!f) { toast('先选一个 apk', 'err'); return; }
    if (!id) { toast('先开机一台云手机', 'err'); return; }
    const btn = $('#storeUpload');
    btn.disabled = true;
    try {
      const form = new FormData();
      form.append('file', f);
      toast(`上传中 ${(f.size / 1048576).toFixed(1)}MB…`);
      const up = await api('/api/apps/upload', { method: 'POST', form });
      await api(`/api/devices/${id}/apps/install`, { method: 'POST', body: { source: 'local', filename: up.filename } });
      toast('已开始安装', 'ok');
      pollStoreJob(id);
    } catch (err) { toast(err.message, 'err'); }
    finally { btn.disabled = false; }
  });

  $('#storeUrlInstall').addEventListener('click', async () => {
    const url = $('#storeUrl').value.trim();
    const id = $('#storeDevice').value;
    if (!/^https?:\/\//.test(url)) { toast('请填 http(s) 开头的 apk 直链', 'err'); return; }
    if (!id) { toast('先开机一台云手机', 'err'); return; }
    try {
      await api(`/api/devices/${id}/apps/install`, { method: 'POST', body: { source: 'url', url } });
      toast('已开始下载安装', 'ok');
      pollStoreJob(id);
    } catch (err) { toast(err.message, 'err'); }
  });

  function renderStoreJob(deviceId, job) {
    const box = $('#storeJob');
    if (!job) { if (!deviceId) box.hidden = true; return; }
    box.hidden = false;
    $('#storeJobBar').style.width = `${job.percent || 0}%`;
    const label = { downloading: '下载', installing: '安装', done: '完成', failed: '失败' }[job.state] || job.state;
    $('#storeJobMsg').innerHTML = job.state === 'failed'
      ? `<span style="color:var(--err)">${esc(job.error || '失败')}</span>`
      : `${esc(job.name)} · ${label} · ${esc(String(job.message || '').slice(0, 60))}`;
  }

  function pollStoreJob(deviceId) {
    clearInterval(S.storeJobTimer);
    S.storeJobTimer = setInterval(async () => {
      try {
        const r = await api(`/api/devices/${deviceId}/apps/job`);
        renderStoreJob(deviceId, r.job);
        if (!r.job || ['done', 'failed'].includes(r.job.state)) {
          clearInterval(S.storeJobTimer);
          if (r.job && r.job.state === 'done') { toast('安装完成', 'ok'); loadStore().catch(() => {}); }
        }
      } catch (_) { clearInterval(S.storeJobTimer); }
    }, 1200);
  }

  // ── 监控任务 ────────────────────────────────────────────────────────
  async function loadTasks() {
    const [tasks, devices] = await Promise.all([api('/api/tasks'), api('/api/devices')]);
    S.tasks = tasks;
    S.devices = devices;
    updateBadges();

    const opts = '<option value="">自动挑一台</option>'
      + devices.map((d) => `<option value="${d.id}">${esc(d.name)}（${d.status}）</option>`).join('');
    if ($('#taskDeviceSelect').innerHTML !== opts) $('#taskDeviceSelect').innerHTML = opts;

    $('#taskTable tbody').innerHTML = tasks.length ? tasks.map((t) => `
      <tr data-id="${t.id}">
        <td>${esc(t.name)}<span class="cell-sub">#${t.id}</span></td>
        <td>${t.platform === 'douyin' ? '抖音' : '小红书'}</td>
        <td class="wrap"><span class="cell-sub">${esc(t.target)}</span></td>
        <td>${esc(t.device_name || '自动')}</td>
        <td>${t.interval_seconds}s</td>
        <td><span class="badge ${t.last_status || ''}">${esc(t.last_status || '未运行')}</span>
            ${t.enabled ? '' : '<span class="badge error">停用</span>'}
            ${t.last_error ? `<span class="cell-sub" style="color:var(--warn)">${esc(t.last_error.slice(0, 60))}</span>` : ''}</td>
        <td>${t.run_count}${t.fail_count ? ` <span style="color:var(--err)">/${t.fail_count}</span>` : ''}</td>
        <td><span class="cell-sub">${fmtTime(t.next_run_at)}</span></td>
        <td><div class="row-actions">
          <button class="btn small" data-act="run">执行</button>
          <button class="btn small" data-act="toggle">${t.enabled ? '停用' : '启用'}</button>
          <button class="btn small" data-act="data">数据</button>
          <button class="btn small danger" data-act="del">删除</button>
        </div></td>
      </tr>`).join('')
      : `<tr><td colspan="9">${empty('◷', '还没有监控任务', '填直播间标识就能开始定时采集直播间信息与商品。')}</td></tr>`;
  }

  $('#taskTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = btn.closest('tr').dataset.id;
    btn.disabled = true;
    try {
      const act = btn.dataset.act;
      if (act === 'run') {
        toast('正在执行一次采集，可能 20-60 秒…');
        const r = await api(`/api/tasks/${id}/run?wait=true`, { method: 'POST' });
        if (r.ok) {
          toast(`完成：${r.is_live ? '直播中' : '未在直播'}，商品 ${r.products.length} 件`, 'ok');
          modal('本次采集结果', `<pre class="log">${esc(JSON.stringify(r, null, 2))}</pre>`);
        } else toast(`失败: ${r.error}`, 'err');
      } else if (act === 'toggle') {
        const t = S.tasks.find((x) => String(x.id) === id);
        await api(`/api/tasks/${id}`, { method: 'PATCH', body: { enabled: !t.enabled } });
      } else if (act === 'data') {
        S.dataTaskId = Number(id);
        goto('data');
        return;
      } else if (act === 'del') {
        if (!confirm('删除任务？历史数据保留。')) return;
        await api(`/api/tasks/${id}`, { method: 'DELETE' });
      }
      await loadTasks();
    } catch (err) { toast(err.message, 'err'); }
    finally { btn.disabled = false; }
  });

  $('#taskForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api('/api/tasks', {
        method: 'POST',
        body: {
          name: f.get('name'), platform: f.get('platform'), target: f.get('target'),
          device_id: f.get('device_id') ? Number(f.get('device_id')) : null,
          interval_seconds: Number(f.get('interval_seconds') || 60),
          collect_products: f.get('collect_products') === 'on',
          collect_comments: f.get('collect_comments') === 'on',
          record_video: f.get('record_video') === 'on',
          enabled: f.get('enabled') === 'on',
        },
      });
      e.target.reset();
      toast('任务已创建，5 秒后开始首次采集', 'ok');
      await loadTasks();
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 数据 ────────────────────────────────────────────────────────────
  const relPath = (p) => String(p).replace(/^.*?\/app\/data\//, '');

  async function loadData() {
    const tasks = S.tasks.length ? S.tasks : await api('/api/tasks');
    S.tasks = tasks;
    const sel = $('#dataTaskSelect');
    const opts = tasks.map((t) => `<option value="${t.id}">#${t.id} ${esc(t.name)}</option>`).join('');
    if (sel.innerHTML !== opts) sel.innerHTML = opts;
    if (S.dataTaskId) sel.value = String(S.dataTaskId);
    S.dataTaskId = Number(sel.value || tasks[0]?.id || 0);
    if (!S.dataTaskId) {
      $('#snapshotTable tbody').innerHTML = `<tr><td colspan="8">${empty('▦', '还没有采集数据', '先建一个监控任务。')}</td></tr>`;
      return;
    }

    const [snaps, latest, keys] = await Promise.all([
      api(`/api/snapshots?task_id=${S.dataTaskId}&limit=40`),
      api(`/api/products/latest?task_id=${S.dataTaskId}`),
      api(`/api/products/keys?task_id=${S.dataTaskId}`),
    ]);

    $('#snapshotTable tbody').innerHTML = snaps.items.length ? snaps.items.map((s) => `
      <tr>
        <td><span class="cell-sub">${fmtTime(s.captured_at)}</span></td>
        <td><span class="badge ${s.is_live ? 'ok' : 'error'}">${s.is_live ? '直播中' : '未直播'}</span></td>
        <td class="wrap">${esc(s.room_title || '-')}</td>
        <td>${esc(s.anchor_name || '-')}</td>
        <td>${fmtNum(s.viewer_count)}</td>
        <td>${fmtNum(s.like_count)}</td>
        <td>${s.product_count}</td>
        <td>${s.screenshot_path ? `<a class="btn small" href="/api/media?path=${encodeURIComponent(relPath(s.screenshot_path))}" target="_blank" rel="noopener">查看</a>` : '-'}</td>
      </tr>`).join('') : '<tr><td colspan="8">暂无快照</td></tr>';

    $('#productTable tbody').innerHTML = latest.items.length ? latest.items.map((p) => `
      <tr>
        <td>${p.position ?? '-'}</td>
        <td class="wrap">${esc(p.title || '-')}</td>
        <td>${p.price ?? '-'}<span class="cell-sub">${esc(p.price_text || '')}</span></td>
        <td>${p.origin_price ?? '-'}</td>
        <td>${esc(p.sales_text || '-')}</td>
        <td>${esc(p.stock_text || '-')}</td>
      </tr>`).join('') : '<tr><td colspan="6">最近一次没采到商品</td></tr>';

    const keySel = $('#productKeySelect');
    const keyOpts = keys.items.map((k) => `<option value="${esc(k.product_key)}">${esc((k.title || k.product_key).slice(0, 40))}（${k.samples}次）</option>`).join('');
    if (keySel.innerHTML !== keyOpts) keySel.innerHTML = keyOpts;
    if (keys.items.length) await loadSeries(keySel.value || keys.items[0].product_key);
    else { $('#seriesChart').innerHTML = ''; $('#seriesTable tbody').innerHTML = ''; }
  }

  async function loadSeries(key) {
    if (!key || !S.dataTaskId) return;
    const s = await api(`/api/products/series?task_id=${S.dataTaskId}&product_key=${encodeURIComponent(key)}`);
    $('#seriesTable tbody').innerHTML = s.points.slice(-40).reverse().map((p) => `
      <tr><td><span class="cell-sub">${fmtTime(p.captured_at)}</span></td><td>${p.price ?? '-'}</td>
      <td>${p.position ?? '-'}</td><td>${esc(p.sales_text || '-')}</td></tr>`).join('')
      || '<tr><td colspan="4">暂无数据</td></tr>';
    $('#seriesChart').innerHTML = sparkline(s.points.map((p) => p.price).filter((v) => v != null));
  }

  function sparkline(values) {
    if (values.length < 2) return '<div class="sub">数据点不足，采集两次以上才有走势</div>';
    const w = 600, h = 140, pad = 22;
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const pts = values.map((v, i) => {
      const x = pad + (i * (w - pad * 2)) / (values.length - 1);
      const y = h - pad - ((v - min) / span) * (h - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="价格走势">
      <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" />
      <text x="4" y="13" fill="currentColor" opacity=".5" font-size="11">最高 ${max}</text>
      <text x="4" y="${h - 5}" fill="currentColor" opacity=".5" font-size="11">最低 ${min}</text>
    </svg>`;
  }

  $('#dataReload').addEventListener('click', () => { S.dataTaskId = Number($('#dataTaskSelect').value); loadData().catch((e) => toast(e.message, 'err')); });
  $('#dataTaskSelect').addEventListener('change', () => { S.dataTaskId = Number($('#dataTaskSelect').value); loadData().catch((e) => toast(e.message, 'err')); });
  $('#productKeySelect').addEventListener('change', (e) => loadSeries(e.target.value).catch((err) => toast(err.message, 'err')));

  // ── 录像 ────────────────────────────────────────────────────────────
  async function loadRecords() {
    const r = await api('/api/recordings?limit=100');
    $('#recordingTable tbody').innerHTML = r.items.length ? r.items.map((x) => `
      <tr data-id="${x.id}">
        <td>${x.id}</td>
        <td>${x.task_id ? '任务 ' + x.task_id : '手动'}<span class="cell-sub">云手机 ${x.device_id ?? '-'}</span></td>
        <td><span class="badge ${x.status}">${esc(x.status)}</span>
            ${x.error ? `<span class="cell-sub" style="color:var(--err)">${esc(x.error.slice(0, 50))}</span>` : ''}</td>
        <td><span class="cell-sub">${fmtTime(x.started_at)}</span></td>
        <td>${fmtDur(x.duration_seconds)}</td>
        <td>${fmtSize(x.size_mb)}<span class="cell-sub">${x.segment_count} 段</span></td>
        <td><div class="row-actions">
          ${x.downloadable ? '<button class="btn small primary" data-act="play">播放</button>' : ''}
          ${x.downloadable ? `<a class="btn small" href="/api/recordings/${x.id}/download">下载</a>` : ''}
          <button class="btn small danger" data-act="del">删除</button>
        </div></td>
      </tr>`).join('')
      : `<tr><td colspan="7">${empty('▶', '还没有录像', '在云手机卡片或控制台里点「录屏」，结束后可在这里在线回放。')}</td></tr>`;
  }

  $('#recordingTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const id = btn.closest('tr').dataset.id;
    if (btn.dataset.act === 'play') {
      const p = $('#player');
      p.src = `/api/recordings/${id}/stream`;
      $('#playerTitle').textContent = `#${id}`;
      p.play().catch(() => {});
      return;
    }
    if (!confirm('删除该录像及文件？')) return;
    try {
      await api(`/api/recordings/${id}`, { method: 'DELETE' });
      const p = $('#player');
      if (p.src.includes(`/${id}/stream`)) { p.removeAttribute('src'); p.load(); }
      toast('已删除', 'ok');
      await loadRecords();
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 套餐 ────────────────────────────────────────────────────────────
  async function loadPlans() {
    const [plans, summary, orders] = await Promise.all([
      api('/api/billing/plans'), api('/api/billing/summary'), api('/api/billing/orders?limit=20'),
    ]);
    S.plans = plans.items;
    S.channels = plans.channels.filter((c) => c.ready);

    const ents = summary.active_entitlements || [];
    $('#quotaInfo').innerHTML = [
      ['计费模式', summary.billing_enabled
        ? (summary.enforce ? '<span class="badge starting">按套餐计费</span>' : '<span class="badge ok">试用中</span> <span class="sub">未强制付费</span>')
        : '<span class="badge">未启用</span>'],
      ['云手机额度', `${summary.device_used} / ${summary.device_quota}　<span class="sub">剩余 ${summary.device_remaining} 台</span>`],
      ['生效套餐', ents.length
        ? ents.map((e) => `${esc(e.plan_name)}　<span class="sub">剩 ${e.days_left ?? '-'} 天 · 已用 ${e.used_devices}/${e.max_devices} 台</span>`).join('<br>')
        : '<span class="sub">还没有已购套餐</span>'],
    ].map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('');

    $('#planGrid').innerHTML = plans.items.map((p) => {
      const s = p.spec;
      const li = (on, text) => `<li class="${on ? '' : 'off'}">${esc(text)}</li>`;
      return `
      <div class="plan ${p.badge ? 'featured' : ''}" data-id="${p.id}">
        ${p.badge ? `<div class="plan-badge">${esc(p.badge)}</div>` : ''}
        <h3>${esc(p.name)}</h3>
        <div class="plan-price">${yuan(p.price_cents)}<span class="plan-cycle"> / ${p.duration_days} 天</span></div>
        ${p.original_price_cents ? `<div class="plan-origin">原价 ${yuan(p.original_price_cents)}</div>` : ''}
        <p class="plan-desc">${esc(p.description || '')}</p>
        <ul class="plan-spec">
          ${li(true, `${s.width}×${s.height} @${s.dpi}dpi`)}
          ${li(true, `${s.memory_mb ? s.memory_mb + 'MB 内存' : '内存不限'} · ${s.cpu_limit ? s.cpu_limit + ' 核 CPU' : 'CPU 不限'}`)}
          ${li(true, `${s.max_devices} 台云手机 · ${s.max_tasks} 个监控任务`)}
          ${li(s.allow_proxy, '独立出口 IP（代理）')}
          ${li(s.allow_recording, '直播录屏')}
          ${li(s.allow_audio, '声音转发')}
        </ul>
        <div class="plan-foot">
          <select class="pay-channel">${S.channels.map((c) => `<option value="${c.channel}">${esc(c.label)}</option>`).join('')
            || '<option value="">暂无可用支付方式</option>'}</select>
          <button class="btn primary block" data-act="buy">立即开通</button>
        </div>
      </div>`;
    }).join('') || `<div class="card">${empty('◈', '还没有上架套餐', '请管理员在后台「定价」里配置。')}</div>`;

    $('#orderTable tbody').innerHTML = orders.items.length ? orders.items.map((o) => `
      <tr data-no="${esc(o.order_no)}">
        <td><span class="cell-sub">${esc(o.order_no)}</span></td>
        <td>${esc(o.plan_name || '-')}</td>
        <td>${yuan(o.amount_cents)}</td>
        <td>${esc(o.channel)}</td>
        <td><span class="badge ${o.status}">${esc(o.status)}</span></td>
        <td><span class="cell-sub">${fmtTime(o.created_at)}</span></td>
        <td><div class="row-actions">
          ${o.status === 'pending' ? '<button class="btn small primary" data-act="pay">继续支付</button><button class="btn small danger" data-act="cancel">取消</button>' : ''}
        </div></td>
      </tr>`).join('') : '<tr><td colspan="7">还没有订单</td></tr>';
  }

  function showPay(order) {
    clearInterval(S.payPoll);
    const isMock = order.channel === 'mock';
    modal('扫码支付', `
      <div class="pay-box">
        <img class="pay-qr" src="/api/billing/orders/${order.order_no}/qr.png?t=${Date.now()}" alt="支付二维码" />
        <div class="pay-info">
          <div class="pay-amount">${yuan(order.amount_cents)}</div>
          <div>${esc(order.plan_name || '')}</div>
          <div class="sub">订单 ${esc(order.order_no)}</div>
          <div class="sub">支付方式 ${esc(order.channel)}</div>
          <div id="payStatus" class="badge pending">等待支付…</div>
          ${isMock
            ? `<p class="hint">本地联调通道：<a href="${esc(order.pay_url)}" target="_blank" rel="noopener">点此模拟付款</a></p>`
            : '<p class="hint">用手机扫码完成付款，本页会自动刷新状态。</p>'}
        </div>
      </div>`);
    S.payPoll = setInterval(async () => {
      try {
        const o = await api(`/api/billing/orders/${order.order_no}`);
        const st = $('#payStatus');
        if (!st) { clearInterval(S.payPoll); return; }
        if (o.status === 'paid') {
          clearInterval(S.payPoll);
          st.className = 'badge ok';
          st.textContent = '支付成功，额度已到账';
          toast('支付成功', 'ok');
          loadPlans().catch(() => {});
        } else if (o.status !== 'pending') {
          clearInterval(S.payPoll);
          st.className = 'badge error';
          st.textContent = `订单${o.status === 'closed' ? '已关闭' : o.status}`;
        }
      } catch (_) {}
    }, 2000);
  }
  onModalClose(() => clearInterval(S.payPoll));

  $('#planGrid').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act="buy"]');
    if (!btn) return;
    const card = btn.closest('.plan');
    const channel = card.querySelector('.pay-channel')?.value;
    if (!channel) { toast('没有可用的支付方式，请联系管理员配置', 'err'); return; }
    btn.disabled = true;
    try {
      const order = await api('/api/billing/orders', { method: 'POST', body: { plan_id: Number(card.dataset.id), channel } });
      showPay(order);
      await loadPlans();
    } catch (err) { toast(err.message, 'err'); }
    finally { btn.disabled = false; }
  });

  $('#orderTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const no = btn.closest('tr').dataset.no;
    try {
      if (btn.dataset.act === 'pay') showPay(await api(`/api/billing/orders/${no}`));
      else { await api(`/api/billing/orders/${no}/cancel`, { method: 'POST' }); toast('已取消', 'ok'); await loadPlans(); }
    } catch (err) { toast(err.message, 'err'); }
  });

  // ── 调度 ────────────────────────────────────────────────────────────
  const loaders = {
    home: loadHome, devices: loadDevices, store: loadStore,
    tasks: loadTasks, data: loadData, records: loadRecords, plans: loadPlans,
  };

  let busy = false;
  async function refresh(manual = false) {
    if (busy) return;
    busy = true;
    try {
      await (loaders[S.view] || loadHome)();
      if (manual) toast('已刷新', 'ok');
    } catch (err) {
      if (manual) toast(err.message, 'err');
      else console.warn('刷新失败', err);
    } finally { busy = false; }
  }

  setupTimer();
  refresh();
})();
