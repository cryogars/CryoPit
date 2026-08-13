import fs from 'node:fs';
let pass=0,fail=0;const check=(c,l)=>{if(c){pass++;console.log('PASS',l)}else{fail++;console.log('FAIL',l)}};
const html=fs.readFileSync(new URL('../cryopit/templates/sections/03_ground.html',import.meta.url),'utf8');
const block=html.split('Ground condition')[1].split('Ground roughness')[0];
check((block.match(/type="checkbox"/g)||[]).length===3,'ground condition exposes three checkboxes');
check(!block.includes('data-clearable-radio'),'multi-select ground condition does not use radio clearing');
check((html.match(/data-clearable-radio/g)||[]).length===5,'five mutually exclusive ground groups remain clearable radios');
const io=fs.readFileSync(new URL('../cryopit/static/js/50_form_io.js',import.meta.url),'utf8');
check(/ground:\{condition:gcs\('gc'\)/.test(io),'collect stores ground conditions as an array');
check(/setChecks\('gc',g\.condition\)/.test(io),'populate restores scalar or array ground conditions');
if(fail){console.error(`${fail} ground multi-select tests failed`);process.exit(1)}
console.log(`${pass} ground multi-select tests passed`);
