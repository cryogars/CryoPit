// Lightweight execution of the real Saved Pits finder without jsdom.
import fs from 'node:fs';
import vm from 'node:vm';

let pass=0, fail=0;
function check(cond,label){if(cond){pass++;console.log('PASS',label);}else{fail++;console.log('FAIL',label);}}

class El {
  constructor(tag='div'){
    this.tagName=tag.toUpperCase();this.children=[];this.value='';this.textContent='';
    this.className='';this.hidden=false;this.disabled=false;this.attrs={};this.listeners={};
    this.type='';this.title='';this._innerHTML='';
  }
  appendChild(child){this.children.push(child);return child;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(name,value){this.attrs[name]=String(value);}
  getAttribute(name){return this.attrs[name];}
  set innerHTML(value){this._innerHTML=value;this.children=[];}
  get innerHTML(){return this._innerHTML;}
  get options(){return this.children.filter(c=>c.tagName==='OPTION');}
}
function textNode(text){return {tagName:'#TEXT',textContent:String(text),children:[]};}
function allText(node){return [node.textContent||'',...(node.children||[]).map(allText)].join(' ');}

const ids=[
  'saved-pits-list','saved-pits-more','saved-pits-count','recovery-pits',
  'saved-pits-filters','saved-pits-search','saved-pits-campaign',
  'saved-pits-date-from','saved-pits-date-to','saved-pits-sort',
  'pitid','tb','sb','record-mode','record-mode-title','record-mode-detail',
  'archive-btn','post-archive','tb-st'
];
const els=Object.fromEntries(ids.map(id=>[id,new El(id.includes('sort')||id.includes('campaign')?'select':'div')]));
els['saved-pits-sort'].value='date';
els['saved-pits-search'].value='Upper Ridge';
els['saved-pits-campaign'].value='WY2026';
els['saved-pits-date-from'].value='2026-01-01';
els['saved-pits-date-to'].value='2026-03-01';
els.pitid.textContent='—';els.tb.children=[];els.sb.children=[];

const calls=[];
const pages={
  0:{pits:[
    {site_id:'s1',pit_id:'ALPHA',site:'Upper Ridge',location:'Grand Mesa',campaign:'WY2026',date:'2026-01-10',attachment_count:2,pending_photos:1,missing_attachments:1},
    {site_id:'s2',pit_id:'BRAVO',site:'Lower Ridge',location:'Grand Mesa',campaign:'WY2026',date:'2026-02-10',attachment_count:0,pending_photos:0,missing_attachments:0}
  ],total:3,offset:0,has_more:true},
  2:{pits:[
    {site_id:'s3',pit_id:'CHARLIE',site:'Basin',location:'Mores Creek',campaign:'WY2027',date:'2027-01-10',attachment_count:4,pending_photos:0,missing_attachments:0}
  ],total:3,offset:2,has_more:false}
};
const context={
  console,setTimeout,clearTimeout,AbortController,URLSearchParams,Promise,
  API:'',ENABLE_EDIT:true,_loaded_site_id:null,_loaded_pit_id:null,_restoring:false,
  document:{
    getElementById:id=>els[id]||null,
    createElement:tag=>new El(tag),
    createTextNode:textNode,
    body:new El('body'),
  },
  fetch:async url=>{
    calls.push(url);
    const q=new URL(url,'https://example.test').searchParams;
    const offset=Number(q.get('offset')||0);
    return {ok:true,status:200,json:async()=>({
      ...pages[offset],
      pending:[{site_id:'recover',pit_id:'RECOVER',updated_at:'2026-03-02 08:00:00'}],
      campaigns:[{name:'WY2026',count:2},{name:'WY2027',count:1}],
    })};
  },
  setst(){},gv:()=>'',collect:()=>({}),populate(){},confirm:()=>true,
  localStorage:{getItem(){return null;},setItem(){},removeItem(){}},
  location:{reload(){}},refreshAttachUI(){},_pendingTotal:()=>0,clearDraft(){},scheduleDraft(){},
  shortPath:x=>x,flushPendingAttachments:async()=>({done:0,failed:0,rejected:[],duplicates:[]}),
  downloadZip(){},URL:{createObjectURL(){return'x';},revokeObjectURL(){}},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(new URL('../cryopit/static/js/60_api.js',import.meta.url),'utf8'),context,{filename:'60_api.js'});

const defaultQuery=context.savedPitsQuery(0);
check(!defaultQuery.includes('sort='),'observation-date default does not need a sort parameter');
els['saved-pits-sort'].value='pit_id';
const query=context.savedPitsQuery(20);
check(query.includes('offset=20'),'finder query carries pagination offset');
check(query.includes('q=Upper+Ridge'),'finder query carries text search');
check(query.includes('campaign=WY2026'),'finder query carries campaign filter');
check(query.includes('date_from=2026-01-01')&&query.includes('date_to=2026-03-01'),'finder query carries date range');
check(query.includes('sort=pit_id'),'finder query carries sort order');
check(!query.includes('owner='),'browser cannot choose an owner scope');

const card=context._renderSavedPit(pages[0].pits[0]);
const cardText=allText(card);
check(card.tagName==='BUTTON','saved pit result is a keyboard-operable button');
check(card.children[0]?.className==='pit-id'&&card.children[0]?.textContent==='ALPHA','Pit ID is the primary row content');
check(cardText.includes('Upper Ridge · Grand Mesa')&&cardText.includes('2026-01-10'),'result shows compact site/location and observation-date context');
check(!cardText.includes('archived'),'ordinary saved-pit rows do not repeat the implicit archived state');
check(!cardText.includes('2 attachments'),'ordinary rows do not spend sidebar space on non-actionable attachment counts');
check(cardText.includes('1 photo pending')&&cardText.includes('1 missing'),'result shows pending and missing attachment states');
check(card.children.some(c=>c.className==='pit-alerts'),'actionable states are grouped in the compact alert row');
check(typeof card.listeners.click==='function','result loads by an explicit click handler');

const quietCard=context._renderSavedPit(pages[0].pits[1]);
check(quietCard.children.length===2,'a normal saved pit renders as exactly two quiet lines');
check(!allText(quietCard).includes('WY2026'),'campaign is not repeated when site/location context is available');

const fallbackCard=context._renderSavedPit({site_id:'s4',pit_id:'DELTA',campaign:'WY2028',date:'2028-01-12',pending_photos:0,missing_attachments:0});
check(allText(fallbackCard).includes('WY2028 · 2028-01-12'),'campaign remains available as context when site/location is missing');

// Clear filters before exercising page accumulation.
els['saved-pits-search'].value='';els['saved-pits-campaign'].value='';
els['saved-pits-date-from'].value='';els['saved-pits-date-to'].value='';els['saved-pits-sort'].value='date';
await context.loadSavedPits();
check(calls[0].includes('offset=0'),'initial finder request starts at offset zero');
check(!calls[0].includes('sort='),'initial finder request uses observation-date default');
check(els['saved-pits-list'].children.length===2,'initial page renders only the configured page');
check(els['saved-pits-count'].textContent==='2 of 3','finder reports shown and total counts');
check(els['saved-pits-more'].hidden===false,'load-more control appears when more results exist');
check(els['recovery-pits'].hidden===false&&allText(els['recovery-pits']).includes('RECOVER'),'recovery-required pits render in a separate section');

await context.loadSavedPits({append:true});
check(calls[1].includes('offset=2'),'load more requests the next offset');
check(els['saved-pits-list'].children.length===3,'load more appends without replacing prior results');
check(els['saved-pits-count'].textContent==='3 of 3','count advances after pagination');
check(els['saved-pits-more'].hidden===true,'load-more control hides at the end');
check(els['saved-pits-campaign'].options.some(o=>o.value==='WY2027'),'campaign facets populate the filter');

if(fail){console.error(`${fail} Saved Pits UI tests failed`);process.exit(1);}
console.log(`${pass} Saved Pits UI tests passed`);
