// Lightweight execution of the real profile-table Enter navigation.
import fs from 'node:fs';
import vm from 'node:vm';

let pass=0,fail=0;
function check(cond,label){if(cond){pass++;console.log('PASS',label);}else{fail++;console.log('FAIL',label);}}

class Input{
  constructor(type='number'){
    this.tagName='INPUT';this.type=type;this.disabled=false;this.readOnly=false;
    this.focused=false;this._row=null;this._cell=null;this._body=null;
  }
  closest(sel){if(sel==='tbody')return this._body;if(sel==='tr')return this._row;if(sel==='td')return this._cell;return null;}
  focus(){this.focused=true;active=this;}
}
class Cell{
  constructor(index,input){this.cellIndex=index;this.input=input;input._cell=this;}
  querySelectorAll(sel){return sel==='input'?[this.input]:[];}
}
class Row{
  constructor(inputs){this.cells=inputs.map((input,i)=>new Cell(i,input));for(const input of inputs)input._row=this;}
}
class Body{
  constructor(id,rows){this.id=id;this.rows=rows;for(const row of rows)for(const cell of row.cells)cell.input._body=this;}
}
let active=null;
const listeners={};
const document={addEventListener(type,fn){listeners[type]=fn;}};
const context={document,console,globalThis:null};context.globalThis=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(new URL('../cryopit/static/js/20_tables.js',import.meta.url),'utf8'),context,{filename:'20_tables.js'});

context.initTableEnterNavigation();
context.initTableEnterNavigation();
check(typeof listeners.keydown==='function','Enter navigation installs a document key handler');

function key(target,opts={}){
  let prevented=false;
  const e={key:'Enter',target,shiftKey:false,ctrlKey:false,altKey:false,metaKey:false,isComposing:false,
           preventDefault(){prevented=true;},...opts};
  listeners.keydown(e);
  return prevented;
}

for(const id of ['tb','db','lb','sb']){
  const a1=new Input(),a2=new Input(),b1=new Input(),b2=new Input();
  const body=new Body(id,[new Row([a1,a2]),new Row([b1,b2])]);
  a2.focus();
  const prevented=key(a2);
  check(prevented,`${id}: Enter is consumed instead of submitting the form`);
  check(active===b2,`${id}: Enter moves to the same column in the next row`);
  check(body.rows.length===2,`${id}: Enter does not create a row`);
  b2.focus();
  const lastPrevented=key(b2);
  check(lastPrevented&&active===b2,`${id}: final-row Enter stays in the current cell`);
}

{
  const target=new Input();new Body('other',[new Row([target])]);
  check(!key(target),'inputs outside the four profile tables keep native Enter behavior');
}
{
  const target=new Input('checkbox');new Body('tb',[new Row([target])]);
  check(!key(target),'checkboxes keep native Enter behavior');
}
{
  const target=new Input();target.readOnly=true;new Body('db',[new Row([target])]);
  check(!key(target),'readonly calculated cells are not table-navigation targets');
}
{
  const a=new Input(),b=new Input();new Body('lb',[new Row([a]),new Row([b])]);
  a.focus();check(!key(a,{shiftKey:true})&&active===a,'modified Enter is not repurposed');
}
{
  const select={tagName:'SELECT',type:'select-one',disabled:false,readOnly:false,
    closest(){return null;}};
  check(!key(select),'selectors retain native Enter behavior');
}

if(fail){console.error(`${fail} table Enter navigation tests failed`);process.exit(1);}
console.log(`${pass} table Enter navigation tests passed`);
