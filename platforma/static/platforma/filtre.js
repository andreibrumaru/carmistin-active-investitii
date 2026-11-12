// Filtre pe fiecare coloană pentru tabelele marcate cu clasa "tabel-filtrabil".
// Sub fiecare cap de tabel: un câmp text (conține…) și, pentru coloanele cu valori repetate, o listă cu bife
// din care se pot alege mai multe valori deodată. Căutarea nu ține cont de majuscule sau diacritice.
// Când un filtru e activ, deasupra tabelului apare numărul de rânduri afișate și butonul „Șterge filtrele”.
(() => {
  const norm = t => (t || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/\s+/g, ' ').trim();
  // valoarea „principală” a celulei: textul de dinaintea primului bloc (ex. partenerul, nu și CUI-ul de dedesubt)
  const principala = c => {
    if (c.dataset.filtru !== undefined) return c.dataset.filtru.trim();
    let t = '';
    for (const n of c.childNodes) {
      if (n.nodeType === 1 && /^(DIV|P|UL|BR|TABLE)$/.test(n.tagName)) { if (t.trim()) break; else continue; }
      if (n.nodeType === 1 && n.matches('input[type=hidden], button, form')) continue;
      t += n.textContent;
    }
    return (t.trim() ? t : c.textContent).replace(/\s+/g, ' ').trim();
  };
  const GOL = '(gol)';
  document.querySelectorAll('table.tabel-filtrabil').forEach(tabel => {
    const antet = tabel.tHead && tabel.tHead.rows[0];
    const corp = tabel.tBodies[0];
    if (!antet || !corp) return;
    const randuri = [...corp.rows].filter(r => r.cells.length === antet.cells.length);
    if (!randuri.length) return;
    const texte = randuri.map(r => [...r.cells].map(c => norm(c.textContent)));
    const valori = randuri.map(r => [...r.cells].map(c => principala(c) || GOL));
    const rand = document.createElement('tr');
    rand.className = 'filtre-coloane';
    const filtre = [];  // {i, inp, bife: Set, buton}
    [...antet.cells].forEach((th, i) => {
      const td = document.createElement('th');
      rand.appendChild(td);
      if (!th.textContent.trim()) return;
      const f = {i, bife: new Set()};
      const grup = document.createElement('div');
      grup.className = 'input-group input-group-sm flex-nowrap';
      const inp = f.inp = document.createElement('input');
      inp.type = 'search';
      inp.className = 'form-control form-control-sm';
      inp.placeholder = 'filtru…';
      inp.setAttribute('aria-label', `Filtru ${th.textContent.trim()}`);
      inp.addEventListener('input', aplica);
      inp.addEventListener('click', e => e.stopPropagation());
      grup.appendChild(inp);
      // listă cu bife: doar pentru coloane text cu valori care se repetă (2–80 valori distincte)
      const distincte = [...new Set(valori.map(v => v[i]))].sort(new Intl.Collator('ro', {numeric: true}).compare);
      if (!th.classList.contains('num') && distincte.length >= 2 && distincte.length <= 80 && distincte.length < randuri.length) {
        const dd = document.createElement('div');
        dd.className = 'dropdown';
        const buton = f.buton = document.createElement('button');
        buton.type = 'button';
        buton.className = 'btn btn-outline-secondary btn-sm filtru-multi-buton';
        buton.title = 'alegeți una sau mai multe valori';
        buton.setAttribute('data-bs-toggle', 'dropdown');
        buton.setAttribute('data-bs-auto-close', 'outside');
        buton.setAttribute('data-bs-popper-config', '{"strategy":"fixed"}');  // să nu fie tăiat de tabelul cu derulare
        buton.innerHTML = '<i class="bi bi-funnel"></i>';
        const meniu = document.createElement('div');
        meniu.className = 'dropdown-menu p-2 filtru-multi-meniu shadow-sm';
        meniu.innerHTML = `<div class="d-flex justify-content-between small mb-1"><a href="#" data-tot>toate</a><a href="#" data-nimic>niciuna</a></div>
          <input type="search" class="form-control form-control-sm mb-1" placeholder="caută în listă…">
          <div class="filtru-multi-lista"></div>`;
        const lista = meniu.querySelector('.filtru-multi-lista');
        distincte.forEach((v, k) => {
          const n = valori.filter(x => x[i] === v).length;
          const id = `fm-${Math.random().toString(36).slice(2)}-${k}`;
          lista.insertAdjacentHTML('beforeend', `<div class="form-check small text-nowrap" data-v="${norm(v)}"><input class="form-check-input" type="checkbox" id="${id}"><label class="form-check-label" for="${id}"></label></div>`);
          const el = lista.lastElementChild;
          el.querySelector('label').textContent = `${v} (${n})`;
          el.querySelector('input').addEventListener('change', e => { e.target.checked ? f.bife.add(v) : f.bife.delete(v); aplica(); });
        });
        meniu.querySelector('input[type=search]').addEventListener('input', e => {
          const q = norm(e.target.value);
          lista.querySelectorAll('.form-check').forEach(x => x.classList.toggle('d-none', !!q && !x.dataset.v.includes(q)));
        });
        const toate = bifat => e => {
          e.preventDefault();
          lista.querySelectorAll('.form-check:not(.d-none) input').forEach(c => { c.checked = bifat; c.dispatchEvent(new Event('change')); });
        };
        meniu.querySelector('[data-tot]').addEventListener('click', toate(true));
        meniu.querySelector('[data-nimic]').addEventListener('click', toate(false));
        meniu.addEventListener('click', e => e.stopPropagation());
        f.meniu = meniu;
        dd.append(buton, meniu);
        grup.appendChild(dd);
      }
      td.appendChild(grup);
      filtre.push(f);
    });
    antet.after(rand);
    // bara de deasupra tabelului: câte rânduri se văd + ștergerea tuturor filtrelor
    const bara = document.createElement('div');
    bara.className = 'filtre-bara d-none d-flex align-items-center gap-2 px-3 py-1 small border-bottom';
    bara.innerHTML = '<i class="bi bi-funnel-fill text-primary"></i><span class="contor"></span><span class="text-secondary activi"></span><button type="button" class="btn btn-sm btn-outline-danger py-0 ms-auto"><i class="bi bi-x-lg"></i> Șterge filtrele</button>';
    (tabel.closest('.table-responsive') || tabel).before(bara);
    bara.querySelector('button').addEventListener('click', () => {
      filtre.forEach(f => {
        f.inp.value = ''; f.bife.clear();
        f.meniu?.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = false);
      });
      aplica();
    });
    function aplica() {
      const active = filtre.filter(f => f.inp.value.trim() || f.bife.size);
      let vizibile = 0;
      randuri.forEach((r, k) => {
        const ok = active.every(f => {
          const q = norm(f.inp.value);
          return (!q || texte[k][f.i].includes(q)) && (!f.bife.size || f.bife.has(valori[k][f.i]));
        });
        r.style.display = ok ? '' : 'none';
        vizibile += ok;
      });
      filtre.forEach(f => {
        if (!f.buton) return;
        f.buton.className = `btn btn-sm filtru-multi-buton ${f.bife.size ? 'btn-primary' : 'btn-outline-secondary'}`;
        f.buton.innerHTML = f.bife.size ? `<i class="bi bi-funnel-fill"></i> ${f.bife.size}` : '<i class="bi bi-funnel"></i>';
      });
      bara.querySelector('.contor').textContent = `${vizibile} din ${randuri.length} rânduri afișate`;
      bara.querySelector('.activi').textContent = '· filtrat după: ' + active.map(f => antet.cells[f.i].textContent.trim()).join(', ');
      bara.classList.toggle('d-none', !active.length);
    }
  });
})();

// Sortare la clic pe capul de coloană (tabelele "tabel-filtrabil" / "tabel-sortabil").
// Text: alfabetic A→Z la primul clic; date și numere: cele mai noi / mai mari întâi. Al doilea clic inversează.
// O celulă poate da valoarea de sortare în data-sort (ex. data ISO); celulele goale rămân la final.
(() => {
  const ISO = /^\d{4}-\d{2}-\d{2}/, RO = /^(\d{2})\.(\d{2})\.(\d{4})/;
  const valoare = c => {
    const t = (c.dataset.sort ?? c.textContent).trim();
    if (!t || t === '—' || t === '-') return null;
    if (c.dataset.sort !== undefined && /^-?\d+(\.\d+)?$/.test(t)) return {tip: 'n', v: parseFloat(t)};  // număr brut din data-sort
    if (ISO.test(t)) return {tip: 'n', v: Date.parse(t.replace(' ', 'T'))};
    const d = t.match(RO); if (d) return {tip: 'n', v: Date.parse(`${d[3]}-${d[2]}-${d[1]}`)};
    const n = t.replace(/\s|RON|EUR|%/g, '');
    if (/^[-+]?[\d.]+(,\d+)?$/.test(n) && /\d/.test(n)) return {tip: 'n', v: parseFloat(n.replace(/\./g, '').replace(',', '.'))};
    return {tip: 't', v: t};
  };
  const cmpText = new Intl.Collator('ro', {sensitivity: 'base', numeric: true}).compare;
  document.querySelectorAll('table.tabel-filtrabil, table.tabel-sortabil').forEach(tabel => {
    const antet = tabel.tHead && tabel.tHead.rows[0], corp = tabel.tBodies[0];
    if (!antet || !corp) return;
    const randuri = [...corp.rows];
    if (randuri.length < 2 || randuri.some(r => r.cells.length !== antet.cells.length)) return;  // rânduri de detaliu: fără sortare
    let activ = null, desc = false;
    [...antet.cells].forEach((th, i) => {
      if (!th.textContent.trim() || th.dataset.faraSortare !== undefined) return;
      th.classList.add('sortabil');
      const sageata = document.createElement('i'); sageata.className = 'bi bi-arrow-down-up sageata-sort'; th.appendChild(sageata);
      th.addEventListener('click', () => {
        const vals = randuri.map(r => valoare(r.cells[i]));
        const numeric = vals.every(v => !v || v.tip === 'n');
        desc = activ === i ? !desc : numeric;  // primul clic: numere/date descrescător, text crescător
        activ = i;
        const ordine = randuri.map((r, k) => [r, vals[k]]).sort(([, a], [, b]) => {
          if (!a || !b) return !a && !b ? 0 : !a ? 1 : -1;
          const c = numeric ? a.v - b.v : cmpText(String(a.v), String(b.v));
          return desc ? -c : c;
        });
        ordine.forEach(([r]) => corp.appendChild(r));
        antet.querySelectorAll('.sageata-sort').forEach(s => s.className = 'bi bi-arrow-down-up sageata-sort');
        sageata.className = `bi ${desc ? 'bi-sort-down' : 'bi-sort-up'} sageata-sort activa`;
      });
    });
  });
})();
