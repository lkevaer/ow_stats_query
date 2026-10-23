'use strict';
(function (root) {
  const COOKIE = 'ow_rating_self';

  function parseTags(text) {
    const parts = String(text || '').normalize('NFKC').trim().split(/[\s,，、;；]+/).filter(Boolean);
    const seen = new Set();
    return parts.filter(tag => {
      if (!/^[^#\s,，、;；]{1,32}#[0-9]{3,12}$/.test(tag)) {
        throw new Error('请填写完整ID，例如 昵称#12345；多人用空格或换行分隔。');
      }
      const key = tag.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function read(cookie) {
    try {
      const entry = String(cookie).split(';').map(s => s.trim()).find(s => s.startsWith(COOKIE + '='));
      const saved = entry ? JSON.parse(decodeURIComponent(entry.slice(COOKIE.length + 1))) : null;
      const tags = parseTags(saved?.tag);
      if (tags.length === 1) return {tag: tags[0], autoJoin: saved.autoJoin === true};
    } catch (_) {}
    return {tag: '', autoJoin: false};
  }

  function cookie(binding, secure = false) {
    const tags = parseTags(binding.tag);
    if (tags.length !== 1) throw new Error('每个浏览器只能绑定一个完整战网ID。');
    return COOKIE + '=' + encodeURIComponent(JSON.stringify({tag: tags[0], autoJoin: binding.autoJoin === true})) +
      '; Path=/; Max-Age=31536000; SameSite=Lax' + (secure ? '; Secure' : '');
  }

  function roster(text, binding) {
    const tags = parseTags(text + (binding.tag && binding.autoJoin ? '\n' + binding.tag : ''));
    if (!tags.length) throw new Error('请输入账号，或绑定本人并打开自动加入开关。');
    if (tags.length > 10) throw new Error('合并本人并去重后最多10人，请减少名单；不会自动截掉账号。');
    return tags;
  }

  const api = {parseTags, read, cookie, roster, clearCookie: COOKIE + '=; Path=/; Max-Age=0; SameSite=Lax'};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RatingAccount = api;
})(globalThis);
