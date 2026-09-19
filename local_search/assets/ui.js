'use strict';
(() => {
  const app = document.getElementById('app');
  const byId = id => document.getElementById(id);
  const norm = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const cards = Array.from(document.querySelectorAll('article.job'));
  const pageSize = Number(app.dataset.pageSize) || 50;
  const labels = {pending:'Pendiente',saved:'Guardada',applied:'Candidatura enviada',interview:'Entrevista',discarded:'Descartada'};
  const fields = ['search','sort','source','category','novelty','status','validity','date','location','minimum','eligibility'];
  let page = 1, filtered = [], state = {}, token = '', connected = false;
  let toastTimer;
  const searchText = new Map(cards.map(c => [c, norm(c.textContent)]));
  const categories = [...new Set(cards.map(c => c.dataset.category))].sort();
  for (const category of categories) {
    const option = document.createElement('option');
    option.value = option.textContent = category;
    byId('category').append(option);
  }
  if (app.dataset.legacy === 'true') { byId('date').value='all'; byId('location').value='all'; }
  function toast(message) {
    byId('toast').textContent = message;
    byId('toast').classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => byId('toast').classList.remove('visible'), 4200);
  }
  function collapse(card, yes) {
    card.classList.toggle('collapsed', yes);
    card.querySelector('.job-body').hidden = yes;
    const button = card.querySelector('.collapse-button');
    button.textContent = yes ? 'Mostrar detalles' : 'Contraer';
    button.setAttribute('aria-expanded', String(!yes));
  }
  function applyState(card, record, forceCollapse=true) {
    const status = record?.status || 'pending';
    for (const name of Object.keys(labels)) card.classList.toggle('state-'+name, name===status);
    card.dataset.status = status;
    card.querySelector('.state-label').textContent = labels[status] || labels.pending;
    for (const b of card.querySelectorAll('.state-button')) b.setAttribute('aria-pressed', String(b.dataset.status===status));
    const notes = card.querySelector('.notes');
    if (notes.dataset.dirty !== 'true') notes.value = record?.notes || '';
    card.querySelector('.has-notes').textContent = record?.notes ? '· Con notas' : '';
    if (forceCollapse) collapse(card, status !== 'pending');
  }
  function filter(resetPage=true) {
    if (resetPage) page = 1;
    const query = norm(byId('search').value);
    const reasons = {};
    filtered = cards.filter(card => {
      const d = card.dataset;
      const reject = [];
      if (query && !searchText.get(card).includes(query) && !norm(state[d.id]?.notes).includes(query)) reject.push('búsqueda');
      if (Number(d.score) < Number(byId('minimum').value)) reject.push('prioridad');
      if (byId('source').value !== 'all' && !d.source.includes(byId('source').value)) reject.push('fuente');
      if (byId('category').value !== 'all' && d.category !== byId('category').value) reject.push('familia');
      if (byId('novelty').value === 'new' && d.new !== 'true' || byId('novelty').value === 'seen' && d.new === 'true') reject.push('novedad');
      if (byId('status').value !== 'all' && d.status !== byId('status').value) reject.push('estado');
      const v=byId('validity').value;
      if (v === 'not_closed' && d.validity === 'closed' || !['not_closed','all'].includes(v) && d.validity !== v) reject.push('vigencia');
      const date=byId('date').value;
      if (date === 'recent' && d.date === 'old' || !['recent','all'].includes(date) && d.date !== date) reject.push('fecha');
      const loc=byId('location').value;
      if (loc === 'target' && d.location === 'outside' || !['target','all'].includes(loc) && d.location !== loc) reject.push('ubicación');
      const eligibility=byId('eligibility').value;
      if (eligibility !== 'all' && d.eligibility !== eligibility) reject.push('requisitos');
      reject.forEach(r => reasons[r] = (reasons[r] || 0)+1);
      return !reject.length;
    });
    const sort=byId('sort').value;
    filtered.sort((a,b) => {
      if (sort==='affinity') return Number(b.dataset.affinity)-Number(a.dataset.affinity) || Number(b.dataset.score)-Number(a.dataset.score);
      if (sort==='new') return Number(b.dataset.new==='true')-Number(a.dataset.new==='true') || Number(b.dataset.score)-Number(a.dataset.score);
      return Number(b.dataset.score)-Number(a.dataset.score) || Number(b.dataset.affinity)-Number(a.dataset.affinity);
    });
    const totalPages=Math.max(1, Math.ceil(filtered.length/pageSize));
    page=Math.max(1,Math.min(page,totalPages));
    // Detach the previous page. All cards remain indexed, with their unsaved notes.
    byId('jobs').replaceChildren();
    const visible=filtered.slice((page-1)*pageSize,page*pageSize);
    // Move only the visible cards; all other records remain searchable in memory.
    for (const card of visible) { byId('jobs').append(card); card.hidden=false; }
    byId('count').textContent = filtered.length+' de '+cards.length+' ofertas pasan los filtros · '+visible.length+' en esta página';
    const message='Página '+page+' de '+totalPages;
    byId('page-label').textContent=message; byId('page-label-bottom').textContent=message;
    ['prev','prev-bottom'].forEach(id => byId(id).disabled=page===1);
    ['next','next-bottom'].forEach(id => byId(id).disabled=page===totalPages);
    byId('empty').hidden=filtered.length>0;
    byId('hidden-reasons').textContent=Object.keys(reasons).length ? 'Ocultaciones por filtro (pueden solaparse): '+Object.entries(reasons).map(([r,n])=>r+' '+n).join(' · ') : 'No hay ofertas ocultas por filtros. La paginación no elimina resultados.';
  }
  async function save(card, status, notes) {
    if (!connected) { toast('Abre el informe con job_search.py open para guardar.'); return; }
    const id=card.dataset.id;
    card.querySelectorAll('.state-button,.save-notes').forEach(b=>b.disabled=true);
    try {
      const response=await fetch('/api/state', {method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify({job_id:id,status,notes,revision:state[id]?.revision || 0})});
      const data=await response.json();
      if (!response.ok) throw new Error(data.error || 'No se pudo guardar');
      state[id]=data;
      card.querySelector('.notes').dataset.dirty='false';
      cards.filter(c=>c.dataset.id===id).forEach(c=>applyState(c,data));
      filter(false);
      toast('Guardado en el historial local.');
    } catch(error) { toast(error.message); }
    finally { card.querySelectorAll('.state-button,.save-notes').forEach(b=>b.disabled=!connected); }
  }
  for (const card of cards) {
    applyState(card,null);
    card.querySelector('.collapse-button').addEventListener('click',()=>collapse(card,!card.classList.contains('collapsed')));
    card.querySelector('.notes').addEventListener('input',e=>e.target.dataset.dirty='true');
    for (const b of card.querySelectorAll('.state-button')) b.addEventListener('click',()=>save(card,b.dataset.status,card.querySelector('.notes').value));
    card.querySelector('.save-notes').addEventListener('click',()=>save(card,card.dataset.status,card.querySelector('.notes').value));
  }
  fields.forEach(id=>byId(id).addEventListener(id==='search'?'input':'change',()=>filter()));
  for (const id of ['prev','prev-bottom']) byId(id).addEventListener('click',()=>{page--;filter(false);byId('count').scrollIntoView({block:'start'});});
  for (const id of ['next','next-bottom']) byId(id).addEventListener('click',()=>{page++;filter(false);byId('count').scrollIntoView({block:'start'});});
  byId('show-all').addEventListener('click',()=>{
    byId('search').value='';byId('minimum').value='0';
    ['source','category','novelty','status','validity','date','location','eligibility'].forEach(id=>byId(id).value='all');filter();
  });
  byId('reset').addEventListener('click',()=>{
    byId('show-all').click();byId('sort').value='priority';byId('validity').value='not_closed';
    if(app.dataset.legacy!=='true'){byId('date').value='recent';byId('location').value='target';}filter();
  });
  byId('export-state').addEventListener('click',async()=>{
    try {
      const response=await fetch('/api/export-states');if(!response.ok)throw new Error('No se pudo exportar');
      const data=await response.json();const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
      const a=document.createElement('a');a.href=url;a.download='candidaturas-'+new Date().toISOString().slice(0,10)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch(error){toast(error.message);}
  });
  async function connect(force=false) {
    if (!['http:','https:'].includes(location.protocol)) {
      byId('storage-message').textContent='Modo lectura de archivo. Para guardar estados y notas compartidos entre fechas, utiliza: python job_search.py open. No se guardará nada solo en este navegador.';
      return;
    }
    try {
      const response=await fetch('/api/states',{cache:'no-store'});if(!response.ok)throw new Error('Almacén no disponible');
      const data=await response.json();state=data.states;token=data.token;connected=true;
      for (const card of cards) {
        if(card.querySelector('.notes').dataset.dirty!=='true')applyState(card,state[card.dataset.id],force);
        card.querySelectorAll('.state-button,.save-notes,.notes').forEach(x=>x.disabled=false);
      }
      byId('export-state').disabled=false;
      byId('storage-message').textContent='Estados y notas conectados al archivo local. «Candidatura enviada» solo registra tu decisión: no envía solicitudes.';
      byId('storage-message').classList.add('connected');filter(false);
    } catch(error) {
      connected=false;
      cards.forEach(c=>c.querySelectorAll('.state-button,.save-notes,.notes').forEach(x=>x.disabled=true));
      byId('export-state').disabled=true;
      byId('storage-message').textContent='No se puede guardar: '+error.message+'. Vuelve a abrir con job_search.py open. Las ofertas siguen disponibles para lectura.';
      byId('storage-message').classList.remove('connected');
    }
  }
  filter();connect(true);
  window.addEventListener('focus',()=>connect(false));
  window.addEventListener('beforeunload',event=>{
    if(cards.some(c=>c.querySelector('.notes').dataset.dirty==='true')){event.preventDefault();event.returnValue='';}
  });
})();
