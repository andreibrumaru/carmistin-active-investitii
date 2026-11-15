// Redimensionează în browser fotografiile alese, înainte de trimitere (latura mare 1920 px, JPEG 85%):
// o poză de telefon de câțiva MB pleacă la câteva sute de KB. Se aplică oricărui <input type="file" data-redimensioneaza>.
// Dacă browserul nu poate citi imaginea (ex. HEIC în Chrome), fișierul pleacă neschimbat și serverul îl tratează.
(() => {
  const LATURA = 1920, CALITATE = 0.85;
  const kb = n => n > 1048576 ? (n / 1048576).toFixed(1).replace('.', ',') + ' MB' : Math.round(n / 1024) + ' KB';
  async function micsoreaza(f) {
    if (!f.type.startsWith('image/') || f.type === 'image/gif') return f;
    try {
      const bmp = await createImageBitmap(f, {imageOrientation: 'from-image'});
      const k = Math.min(1, LATURA / Math.max(bmp.width, bmp.height));
      const c = document.createElement('canvas');
      c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
      const ctx = c.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(bmp, 0, 0, c.width, c.height);
      const blob = await new Promise(r => c.toBlob(r, 'image/jpeg', CALITATE));
      if (!blob || blob.size >= f.size) return f;
      return new File([blob], f.name.replace(/\.[^.]+$/, '') + '.jpg', {type: 'image/jpeg', lastModified: f.lastModified});
    } catch (e) { return f; }
  }
  document.addEventListener('change', async ev => {
    const inp = ev.target;
    if (!(inp instanceof HTMLInputElement) || inp.type !== 'file' || !inp.hasAttribute('data-redimensioneaza') || inp.dataset.procesat === '1') return;
    const fisiere = [...inp.files];
    if (!fisiere.length || typeof DataTransfer === 'undefined') return;
    const nota = inp.parentNode.querySelector('.nota-poze') || Object.assign(document.createElement('div'), {className: 'nota-poze form-text'});
    inp.after(nota);
    nota.textContent = 'Se pregătesc fotografiile…';
    const btn = inp.form?.querySelector('button:not([type=button])'); if (btn) btn.disabled = true;
    const noi = await Promise.all(fisiere.map(micsoreaza));
    const dt = new DataTransfer(); noi.forEach(f => dt.items.add(f));
    inp.dataset.procesat = '1'; inp.files = dt.files; delete inp.dataset.procesat;
    const inainte = fisiere.reduce((a, f) => a + f.size, 0), dupa = noi.reduce((a, f) => a + f.size, 0);
    nota.textContent = `${noi.length} fotografi${noi.length === 1 ? 'e' : 'i'} pregătit${noi.length === 1 ? 'ă' : 'e'}: ${kb(inainte)} → ${kb(dupa)}`;
    if (btn) btn.disabled = false;
  });
})();
