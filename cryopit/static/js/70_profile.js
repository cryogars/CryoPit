// "No title" hides the title box entirely rather than leaving an input that
// does nothing — a disabled field still invites you to type in it. Unchecking
// brings the box back with whatever was in it.
function toggleFigTitle(){
  const off=document.getElementById('fig-notitle')?.checked;
  const row=document.getElementById('fig-title-row');
  if(row)row.style.display=off?'none':'';
  // Re-render so the change is visible immediately — but ONLY if a profile is
  // already drawn. Rendering on a blank §10 would fire a server round-trip the
  // user never asked for, and fail noisily on a pit with no layers yet.
  const wrap=document.getElementById('profile-wrap');
  if(wrap&&wrap.querySelector('img')&&typeof drawProfile==='function')drawProfile();
}

// §11 profile: rendered SERVER-SIDE (cryopit/plot.py — the reference
// figure with ICSSG symbols and the documented gap-fill styling). This just
// posts the current form state and shows the returned PNG.
function drawProfile(){
  const wrap=document.getElementById('profile-wrap');
  // Keep the existing figure on screen while the new one renders. It used to be
  // destroyed before the request even started, so every re-render blanked the
  // panel for the ~2 s the round trip takes — read as a "blink" when the only
  // thing that actually changed was one number. The figure is drawn by
  // matplotlib on the server (publication typography, hatching, ICSSG grain
  // symbols), so the delay is inherent; the blank was not.
  const prev=wrap.querySelector('img');
  if(prev){wrap.classList.add('is-rendering');}
  else{wrap.innerHTML='<p class="pf-msg">rendering…</p>';}
  const{p,e}=validate();
  if(e.length){wrap.classList.remove('is-rendering');
    wrap.innerHTML='<p class="pf-msg err">Render blocked: '+esc(e.join('; '))+'</p>';return;}
  post('/api/profile',p)
    .then(async r=>{
      if(!r.ok){const j=await r.json().catch(()=>({msg:'render failed'}));throw new Error(j.msg||'render failed');}
      return r.blob();
    })
    .then(b=>{
      const url=URL.createObjectURL(b);
      wrap.classList.remove('is-rendering');
      wrap.innerHTML='';
      const img=document.createElement('img');
      img.src=url;img.alt='Snow profile';img.style.maxWidth='100%';
      img.onload=()=>URL.revokeObjectURL(url);
      wrap.appendChild(img);
      const pip=document.getElementById('p10');
      if(pip)pip.classList.add('done');
      const pm=document.getElementById('p10-meta');
      if(pm)pm.textContent='rendered';
    })
    .catch(err=>{wrap.classList.remove('is-rendering');
      wrap.innerHTML='<p class="pf-msg err">'+esc(err.message)+'</p>';});
}

