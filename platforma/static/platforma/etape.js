// Etapele proiectului ca arbore: etape principale cu subetape (se deschid / închid), status schimbat direct din rând,
// mutare cu mouse-ul în cadrul aceluiași nivel, inserare cu „+”, data finalizării și detaliile salvate pe loc.
(() => {
  const lista = document.getElementById('etLista');
  if (!lista) return;
  const editabil = !!lista.querySelector('.et-maner');
  const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1];
  const cheieDeschise = 'etape-deschise-' + location.pathname;
  const citesteDeschise = () => { try { return new Set(JSON.parse(localStorage.getItem(cheieDeschise) || '[]')); } catch (e) { return new Set(); } };
  const deschise = citesteDeschise();
  const salveazaDeschise = () => { try { localStorage.setItem(cheieDeschise, JSON.stringify([...deschise])); } catch (e) { /* fără stocare */ } };
  const posteaza = async (url, date) => {
    const r = await fetch(url, {method: 'POST', headers: {'X-CSRFToken': csrf, 'Accept': 'application/json'}, body: date instanceof FormData ? date : new URLSearchParams(date)});
    let j = {}; try { j = await r.json(); } catch (e) { j = {ok: false, mesaj: r.status === 403 ? 'Nu aveți drept de modificare.' : 'Eroare la salvare.'}; }
    if (!j.ok) alert(j.mesaj || 'Eroare la salvare.');
    return j;
  };
  const dinHtml = html => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const grupuri = () => [...lista.children].filter(x => x.classList.contains('et-grup'));
  let coloreazaAvatare = () => {};

  function actualizeaza() {  // numerotare 1, 1.1, 1.2 … și starea deschis / închis a fiecărei etape
    grupuri().forEach((g, i) => {
      const rand = g.querySelector(':scope > .et-rand'), sub = g.querySelector(':scope > .et-sub');
      const x = rand.querySelector('.et-index'); if (x) x.textContent = i + 1;
      const copii = [...sub.children].filter(c => c.classList.contains('et-rand'));
      copii.forEach((c, j) => { const y = c.querySelector('.et-index'); if (y) y.textContent = `${i + 1}.${j + 1}`; });
      const deschis = deschise.has(g.dataset.id) || !!sub.querySelector('.et-nou');
      g.classList.toggle('deschis', deschis && (copii.length > 0 || !!sub.querySelector('.et-nou')));
      rand.querySelector('.et-caret')?.classList.toggle('gol', copii.length === 0);
    });
    coloreazaAvatare();
  }
  function inlocuiesteRand(id, html) {
    const vechi = lista.querySelector(`.et-rand[data-id="${id}"]`); if (!vechi || !html) return;
    vechi.replaceWith(dinHtml(html));
  }
  function aplica(j) {  // rândul modificat + rândul etapei principale (progres recalculat din subetape)
    if (j.parinte_id && j.parinte_html) inlocuiesteRand(j.parinte_id, j.parinte_html);
    actualizeaza();
  }
  actualizeaza();

  // ---- deschide / închide subetapele
  lista.addEventListener('click', e => {
    const c = e.target.closest('.et-caret'); if (!c) return;
    const g = c.closest('.et-grup');
    deschise.has(g.dataset.id) ? deschise.delete(g.dataset.id) : deschise.add(g.dataset.id);
    salveazaDeschise(); actualizeaza();
  });
  document.getElementById('etDeschideToate')?.addEventListener('click', () => { grupuri().forEach(g => deschise.add(g.dataset.id)); salveazaDeschise(); actualizeaza(); });
  document.getElementById('etInchideToate')?.addEventListener('click', () => { deschise.clear(); salveazaDeschise(); actualizeaza(); });
  if (!editabil) return;

  // ---- status direct din rând
  lista.addEventListener('click', async e => {
    const b = e.target.closest('.et-alege-status'); if (!b) return;
    const rand = b.closest('.et-rand');
    const j = await posteaza(rand.dataset.url, {actiune: 'status', status: b.dataset.status});
    if (j.ok) { inlocuiesteRand(rand.dataset.id, j.html); aplica(j); }
  });

  // ---- mutare cu mouse-ul: etapa principală (cu subetapele ei) între etape; subetapa în cadrul etapei ei
  let tras = null, container = null;
  lista.addEventListener('mousedown', e => {
    const m = e.target.closest('.et-maner'); if (!m) return;
    const rand = m.closest('.et-rand');
    (rand.classList.contains('et-copil') ? rand : rand.closest('.et-grup')).draggable = true;
  });
  lista.addEventListener('dragstart', e => {
    tras = e.target.closest('.et-copil[draggable="true"], .et-grup[draggable="true"]'); if (!tras) return;
    container = tras.parentElement;
    e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', tras.dataset.id);
    setTimeout(() => tras.classList.add('tras'), 0);
  });
  lista.addEventListener('dragover', e => {
    if (!tras) return;
    const tinta = tras.classList.contains('et-grup') ? e.target.closest('.et-grup') : e.target.closest('.et-copil');
    if (!tinta || tinta === tras || tinta.parentElement !== container) return;
    e.preventDefault();
    const r = (tinta.classList.contains('et-grup') ? tinta.querySelector(':scope > .et-rand') : tinta).getBoundingClientRect();
    (e.clientY < r.top + r.height / 2) ? tinta.before(tras) : tinta.after(tras);
  });
  lista.addEventListener('dragend', async () => {
    if (!tras) return;
    tras.classList.remove('tras'); tras.draggable = false;
    const date = new URLSearchParams();
    [...container.children].filter(x => x.dataset.id && (x.classList.contains('et-grup') || x.classList.contains('et-copil'))).forEach(x => date.append('etapa', x.dataset.id));
    date.append('parinte', container.dataset.parinte || '');
    tras = null; actualizeaza();
    const j = await posteaza(lista.dataset.ordine, date);
    if (!j.ok) location.reload();
  });

  // ---- inserare: „+” între rânduri (același nivel), „Etapă nouă” la final, nod „subetapă” pe etapa principală
  function campNou(containerTinta, dupa, parinte) {
    document.querySelectorAll('.et-nou').forEach(x => x.remove());
    const d = dinHtml(`<div class="et-nou${parinte ? ' sub' : ''}"><i class="bi bi-plus-circle text-primary"></i><input class="form-control form-control-sm" placeholder="${parinte ? 'Subetapă nouă' : 'Etapă nouă'} – Enter salvează, Esc renunță"></div>`);
    dupa ? dupa.after(d) : containerTinta.appendChild(d);
    if (parinte) { deschise.add(String(parinte)); actualizeaza(); }
    const inp = d.querySelector('input'); inp.focus();
    inp.addEventListener('keydown', async e => {
      if (e.key === 'Escape') { d.remove(); actualizeaza(); }
      if (e.key !== 'Enter' || !inp.value.trim()) return;
      e.preventDefault(); inp.disabled = true;
      const prec = d.previousElementSibling;
      const j = await posteaza(lista.dataset.adauga, {denumire: inp.value.trim(), dupa: prec ? prec.dataset.id || '' : '', parinte: parinte || ''});
      if (!j.ok) { inp.disabled = false; return; }
      let nou = dinHtml(j.html);
      if (!parinte) {  // etapă principală nouă: grupul ei, cu lista (goală) de subetape
        const g = dinHtml(`<div class="et-grup" data-id="${nou.dataset.id}"><div class="et-sub" data-parinte="${nou.dataset.id}"></div></div>`);
        g.prepend(nou); nou = g;
      }
      d.replaceWith(nou); aplica(j); campNou(containerTinta, nou, parinte);
    });
    inp.addEventListener('blur', () => setTimeout(() => { if (!inp.value.trim() && document.activeElement !== inp && d.isConnected) { d.remove(); actualizeaza(); } }, 150));
  }
  document.addEventListener('click', e => {
    const b = e.target.closest('.et-plus, .et-adauga-sub'); if (!b) return;
    const rand = b.closest('.et-rand');
    if (b.classList.contains('et-adauga-sub')) {  // subetapă la finalul etapei principale
      const g = rand.closest('.et-grup'); campNou(g.querySelector(':scope > .et-sub'), null, g.dataset.id); return;
    }
    if (!rand) { const ult = grupuri().pop(); campNou(lista, ult || null, null); return; }  // „Etapă nouă” la final
    if (rand.classList.contains('et-copil')) campNou(rand.parentElement, rand, rand.dataset.parinte);
    else campNou(lista, rand.closest('.et-grup'), null);
  });

  // ---- responsabilul etapei: ales din echipa proiectului (ca la definirea echipei); alți utilizatori se adaugă atunci în echipă
  const echipa = JSON.parse(document.getElementById('etEchipa')?.textContent || '[]');
  const altii = JSON.parse(document.getElementById('etUtilizatori')?.textContent || '[]');
  const fara = s => [...(s || '')].map(c => c.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()).join('');
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const CULORI = ['#1d9e75', '#378add', '#d85a30', '#7f77dd', '#ba7517', '#d4537e', '#0f6e56', '#185fa5'];
  const culoare = s => CULORI[[...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7) % CULORI.length];
  const initiale = n => n.split(/\s+/).filter(Boolean).slice(0, 2).map(x => x[0]).join('').toUpperCase() || '?';
  const avatar = (n, cont) => `<span class="sp-avatar${cont ? '' : ' extern'}" style="${cont ? `background:${culoare(n)}` : ''}">${esc(initiale(n))}</span>`;
  const marcheaza = (nume, q) => { const i = q ? fara(nume).indexOf(fara(q)) : -1; return i < 0 ? esc(nume) : esc(nume.slice(0, i)) + '<mark>' + esc(nume.slice(i, i + q.length)) + '</mark>' + esc(nume.slice(i + q.length)); };
  // avatarele responsabililor: culoare + inițiale (o singură dată pe element; fără observator de modificări – ar intra în buclă)
  coloreazaAvatare = () => lista.querySelectorAll('.et-resp-btn .sp-avatar:not([data-colorat])').forEach(a => {
    a.dataset.colorat = '1';
    if (a.classList.contains('extern')) return;
    a.style.background = culoare(a.dataset.nume || ''); a.textContent = initiale(a.dataset.nume || '');
  });
  coloreazaAvatare();
  let panou = null, butonActiv = null, optiuni = [], activ = 0;
  function inchidePanou() { panou?.remove(); panou = null; butonActiv = null; }
  function pozitioneaza() {  // sub buton dacă încape, altfel deasupra; poziție fixă pe ecran, corectată cu mărimea afișării (zoom)
    if (!panou || !butonActiv) return;
    const z = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    const r = butonActiv.getBoundingClientRect(), vh = window.innerHeight, vw = window.innerWidth;
    const jos = vh - r.bottom - 8, sus = r.top - 8;
    panou.style.position = 'fixed';
    panou.style.maxHeight = '';
    const hNat = panou.getBoundingClientRect().height;  // înălțimea vizuală fără limită
    const sub = hNat <= jos || jos >= sus;
    const loc = Math.max(120, sub ? jos : sus);
    const h = Math.min(hNat, loc, 380 * z);
    panou.style.maxHeight = h / z + 'px';
    const top = sub ? r.bottom + 4 : r.top - 4 - h;
    const left = Math.max(8, Math.min(r.left, vw - 330 * z - 8));
    panou.style.left = left / z + 'px';
    panou.style.top = Math.max(8, top) / z + 'px';
  }


  function deschidePanou(btn) {
    inchidePanou(); butonActiv = btn;
    panou = document.createElement('div'); panou.className = 'sp-lista et-resp-lista';
    panou.innerHTML = '<input class="form-control form-control-sm mb-1" placeholder="Scrieți un nume…"><div class="et-resp-optiuni"></div>';
    document.body.appendChild(panou);
    const inp = panou.querySelector('input'); inp.focus({preventScroll: true});
    const deseneaza = () => {
      const q = inp.value.trim(), curent = btn.dataset.membru;
      const din = echipa.filter(x => !q || fara(x.nume).includes(fara(q)));
      const inEchipa = new Set(echipa.filter(x => x.user).map(x => x.user));
      const ext = altii.filter(u => !inEchipa.has(u.id) && q && fara(u.nume).includes(fara(q))).slice(0, 6);
      optiuni = [...din.map(x => ({tip: 'membru', id: x.id})), ...ext.map(u => ({tip: 'user', id: u.id}))];
      if (curent) optiuni.push({tip: 'fara'});
      activ = Math.min(activ, Math.max(optiuni.length - 1, 0));
      let i = 0, h = '';
      if (din.length) h += '<div class="et-resp-titlu">Echipa proiectului</div>' + din.map(x => `<div class="sp-optiune${i === activ ? ' activ' : ''}${String(x.id) === curent ? ' ales' : ''}" data-i="${i++}">${avatar(x.nume, x.cont)}<span>${marcheaza(x.nume, q)}</span><span class="sp-rol">${esc(x.categorie)}</span></div>`).join('');
      if (ext.length) h += '<div class="et-resp-titlu">Alți utilizatori – se adaugă în echipă</div>' + ext.map(u => `<div class="sp-optiune${i === activ ? ' activ' : ''}" data-i="${i++}">${avatar(u.nume, true)}<span>${marcheaza(u.nume, q)}</span><span class="sp-rol">${esc(u.rol)}</span></div>`).join('');
      if (!din.length && !ext.length) h += `<div class="sp-gol">${echipa.length ? `Nimeni „${esc(q)}” în echipă.` : 'Echipa proiectului nu e definită.'}${altii.length && !q ? ' Scrieți un nume pentru a căuta printre utilizatori.' : ''}</div>`;
      if (curent) h += `<div class="sp-optiune text-danger${i === activ ? ' activ' : ''}" data-i="${i++}"><i class="bi bi-x-circle"></i><span>Fără responsabil</span></div>`;
      if (lista.dataset.setari) h += `<a class="sp-extern" href="${lista.dataset.setari}"><span class="sp-plus"><i class="bi bi-people"></i></span> Definirea echipei (Setări)</a>`;
      panou.querySelector('.et-resp-optiuni').innerHTML = h;
      pozitioneaza();
    };
    const alege = async o => {
      if (!o || !butonActiv) return;
      const rand = butonActiv.closest('.et-rand');
      const date = {actiune: 'responsabil', responsabil: o.tip === 'membru' ? o.id : '', user: o.tip === 'user' ? o.id : ''};
      inchidePanou();
      const j = await posteaza(rand.dataset.url, date);
      if (!j.ok) return;
      if (o.tip === 'user') location.reload();  // a intrat un om nou în echipă: lista echipei se reîncarcă
      else { inlocuiesteRand(rand.dataset.id, j.html); aplica(j); }
    };
    inp.addEventListener('input', () => { activ = 0; deseneaza(); });
    inp.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); activ = Math.min(activ + 1, optiuni.length - 1); deseneaza(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); activ = Math.max(activ - 1, 0); deseneaza(); }
      else if (e.key === 'Enter') { e.preventDefault(); alege(optiuni[activ]); }
      else if (e.key === 'Escape') inchidePanou();
    });
    panou.addEventListener('mousedown', e => { const o = e.target.closest('.sp-optiune'); if (o) { e.preventDefault(); alege(optiuni[+o.dataset.i]); } });
    deseneaza();
  }
  lista.addEventListener('click', e => { const b = e.target.closest('.et-resp-btn'); if (b) { e.stopPropagation(); butonActiv === b ? inchidePanou() : deschidePanou(b); } });
  document.addEventListener('mousedown', e => { if (panou && !panou.contains(e.target) && !e.target.closest('.et-resp-btn')) inchidePanou(); });
  window.addEventListener('scroll', () => pozitioneaza(), {passive: true});  // la derulare lista rămâne deschisă și se mută cu butonul
  window.addEventListener('resize', () => pozitioneaza());

  // ---- data finalizării, detalii, ștergere
  lista.addEventListener('change', async e => {
    if (!e.target.classList.contains('et-data')) return;
    const rand = e.target.closest('.et-rand');
    const j = await posteaza(rand.dataset.url, {actiune: 'data_final', final_real: e.target.value});
    if (j.ok) { inlocuiesteRand(rand.dataset.id, j.html); aplica(j); }
  });
  lista.addEventListener('submit', async e => {
    const f = e.target.closest('.et-form'); if (!f) return;
    e.preventDefault();
    const rand = f.closest('.et-rand'), j = await posteaza(f.action, new FormData(f));
    if (j.ok) { inlocuiesteRand(rand.dataset.id, j.html); aplica(j); }
  });
  lista.addEventListener('click', async e => {
    const b = e.target.closest('.et-sterge'); if (!b) return;
    const rand = b.closest('.et-rand'), nume = rand.querySelector('.et-nume-text').textContent.trim();
    const sub = rand.classList.contains('et-principala') ? rand.closest('.et-grup').querySelectorAll('.et-copil').length : 0;
    const mesaj = `Ștergeți ${rand.classList.contains('et-copil') ? 'subetapa' : 'etapa'} „${nume}”?` + (sub ? ` Se șterg și cele ${sub} subetape ale ei.` : '');
    const da = window.confirmaApp ? await window.confirmaApp(mesaj) : confirm(mesaj);
    if (!da) return;
    const j = await posteaza(rand.dataset.url, {actiune: 'sterge'});
    if (!j.ok) return;
    (rand.classList.contains('et-principala') ? rand.closest('.et-grup') : rand).remove(); aplica(j);
  });
})();
