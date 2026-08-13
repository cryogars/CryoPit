import fs from 'node:fs';
import vm from 'node:vm';

let pass=0,fail=0;
function check(cond,label){if(cond){console.log('PASS',label);pass++;}else{console.log('FAIL',label);fail++;}}

class Classes{
  constructor(){this.values=new Set();}
  add(v){this.values.add(v);}
  toggle(v,on){if(on)this.values.add(v);else this.values.delete(v);}
  contains(v){return this.values.has(v);}
}
class Control{
  constructor(value=''){this.value=value;this.disabled=false;this.type='text';this.attrs={};this.dataset={};}
  matches(sel){return sel==='[contenteditable="true"]'&&this.contenteditable===true;}
  closest(){return null;}
  getAttribute(k){return this.attrs[k]??null;}
  setAttribute(k,v){this.attrs[k]=String(v);}
}
const label={id:'',textContent:' Total depth (cm) * '};
const control=new Control('120');
const card={
  classList:new Classes(),
  querySelector(sel){return sel===':scope > .rl'?label:null;},
  querySelectorAll(sel){return sel.includes('input')?[control]:[];}
};
const listeners={};
const bodyClasses=new Classes();
const record={hidden:false,getBoundingClientRect:()=>({height:47})},post={hidden:true,getBoundingClientRect:()=>({height:78})};
const bodyStyle={props:{},setProperty(k,v){this.props[k]=v;}};
const document={
  body:{classList:bodyClasses,style:bodyStyle},
  querySelectorAll(sel){if(sel==='.ri')return [card];if(sel==='table')return [];return [];},
  addEventListener(type,fn){listeners[type]=fn;},
  getElementById(id){if(id==='record-mode')return record;if(id==='post-archive')return post;return null;}
};
class MutationObserver{constructor(fn){this.fn=fn;}observe(){}}
const context={document,MutationObserver,queueMicrotask,globalThis:null,console};context.globalThis=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(new URL('../cryopit/static/js/95_stage13_ui.js',import.meta.url),'utf8'),context);

check(label.id==='field-label-1','field labels receive stable IDs');
check(control.attrs['aria-labelledby']==='field-label-1','unlabelled field receives aria-labelledby');
check(card.classList.contains('has-value'),'populated field card receives visual state');
check(bodyClasses.contains('ui-ready'),'page records completion of UI enhancement');
check(bodyClasses.contains('record-banner-open'),'visible edit banner is represented below the fixed command bar');
check(!bodyClasses.contains('post-banner-open'),'hidden post-archive banner does not reserve space');
check(bodyStyle.props['--lifecycle-banner-h']==='47px','working shell reserves the measured banner height');

control.value='';
listeners.input({target:{closest:()=>card}});
check(!card.classList.contains('has-value'),'visual state clears when the field is emptied');
check(typeof context.enhanceCryoPitUI==='function','enhancer remains callable after dynamic form changes');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail?1:0);
