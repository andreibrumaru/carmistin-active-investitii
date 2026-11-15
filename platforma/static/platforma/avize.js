// CU & avize: denumire editabilă direct în tabel, status din listă, valabilitate pe loc, „+” pentru adăugare (Enter salvează și deschide rândul următor).
(() => {
  const lista = document.getElementById('avLista');
  if (!lista) return;
  const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1];
  const posteaza = async (url, date) => {
    const r = await fetch(url, {method: 'POST', headers: {'X-CSRFToken': csrf, 'Accept': 'application/json'}, body: new URLSearchParams(date)});
    let j = {}; try { j = await r.json(); } catch (e) { j = {ok: false, mesaj: r.status === 403 ? 'Nu aveți drept de modificare.' : 'Eroare la salvare.'}; }
    if (!j.ok) alert(j.mesaj || 'Eroare la salvare.');
    return j;
  };
  const dinHtml = html => { const t = document.createElement('tbody'); t.innerHTML = html.trim(); return t.firstElementChild; };
  const numeroteaza = () => lista.querySelectorAll('.av-rand').forEach((r, i) => { r.querySelector('.av-nr').textContent = i + 1; });
  numeroteaza();

  // mutare cu mouse-ul (mânerul ⋮⋮ din prima coloană)
  let tras = null;
  lista.addEventListener('mousedown', e => { const mn = e.target.closest('.av-maner'); if (mn) mn.closest('.av-rand').draggable = true; });
  document.addEventListener('mouseup', () => { if (!tras) lista.querySelectorAll('.av-rand[draggable="true"]').forEach(r => r.draggable = false); });
  lista.addEventListener('dragstart', e => {
    tras = e.target.closest('.av-rand[draggable="true"]'); if (!tras) return;
    e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', tras.dataset.id);
    setTimeout(() => tras.classList.add('tras'), 0);
  });
  lista.addEventListener('dragover', e => {
    if (!tras) return;
    const tinta = e.target.closest('.av-rand'); if (!tinta || tinta === tras) return;
    e.preventDefault();
    const r = tinta.getBoundingClientRect();
    (e.clientY < r.top + r.height / 2) ? tinta.before(tras) : tinta.after(tras);
  });
  lista.addEventListener('drop', e => { if (tras) e.preventDefault(); });
  lista.addEventListener('dragend', async () => {
    if (!tras) return;
    tras.classList.remove('tras'); tras.draggable = false; tras = null; numeroteaza();
    const date = new URLSearchParams(); lista.querySelectorAll('.av-rand').forEach(r => date.append('aviz', r.dataset.id));
    const j = await posteaza(lista.dataset.ordine, date);
    if (!j.ok) location.reload();
  });
  // rând cu document atașat: denumirea e link (deschide documentul); creionul o face editabilă
  lista.addEventListener('click', e => {
    const b = e.target.closest('.av-redenumeste'); if (!b) return;
    const td = b.closest('td'), inp = td.querySelector('.av-nume');
    td.querySelector('.av-link').classList.add('d-none'); b.classList.add('d-none'); inp.classList.remove('d-none'); inp.focus(); inp.select();
  });
  // denumire: se salvează la Enter sau la ieșirea din câmp, dacă s-a schimbat
  lista.addEventListener('focusin', e => { if (e.target.classList.contains('av-nume')) e.target.dataset.vechi = e.target.value; });
  lista.addEventListener('keydown', e => {
    if (!e.target.classList.contains('av-nume')) return;
    if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
    if (e.key === 'Escape') { e.target.value = e.target.dataset.vechi; e.target.blur(); }
  });
  lista.addEventListener('focusout', async e => {
    const inp = e.target; if (!inp.classList.contains('av-nume')) return;
    const val = inp.value.trim();
    if (!val) { inp.value = inp.dataset.vechi; return; }
    const rand = inp.closest('.av-rand'), link = inp.closest('td').querySelector('.av-link');
    if (val === inp.dataset.vechi) { if (link) { inp.classList.add('d-none'); link.classList.remove('d-none'); rand.querySelector('.av-redenumeste')?.classList.remove('d-none'); } return; }
    const j = await posteaza(rand.dataset.url, {actiune: 'denumire', denumire: val});
    if (j.ok && inp.closest('td').querySelector('.av-link')) { rand.replaceWith(dinHtml(j.html)); numeroteaza(); return; }
    if (j.ok) { inp.dataset.vechi = val; inp.classList.add('salvat'); setTimeout(() => inp.classList.remove('salvat'), 900); }
    else inp.value = inp.dataset.vechi;
  });
  // status și valabilitate
  lista.addEventListener('click', async e => {
    const b = e.target.closest('.av-alege-status'); if (!b) return;
    const rand = b.closest('.av-rand'), j = await posteaza(rand.dataset.url, {actiune: 'status', status: b.dataset.status});
    if (j.ok) { rand.replaceWith(dinHtml(j.html)); numeroteaza(); }
  });
  lista.addEventListener('change', async e => {
    if (!e.target.classList.contains('av-valabil')) return;
    const rand = e.target.closest('.av-rand'), j = await posteaza(rand.dataset.url, {actiune: 'valabil', valabil_pana: e.target.value});
    if (j.ok) { rand.replaceWith(dinHtml(j.html)); numeroteaza(); }
  });
  // ștergere
  lista.addEventListener('click', async e => {
    const b = e.target.closest('.av-sterge'); if (!b) return;
    const rand = b.closest('.av-rand'), nume = rand.querySelector('.av-nume')?.value || '';
    const da = window.confirmaApp ? await window.confirmaApp(`Ștergeți „${nume}” din lista de avize?`) : confirm(`Ștergeți „${nume}”?`);
    if (!da) return;
    const j = await posteaza(rand.dataset.url, {actiune: 'sterge'});
    if (j.ok) { rand.remove(); numeroteaza(); }
  });
  // adăugare: „+” după un rând sau „+ Aviz / studiu” la final
  function campNou(dupa) {
    document.querySelectorAll('.av-nou').forEach(x => x.remove());
    const tr = dinHtml(`<tr class="av-nou"><td><i class="bi bi-plus-circle text-primary"></i></td><td colspan="6"><input class="form-control form-control-sm" placeholder="Denumirea avizului / studiului – Enter salvează, Esc renunță (ex. Aviz ISU, Studiu geotehnic, DTAC)"></td></tr>`);
    dupa ? dupa.after(tr) : lista.appendChild(tr);
    const inp = tr.querySelector('input'); inp.focus();
    inp.addEventListener('keydown', async e => {
      if (e.key === 'Escape') tr.remove();
      if (e.key !== 'Enter' || !inp.value.trim()) return;
      e.preventDefault(); inp.disabled = true;
      const prec = tr.previousElementSibling;
      const j = await posteaza(lista.dataset.adauga, {denumire: inp.value.trim(), dupa: prec?.dataset.id || ''});
      if (!j.ok) { inp.disabled = false; return; }
      const nou = dinHtml(j.html); tr.replaceWith(nou); numeroteaza(); campNou(nou);
    });
    inp.addEventListener('blur', () => setTimeout(() => { if (!inp.value.trim() && tr.isConnected && document.activeElement !== inp) tr.remove(); }, 150));
  }
  lista.addEventListener('click', e => { const b = e.target.closest('.av-plus'); if (b) campNou(b.closest('.av-rand')); });
  document.querySelector('.av-plus-final')?.addEventListener('click', () => campNou(lista.lastElementChild?.classList.contains('av-rand') ? lista.lastElementChild : null));
})();
