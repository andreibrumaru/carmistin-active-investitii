// Meniul lateral: bara de icoane deschide panoul secțiunii la trecerea mouse-ului (sau la focus din tastatură).
// Pinul fixează panoul deschis; alegerea se ține minte (cookie, un an).
(() => {
  const nav = document.getElementById('meniu');
  if (!nav) return;
  const curent = nav.dataset.curent, sectiuni = [...nav.querySelectorAll('.panou-sec')], butoane = [...nav.querySelectorAll('.rail-btn')];
  const existent = k => sectiuni.some(s => s.dataset.sec === k);
  const arata = k => {
    if (!existent(k)) k = existent(curent) ? curent : sectiuni[0]?.dataset.sec;
    sectiuni.forEach(s => { s.hidden = s.dataset.sec !== k; });
    butoane.forEach(b => b.classList.toggle('arata', b.dataset.sec === k && nav.classList.contains('deschis')));
  };
  let inchide;
  const deschide = k => { clearTimeout(inchide); nav.classList.add('deschis'); arata(k); };
  butoane.forEach(b => {
    b.addEventListener('mouseenter', () => deschide(b.dataset.sec));
    b.addEventListener('focus', () => deschide(b.dataset.sec));
  });
  nav.querySelector('.rail-cont')?.addEventListener('click', e => { e.preventDefault(); deschide('cont'); });
  nav.querySelector('[data-sec="notificari"]')?.addEventListener('click', e => { e.preventDefault(); deschide('notificari'); });
  nav.addEventListener('mouseleave', () => { inchide = setTimeout(() => { nav.classList.remove('deschis'); arata(curent); }, 180); });
  nav.addEventListener('mouseenter', () => clearTimeout(inchide));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { nav.classList.remove('deschis'); arata(curent); } });
  document.getElementById('meniuPin')?.addEventListener('click', () => {
    const fixat = nav.classList.toggle('fixat');
    document.getElementById('meniuPin').setAttribute('aria-pressed', fixat);
    document.cookie = `meniu_fixat=${fixat ? 1 : 0}; path=/; max-age=31536000; SameSite=Lax`;
    if (!fixat) nav.classList.add('deschis');  // rămâne deschis cât timp mouse-ul e pe meniu
  });
  arata(curent);
})();
