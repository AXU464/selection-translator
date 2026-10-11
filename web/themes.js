/* Shared theme tokens for the selection bar, reading card and preferences. */
(() => {
  const media = matchMedia('(prefers-color-scheme: dark)');
  let selected = 'system';
  const mix = (a, b, amount) => '#' + [1, 3, 5].map(i => Math.round(
    parseInt(a.slice(i, i + 2), 16) * (1 - amount) + parseInt(b.slice(i, i + 2), 16) * amount
  ).toString(16).padStart(2, '0')).join('');
  const theme = window.Themes = {
    palettes: {},
    apply(id) {
      selected = id === 'system' || theme.palettes[id] ? id : 'system';
      const p = theme.palettes[selected === 'system' ? (media.matches ? 'graphite' : 'paper') : selected];
      if (!p) return;
      const dark = p.group !== '浅色';
      const values = {
        bg: p.bg, surface: mix(p.bg, '#ffffff', dark ? .035 : .48),
        soft: mix(p.bg, p.track, .55), code: mix(p.bg, p.track, .7), ink: p.fg,
        muted: p.muted, faint: p.muted, line: p.border, accent: p.accent, tint: p.track,
        danger: dark ? '#ffb4a8' : '#a52b32', 'danger-bg': dark ? '#4c2c30' : '#fff0ed',
        border: p.border, text: p.fg, hover: p.track,
        shadow: dark ? '0 12px 40px #0006' : '0 12px 40px #233d3024, 0 2px 8px #233d3010'
      };
      Object.entries(values).forEach(([key, value]) => document.documentElement.style.setProperty('--' + key, value));
      document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
      document.documentElement.dataset.theme = selected;
    }
  };
  theme.ready = fetch('themes.json').then(r => { if (!r.ok) throw Error('主题加载失败'); return r.json(); })
    .then(data => { theme.palettes = data; theme.apply(selected); return data; });
  // Remember an early backend request while the small local catalog is loading.
  window.applyTheme = id => { theme.ready.then(() => theme.apply(id)); };
  media.addEventListener('change', () => { if (selected === 'system') theme.apply(selected); });
})();
