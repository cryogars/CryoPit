import fs from 'node:fs';
import vm from 'node:vm';

let pass=0, fail=0;
function check(cond,label){
  if(cond){pass++;console.log('PASS',label);}
  else{fail++;console.log('FAIL',label);}
}

class Classes{
  constructor(){this.values=new Set();}
  add(v){this.values.add(v);}
  remove(v){this.values.delete(v);}
  contains(v){return this.values.has(v);}
}
function node(tag='div'){
  let html='';
  const n={tagName:tag.toUpperCase(),className:'',textContent:'',style:{},children:[],classList:new Classes(),
    querySelector(sel){return sel==='img'?this.children.find(x=>x.tagName==='IMG')||null:null;},
    appendChild(x){this.children.push(x);return x;}};
  Object.defineProperty(n,'innerHTML',{get(){return html;},set(v){html=v;if(v==='')this.children=[];}});
  return n;
}

const wrap=node();
const p10=node();
const meta=node();
let payload={temperature:[],density:[],stratigraphy:[],lwc:[]};
let postCalls=0, validateCalls=0;
const context={
  console,
  document:{
    getElementById(id){return id==='profile-wrap'?wrap:id==='p10'?p10:id==='p10-meta'?meta:null;},
    createElement(tag){return node(tag);},
  },
  collect(){return payload;},
  validate(){validateCalls++;return{p:payload,e:[]};},
  post(){postCalls++;return Promise.resolve({ok:true,blob:async()=>new Blob(['png'])});},
  esc:s=>String(s),
  URL:{createObjectURL(){return'blob:test';},revokeObjectURL(){}},
  Blob,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(new URL('../cryopit/static/js/70_profile.js',import.meta.url),'utf8'),context);

// Completely blank: LWC deliberately does not count and no request is made.
payload={temperature:[],density:[],stratigraphy:[],lwc:[{top:10,bottom:0,a:3}]};
context.drawProfile();
check(postCalls===0,'LWC-only/blank profile does not call the server renderer');
check(validateCalls===0,'blank profile short-circuits before whole-form validation');
check(wrap.innerHTML.includes('No profile data yet'),'blank profile shows the instructional empty-state message');
check(meta.textContent==='waiting for data','blank profile metadata says it is waiting for data');

// Temperature-only: render and explain that stratigraphy is absent.
payload={temperature:[{height:100,temp:-5}],density:[],stratigraphy:[],lwc:[]};
context.drawProfile();
await new Promise(resolve=>setTimeout(resolve,0));
check(postCalls===1,'temperature-only profile calls the renderer');
check(wrap.children.some(x=>x.className==='pf-note'&&x.textContent.includes('without stratigraphy')),
      'temperature-only profile shows the no-stratigraphy note');
check(wrap.children.some(x=>x.tagName==='IMG'),'temperature-only profile still displays an image');
check(meta.textContent==='rendered · no stratigraphy','metadata records a render without stratigraphy');

// Complete stratigraphy: render normally, without the informational note.
payload={temperature:[],density:[],stratigraphy:[{top:100,bottom:0}],lwc:[]};
context.drawProfile();
await new Promise(resolve=>setTimeout(resolve,0));
check(postCalls===2,'stratigraphy profile calls the renderer');
check(!wrap.children.some(x=>x.className==='pf-note'),'complete stratigraphy renders without the no-stratigraphy note');
check(meta.textContent==='rendered','normal profile metadata remains rendered');

if(fail){console.error(`${fail} profile empty-state UI tests failed`);process.exit(1);}
console.log(`${pass} profile empty-state UI tests passed`);
