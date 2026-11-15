// Registrul / lista rapoartelor de șantier: click pe rând deschide PDF-ul raportului în vizualizator (sau fișa, unde nu e PDF);
// „Extinde” (tabloul de bord) arată toată lista.
(() => {
  document.querySelectorAll('.rapoarte-dashboard .rand-raport, .lista-rapoarte .rand-raport[data-href]').forEach(tr => tr.addEventListener('click', e => {
    if (e.target.closest('a, button')) return;
    const pdf = tr.querySelector('a[data-vizualizeaza]');
    if (pdf) pdf.click(); else location.href = tr.dataset.href;
  }));
  const b = document.getElementById('rdExtinde');
  if (b) b.addEventListener('click', () => {
    const extra = document.querySelectorAll('.rd-extra'), ascuns = extra[0]?.classList.contains('d-none');
    extra.forEach(tr => tr.classList.toggle('d-none', !ascuns));
    b.innerHTML = ascuns ? '<i class="bi bi-chevron-up"></i> Restrânge' : `<i class="bi bi-chevron-down"></i> Extinde lista (${extra.length + 10})`;
  });
})();
