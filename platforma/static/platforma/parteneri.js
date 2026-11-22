// Partenerul unui contract / furnizorul unei facturi: căutare în registrul de parteneri (după denumire)
// și preluarea datelor de la ANAF după CUI. La salvarea formularului, partenerul rămâne în registru.
(() => {
  const PERECHI = [['partener', 'cui_partener'], ['furnizor', 'cui_furnizor']];
  const esc = t => String(t ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  function fisa(d) {
    const b = [];
    if (d.inactiv) b.push('<span class="badge text-bg-danger"><i class="bi bi-exclamation-octagon"></i> INACTIV fiscal</span>');
    if (d.radiat) b.push('<span class="badge text-bg-danger">radiat</span>');
    if (d.platitor_tva === true) b.push('<span class="badge text-bg-success">plătitor TVA</span>');
    if (d.platitor_tva === false) b.push('<span class="badge text-bg-warning">neplătitor TVA</span>');
    if (d.tva_incasare) b.push('<span class="badge text-bg-info">TVA la încasare</span>');
    if (d.e_factura) b.push('<span class="badge text-bg-light border">RO e-Factura</span>');
    const sursa = d.sursa === 'anaf' ? 'date ANAF de azi' : (d.verificat ? `registru · verificat ANAF ${esc(d.verificat)}` : 'registru · neverificat la ANAF');
    return `<div class="pt-fisa${d.inactiv || d.radiat ? ' pt-fisa-rau' : ''}"><div class="d-flex gap-2 align-items-start"><b class="flex-grow-1">${esc(d.denumire)}</b><span class="text-secondary">${sursa}</span></div>
      <div class="text-secondary">CUI ${esc(d.cui)}${d.nr_reg_com ? ' · ' + esc(d.nr_reg_com) : ''}</div>
      ${d.adresa ? `<div class="text-secondary">${esc(d.adresa)}</div>` : ''}<div class="d-flex flex-wrap gap-1 mt-1">${b.join('')}</div>
      ${d.avertisment ? `<div class="text-warning-emphasis mt-1">${esc(d.avertisment)}</div>` : ''}</div>`;
  }

  function leaga(nume, cui) {
    const cont = cui.closest('div[class*="col"]') || cui.parentElement;
    // butonul ANAF lângă CUI
    const grup = document.createElement('div'); grup.className = 'input-group';
    cui.parentNode.insertBefore(grup, cui); grup.appendChild(cui);
    const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'btn btn-outline-primary';
    btn.innerHTML = '<i class="bi bi-search"></i> ANAF'; btn.title = 'Preia datele firmei de la ANAF după CUI';
    grup.appendChild(btn);
    const info = document.createElement('div'); info.className = 'small mt-1 pt-info'; cont.appendChild(info);
    let ultimul = '';

    async function anaf(suprascrie) {
      const v = cui.value.replace(/\D/g, '');
      if (!v) { info.innerHTML = '<span class="text-danger">Introduceți CUI-ul.</span>'; return; }
      ultimul = v;
      info.innerHTML = '<span class="text-secondary"><span class="spinner-border spinner-border-sm"></span> Interoghez ANAF…</span>';
      btn.disabled = true;
      try {
        const r = await fetch(`/parteneri/anaf/?cui=${encodeURIComponent(v)}`, {headers: {Accept: 'application/json'}});
        const d = await r.json();
        if (!r.ok || d.eroare) { info.innerHTML = `<span class="text-danger">${esc(d.eroare || 'Eroare la interogare.')}</span>`; return; }
        if (suprascrie || !nume.value.trim()) nume.value = d.denumire;
        info.innerHTML = fisa(d) + (d.sursa === 'anaf' && !d.in_registru ? '<div class="text-secondary mt-1"><i class="bi bi-info-circle"></i> Partenerul nou se adaugă în registru la salvare.</div>' : '');
      } catch (e) {
        info.innerHTML = '<span class="text-danger">Serviciul ANAF nu a răspuns. Încercați din nou.</span>';
      } finally { btn.disabled = false; }
    }
    btn.addEventListener('click', () => anaf(true));
    cui.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); anaf(true); } });
    cui.addEventListener('change', () => { const v = cui.value.replace(/\D/g, ''); if (v.length >= 2 && v !== ultimul) anaf(false); });

    // lista derulantă cu partenerii din registru + căutare; partener nou = CUI + ANAF, adăugat direct în registru
    const box = nume.parentElement; box.classList.add('position-relative');
    const grupN = document.createElement('div'); grupN.className = 'input-group';
    nume.parentNode.insertBefore(grupN, nume); grupN.appendChild(nume);
    const sageata = document.createElement('button'); sageata.type = 'button'; sageata.className = 'btn btn-outline-secondary pt-sageata';
    sageata.innerHTML = '<i class="bi bi-chevron-down"></i>'; sageata.title = 'Alege din registrul partenerilor'; sageata.tabIndex = -1;
    grupN.appendChild(sageata);
    nume.setAttribute('autocomplete', 'off'); nume.placeholder = 'Alegeți din registru sau căutați după denumire / CUI';
    const lista = document.createElement('div'); lista.className = 'pt-lista d-none'; box.appendChild(lista);
    const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || document.querySelector('[name=csrfmiddlewaretoken]')?.value;
    let t, rez = [], total = 0, activ = -1, ales = nume.value.trim(), cerere = 0;

    const deschis = () => !lista.classList.contains('d-none');
    const inchide = () => { lista.classList.add('d-none'); activ = -1; };
    function alege(d, mesaj) {
      nume.value = d.denumire; cui.value = d.cui; ultimul = d.cui; ales = d.denumire;
      info.innerHTML = fisa(d) + (mesaj ? `<div class="text-success mt-1"><i class="bi bi-check2-circle"></i> ${mesaj}</div>` : '');
      inchide(); nume.dispatchEvent(new Event('change', {bubbles: true}));
    }
    function deseneaza(q) {
      const cifre = q.replace(/\D/g, '');
      lista.innerHTML = `<div class="pt-opt-antet">${q ? `${rez.length} rezultat${rez.length === 1 ? '' : 'e'} pentru „${esc(q)}”` : `Registrul partenerilor · ${total}`}</div>`
        + rez.map((d, i) => `<button type="button" class="pt-opt pt-opt-linie${i === activ ? ' activ' : ''}${d.denumire === ales ? ' ales' : ''}" data-i="${i}" title="${esc(d.denumire)} · CUI ${esc(d.cui)}">${esc(d.denumire)}${d.inactiv || d.radiat ? ' <i class="bi bi-exclamation-octagon-fill text-danger" title="inactiv / radiat"></i>' : ''}</button>`).join('')
        + (q && !rez.length ? '<div class="pt-opt-nota">Partenerul nu este în registru.</div>' : '')
        + `<div class="pt-nou"><div class="pt-nou-cap"><i class="bi bi-plus-circle"></i> Partener nou după CUI (ANAF)</div>
             <div class="input-group input-group-sm"><input class="form-control pt-nou-cui" placeholder="CUI, ex. 14399840" value="${esc(cifre.length >= 2 && cifre.length === q.replace(/^RO/i, '').trim().length ? cifre : '')}">
             <button type="button" class="btn btn-primary pt-nou-btn"><i class="bi bi-search"></i> Caută la ANAF</button></div><div class="pt-nou-rez small mt-1"></div></div>`;
      lista.classList.remove('d-none');
    }
    async function cauta(q) {
      const nr = ++cerere;
      const r = await fetch(`/parteneri/cauta/?q=${encodeURIComponent(q)}`, {headers: {Accept: 'application/json'}});
      const d = await r.json();
      if (nr !== cerere) return;  // a sosit între timp un răspuns mai nou
      rez = d.rezultate || []; if (!q) total = d.total ?? rez.length;
      activ = -1; deseneaza(q);
    }
    const deschide = () => cauta(nume.value.trim() === ales ? '' : nume.value.trim());
    sageata.addEventListener('click', () => { if (deschis()) inchide(); else { nume.focus(); deschide(); } });
    nume.addEventListener('focus', () => { if (!deschis()) deschide(); });
    nume.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => cauta(nume.value.trim()), 200); });
    nume.addEventListener('keydown', e => {
      if (e.key === 'Escape') return inchide();
      if (!deschis() || !rez.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); activ = (activ + (e.key === 'ArrowDown' ? 1 : -1) + rez.length) % rez.length;
        lista.querySelectorAll('.pt-opt').forEach((o, i) => o.classList.toggle('activ', i === activ));
        lista.querySelectorAll('.pt-opt')[activ]?.scrollIntoView({block: 'nearest'});
      } else if (e.key === 'Enter' && activ >= 0) { e.preventDefault(); alege(rez[activ]); }
    });
    lista.addEventListener('mousedown', e => {
      const o = e.target.closest('.pt-opt'); if (!o) return;
      e.preventDefault(); alege(rez[+o.dataset.i]);
    });
    // partener nou: CUI → ANAF → fișa firmei → „Adaugă în registru și selectează”
    let gasit = null;
    async function cautaAnaf() {
      const camp = lista.querySelector('.pt-nou-cui'), out = lista.querySelector('.pt-nou-rez');
      const v = camp.value.replace(/\D/g, '');
      if (!v) { out.innerHTML = '<span class="text-danger">Introduceți CUI-ul.</span>'; return; }
      out.innerHTML = '<span class="text-secondary"><span class="spinner-border spinner-border-sm"></span> Interoghez ANAF…</span>';
      try {
        const r = await fetch(`/parteneri/anaf/?cui=${encodeURIComponent(v)}`, {headers: {Accept: 'application/json'}});
        const d = await r.json();
        if (!r.ok || d.eroare) { out.innerHTML = `<span class="text-danger">${esc(d.eroare || 'Eroare la interogare.')}</span>`; return; }
        gasit = d;
        out.innerHTML = fisa(d) + `<button type="button" class="btn btn-sm btn-success mt-2 pt-nou-adauga"><i class="bi bi-check2"></i> ${d.in_registru || d.sursa !== 'anaf' ? 'Selectează (există deja în registru)' : 'Adaugă în registru și selectează'}</button>`;
      } catch (e) { out.innerHTML = '<span class="text-danger">Serviciul ANAF nu a răspuns. Încercați din nou.</span>'; }
    }
    async function adauga() {
      const out = lista.querySelector('.pt-nou-rez');
      try {
        const r = await fetch('/parteneri/adauga/', {method: 'POST', headers: {'X-CSRFToken': csrf, Accept: 'application/json'}, body: new URLSearchParams({cui: gasit.cui})});
        const d = await r.json();
        if (!r.ok || d.eroare) { out.insertAdjacentHTML('beforeend', `<div class="text-danger mt-1">${esc(d.eroare || 'Nu s-a putut adăuga.')}</div>`); return; }
        alege(d, gasit.in_registru ? 'Partener selectat din registru.' : 'Partenerul a fost adăugat în registru.');
      } catch (e) { out.insertAdjacentHTML('beforeend', '<div class="text-danger mt-1">Eroare la salvare. Încercați din nou.</div>'); }
    }
    lista.addEventListener('click', e => {
      if (e.target.closest('.pt-nou-btn')) cautaAnaf();
      else if (e.target.closest('.pt-nou-adauga')) adauga();
    });
    lista.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.classList.contains('pt-nou-cui')) { e.preventDefault(); cautaAnaf(); } });
    document.addEventListener('mousedown', e => { if (deschis() && !box.contains(e.target)) inchide(); });
    box.addEventListener('focusout', e => { if (e.relatedTarget && !box.contains(e.relatedTarget)) inchide(); });
  }

  document.addEventListener('DOMContentLoaded', () => {
    PERECHI.forEach(([n, c]) => {
      const nume = document.querySelector(`form input[name="${n}"]`), cui = document.querySelector(`form input[name="${c}"]`);
      if (nume && cui && !cui.dataset.ptLegat) { cui.dataset.ptLegat = '1'; leaga(nume, cui); }
    });
  });
})();
