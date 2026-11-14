// Confirmări și motive cerute în fereastra aplicației (nu în ferestrele native confirm/prompt ale browserului,
// care în unele browsere – de exemplu panoul de browser din Claude – sunt blocate și anulează acțiunea pe tăcute).
//
// Utilizare în șabloane:
//   <form data-confirm="Ștergeți?">                         – cere confirmare înainte de trimitere
//   <button data-confirm="Ștergeți?">                       – confirmare doar pentru butonul respectiv
//   <form data-prompt="Motivul respingerii:" data-prompt-camp="motiv" data-prompt-obligatoriu="1">
//                                                           – cere un text și îl pune în câmpul ascuns „motiv”
//   <form data-confirm="…" data-revert="1">                 – la renunțare, bifa din formular revine
//   window.confirmaApp("text") → Promise<boolean>           – pentru scripturi
(() => {
  document.body.insertAdjacentHTML('beforeend', `
<div class="modal fade" id="dlgConfirmare" tabindex="-1" aria-hidden="true"><div class="modal-dialog modal-dialog-centered"><div class="modal-content">
  <div class="modal-body pt-4">
    <div class="d-flex gap-3"><i class="bi bi-question-circle fs-3 text-primary"></i><div class="flex-grow-1">
      <p class="mb-2 fw-semibold" id="dlgText"></p>
      <textarea id="dlgCamp" class="form-control d-none" rows="2"></textarea>
      <div id="dlgEroare" class="text-danger small mt-1 d-none">Completați câmpul.</div></div></div>
  </div>
  <div class="modal-footer border-0 pt-0"><button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Renunță</button>
    <button type="button" class="btn btn-primary" id="dlgOk">Confirmă</button></div>
</div></div></div>`);
  const el = document.getElementById('dlgConfirmare'), text = document.getElementById('dlgText'), camp = document.getElementById('dlgCamp'),
        eroare = document.getElementById('dlgEroare'), ok = document.getElementById('dlgOk');
  const fereastra = new bootstrap.Modal(el);
  let rezolva = null, cuCamp = false, obligatoriu = false;

  function cere(mesaj, opt = {}) {
    cuCamp = !!opt.camp; obligatoriu = !!opt.obligatoriu;
    text.textContent = mesaj;
    camp.classList.toggle('d-none', !cuCamp); camp.value = opt.valoare || '';
    eroare.classList.add('d-none');
    ok.className = 'btn ' + (/șter|definitiv|respin|arhiv/i.test(mesaj) ? 'btn-danger' : 'btn-primary');
    return new Promise(res => { rezolva = res; fereastra.show(); });
  }
  el.addEventListener('shown.bs.modal', () => (cuCamp ? camp : ok).focus());
  ok.addEventListener('click', () => {
    if (cuCamp && obligatoriu && !camp.value.trim()) { eroare.classList.remove('d-none'); camp.focus(); return; }
    const r = rezolva; rezolva = null; fereastra.hide(); r && r(cuCamp ? camp.value : true);
  });
  camp.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ok.click(); } });
  el.addEventListener('hidden.bs.modal', () => { if (rezolva) { const r = rezolva; rezolva = null; r(null); } });

  window.confirmaApp = mesaj => cere(mesaj).then(v => v !== null);

  document.addEventListener('submit', e => {
    const f = e.target, buton = e.submitter;
    const mesaj = (buton && buton.dataset.confirm) || f.dataset.confirm, intrebare = f.dataset.prompt;
    if (!mesaj && !intrebare) return;
    if (f._confirmat) { f._confirmat = false; return; }
    e.preventDefault(); e.stopImmediatePropagation();
    cere(intrebare || mesaj, {camp: !!intrebare, obligatoriu: f.dataset.promptObligatoriu === '1'}).then(v => {
      if (v === null) {
        if (f.dataset.revert) f.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = !c.checked);
        return;
      }
      if (intrebare) { const t = f.querySelector(`[name="${f.dataset.promptCamp}"]`); if (t) t.value = v; }
      f._confirmat = true;
      if (buton) f.requestSubmit(buton); else f.requestSubmit();
    });
  }, true);
})();
