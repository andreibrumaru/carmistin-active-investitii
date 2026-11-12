// Deschide documentele (PDF, imagini) într-o fereastră din aplicație, fără descărcare.
// Se aplică automat oricărui link către /documente/<id>/ (exceptând descărcările explicite ?dl=1).
(() => {
  const modalEl = document.getElementById('vizualizator');
  if (!modalEl || !window.bootstrap) return;
  if (window.pdfjsLib) {
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
  }
  const modal = new bootstrap.Modal(modalEl);
  const corp = document.getElementById('vizCorp'), titlu = document.getElementById('vizTitlu'), info = document.getElementById('vizInfo');
  let pdf = null, scala = 1.3, randare = 0;

  async function deseneazaPdf() {
    const eu = ++randare;
    corp.innerHTML = '';
    for (let i = 1; i <= pdf.numPages; i++) {
      if (eu !== randare) return;
      const pag = await pdf.getPage(i);
      const vp = pag.getViewport({scale: scala * (window.devicePixelRatio || 1)});
      const c = document.createElement('canvas');
      c.width = vp.width; c.height = vp.height;
      c.style.width = (vp.width / (window.devicePixelRatio || 1)) + 'px';
      c.className = 'd-block mx-auto mb-2 shadow-sm bg-white';
      corp.appendChild(c);
      await pag.render({canvasContext: c.getContext('2d'), viewport: vp}).promise;
    }
  }

  async function deschide(url, nume) {
    pdf = null;
    titlu.textContent = nume || 'Document';
    info.textContent = 'Se încarcă…';
    corp.innerHTML = '<div class="text-center p-5"><div class="spinner-border"></div></div>';
    const sep = url.includes('?') ? '&' : '?';
    document.getElementById('vizDescarca').href = url + sep + 'dl=1';
    document.getElementById('vizTab').href = url;
    const vizibil = modalEl.classList.contains('show') ? Promise.resolve()
      : new Promise(ok => modalEl.addEventListener('shown.bs.modal', ok, {once: true}));
    modal.show();
    try {
      const r = await fetch(url, {credentials: 'same-origin'});
      if (!r.ok) throw new Error(r.status === 403 ? 'Nu aveți acces la acest document.' : 'Documentul nu a putut fi încărcat.');
      const tip = r.headers.get('Content-Type') || '';
      const date = await r.arrayBuffer();
      if (tip.includes('pdf') && window.pdfjsLib) {
        pdf = await pdfjsLib.getDocument({data: date}).promise;
        await vizibil;  // dimensiunile ferestrei sunt corecte abia după deschidere
        const p1 = await pdf.getPage(1);  // potrivire pe lățimea ferestrei
        scala = Math.max(0.5, Math.min(2.5, (corp.clientWidth - 40) / p1.getViewport({scale: 1}).width));
        info.textContent = `${pdf.numPages} pagin${pdf.numPages === 1 ? 'ă' : 'i'}`;
        await deseneazaPdf();
      } else if (tip.startsWith('image/')) {
        const img = new Image();
        img.src = URL.createObjectURL(new Blob([date], {type: tip}));
        img.className = 'd-block mx-auto bg-white shadow-sm';
        img.style.maxWidth = '100%';
        corp.innerHTML = ''; corp.appendChild(img);
        info.textContent = 'imagine';
      } else if (tip.startsWith('text/')) {
        corp.innerHTML = '';
        const pre = document.createElement('pre');
        pre.className = 'bg-white p-3';
        pre.textContent = new TextDecoder().decode(date);
        corp.appendChild(pre);
        info.textContent = '';
      } else {
        info.textContent = '';
        corp.innerHTML = '<div class="text-center p-5 bg-white">Acest tip de fișier nu poate fi afișat în aplicație.<br>Folosiți butonul „Descarcă”.</div>';
      }
    } catch (e) {
      info.textContent = '';
      corp.innerHTML = `<div class="alert alert-danger m-3">${e.message}</div>`;
    }
  }

  document.getElementById('vizPlus').addEventListener('click', () => { if (pdf) { scala = Math.min(scala + 0.25, 3); deseneazaPdf(); } });
  document.getElementById('vizMinus').addEventListener('click', () => { if (pdf) { scala = Math.max(scala - 0.25, 0.5); deseneazaPdf(); } });
  modalEl.addEventListener('hidden.bs.modal', () => { randare++; corp.innerHTML = ''; pdf = null; });

  document.addEventListener('click', e => {
    const a = e.target.closest('a[href]');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || !/^\/documente\/\d+\/$/.test(url.pathname) || url.searchParams.get('dl') === '1') return;
    if (a.closest('#vizualizator')) return;
    e.preventDefault();
    const nume = a.getAttribute('title') || a.textContent.trim() || 'Document';
    deschide(url.pathname, nume.length > 3 ? nume : 'Document');
  });
  window.deschideDocument = deschide;
})();
