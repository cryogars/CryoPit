import fs from 'node:fs';
import vm from 'node:vm';

// Substitute EVERY page placeholder, not a hand-kept list of the ones that
// happened to exist when this file was written. These harnesses load the raw
// static/js sources rather than the assembled page, so any placeholder the app
// introduces later arrives here as an undefined identifier and the whole file
// dies on load. Unknown tokens become null, which is a defined value the VM can
// run.
const KNOWN_PLACEHOLDERS = {
  __CSRF_TOKEN__: "stage12-test-token",
  __ENABLE_EDIT__: "true",
  __SHOW_EXAMPLE_PLACEHOLDERS__: "false",
};
const substitutePlaceholders = src =>
  src.replace(/__[A-Z0-9_]+__/g, m =>
    Object.prototype.hasOwnProperty.call(KNOWN_PLACEHOLDERS, m)
      ? KNOWN_PLACEHOLDERS[m] : "null");

const source = substitutePlaceholders(
  fs.readFileSync(new URL('../cryopit/static/js/00_core.js', import.meta.url), 'utf8'));
const calls = [];
const context = {
  console,
  fetch: async (path, options = {}) => {
    calls.push({path, options});
    return {ok: true};
  },
  Headers,
};
vm.createContext(context);
vm.runInContext(source, context, {filename: '00_core.js'});

async function testUnsafeRequestsCarryToken(){
  await context.apiFetch('/api/archive', {method: 'POST', headers: {'Content-Type': 'application/json'}});
  const call = calls.at(-1);
  if(call.options.headers['X-CryoPit-CSRF'] !== 'stage12-test-token'){
    throw new Error('unsafe request did not carry rendered CSRF token');
  }
}

async function testGetDoesNotCarryToken(){
  await context.apiFetch('/api/pits');
  const call = calls.at(-1);
  if(call.options.headers && call.options.headers['X-CryoPit-CSRF']){
    throw new Error('GET unexpectedly carried CSRF token');
  }
}

async function testHeadersObjectIsSupported(){
  const headers = new Headers({'Accept': 'application/json'});
  await context.apiFetch('/api/attach/site', {method: 'DELETE', headers});
  const call = calls.at(-1);
  if(call.options.headers.get('X-CryoPit-CSRF') !== 'stage12-test-token'){
    throw new Error('Headers instance did not receive CSRF token');
  }
}

const tests=[testUnsafeRequestsCarryToken,testGetDoesNotCarryToken,testHeadersObjectIsSupported];
let failures=0;
for(const test of tests){
  try{ await test(); console.log('PASS',test.name); }
  catch(error){ failures++; console.error('FAIL',test.name,error); }
}
if(failures) process.exit(1);
console.log(`${tests.length} CSRF UI tests passed`);
