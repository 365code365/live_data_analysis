/* 设备控制台：屏幕(noVNC 精简页) + 安卓快捷操作 + 声音 + 粘贴 + 应用管理 */
(() => {
  'use strict';

  // 主题、请求、提示、弹窗都用 common.js 里的公共实现，三个页面保持一致
  const { $, esc, api, toast, modal, bindModal, mountThemePicker, setHTML } = window.LDM;

  const deviceId = Number(new URLSearchParams(location.search).get('device') || 0);
  const state = {
    device: null, timer: null, jobTimer: null, recording: false,
    unmaskTimer: null, maskShown: false,
    screenConnected: false, screenInstance: null, screenReloads: 0, frameUrl: null,
    // 画面的真实形状。投屏页会把 VNC 帧缓冲尺寸报过来，手机转横屏后这里跟着变；
    // 报上来之前先用数据库里的分辨率兜底。
    fb: null,
  };
  const screenSize = () => state.fb
    || (state.device ? { width: state.device.width, height: state.device.height } : null);

  // ── 基础 ────────────────────────────────────────────────────────────
  bindModal();
  mountThemePicker($('#themePicker'));

  function mask(title, body) {
    $('#maskTitle').textContent = title;
    $('#maskBody').textContent = body || '';
    $('#screenMask').hidden = false;
    state.maskShown = true;
  }
  function unmask() {
    $('#screenMask').hidden = true;
    state.maskShown = false;
  }
  $('#maskStart').addEventListener('click', async (e) => {
    e.target.disabled = true;
    mask('正在启动设备…', '安卓首次开机约 1-2 分钟，起来后画面会自动出现');
    try {
      await api(`/api/devices/${deviceId}/start`, { method: 'POST' });
      toast('已启动，等安卓开机', 'ok');
    } catch (err) {
      mask('启动失败', err.message);
    } finally {
      e.target.disabled = false;
      loadDevice().catch(() => {});
    }
  });

  // 重试只让画面重连，不重载整个控制台（省得音量、粘贴框这些状态全丢）
  $('#maskRetry').addEventListener('click', () => {
    state.screenConnected = false;
    mask('正在重新连接…', '');
    const frame = $('#vncFrame');
    if (frame.contentWindow) {
      frame.contentWindow.postMessage({ target: 'ldm-screen', action: 'reconnect' }, '*');
    } else if (state.frameUrl) {
      frame.src = state.frameUrl;
    }
  });

  // ── 加载设备 ────────────────────────────────────────────────────────
  async function loadDevice() {
    const d = await api(`/api/devices/${deviceId}`);
    state.device = d;
    $('#devName').textContent = d.name;
    $('#devMeta').textContent = `${d.width}×${d.height} @${d.dpi}dpi · ${d.proxy_name || '直连'}`;
    document.title = `${d.name} · 设备控制台`;

    // 分辨率变了才重算尺寸，别每轮都动布局
    if (state.lastGeom !== `${d.width}x${d.height}`) {
      state.lastGeom = `${d.width}x${d.height}`;
      applyZoom();
    }

    const cs = d.container_states || {};
    // screen_ready 由服务端算；老接口没有这个字段时退回自己判断
    const ok = d.screen_ready !== undefined && d.screen_ready !== null
      ? d.screen_ready
      : (cs.vnc === 'running' && cs.android === 'running');
    $('#liveDot').style.background = ok ? 'var(--ok)' : 'var(--err)';
    $('#liveDot').style.boxShadow = `0 0 8px ${ok ? 'var(--ok)' : 'var(--err)'}`;

    const statusHtml = [
      ['状态', `<span class="badge ${d.status}">${esc(d.status)}</span>`],
      ['容器', ['gw', 'android', 'vnc'].map((r) => {
        const st = cs[r];
        return `<span class="badge ${st === 'running' ? 'ok' : (st ? 'error' : '')}">${r} ${st || '-'}</span>`;
      }).join(' ')],
      ['出口 IP', esc(d.egress_ip || '未检测')],
      ['规格', `${d.memory_mb ? d.memory_mb + 'MB' : '内存不限'} · ${d.cpu_limit ? d.cpu_limit + ' 核' : 'CPU 不限'}`],
      ['声音', d.enable_audio ? `已开启（端口 ${d.audio_port || '-'}）` : '已关闭'],
    ].map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('');
    // 内容没变就不重写 DOM，避免无谓的重排
    setHTML($('#devStatus'), statusHtml);

    $('#btnRecord').textContent = d.recording ? '停止录屏' : '开始录屏';
    state.recording = !!d.recording;

    if (!ok) {
      state.screenConnected = false;
      state.frameUrl = null;
      // 画面没了，形状信息也作废，下次连上重新按实际帧缓冲算
      state.fb = null;
      state.fbKey = null;
      $('#vncFrame').removeAttribute('src');
      const stopped = d.status === 'stopped' || !cs.android;
      const info = stopped ? null : await api(`/api/devices/${deviceId}/vnc`).catch(() => null);
      mask(
        stopped ? '设备已停止' : '设备还没准备好',
        stopped
          ? `容器状态：网关 ${cs.gw || '未运行'} / 安卓 ${cs.android || '未运行'} / 画面 ${cs.vnc || '未运行'}\n点下面的「启动设备」拉起来，安卓开机约 1-2 分钟。`
          : (info && info.problem) || '容器未全部就绪，启动后自动恢复',
      );
      $('#maskStart').hidden = !stopped;
      return;
    }
    $('#maskStart').hidden = true;

    // 只有「还没连上」时才盖遮罩。
    // 之前每轮轮询都无条件重新盖一次，画面明明是好的却一直显示「正在连接」。
    if (!state.screenConnected && !state.maskShown) {
      mask('正在连接画面…', '');
      // 兜底：投屏页没回状态（比如镜像是旧版）也不能一直挡着
      clearTimeout(state.unmaskTimer);
      state.unmaskTimer = setTimeout(() => {
        state.unmaskTimer = null;
        unmask();
      }, 9000);
    }

    // screen.html 是本项目自带的极简投屏页（在 VNC 镜像里），
    // 直接用 noVNC 的 RFB 内核，只渲染画面，没有任何自带 UI。
    const url = `http://${location.hostname}:${d.novnc_port}/screen.html`
      + `?password=${encodeURIComponent(d.vnc_password || '')}&scale=1&reconnect=1`;
    // 只在第一次（或地址真的变了）赋值 src。
    // 每次轮询都赋值会让 iframe 整页重载，画面就会一直闪。
    if (state.frameUrl !== url) {
      state.frameUrl = url;
      $('#vncFrame').src = url;
    }
    setupAudio(d);
  }

  // ── 尺寸：按画面真实形状算，缩放比永不超过 1（不放大）────────────────
  function applyZoom() {
    const size = screenSize();
    if (!size) return;
    const frame = $('#screenFrame');
    const wrap = frame.parentElement;
    const availW = Math.max(120, wrap.clientWidth - 4);
    const availH = Math.max(120, wrap.clientHeight - 4);

    const mode = localStorage.getItem('ldm_zoom') || 'fit';
    const fitScale = Math.min(availW / size.width, availH / size.height, 1);
    let scale = mode === 'fit' ? fitScale : Number(mode);
    // 手动挡也不允许超出可视区域，否则会出现滚动条挡住导航键
    scale = Math.min(scale, availW / size.width, availH / size.height);
    scale = Math.max(0.1, scale);

    frame.style.width = `${Math.round(size.width * scale)}px`;
    frame.style.height = `${Math.round(size.height * scale)}px`;
    // 横屏画面靠宽度吃饭，把右侧操作栏收窄一点让画面更大
    const landscape = size.width > size.height;
    $('.console-layout').classList.toggle('landscape', landscape);
    $('.screen-pane').classList.toggle('landscape', landscape);

    const pct = Math.round(scale * 100);
    const info = $('#zoomInfo');
    info.textContent = `${size.width}×${size.height}${size.width > size.height ? ' 横屏' : ''} · ${pct}%`;
    // 选了 100% 却被压到更小，说明窗口放不下，要讲清楚原因
    const clamped = mode !== 'fit' && Math.abs(scale - Number(mode)) > 0.01;
    info.title = clamped
      ? `窗口高度不够，${Math.round(Number(mode) * 100)}% 放不下，已按 ${pct}% 显示`
      : (pct >= 100 ? '与手机屏幕同样的物理像素' : '等比缩小，不失真');
    info.style.color = clamped ? 'var(--warn)' : '';
  }

  $('#zoomSelect').addEventListener('change', (e) => {
    localStorage.setItem('ldm_zoom', e.target.value);
    applyZoom();
  });
  window.addEventListener('resize', () => applyZoom());

  // 投屏页把连接状态抛过来：黑屏/断线时能看到真实原因，
  // 同时上报到服务端，方便事后查「画面为什么不稳」。
  window.addEventListener('message', (e) => {
    const data = e.data || {};
    if (data.source !== 'ldm-screen') return;

    if (data.instance && data.instance !== state.screenInstance) {
      // instance 变了说明投屏页整页重载过（正常的就地重连不会换 instance）
      if (state.screenInstance) state.screenReloads = (state.screenReloads || 0) + 1;
      state.screenInstance = data.instance;
    }

    // 投屏页会带上 VNC 帧缓冲的真实尺寸。手机转横屏后它会变成横的，
    // 这里跟着重算 iframe 尺寸，画面才不会被塞进竖框里留大黑边。
    if (data.width && data.height) {
      const key = `${data.width}x${data.height}`;
      if (state.fbKey !== key) {
        state.fbKey = key;
        state.fb = { width: data.width, height: data.height };
        applyZoom();
      }
    }

    switch (data.state) {
      case 'connected':
        state.screenConnected = true;
        clearTimeout(state.unmaskTimer);
        state.unmaskTimer = null;
        unmask();
        break;
      case 'connecting':
        state.screenConnected = false;
        break;
      case 'auth_required':
      case 'auth_failed':
        state.screenConnected = false;
        mask('VNC 认证失败', `${data.detail || ''}\n设备的 VNC 密码可能被改过，重启设备可重新下发。`);
        break;
      case 'disconnected':
        state.screenConnected = false;
        mask('画面连接中断', '正在自动重连…');
        break;
      case 'error':
        state.screenConnected = false;
        mask('画面加载失败', data.detail || '');
        break;
      default:
        break;
    }
    reportScreenState(data);
  });

  // 只上报状态变化，不刷流水
  let lastReported = '';
  function reportScreenState(data) {
    const key = `${data.state}|${data.detail || ''}`;
    if (key === lastReported) return;
    lastReported = key;
    fetch(`/api/devices/${deviceId}/screen-report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        state: data.state,
        detail: data.detail || '',
        instance: data.instance || '',
        reloads: state.screenReloads || 0,
      }),
    }).catch(() => { /* 上报失败不影响使用 */ });
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
      if (r.applied) {
        toast(`已转到方向 ${r.orientation}，画面形状会自动跟着变`, 'ok');
      } else {
        // 转屏请求下发了但显示没动，说明这台实例不支持，讲清楚而不是报个假的成功
        modal('这台云手机转不了屏', `<div class="alert warn" style="margin:0">
          <div class="alert-title">已下发方向 ${r.orientation}，但显示仍是方向 ${r.display_rotation ?? '未知'}</div>
          <div class="alert-body">${esc(r.note || '')}</div></div>`);
      }
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#btnWake').addEventListener('click', async () => {
    try {
      const r = await api(`/api/devices/${deviceId}/display/keep-awake`, { method: 'POST' });
      toast(r.screen_on ? '屏幕已点亮并设为常亮' : '已下发常亮设置，稍等一下', 'ok');
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
    $('#zoomSelect').value = localStorage.getItem('ldm_zoom') || 'fit';
    try {
      await loadDevice();
      await loadCatalog();
      await loadApps();
      api(`/api/devices/${deviceId}/volume`).then(applyVolume).catch(() => {});
    } catch (err) {
      mask('加载失败', err.message);
    }
    // 标签页在后台时不轮询：既省资源，也避免切回来时一堆状态同时刷新导致画面抖一下
    state.timer = setInterval(() => {
      if (document.hidden) return;
      loadDevice().catch(() => {});
    }, 15000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) loadDevice().catch(() => {});
    });
  }

  boot();
})();
