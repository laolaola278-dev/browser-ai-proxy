(function(){
  window.DuckBridge && (async function(){
    try {
      var st = await fetch('/duckchat/v1/status',
          {headers:{'x-vqd-accept':'1'}, credentials:'include', cache:'no-store'});
      var raw = st.headers.get('x-vqd-hash-1');
      if (!raw) { DuckBridge.fail('status 未下发 x-vqd-hash-1'); return; }
      var res = await eval(atob(raw));
      if (!res || !res.client_hashes) { DuckBridge.fail('挑战脚本未返回 client_hashes'); return; }
      var enc = new TextEncoder();
      var out = [];
      for (var i = 0; i < res.client_hashes.length; i++) {
        var d = await crypto.subtle.digest('SHA-256', enc.encode(res.client_hashes[i]));
        var bytes = new Uint8Array(d), s = '';
        for (var j = 0; j < bytes.length; j++) s += String.fromCharCode(bytes[j]);
        out.push(btoa(s));
      }
      res.client_hashes = out;
      res.meta = Object.assign({}, res.meta,
          {origin: location.origin, stack: 'no-stack', duration: '1'});
      DuckBridge.post(btoa(JSON.stringify(res)), navigator.userAgent);
    } catch (e) {
      DuckBridge.fail(String(e));
    }
  })();
  return 'started';
})()