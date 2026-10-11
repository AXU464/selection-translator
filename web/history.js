(() => {
  const $ = id => document.getElementById(id), api = () => window.pywebview.api;
  let page = 0, selected = null, revision = 0, detailRevision = 0, timer, busy = false;
  const filters = () => [$('history-search').value, $('history-tag-filter').value];
  const message = text => $('history-status').textContent = text;
  function showDetail(row) {
    selected = row ? row.id : null;
    $('history-detail').hidden = !row;
    $('history-source').textContent = row ? row.original : '';
    $('history-translation').textContent = row ? row.translation : '';
    $('history-tags').value = row ? (row.tags || []).join(', ') : '';
    $('history-date').textContent = row ? new Date(row.ts * 1000).toLocaleString() + (row.direction === 'zh' ? ' · → 中文' : ' · → English') : '';
    document.querySelectorAll('.history-entry').forEach(el => el.setAttribute('aria-pressed', String(Number(el.dataset.id) === selected)));
  }
  async function detail(id) {
    const current = ++detailRevision;
    try { const row = await api().history_detail(id); if (current === detailRevision) showDetail(row); }
    catch (_) { message('暂时无法读取这条历史，请重试。'); }
  }
  async function refresh() {
    if ($('history-page').hidden || !window.pywebview) return;
    const current = ++revision, [keyword, tag] = filters();
    try {
      const result = await api().history_list(keyword, tag, page);
      if (current !== revision) return;
      if (page && !result.items.length) { page = 0; return refresh(); }
      $('history-tag-filter').replaceChildren(new Option('全部标签', ''));
      for (const value of result.tags) $('history-tag-filter').append(new Option(value, value));
      if (tag && !result.tags.includes(tag)) $('history-tag-filter').append(new Option(tag, tag));
      $('history-tag-filter').value = tag;
      $('history-list').replaceChildren();
      for (const row of result.items) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'history-entry'; button.dataset.id = row.id;
        button.setAttribute('aria-pressed', String(row.id === selected));
        const label = document.createElement('span'); label.textContent = row.original;
        const meta = document.createElement('small'); meta.textContent = new Date(row.ts * 1000).toLocaleDateString() + ' · ' + (row.direction === 'zh' ? '→ 中文' : '→ English') + (row.tags.length ? ' · ' + row.tags.join(' / ') : '');
        button.append(label, meta); button.addEventListener('click', () => detail(row.id)); $('history-list').append(button);
      }
      $('history-count').textContent = result.total ? `${result.total} 条 · ${page + 1}/${Math.ceil(result.total / 30)} 页` : '暂无匹配记录';
      $('history-prev').disabled = page === 0; $('history-next').disabled = (page + 1) * 30 >= result.total;
      if (!result.items.some(row => row.id === selected)) { ++detailRevision; showDetail(null); }
      if (result.error) message(result.error);
    } catch (_) { message('历史暂时无法读取，翻译功能仍可继续使用。'); }
  }
  window.History = {refresh};
  $('history-search').addEventListener('input', () => { ++revision; clearTimeout(timer); page=0; timer=setTimeout(refresh, 180); });
  $('history-tag-filter').addEventListener('change', () => { page=0; refresh(); });
  $('history-prev').addEventListener('click', () => { page=Math.max(0,page-1); refresh(); });
  $('history-next').addEventListener('click', () => { ++page; refresh(); });
  async function action(button, task) {
    if (busy) return;
    busy=true; button.disabled=true;
    try { await task(); } catch (_) { message('操作未完成，请重试。'); }
    finally { busy=false; button.disabled=false; }
  }
  $('history-save-tags').addEventListener('click', () => action($('history-save-tags'), async () => {
    if (selected === null) return;
    const result = await api().history_tags(selected, $('history-tags').value);
    message(result.ok ? '标签已保存' : result.error); await refresh();
  }));
  $('history-show-card').addEventListener('click', () => action($('history-show-card'), async () => {
    if (selected !== null) await api().history_open(selected);
  }));
  $('clear-history').addEventListener('click', () => action($('clear-history'), async () => {
    const result = await api().history_clear();
    if (result.cancelled) return;
    if (result.ok) { ++detailRevision; showDetail(null); page=0; $('history-search').value=''; $('history-tag-filter').value=''; }
    message(result.ok ? '历史已清空' : result.error); await refresh();
  }));
  for (const [id, kind] of [['export-md','markdown'], ['export-tsv','tsv']]) {
    $(id).addEventListener('click', () => action($(id), async () => {
      const result = await api().history_export(kind, ...filters());
      message(result.ok ? '已导出：' + result.path : result.error); $('open-exports').hidden = !result.ok;
    }));
  }
  $('open-exports').addEventListener('click', () => action($('open-exports'), () => api().history_export_folder()));
})();
