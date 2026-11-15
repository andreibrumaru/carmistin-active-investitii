// Echipa proiectului: selector de persoane pe fiecare categorie (ca un câmp cu „etichete”).
// Scrieți câteva litere → lista utilizatorilor, cu potrivirea evidențiată (fără diacritice), navigare cu ↑ ↓ Enter, Esc.
// Alegerea se salvează imediat; × scoate persoana din echipă. Persoanele externe se adaugă din formularul „Persoană externă”.
(() => {
  const script = document.currentScript;
  const urlAdauga = script.dataset.adauga;
  const utilizatori = JSON.parse(document.getElementById('utilizatoriEchipa')?.textContent || '[]');
  const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || document.querySelector('[name=csrfmiddlewaretoken]')?.value;
  const fara = s => [...(s || '')].map(c => c.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()).join('');  // păstrează pozițiile
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const CULORI = ['#1d9e75', '#378add', '#d85a30', '#7f77dd', '#ba7517', '#d4537e', '#0f6e56', '#185fa5'];
  const culoare = s => CULORI[[...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7) % CULORI.length];
  const initiale = n => n.split(/\s+/).filter(Boolean).slice(0, 2).map(x => x[0]).join('').toUpperCase() || '?';
  const avatar = (n, cont) => `<span class="sp-avatar${cont ? '' : ' extern'}" style="${cont ? `background:${culoare(n)}` : ''}">${esc(initiale(n))}</span>`;
  const evidentiaza = (nume, q) => {
    const i = q ? fara(nume).indexOf(fara(q)) : -1;
    return i < 0 ? esc(nume) : esc(nume.slice(0, i)) + '<mark>' + esc(nume.slice(i, i + q.length)) + '</mark>' + esc(nume.slice(i + q.length));
  };
  const toast = (mesaj, ok = true) => {
    let t = document.getElementById('spMesaj');
    if (!t) { t = Object.assign(document.createElement('div'), {id: 'spMesaj', className: 'sp-mesaj'}); document.body.appendChild(t); }
    t.textContent = mesaj; t.classList.toggle('eroare', !ok); t.classList.add('vizibil');
    clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove('vizibil'), 3500);
  };
  const posteaza = async (url, date) => {
    const r = await fetch(url, {method: 'POST', headers: {'X-CSRFToken': csrf, 'Accept': 'application/json'}, body: new URLSearchParams(date)});
    try { return await r.json(); } catch (e) { return {ok: false, mesaj: r.status === 403 ? 'Nu aveți drept să modificați echipa.' : 'Eroare la salvare.'}; }
  };

  document.querySelectorAll('.selector-persoane').forEach(box => {
    const camp = box.querySelector('.sp-camp'), input = box.querySelector('.sp-input'), lista = box.querySelector('.sp-lista');
    const membri = JSON.parse(box.querySelector('script[type="application/json"]').textContent);
    const nr = (box.closest('.echipa-rand') || box.closest('.card'))?.querySelector('[data-nr]');
    const editabil = !box.classList.contains('doar-citire');
    let rezultate = [], activ = 0;

    function chip(mb) {
      const el = document.createElement('span');
      el.className = 'sp-chip' + (mb.cont ? '' : ' extern');
      el.title = (mb.cont ? 'Utilizator al aplicației – vede proiectul și scrie rapoarte' : 'Persoană externă') + (mb.detalii ? '\n' + mb.detalii : '');
      el.innerHTML = avatar(mb.nume, mb.cont) + `<span class="sp-nume">${esc(mb.nume)}</span>` +
        (editabil ? `<button type="button" class="sp-x" aria-label="Scoate ${esc(mb.nume)} din echipă">×</button>` : '');
      el.querySelector('.sp-x')?.addEventListener('click', async () => {
        // confirmarea din aplicație (fereastra nativă confirm() e blocată în aplicația desktop)
        const mesaj = `Scoateți pe ${mb.nume} din echipa proiectului (${box.dataset.eticheta})?` + (mb.cont ? ' Va pierde accesul la proiect, dacă nu are alt drept.' : '');
        const da = window.confirmaApp ? await window.confirmaApp(mesaj) : window.confirm(mesaj);
        if (!da) return;
        const r = await posteaza(mb.sterge, {});
        if (!r.ok) return toast(r.mesaj, false);
        membri.splice(membri.indexOf(mb), 1); el.remove(); actualizeazaNr(); toast(r.mesaj);
      });
      return el;
    }
    function actualizeazaNr() {
      if (nr) nr.textContent = membri.length;
      if (input) input.placeholder = membri.length ? '+ adaugă' : 'Scrieți un nume…';
    }
    membri.forEach(mb => camp.insertBefore(chip(mb), input || null));
    if (!editabil) return;

    function deseneaza() {
      const q = input.value.trim(), deja = new Set(membri.filter(x => x.user).map(x => x.user));
      rezultate = utilizatori.filter(u => !deja.has(u.id) && (!q || fara(u.nume).includes(fara(q)))).slice(0, 8);
      activ = Math.min(activ, Math.max(rezultate.length - 1, 0));
      lista.innerHTML = rezultate.map((u, i) => `<div class="sp-optiune${i === activ ? ' activ' : ''}" role="option" data-i="${i}">${avatar(u.nume, true)}<span>${evidentiaza(u.nume, q)}</span><span class="sp-rol">${esc(u.rol)}</span></div>`).join('')
        || `<div class="sp-gol">Niciun utilizator „${esc(q)}”.</div>`;
      lista.insertAdjacentHTML('beforeend', `<div class="sp-extern" data-extern="1"><span class="sp-plus">+</span> Adaugă o persoană externă${q ? ` „${esc(q)}”` : ''}</div>`);
      lista.hidden = false;
    }
    async function alege(u) {
      input.value = ''; lista.hidden = true;
      const r = await posteaza(urlAdauga, {categorie: box.dataset.categorie, user: u.id});
      if (!r.ok) return toast(r.mesaj, false);
      membri.push(r.membru); camp.insertBefore(chip(r.membru), input); actualizeazaNr(); toast(r.mesaj);
      input.focus(); activ = 0; deseneaza();  // lista rămâne deschisă: se pot adăuga mai multe persoane la rând
    }
    function extern() {
      const f = document.getElementById('formEchipa'), cat = document.getElementById('externCategorie');
      if (cat) cat.value = box.dataset.categorie;
      const nume = f?.querySelector('[name=nume]'); if (nume) nume.value = input.value.trim();
      input.value = ''; lista.hidden = true;
      if (f && window.bootstrap) bootstrap.Collapse.getOrCreateInstance(f, {toggle: false}).show();
      f?.scrollIntoView({behavior: 'smooth', block: 'center'}); setTimeout(() => (f?.querySelector('[name=functie]') || nume)?.focus(), 300);
    }
    camp.addEventListener('click', e => { if (e.target.closest('.sp-x')) return; input.focus(); if (lista.hidden) { activ = 0; deseneaza(); } });
    input.addEventListener('focus', () => { activ = 0; deseneaza(); });
    // click pe „+ adaugă” când câmpul are deja focus (ex. după o adăugare sau după Esc): redeschide lista
    input.addEventListener('mousedown', () => { if (document.activeElement === input && lista.hidden) { activ = 0; deseneaza(); } });
    input.addEventListener('input', () => { activ = 0; deseneaza(); });
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); activ = Math.min(activ + 1, rezultate.length - 1); deseneaza(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); activ = Math.max(activ - 1, 0); deseneaza(); }
      else if (e.key === 'Enter') { e.preventDefault(); if (rezultate[activ]) alege(rezultate[activ]); else if (input.value.trim()) extern(); }
      else if (e.key === 'Escape') { lista.hidden = true; input.blur(); }
    });
    lista.addEventListener('mousedown', e => {
      e.preventDefault();
      const o = e.target.closest('.sp-optiune');
      if (o) alege(rezultate[+o.dataset.i]); else if (e.target.closest('[data-extern]')) extern();
    });
    lista.addEventListener('mousemove', e => { const o = e.target.closest('.sp-optiune'); if (o && +o.dataset.i !== activ) { activ = +o.dataset.i; deseneaza(); } });
    input.addEventListener('blur', () => setTimeout(() => { lista.hidden = true; }, 120));
    document.addEventListener('mousedown', e => { if (!box.contains(e.target)) lista.hidden = true; });
  });
})();
