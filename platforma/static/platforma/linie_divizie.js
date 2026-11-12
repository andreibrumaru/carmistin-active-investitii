// Divizia se alege în funcție de linia de business (ex. Pui → Reproducție, Incubație, Broiler).
(() => {
  const linie = document.getElementById('id_linie'), div = document.getElementById('id_divizie');
  if (!linie || !div) return;
  const toate = [...div.options].filter(o => o.value);
  const filtreaza = () => {
    const l = (linie.value || '').toLowerCase(), curent = div.value;
    div.length = 1;
    div.options[0].text = l ? '—' : '— alegeți întâi linia —';
    toate.filter(o => (o.dataset.linie || '').toLowerCase() === l).forEach(o => div.add(o));
    div.value = [...div.options].some(o => o.value === curent) ? curent : '';
    div.disabled = !l;
  };
  linie.addEventListener('change', filtreaza);
  div.form?.addEventListener('submit', () => { div.disabled = false; });
  filtreaza();
})();
