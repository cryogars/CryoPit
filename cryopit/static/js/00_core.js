/* Same-origin API: the page and the JSON routes come from one Flask process,
   so paths are relative — no host, no port, no CORS. */
const API = '';
const CSRF_TOKEN = '__CSRF_TOKEN__';

/* Same-origin request helper. Every state-changing call carries the stateless
   token rendered into this authenticated page; GET/HEAD stay cache-friendly. */
function apiFetch(path,options={}){
  const opts={...options};
  const method=String(opts.method||'GET').toUpperCase();
  if(!['GET','HEAD','OPTIONS'].includes(method)){
    if(typeof Headers!=='undefined'&&opts.headers instanceof Headers){
      opts.headers.set('X-CryoPit-CSRF',CSRF_TOKEN);
    }else{
      opts.headers=Object.assign({},opts.headers||{},{'X-CryoPit-CSRF':CSRF_TOKEN});
    }
  }
  return fetch(API+path,opts);
}
/* Whether the saved-pits/load workflow is enabled (injected from config).
   Draft autosave/restore is INDEPENDENT of this — it uses localStorage, not the
   DB, so it stays active even when edit is off. */
const ENABLE_EDIT = __ENABLE_EDIT__;
/* Presentation-only switch for sample-looking field placeholders. Real values,
   configured defaults, instructional prompts, derived values, and autofill/copy
   results never pass through this helper. */
const SHOW_EXAMPLE_PLACEHOLDERS = __SHOW_EXAMPLE_PLACEHOLDERS__;
function examplePlaceholder(value){
  return SHOW_EXAMPLE_PLACEHOLDERS?String(value):'';
}
const G=['PP','RG','FC','SH','MM','DF','DH','MF','IF',
  'PPsd','PPgp','PPrm','RGwp','RGxf','RGlr',
  'FCsf','FCxr','FCso',
  'DHcp','DHpr','DHla','DHxr','SHxr',
  'MFcl','MFsl','MFcr','IFil','IFsc','IFrc','IFbi'];
const H=['F','4F','1F','P','K','I'];
const W=['D','M','W','V','S'];
// Canonical instrument checklist — derived from the field sheet. This is a
// CLOSED list of 14 (no write-in): unlisted instruments go in the Misc field of
// the Additional Comments section, mirroring the paper sheet.
// `n` = canonical name — the DB seed contains these 14 PLUS the three §8 SSA
// devices (IceCube/IRIS/IRIS2), which are selected per-pit, not checked off.
// Saves resolve names with get-or-create, so the seed is a fast path, not a
// requirement.
// `sn:1` = takes a serial number (devices + rams), absent = Y/N only
// (survey methods & documentation render with no SN column at all).
const INST=[
  {g:'Instruments'},
  {n:'Digital LWC',sn:1},{n:'Lyte Probe',sn:1},{n:'SMP',sn:1},{n:'SSA / NIR Box',sn:1},
  {n:'Standard ram',sn:1},{n:'Powder Ram',sn:1},{n:'Force Ram',sn:1},
  {n:'Slush Ram',sn:1},{n:'Snow Scope',sn:1},{n:'Force Snow Scope',sn:1},
  {n:'Other',sn:1,w:1},   // write-in: name typed by the crew, as on the sheet
  {g:'Surveys & documentation'},
  {n:'HS Transects'},{n:'Snow Scope Transects'},
  {n:'Stratigraphy pictures'},{n:'Pit pictures'},
];

/* num(): the one number parser. Returns null for blank/garbage and PRESERVES
   legitimate zeros. Replaces both broken patterns:
     parseFloat(x)||0    -> a blank became a real 0 (fabricated measurement)
     parseFloat(x)||null -> a typed 0 became null (lost measurement)        */
function num(v){
  if(v===undefined||v===null)return null;
  const n=parseFloat(v);
  return Number.isFinite(n)?n:null;
}

/* esc(): the one HTML escaper. Any string that reaches an innerHTML template
   goes through it — filenames off the user's disk especially, since those are
   arbitrary text the app never chose. */
function esc(s){
  return String(s===null||s===undefined?'':s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

let _loaded_site_id=null; /* immutable identity of the loaded/archived pit */
let _loaded_pit_id=null;  /* human identifier at the last successful load/archive */
let _restoring=false;   /* true while populate() runs; suppresses draft churn   */


// Official ICSSG main-class colors (Fierz et al. 2009); subtypes inherit
// their main class via 2-letter prefix. Used by the live rail; the server-
// rendered figure (cryopit/plot.py) carries the identical table.
const ICSSG_MAIN={PP:'#00FF00',MM:'#FFD700',DF:'#228B22',RG:'#FFB6C1',FC:'#ADD8E6',
  DH:'#0000FF',SH:'#FA00FF',MF:'#FF0000',IF:'#00FFFF'};
function grainColor(code){
  if(code==='MFcr')return '#ffffff';   // crust: white (striped in the figure)
  return ICSSG_MAIN[code]||ICSSG_MAIN[(code||'').slice(0,2)]||'grey';
}
