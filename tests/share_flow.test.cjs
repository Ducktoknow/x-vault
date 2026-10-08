// Lightweight browser-flow regression: node tests/share_flow.test.cjs
// No browser dependencies; mocks the small DOM/API surface used by X Vault.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
const inline = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(inline, 'inline script should exist');
new vm.Script(inline[1]); // syntax check

async function scenario({href, hasToken = true, isVideo = false}) {
  const elements = new Map(), storage = new Map(), saves = [], historyUpdates = [];
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      style: {}, value: '', textContent: '', hidden: true,
      append() {}, replaceChildren() {}, setAttribute() {},
      getAttribute() { return null; }
    });
    return elements.get(id);
  };
  if (hasToken) storage.set('xvault_token', 'token-012345678901234567890');
  const ctx = {
    URL, URLSearchParams, console,
    location: { href, search: new URL(href).search },
    history: {replaceState(_a,_b,url){ historyUpdates.push(url); }},
    document: {
      getElementById: element,
      createElement(tag) { return element('dynamic:' + tag + ':' + Math.random()); },
      body: { append(){} }
    },
    localStorage: {
      getItem(k){return storage.get(k) || null;},
      setItem(k,v){storage.set(k,v);}
    },
    navigator: {},
    fetch: async (url, opts) => {
      if (url === '/api/archives') return { ok: true, json: async () => ({items: []}) };
      if (url === '/api/save') {
        saves.push(JSON.parse(opts.body));
        return {ok: true, json: async()=> isVideo ?
          ({status:'needs_choice',preview:{author:'example',text_preview:'video',
            video_count:1,post_count:1}}) : ({status:'queued',new:true})};
      }
      throw new Error('Unexpected fetch: '+url);
    }
  };
  vm.runInNewContext(inline[1], ctx);
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  return {elements, storage, saves, historyUpdates, ctx, flush:()=>new Promise(resolve=>setImmediate(resolve))};
}

(async () => {
  const link='https://x.com/example/status/123456789';
  let t=await scenario({href:'https://vault.onrender.com/?url='+encodeURIComponent(link)});
  assert.equal(t.saves.length,1, 'shared URL should auto-submit once');
  assert.equal(t.saves[0].url,link);
  assert.equal(t.elements.get('message').textContent,'已自动收藏图文帖子。');
  assert.equal(t.historyUpdates.length,1,'shared link stripped from history');

  t=await scenario({href:'https://vault.onrender.com/?text='+encodeURIComponent('看看这个 '+link+' 好看'), isVideo:true});
  assert.equal(t.saves.length,1,'shared text should auto-submit once');
  assert.equal(t.saves[0].url,link);
  assert.equal(t.elements.get('videoChoice').hidden,false,'video must wait for destination choice');

  t=await scenario({href:'https://vault.onrender.com/?url='+encodeURIComponent(link),hasToken:false});
  assert.equal(t.saves.length,0,'must not submit without stored token');
  assert.equal(t.elements.get('tokenWrap').style.display,'block');
  t.ctx.document.getElementById('token').value='token-initial-012345678901234';
  t.elements.get('saveToken').onclick();
  await t.flush();
  await t.flush();
  assert.equal(t.saves.length,1,'saving token should resume shared submission once');

  t=await scenario({href:'https://vault.onrender.com/'});
  assert.equal(t.saves.length,0,'normal homepage should not auto-submit');

  t=await scenario({href:'https://vault.onrender.com/?text='+encodeURIComponent('not an x link')});
  assert.equal(t.saves.length,0,'irrelevant shared text must not submit');

  console.log('5 share-flow scenarios passed');
})().catch(err=>{console.error(err);process.exitCode=1;});