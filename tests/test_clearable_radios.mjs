// Lightweight execution of the real optional-radio clear-selection helper.
import fs from 'node:fs';
import vm from 'node:vm';

let pass=0,fail=0;
function check(cond,label){if(cond){pass++;console.log('PASS',label);}else{fail++;console.log('FAIL',label);}}

class Radio{
  constructor(name,value){this.type='radio';this.name=name;this.value=value;this.checked=false;this.listeners={};this.focused=false;}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  dispatchEvent(event){event.target=this;for(const fn of this.listeners[event.type]||[])fn(event);return true;}
  focus(opts){this.focused=true;this.focusOptions=opts;}
}
class Button{
  constructor(){this.attrs={};this.listeners={};this.hidden=false;this.type='';this.className='';this.textContent='';}
  setAttribute(k,v){this.attrs[k]=String(v);}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  click(){for(const fn of this.listeners.click||[])fn({target:this});}
}
class Card{
  constructor(label){this.label={textContent:label};this.button=null;}
  querySelector(sel){if(sel===':scope > .rl')return this.label;if(sel===':scope > .radio-clear')return this.button;return null;}
}
class Group{
  constructor(name,label){this.dataset={};this.radios=[new Radio(name,'A'),new Radio(name,'B')];this.parentElement=new Card(label);}
  querySelectorAll(sel){return sel==='input[type="radio"][name]'?this.radios:[];}
  querySelector(sel){if(sel==='input[type="radio"]:checked')return this.radios.find(r=>r.checked)||null;return null;}
  closest(sel){return sel==='.ri'?this.parentElement:null;}
  insertAdjacentElement(where,button){if(where==='afterend')this.parentElement.button=button;}
}
const group=new Group('sky','Sky condition');
const document={
  querySelectorAll(sel){return sel==='[data-clearable-radio]'?[group]:[];},
  createElement(tag){return tag==='button'?new Button():null;}
};
class Event{constructor(type,opts={}){this.type=type;this.bubbles=!!opts.bubbles;}}
const context={document,Event,console,globalThis:null};context.globalThis=context;
const src=fs.readFileSync(new URL('../cryopit/static/js/40_ui.js',import.meta.url),'utf8');
const start=src.indexOf('function refreshClearableRadioGroups()');
const end=src.indexOf('\nfunction tick(){',start);
vm.createContext(context);
vm.runInContext(src.slice(start,end),context,{filename:'40_ui-clearable-radios.js'});

context.initClearableRadios();
const button=group.parentElement.button;
check(!!button,'clear action is added to an explicitly marked optional radio group');
check(button.type==='button','clear action cannot submit or archive the form');
check(button.textContent==='Clear selection','clear action uses consistent visible wording');
check(button.attrs['aria-label']==='Clear Sky condition selection','clear action has a field-specific accessible name');
check(button.hidden,'clear action is hidden while the group is unanswered');

group.radios[1].checked=true;
group.radios[1].dispatchEvent(new Event('change',{bubbles:true}));
check(!button.hidden,'clear action appears after a radio answer is selected');
button.click();
check(group.radios.every(r=>!r.checked),'clear action returns the radio group to unanswered');
check(button.hidden,'clear action hides again after clearing');
check(group.radios[0].focused,'focus returns to the native radio group after the disappearing button is used');
check(group.radios[0].focusOptions?.preventScroll===true,'focus return requests no scrolling');

context.initClearableRadios();
check(group.parentElement.button===button,'initialization is idempotent and does not duplicate the action');

group.radios[0].checked=true;
context.refreshClearableRadioGroups();
check(!button.hidden,'programmatic restore can refresh clear-action visibility');
group.radios[0].checked=false;
context.refreshClearableRadioGroups();
check(button.hidden,'programmatic unanswered state hides the clear action');

if(fail){console.error(`${fail} optional-radio tests failed`);process.exit(1);}
console.log(`${pass} optional-radio tests passed`);
