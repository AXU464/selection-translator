(() => {
  const $ = id => document.getElementById(id);
  const fields = [...document.querySelectorAll('[data-key]')];
  let original = {}, theme = 'system', loaded = false, busy = false, generation = 0;
  let previews = Promise.resolve();
  const api = () => window.pywebview.api;
  let testing = false, connectionRevision = 0;
  for (const field of [$('api-key'), document.querySelector('[data-key="api.base_url"]')]) {
    field.addEventListener('input', () => { ++connectionRevision; $('connection-status').textContent = ''; });
  }
  $('test-connection').addEventListener('click', async () => {
    if (testing || !loaded) return;
    testing = true; const current = generation, revision = connectionRevision;
    $('test-connection').disabled = true; $('test-connection').textContent = '测试中…';
    $('connection-status').textContent = '正在检查当前表单中的连接…';
    try {
      const result = await api().test_connection({'api.api_key': $('api-key').value,
        'api.base_url': document.querySelector('[data-key="api.base_url"]').value});
      if (current !== generation || revision !== connectionRevision) return;
      $('connection-status').textContent = result.ok
        ? `连接正常 · ${result.elapsed} 秒。` + (result.models.length ? `可用模型：${result.models.join('、')}` : '服务未列出可用模型。') + '（模型列表测试，不检查余额。）'
        : result.error;
      $('connection-status').classList.toggle('error', !result.ok);
    } catch (_) { if (current === generation) $('connection-status').textContent = '连接测试未完成，请重试。'; }
    finally { testing = false; $('test-connection').disabled = false; $('test-connection').textContent = '测试连接'; }
  });
  function status(text, error = false) { $('status').textContent = text; $('status').classList.toggle('error', error); }
  function selectPage(id) {
    document.querySelectorAll('main > section').forEach(el => el.hidden = el.id !== id);
    document.querySelectorAll('.nav-item').forEach(el => { el.classList.toggle('active', el.dataset.page === id); if (el.dataset.page === id) el.setAttribute('aria-current','page'); else el.removeAttribute('aria-current'); });
    $('content').scrollTop = 0;
    $('cancel').textContent = id === 'history-page' ? '关闭' : '取消';
    if (id === 'history-page') status('历史与标签自动保存；数量上限需点击「保存设置」。');
    if (id === 'history-page' && window.History) window.History.refresh();
  }
  document.querySelectorAll('.nav-item').forEach(el => el.addEventListener('click', () => selectPage(el.dataset.page)));
  function appearance() {
    $('font-value').textContent = $('font-size').value + ' px';
    $('reading-preview').style.setProperty('--preview-font', $('font-size').value + 'px');
    $('theme-system').setAttribute('aria-pressed', String(theme === 'system'));
    document.querySelectorAll('[data-theme-id]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.themeId === theme)));
  }
  function preview() {
    appearance();
    if (!loaded || busy) return;
    const current = generation;
    const values = {'ui.theme': theme, 'ui.font_size': Number($('font-size').value)};
    previews = previews.then(async () => {
      if (current === generation && loaded) await api().preview(values);
    }).catch(() => status('预览未完成，请重试。', true));
  }
  function chooseTheme(id) { if (busy) return; theme = id; preview(); status('正在预览 · 保存后会记住这个外观'); }
  $('theme-system').addEventListener('click', () => chooseTheme('system'));
  $('font-size').addEventListener('input', preview);
  for (const field of [...fields, $('api-key')]) {
    field.addEventListener('input', () => {
      if (loaded && !busy) status('有未保存的更改 · 点击保存应用');
    });
  }
  async function buildThemes() {
    const palettes = await Themes.ready;
    $('theme-grid').replaceChildren();
    for (const group of ['浅色', '彩色', '深色']) {
      const heading = document.createElement('div'); heading.className = 'group-title'; heading.textContent = group;
      const grid = document.createElement('div'); grid.className = 'theme-group';
      for (const [id, p] of Object.entries(palettes).filter(([, p]) => p.group === group)) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'theme-tile'; button.dataset.themeId = id;
        button.setAttribute('aria-label', p.name + ' · ' + p.english); button.setAttribute('aria-pressed','false');
        button.style.setProperty('--swatch-bg',p.bg); button.style.setProperty('--swatch-ink',p.fg); button.style.setProperty('--swatch-accent',p.accent);
        const swatch = document.createElement('span'); swatch.className='swatch'; swatch.setAttribute('aria-hidden','true');
        swatch.innerHTML='<span class="swatch-letter">Aa</span><span class="swatch-lines"><i></i><i></i></span>';
        const label=document.createElement('span'); label.className='theme-name'; label.textContent=p.name;
        const english=document.createElement('small'); english.textContent=p.english; label.append(english);
        button.append(swatch,label); button.addEventListener('click',()=>chooseTheme(id)); grid.append(button);
      }
      $('theme-grid').append(heading,grid);
    }
  }
  const catalog = buildThemes();
  function clearSecrets() { $('api-key').value=''; $('api-key').type='password'; $('toggle-key').textContent='显示'; $('toggle-key').setAttribute('aria-pressed','false'); }
  function fill(data) {
    original = data.values;
    fields.forEach(el => { const value=original[el.dataset.key]; if(el.type==='checkbox') el.checked=!!value; else el.value=value ?? ''; });
    theme = original['ui.theme'] || 'system';
    clearSecrets();
    $('key-state').textContent = data.hasApiKey ? '已配置' : '未配置';
    $('api-key').placeholder = data.hasApiKey ? '已保存密钥 · 留空保持不变' : '输入 API Key';
    appearance();
  }
  window.Settings = {
    clearSecrets,
    openPage: selectPage,
    async load(page = 'appearance') {
      const current=++generation; loaded=false; busy=false; $('save').disabled=true;
      $('connection-status').textContent = '';
      status('正在读取设置…');
      try { await catalog; const data=await api().read(); if(current!==generation) return; fill(data); loaded=true; $('save').disabled=false; status('外观可即时预览，保存后生效。'); selectPage(page); }
      catch (_) { status('无法读取设置，请关闭后重新打开。',true); }
    }
  };
  $('toggle-key').addEventListener('click',()=>{ const show=$('api-key').type==='password'; $('api-key').type=show?'text':'password'; $('toggle-key').textContent=show?'隐藏':'显示'; $('toggle-key').setAttribute('aria-pressed',String(show)); });
  async function close() {
    if(busy) return;
    ++generation; loaded=false; clearSecrets();
    await previews;
    await api().close();
  }
  $('cancel').addEventListener('click',()=>close().catch(()=>status('关闭失败，请重试。',true)));
  document.addEventListener('keydown', ev=>{ if(ev.key==='Escape'){ev.preventDefault();close().catch(()=>{});} });
  $('save').addEventListener('click',async()=>{
    if(!loaded || busy) return;
    for(const el of fields) {
      if(!el.checkValidity()) { selectPage(el.closest('section').id); const details=el.closest('details'); if(details) details.open=true; el.reportValidity(); return; }
    }
    const values={'ui.theme':theme};
    fields.forEach(el=>values[el.dataset.key]=el.type==='checkbox'?el.checked:(['range','number'].includes(el.type)?Number(el.value):el.value));
    const changes=Object.fromEntries(Object.entries(values).filter(([key,value])=>value!==original[key]));
    if($('api-key').value.trim()) changes['api.api_key']=$('api-key').value.trim();
    busy=true; $('save').disabled=true; $('cancel').disabled=true; status('正在保存…');
    try {
      await previews;
      const result=await api().save(changes);
      if(!result.ok) { status(result.error || '保存失败，请重试。',true); return; }
      fill(result); status('已保存 · 所有设置已生效');
    } catch (_) { status('保存未完成，请重试。',true); }
    finally { busy=false; $('save').disabled=false; $('cancel').disabled=false; }
  });
  window.addEventListener('pywebviewready',()=>window.Settings.load());
})();
