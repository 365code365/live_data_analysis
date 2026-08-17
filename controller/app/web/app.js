/* 用户前台：云手机、应用市场、监控任务、数据、录像、套餐。
   后台内容（代理、定价、系统信息、全量日志）不在这里，见 /admin。 */
(() => {
  'use strict';

  const {
    $, $$, esc, api, toast, modal, closeModal, onModalClose, bindModal,
    fmtTime, fmtNum, fmtDur, fmtSize, yuan, empty, mountThemePicker, mountNav,
    setHTML, setOptions, userBusy,
  } = window.LDM;

  const S = {
    view: 'home', timer: null, devices: [], tasks: [], catalog: [], plans: [],
    channels: [], dataTaskId: null, storeJobTimer: null, payPoll: null, quota: null,
    fitFrame: null, // 画面弹窗当前用的尺寸换算函数（投屏页报回真实分辨率时要用）
    thumbAt: {},    // 每台设备上次取预览图的时间，用来限流
    thumbTimer: null,
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
    clearInterval(S.thumbTimer);
    if (!$('#autoRefresh').checked) return;
    S.timer = setInterval(() => {
      // 标签页在后台、弹窗开着、或用户正在填表单时不要刷，省得打断操作
      if (document.hidden || userBusy()) return;
      refresh();
    }, 15000);
    // 预览图单独一条更慢的节拍，跟列表刷新解耦
    S.thumbTimer = setInterval(() => refreshThumbs(), 10000);
  }
  // 切回前台时补一次，但不在后台空转
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && $('#autoRefresh').checked) refresh();
  });

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
    setHTML($('#statGrid'), cards.map(([label, num, trend]) => `
      <div class="stat"><div class="num">${fmtNum(num)}</div>
        <div class="label">${label}</div><div class="trend">${esc(trend)}</div></div>`).join(''));

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

    setHTML($('#homeDevices'), devices.length
      ? devices.slice(0, 5).map((d) => `
        <div class="img-row" style="padding:8px 0;border-bottom:1px solid var(--border)">
          <span class="badge ${d.status}">${esc(d.status)}</span>
          <strong>${esc(d.name)}</strong>
          <span class="img-sub">${d.width}×${d.height}</span>
          <span class="spacer" style="flex:1"></span>
          <a class="btn small primary" href="/console?device=${d.id}" target="_blank" rel="noopener">进入</a>
        </div>`).join('')
      : empty('▤', '还没有云手机', '创建一台安卓实例，装上抖音/小红书就能开始监控。',
        '<button class="btn primary" data-goto="devices">新建云手机</button>'));
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

    const usable = (quota?.active_entitlements || []).filter((e) => e.remaining_devices > 0);
    const hint = quota && quota.enforce && !usable.length
      ? '当前为强制付费模式且没有可用名额，请先购买套餐。'
      : `共 ${devices.length} 台 · 运行中 ${devices.filter((d) => d.status === 'running').length} 台`
        + (quota && quota.device_quota ? ` · 套餐额度 ${quota.device_used}/${quota.device_quota}` : '');
    if ($('#deviceFormHint').textContent !== hint) $('#deviceFormHint').textContent = hint;

    renderDevices(devices);
    refreshThumbs();
  }

  // ── 设备卡片：就地更新，不重建 ──────────────────────────────────────
  // 每 15 秒把整个网格 innerHTML 重写一遍的话，卡片节点被销毁重建，
  // 预览图重新加载、按钮闪一下、滚动位置也可能跳，看着就是页面在抖。
  // 所以这里按 id 找到已有卡片，只有内容真的变了才改对应的那一块，
  // 预览图所在的节点永远不重写。
  function renderDevices(devices) {
    const list = $('#deviceList');
    if (!devices.length) {
      setHTML(list, empty('▤', '还没有云手机', '点上面的「创建」开一台，安卓开机约 1-2 分钟。'));
      return;
    }
    if (list.querySelector('.empty')) list.innerHTML = '';

    const alive = new Set(devices.map((d) => String(d.id)));
    Array.from(list.children).forEach((el) => {
      if (!alive.has(el.dataset.id)) el.remove();
    });

    devices.forEach((d, i) => {
      let card = list.querySelector(`.device[data-id="${d.id}"]`);
      if (!card) {
        card = document.createElement('div');
        card.className = 'device';
        card.dataset.id = String(d.id);
        card.innerHTML = `
          <header data-part="head"></header>
          <div class="thumb" data-act="open">
            <div class="ph" id="thumb-ph-${d.id}"></div>
            <img id="thumb-${d.id}" alt="" hidden />
            <div class="play">点击进入控制台</div>
          </div>
          <div data-part="body"></div>`;
        list.appendChild(card);
      } else if (card !== list.children[i]) {
        // 顺序变了就搬节点（appendChild 是移动而不是重建，不会丢预览图）
        list.appendChild(card);
      }

      setHTML(card.querySelector('[data-part="head"]'), deviceHead(d));
      setHTML(card.querySelector('[data-part="body"]'), deviceBody(d));

      const ph = $(`#thumb-ph-${d.id}`);
      const img = $(`#thumb-${d.id}`);
      const text = d.screen_ready ? (img && img.src ? '' : '加载预览…') : '未开机';
      if (ph.textContent !== text) ph.textContent = text;
      // 关机了就把旧预览撤掉，别让人以为还在跑
      if (!d.screen_ready && img && img.src) {
        if (img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
        img.removeAttribute('src');
        img.hidden = true;
      }
      if (d.screen_ready && img && img.src) ph.hidden = true;
      else ph.hidden = false;
    });
  }

  function deviceHead(d) {
    const cs = d.container_states || {};
    const chip = (role, label) => {
      const st = cs[role];
      return `<span class="badge ${st === 'running' ? 'ok' : (st ? 'error' : '')}">${label}</span>`;
    };
    return `
      <h3>${esc(d.name)}</h3>
      <span class="badge ${d.status}">${esc(d.status)}</span>
      ${d.recording ? '<span class="badge recording">录屏中</span>' : ''}
      <span style="flex:1"></span>
      ${chip('gw', '网络')}${chip('android', '安卓')}${chip('vnc', '画面')}`;
  }

  function deviceBody(d) {
    return `
      <div class="kv">
        <div class="k">配置</div><div class="v">${[
          d.perf_name || (d.memory_mb ? `${(d.memory_mb / 1024).toFixed(0)}GB 内存` : '内存不限'),
          d.cpu_limit ? `${d.cpu_limit} 核` : 'CPU 不限',
          d.memory_mb && d.perf_name ? `${(d.memory_mb / 1024).toFixed(0)}GB 内存` : '',
          d.disk_gb ? `${d.disk_gb}GB 磁盘${d.disk_quota ? '' : '（未限额）'}` : '',
        ].filter(Boolean).map(esc).join(' · ')}</div>
        <div class="k">屏幕</div><div class="v">${d.width}×${d.height} @${d.dpi}dpi
          ${d.width > d.height ? '<span class="app-tag">横屏</span>' : ''}</div>
        <div class="k">出口 IP</div><div class="v">${esc(d.egress_ip || '未检测')}${
          d.proxy_name ? ` <span class="app-tag">${esc(d.proxy_name)}</span>` : ''}</div>
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
      </div>`;
  }

  // ── 预览图：先解码再换图，避免闪白 ──────────────────────────────────
  const THUMB_MIN_GAP = 20000;
  function refreshThumbs(force = false) {
    if (document.hidden) return;
    S.devices.forEach((d) => {
      if (!d.screen_ready) return;
      const last = S.thumbAt[d.id] || 0;
      if (!force && Date.now() - last < THUMB_MIN_GAP) return;
      S.thumbAt[d.id] = Date.now();
      loadThumb(d.id);
    });
  }

  async function loadThumb(id) {
    try {
      const res = await fetch(`/api/devices/${id}/screenshot?t=${Date.now()}`);
      if (!res.ok) return;
      const url = URL.createObjectURL(await res.blob());
      const img = $(`#thumb-${id}`);
      if (!img) { URL.revokeObjectURL(url); return; }
      // 直接改 src 会先变空再显示，肉眼就是闪一下。
      // 先在离屏对象里解好，成功了才换上去，并回收上一张的 blob。
      const probe = new Image();
      probe.onload = () => {
        const old = img.src;
        img.src = url;
        img.hidden = false;
        const ph = $(`#thumb-ph-${id}`);
        if (ph) { ph.hidden = true; ph.textContent = ''; }
        if (old && old.startsWith('blob:')) URL.revokeObjectURL(old);
      };
      probe.onerror = () => URL.revokeObjectURL(url);
      probe.src = url;
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

  // ── 新建云手机：弹窗里选档位，不让用户填裸数字 ──────────────────────
  // 内存/CPU 填错会直接 OOM，分辨率填奇怪的值 redroid 起不来，
  // 所以性能、屏幕、磁盘、出口 IP 全部做成固定选项（服务端 /api/specs 给）。
  $('#newDeviceBtn').addEventListener('click', () => openCreateDialog());

  async function openCreateDialog() {
    let specs;
    try {
      specs = await api('/api/specs');
    } catch (err) { toast(`读取可选规格失败: ${err.message}`, 'err'); return; }

    const pick = {
      perf: specs.defaults.perf,
      screen: specs.defaults.screen,
      disk: (specs.performance.find((p) => p.code === specs.defaults.perf) || {}).disk_gb || 0,
      region: '',
      plan: '',
    };
    const usable = (specs.quota?.active_entitlements || []).filter((e) => e.remaining_devices > 0);
    const planByCode = Object.fromEntries(specs.plans.map((p) => [p.code, p]));
    const enforce = !!specs.quota?.enforce;

    const card = (on, badge, name, spec, note) => `
      <div class="pick${on ? ' active' : ''}">
        ${badge ? `<span class="pick-badge">${esc(badge)}</span>` : ''}
        <div class="pick-name">${esc(name)}</div>
        <div class="pick-spec">${esc(spec)}</div>
        ${note ? `<div class="pick-note">${esc(note)}</div>` : ''}
      </div>`;

    const body = () => {
      const plan = pick.plan ? planByCode[pick.plan] : null;
      const locked = !!plan; // 选了套餐就按套餐规格开，性能/屏幕不让改
      return `
      <div class="form-block">
        <div class="label">名称</div>
        <input id="cdName" placeholder="douyin-01" maxlength="80" style="width:100%" />
      </div>

      ${specs.plans.length ? `
      <div class="form-block">
        <div class="label">套餐 ${enforce ? '<span class="badge starting">必选</span>' : '<span class="sub">可不选，试用模式按下面的档位开</span>'}</div>
        <select id="cdPlan" style="width:100%">
          ${enforce ? '' : '<option value="">不绑定套餐（试用）</option>'}
          ${usable.map((e) => `<option value="${esc(e.plan_code)}" ${pick.plan === e.plan_code ? 'selected' : ''}>${esc(e.plan_name)}（剩 ${e.remaining_devices} 台）</option>`).join('')}
        </select>
        ${enforce && !usable.length ? '<div class="pick-note" style="color:var(--err)">没有可用名额，请先去「套餐与账单」购买。</div>' : ''}
        ${locked ? `<div class="pick-note">已按套餐「${esc(plan.name)}」的规格开机，下面的性能与屏幕以套餐为准。</div>` : ''}
      </div>` : ''}

      <div class="form-block">
        <div class="label">性能 <span class="sub">内存与 CPU 是硬限制，直接作用在容器上</span></div>
        <div class="pick-grid ${locked ? 'locked' : ''}" id="cdPerf">
          ${specs.performance.map((p) => `
            <div data-code="${esc(p.code)}">${card(
              !locked && pick.perf === p.code, p.badge, p.name,
              `${p.memory_mb / 1024}GB 内存 · ${p.cpu_limit} 核 · ${p.disk_gb}GB 磁盘`, p.note,
            )}</div>`).join('')}
        </div>
      </div>

      <div class="form-block">
        <div class="label">屏幕 <span class="sub">方向在开机时定死，建好之后不能转屏</span></div>
        <div class="pick-grid ${locked ? 'locked' : ''}" id="cdScreen">
          ${specs.screens.map((s) => `
            <div data-code="${esc(s.code)}">${card(
              !locked && pick.screen === s.code, s.badge, s.name,
              `${s.width}×${s.height} @${s.dpi}dpi · ${s.orientation === 'landscape' ? '横屏' : '竖屏'}`, s.note,
            )}</div>`).join('')}
        </div>
      </div>

      <div class="form-row" style="gap:16px">
        <label style="min-width:170px">磁盘（安卓 /data）
          <select id="cdDisk">
            ${specs.disks.map((g) => `<option value="${g}" ${Number(pick.disk) === g ? 'selected' : ''}>${g} GB</option>`).join('')}
          </select>
        </label>
        <label class="grow">出口 IP 区域
          <select id="cdRegion">
            <option value="">直连（不走代理）</option>
            ${specs.regions.map((r) => `<option value="${r.id}" ${String(pick.region) === String(r.id) ? 'selected' : ''}>${
              esc(r.name)}${r.region ? ` · ${esc(r.region)}` : ''}${r.ip_masked ? ` · ${esc(r.ip_masked)}` : ''}${
              r.verified ? '' : '（未验证）'}${r.in_use ? ` · 已有 ${r.in_use} 台在用` : ''}</option>`).join('')}
          </select>
        </label>
      </div>
      ${specs.regions.length ? '' : '<p class="hint">还没有可用的出口 IP。需要独立 IP 请让管理员在后台「代理池」里添加。</p>'}
      ${specs.disk_quota_supported ? '' : '<p class="hint">当前宿主文件系统没开 project quota，磁盘容量只作规格登记，不是硬限制。</p>'}

      <div class="form-row" style="margin-top:14px;gap:18px">
        <label class="switch"><input type="checkbox" id="cdAudio" checked /> 开启声音转发</label>
        <label class="switch"><input type="checkbox" id="cdAutostart" checked /> 创建后立即开机</label>
      </div>

      <div class="summary" style="margin-top:14px">
        将创建：<strong id="cdSummary">${esc(summaryText())}</strong>
      </div>

      <div class="btn-group" style="margin-top:16px">
        <button class="btn primary" id="cdSubmit">创建并开机</button>
        <button class="btn ghost" id="cdCancel">取消</button>
        <span class="sub" id="cdStatus"></span>
      </div>`;
    };

    // 选档位只改高亮和摘要，不整体重绘弹窗：重绘会丢焦点、把滚动条弹回顶部，
    // 那就是另一种抖动了。只有「选套餐」需要重绘（它会锁住性能与屏幕）。
    const summaryText = () => {
      const plan = pick.plan ? planByCode[pick.plan] : null;
      const tier = specs.performance.find((p) => p.code === pick.perf);
      const scr = specs.screens.find((s) => s.code === pick.screen);
      const s = plan ? plan.spec : {
        memory_mb: tier?.memory_mb, cpu_limit: tier?.cpu_limit,
        width: scr?.width, height: scr?.height, dpi: scr?.dpi,
      };
      const region = specs.regions.find((r) => String(r.id) === String(pick.region));
      return [
        `${s.width}×${s.height} @${s.dpi}dpi`,
        s.memory_mb ? `${s.memory_mb / 1024}GB 内存` : '内存不限',
        s.cpu_limit ? `${s.cpu_limit} 核` : 'CPU 不限',
        pick.disk ? `${pick.disk}GB 磁盘` : '磁盘不限',
        region ? `出口 ${region.region || region.name}` : '直连出网',
      ].join(' · ');
    };
    const updateSummary = () => {
      const el = $('#cdSummary');
      if (el) el.textContent = summaryText();
    };

    const render = () => {
      const keep = $('#modalBody') ? $('#modalBody').scrollTop : 0;
      modal('新建云手机', body(), { wide: true });
      const name = $('#cdName');
      if (name) name.value = S.newName || '';
      $('#modalBody').scrollTop = keep;
      bind();
    };

    const bind = () => {
      const nameEl = $('#cdName');
      if (nameEl) nameEl.addEventListener('input', (e) => { S.newName = e.target.value; });

      const planSel = $('#cdPlan');
      if (planSel) planSel.addEventListener('change', (e) => { pick.plan = e.target.value; render(); });

      const onPick = (id, key) => {
        const box = $(id);
        if (!box || box.classList.contains('locked')) return;
        box.addEventListener('click', (e) => {
          const item = e.target.closest('[data-code]');
          if (!item) return;
          pick[key] = item.dataset.code;
          box.querySelectorAll('.pick').forEach((p) => p.classList.remove('active'));
          item.querySelector('.pick').classList.add('active');
          // 换性能档位时磁盘跟着推荐值走（用户之后还能自己改）
          if (key === 'perf') {
            const t = specs.performance.find((p) => p.code === pick.perf);
            if (t) {
              pick.disk = t.disk_gb;
              const disk = $('#cdDisk');
              if (disk) disk.value = String(t.disk_gb);
            }
          }
          updateSummary();
        });
      };
      onPick('#cdPerf', 'perf');
      onPick('#cdScreen', 'screen');

      const disk = $('#cdDisk');
      if (disk) disk.addEventListener('change', (e) => { pick.disk = Number(e.target.value); updateSummary(); });
      const region = $('#cdRegion');
      if (region) region.addEventListener('change', (e) => { pick.region = e.target.value; updateSummary(); });

      $('#cdCancel').addEventListener('click', () => { S.newName = ''; closeModal(); });
      $('#cdSubmit').addEventListener('click', submit);
    };

    const submit = async () => {
      const name = ($('#cdName').value || '').trim();
      if (!name) { toast('先给它起个名字', 'err'); $('#cdName').focus(); return; }
      const plan = pick.plan ? planByCode[pick.plan] : null;
      const btn = $('#cdSubmit');
      btn.disabled = true;
      $('#cdStatus').textContent = $('#cdAutostart').checked
        ? '正在创建容器并开机，约 20-40 秒…' : '正在创建…';
      try {
        await api('/api/devices', {
          method: 'POST',
          body: {
            name,
            plan_id: plan ? plan.id : null,
            // 选了套餐时规格以套餐为准，这里就不再传档位，避免两边打架
            perf: plan ? null : pick.perf,
            screen: plan ? null : pick.screen,
            disk_gb: Number(pick.disk) || null,
            proxy_id: pick.region ? Number(pick.region) : null,
            enable_audio: $('#cdAudio').checked,
            autostart: $('#cdAutostart').checked,
          },
        });
        S.newName = '';
        closeModal();
        toast('云手机已创建，安卓开机约 1-2 分钟', 'ok');
        await loadDevices();
      } catch (err) {
        btn.disabled = false;
        $('#cdStatus').textContent = '';
        modal('创建失败', `<div class="alert" style="margin:0"><div class="alert-body">${esc(err.message)}</div></div>`);
      }
    };

    if (enforce && usable.length) pick.plan = usable[0].plan_code;
    render();
  }

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
    setOptions($('#storeDevice'), deviceOptions(cur));
    const target = $('#storeDevice').value;

    // 已装应用（拿第一台运行中的设备做参考）
    let installed = [];
    if (target) {
      try { installed = (await api(`/api/devices/${target}/apps`)).items || []; } catch (_) {}
    }
    const installedPkgs = new Set(installed.map((a) => a.package));

    const initial = (name) => (name || '?').trim().charAt(0).toUpperCase();
    setHTML($('#storeGrid'), cat.items.map((a) => {
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
    }).join(''));

    $('#installedHint').textContent = target
      ? `云手机「${(runningDevices().find((d) => String(d.id) === target) || {}).name || ''}」上的第三方应用`
      : '先开机一台云手机';
    setHTML($('#installedGrid'), installed.length ? installed.map((a) => `
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
      : empty('⊞', '这台云手机还没装第三方应用', '在上面的应用市场里点「安装」，或上传自己的 apk。'));

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
    const fit = (fw, fh) => {
      // 高度按视口给上限，宽度按弹窗可用宽度给上限，两边取小，且不放大
      const maxH = window.innerHeight * 0.62;
      const maxW = Math.min(window.innerWidth * 0.72, 1180);
      const s = Math.min(maxH / fh, maxW / fw, 1);
      return { w: Math.round(fw * s), h: Math.round(fh * s) };
    };
    const { w, h } = fit(dev.width, dev.height);
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

    S.fitFrame = fit;
  }

  // 投屏页会报上来 VNC 帧缓冲的真实尺寸；手机转横屏后弹窗里的画面跟着变形状，
  // 否则横屏画面会被塞进竖着的 iframe 里，上下一大片黑。
  window.addEventListener('message', (e) => {
    const d = e.data || {};
    if (d.source !== 'ldm-screen' || !d.width || !d.height || !S.fitFrame) return;
    const frame = $('#modalBody .screen-modal-frame');
    if (!frame) return;
    const s = S.fitFrame(d.width, d.height);
    frame.style.width = `${s.w}px`;
    frame.style.height = `${s.h}px`;
  });

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
    setHTML($('#storeJobMsg'), job.state === 'failed'
      ? `<span style="color:var(--err)">${esc(job.error || '失败')}</span>`
      : `${esc(job.name)} · ${label} · ${esc(String(job.message || '').slice(0, 60))}`);
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
    setOptions($('#taskDeviceSelect'), opts);

    setHTML($('#taskTable tbody'), tasks.length ? tasks.map((t) => `
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
      : `<tr><td colspan="9">${empty('◷', '还没有监控任务', '填直播间标识就能开始定时采集直播间信息与商品。')}</td></tr>`);
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
    setOptions(sel, opts);
    if (S.dataTaskId) sel.value = String(S.dataTaskId);
    S.dataTaskId = Number(sel.value || tasks[0]?.id || 0);
    if (!S.dataTaskId) {
      setHTML($('#snapshotTable tbody'), `<tr><td colspan="8">${empty('▦', '还没有采集数据', '先建一个监控任务。')}</td></tr>`);
      return;
    }

    const [snaps, latest, keys] = await Promise.all([
      api(`/api/snapshots?task_id=${S.dataTaskId}&limit=40`),
      api(`/api/products/latest?task_id=${S.dataTaskId}`),
      api(`/api/products/keys?task_id=${S.dataTaskId}`),
    ]);

    setHTML($('#snapshotTable tbody'), snaps.items.length ? snaps.items.map((s) => `
      <tr>
        <td><span class="cell-sub">${fmtTime(s.captured_at)}</span></td>
        <td><span class="badge ${s.is_live ? 'ok' : 'error'}">${s.is_live ? '直播中' : '未直播'}</span></td>
        <td class="wrap">${esc(s.room_title || '-')}</td>
        <td>${esc(s.anchor_name || '-')}</td>
        <td>${fmtNum(s.viewer_count)}</td>
        <td>${fmtNum(s.like_count)}</td>
        <td>${s.product_count}</td>
        <td>${s.screenshot_path ? `<a class="btn small" href="/api/media?path=${encodeURIComponent(relPath(s.screenshot_path))}" target="_blank" rel="noopener">查看</a>` : '-'}</td>
      </tr>`).join('') : '<tr><td colspan="8">暂无快照</td></tr>');

    setHTML($('#productTable tbody'), latest.items.length ? latest.items.map((p) => `
      <tr>
        <td>${p.position ?? '-'}</td>
        <td class="wrap">${esc(p.title || '-')}</td>
        <td>${p.price ?? '-'}<span class="cell-sub">${esc(p.price_text || '')}</span></td>
        <td>${p.origin_price ?? '-'}</td>
        <td>${esc(p.sales_text || '-')}</td>
        <td>${esc(p.stock_text || '-')}</td>
      </tr>`).join('') : '<tr><td colspan="6">最近一次没采到商品</td></tr>');

    const keySel = $('#productKeySelect');
    const keyOpts = keys.items.map((k) => `<option value="${esc(k.product_key)}">${esc((k.title || k.product_key).slice(0, 40))}（${k.samples}次）</option>`).join('');
    setOptions(keySel, keyOpts);
    if (keys.items.length) await loadSeries(keySel.value || keys.items[0].product_key);
    else { setHTML($('#seriesChart'), ''); setHTML($('#seriesTable tbody'), ''); }
  }

  async function loadSeries(key) {
    if (!key || !S.dataTaskId) return;
    const s = await api(`/api/products/series?task_id=${S.dataTaskId}&product_key=${encodeURIComponent(key)}`);
    setHTML($('#seriesTable tbody'), s.points.slice(-40).reverse().map((p) => `
      <tr><td><span class="cell-sub">${fmtTime(p.captured_at)}</span></td><td>${p.price ?? '-'}</td>
      <td>${p.position ?? '-'}</td><td>${esc(p.sales_text || '-')}</td></tr>`).join('')
      || '<tr><td colspan="4">暂无数据</td></tr>');
    setHTML($('#seriesChart'), sparkline(s.points.map((p) => p.price).filter((v) => v != null)));
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
    setHTML($('#recordingTable tbody'), r.items.length ? r.items.map((x) => `
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
      : `<tr><td colspan="7">${empty('▶', '还没有录像', '在云手机卡片或控制台里点「录屏」，结束后可在这里在线回放。')}</td></tr>`);
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
    setHTML($('#quotaInfo'), [
      ['计费模式', summary.billing_enabled
        ? (summary.enforce ? '<span class="badge starting">按套餐计费</span>' : '<span class="badge ok">试用中</span> <span class="sub">未强制付费</span>')
        : '<span class="badge">未启用</span>'],
      ['云手机额度', `${summary.device_used} / ${summary.device_quota}　<span class="sub">剩余 ${summary.device_remaining} 台</span>`],
      ['生效套餐', ents.length
        ? ents.map((e) => `${esc(e.plan_name)}　<span class="sub">剩 ${e.days_left ?? '-'} 天 · 已用 ${e.used_devices}/${e.max_devices} 台</span>`).join('<br>')
        : '<span class="sub">还没有已购套餐</span>'],
    ].map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join(''));

    setHTML($('#planGrid'), plans.items.map((p) => {
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
    }).join('') || `<div class="card">${empty('◈', '还没有上架套餐', '请管理员在后台「定价」里配置。')}</div>`);

    setHTML($('#orderTable tbody'), orders.items.length ? orders.items.map((o) => `
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
      </tr>`).join('') : '<tr><td colspan="7">还没有订单</td></tr>');
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
