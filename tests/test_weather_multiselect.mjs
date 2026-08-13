// Lightweight execution of the real weather multi-select helpers and listener.
import fs from 'node:fs';
import vm from 'node:vm';

let pass=0,fail=0;
function check(cond,label){if(cond){pass++;console.log('PASS',label);}else{fail++;console.log('FAIL',label);}}

class ClassList{
  constructor(){this.values=new Set();}
  toggle(name,on){if(on)this.values.add(name);else this.values.delete(name);}
  contains(name){return this.values.has(name);}
}
class Pill{constructor(){this.classList=new ClassList();}}
class Input{
  constructor(group,name,value){this.group=group;this.name=name;this.value=value;this.type='checkbox';this.checked=false;this.listeners={};this.pill=new Pill();}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  dispatchEvent(event){event.target=this;for(const fn of this.listeners[event.type]||[])fn(event);}
  closest(sel){if(sel==='[data-weather-multi]')return this.group;if(sel==='.tog')return this.pill;return null;}
}
class Group{
  constructor(name,values,exclusive=''){this.dataset={exclusiveValue:exclusive};this.inputs=values.map(v=>new Input(this,name,v));}
  querySelectorAll(sel){return sel==='input[type="checkbox"]'?this.inputs:[];}
}
const precip=new Group('pt',['None','Rain','Snow'],'None');
const sky=new Group('sky',['Clear','Overcast']);
const all=[...precip.inputs,...sky.inputs];
const document={
  querySelectorAll(sel){
    if(sel==='.toggles input')return all;
    const checked=sel.match(/^input\[name="([^"]+)"\]:checked$/);
    if(checked)return all.filter(i=>i.name===checked[1]&&i.checked);
    const named=sel.match(/^input\[name="([^"]+)"\]$/);
    if(named)return all.filter(i=>i.name===named[1]);
    return [];
  }
};
class Event{constructor(type,opts={}){this.type=type;this.bubbles=!!opts.bubbles;}}
let ticks=0;
const context={document,Event,console,tick(){ticks++;},globalThis:null};context.globalThis=context;
vm.createContext(context);

const ui=fs.readFileSync(new URL('../cryopit/static/js/40_ui.js',import.meta.url),'utf8');
const gcsStart=ui.indexOf('function gcs(name)');
const gcsEnd=ui.indexOf('\n\n',gcsStart);
vm.runInContext(ui.slice(gcsStart,gcsEnd),context,{filename:'40_ui-gcs.js'});
const rail=fs.readFileSync(new URL('../cryopit/static/js/75_rail.js',import.meta.url),'utf8');
const listenerStart=rail.indexOf("document.querySelectorAll('.toggles input')");
const listenerEnd=rail.indexOf('\n\n// Live redraw',listenerStart);
vm.runInContext(rail.slice(listenerStart,listenerEnd),context,{filename:'75_rail-weather.js'});

precip.inputs[1].checked=true;precip.inputs[1].dispatchEvent(new Event('change',{bubbles:true}));
precip.inputs[2].checked=true;precip.inputs[2].dispatchEvent(new Event('change',{bubbles:true}));
check(precip.inputs[1].checked&&precip.inputs[2].checked,'specific weather observations coexist');
check(JSON.stringify(context.gcs('pt'))===JSON.stringify(['Rain','Snow']),'checked weather values are collected as an array');
check(precip.inputs[1].pill.classList.contains('on')&&precip.inputs[2].pill.classList.contains('on'),'each selected weather pill is lit');

precip.inputs[0].checked=true;precip.inputs[0].dispatchEvent(new Event('change',{bubbles:true}));
check(precip.inputs[0].checked&&precip.inputs.slice(1).every(i=>!i.checked),'None clears specific precipitation observations');
precip.inputs[2].checked=true;precip.inputs[2].dispatchEvent(new Event('change',{bubbles:true}));
check(!precip.inputs[0].checked&&precip.inputs[2].checked,'specific precipitation clears None');

sky.inputs[0].checked=true;sky.inputs[0].dispatchEvent(new Event('change',{bubbles:true}));
sky.inputs[1].checked=true;sky.inputs[1].dispatchEvent(new Event('change',{bubbles:true}));
check(sky.inputs.every(i=>i.checked),'nonexclusive weather groups allow all observed conditions');
check(ticks===6,'every weather change continues through the normal tick pipeline');

// Execute the real restore helpers against the same fake DOM.
const io=fs.readFileSync(new URL('../cryopit/static/js/50_form_io.js',import.meta.url),'utf8');
const restoreStart=io.indexOf('function weatherValues(val)');
const restoreEnd=io.indexOf('\nfunction refreshTogs()',restoreStart);
vm.runInContext(io.slice(restoreStart,restoreEnd),context,{filename:'50_form_io-weather.js'});
context.setChecks('sky',['Clear','Overcast']);
check(sky.inputs.every(i=>i.checked),'array-valued weather restores every selection');
context.setChecks('sky','Clear');
check(sky.inputs[0].checked&&!sky.inputs[1].checked,'legacy scalar weather restores one selection');
context.setChecks('sky','Clear; Overcast');
check(sky.inputs.every(i=>i.checked),'semicolon-normalized weather text also restores');

check(/weather:\{precip_rate:gcs\('pr'\),precip_type:gcs\('pt'\),sky:gcs\('sky'\),wind:gcs\('wind'\)\}/.test(io),'collect() uses array collection for all four weather groups');

if(fail){console.error(`${fail} weather multi-select tests failed`);process.exit(1);}
console.log(`${pass} weather multi-select tests passed`);
