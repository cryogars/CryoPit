// Stage 13 presentation/accessibility enhancement --------------------------
// This module does not collect, validate, calculate, archive or upload data.
// It gives the existing controls dependable accessible names and adds a
// visual "has a value" state to field cards without changing form values.
(function(){
  function cleanLabel(text){
    return String(text||'').replace(/\s*\*\s*/g,' ').replace(/\s+/g,' ').trim();
  }
  function fieldHasValue(control){
    if(!control||control.disabled)return false;
    if(control.matches('[contenteditable="true"]')){
      const v=(control.textContent||'').trim();return !!v&&v!=='—';
    }
    if(control.type==='checkbox'||control.type==='radio')return !!control.checked;
    if(control.type==='file')return !!(control.files&&control.files.length);
    return String(control.value||'').trim()!=='';
  }
  function refreshFieldCard(card){
    if(!card)return;
    const controls=[...card.querySelectorAll('input,select,textarea,[contenteditable="true"]')];
    card.classList.toggle('has-value',controls.some(fieldHasValue));
  }
  function labelFieldCards(){
    document.querySelectorAll('.ri').forEach((card,index)=>{
      const label=card.querySelector(':scope > .rl');
      if(!label)return;
      if(!label.id)label.id='field-label-'+(index+1);
      const name=cleanLabel(label.textContent);
      card.querySelectorAll('input,select,textarea,[contenteditable="true"]').forEach(control=>{
        const wrapped=control.closest&&control.closest('label');
        if(!wrapped&&!control.getAttribute('aria-label')&&!control.getAttribute('aria-labelledby')){
          control.setAttribute('aria-labelledby',label.id);
        }
        if(name&&!control.title&&control.type!=='file')control.dataset.fieldName=name;
      });
      refreshFieldCard(card);
    });
  }
  function labelTableControls(){
    document.querySelectorAll('table').forEach(table=>{
      const headers=[...table.querySelectorAll('thead th')].map(h=>cleanLabel(h.textContent));
      table.querySelectorAll('tbody tr').forEach((row,rowIndex)=>{
        [...row.children].forEach((cell,colIndex)=>{
          const heading=headers[colIndex];if(!heading)return;
          cell.querySelectorAll('input,select,textarea,button').forEach(control=>{
            if(!control.getAttribute('aria-label')&&!control.getAttribute('aria-labelledby')){
              control.setAttribute('aria-label',`${heading}, row ${rowIndex+1}`);
            }
          });
        });
      });
    });
  }
  function refreshFromEvent(event){
    const card=event.target&&event.target.closest&&event.target.closest('.ri');
    if(card)refreshFieldCard(card);
  }
  function syncLifecycleBanners(){
    const record=document.getElementById('record-mode');
    const post=document.getElementById('post-archive');
    const recordOpen=!!record&&!record.hidden;
    const postOpen=!!post&&!post.hidden;
    document.body.classList.toggle('record-banner-open',recordOpen);
    document.body.classList.toggle('post-banner-open',postOpen);
    const active=postOpen?post:(recordOpen?record:null);
    const measure=()=>{
      const h=active&&active.getBoundingClientRect?Math.ceil(active.getBoundingClientRect().height):0;
      if(h&&document.body.style&&document.body.style.setProperty){
        document.body.style.setProperty('--lifecycle-banner-h',h+'px');
      }
    };
    if(typeof requestAnimationFrame==='function')requestAnimationFrame(measure);else measure();
  }
  function enhanceCryoPitUI(){
    labelFieldCards();labelTableControls();syncLifecycleBanners();
    document.body.classList.add('ui-ready');
    document.addEventListener('input',refreshFromEvent,true);
    document.addEventListener('change',refreshFromEvent,true);
    // Dynamic table rows are added after startup; refresh their accessible
    // names when the DOM changes, but batch work into one microtask.
    let queued=false;
    const observer=new MutationObserver(()=>{
      if(queued)return;queued=true;
      queueMicrotask(()=>{queued=false;labelFieldCards();labelTableControls();});
    });
    const main=document.getElementById('main');if(main)observer.observe(main,{childList:true,subtree:true});
    const bannerObserver=new MutationObserver(syncLifecycleBanners);
    ['record-mode','post-archive'].forEach(id=>{const el=document.getElementById(id);if(el)bannerObserver.observe(el,{attributes:true,attributeFilter:['hidden']});});
  }
  enhanceCryoPitUI();
  globalThis.enhanceCryoPitUI=enhanceCryoPitUI;
})();
