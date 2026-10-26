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

  function roster(text, binding, selected = []) {
    const tags = parseTags(text + '\n' + selected.join('\n') + (binding.tag && binding.autoJoin ? '\n' + binding.tag : ''));
    if (!tags.length) throw new Error('请输入账号，或绑定本人并打开自动加入开关。');
    if (tags.length > 10) throw new Error('合并本人并去重后最多10人，请减少名单；不会自动截掉账号。');
    return tags;
  }

  const FAVORITES_KEY = 'ow_rating_favorites_v1';
  const MAX_FAVORITES = 50;
  function cleanFavorites(value) {
    const tags=[], seen=new Set();
    for(const raw of Array.isArray(value?.tags)?value.tags:[]){
      try{
        const parsed=parseTags(raw);
        if(parsed.length!==1||seen.has(parsed[0].toLowerCase()))continue;
        tags.push(parsed[0]);seen.add(parsed[0].toLowerCase());
      }catch(_){}
      if(tags.length===MAX_FAVORITES)break;
    }
    const selectedKeys=new Set((Array.isArray(value?.selected)?value.selected:[]).map(tag=>String(tag).normalize('NFKC').toLowerCase()));
    return {tags,selected:tags.filter(tag=>selectedKeys.has(tag.toLowerCase())).slice(0,10)};
  }
  function readFavorites(storage) {
    try{return cleanFavorites(JSON.parse(storage.getItem(FAVORITES_KEY)));}catch(_){return {tags:[],selected:[]};}
  }
  function saveFavorites(storage,value) {
    const state=cleanFavorites(value);
    storage.setItem(FAVORITES_KEY,JSON.stringify(state));
    return state;
  }
  function addFavorites(value,text) {
    const state=cleanFavorites(value),added=parseTags(text);
    if(!added.length)throw new Error('请先输入要保存的完整战网ID。');
    const tags=parseTags([...state.tags,...added].join('\n'));
    if(tags.length>MAX_FAVORITES)throw new Error('常用ID最多保存50个，请先删除不用的账号。');
    return {tags,selected:state.selected};
  }
  function toggleFavorite(value,tag) {
    const state=cleanFavorites(value),actual=state.tags.find(item=>item.toLowerCase()===String(tag).toLowerCase());
    if(!actual)return state;
    const selected=state.selected.includes(actual)?state.selected.filter(item=>item!==actual):[...state.selected,actual];
    if(selected.length>10)throw new Error('一次最多选择10个常用ID。');
    return {...state,selected};
  }
  function removeFavorite(value,tag) {
    const state=cleanFavorites(value),key=String(tag).toLowerCase();
    return {tags:state.tags.filter(item=>item.toLowerCase()!==key),selected:state.selected.filter(item=>item.toLowerCase()!==key)};
  }
  const api = {parseTags, read, cookie, roster, readFavorites, saveFavorites, addFavorites, toggleFavorite, removeFavorite,
    FAVORITES_KEY, MAX_FAVORITES, clearCookie: COOKIE + '=; Path=/; Max-Age=0; SameSite=Lax'};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RatingAccount = api;
})(globalThis);
