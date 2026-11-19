'use strict';
// 外观：跟随系统 / 浅色 / 深色。在样式表之后、页面渲染前设定，避免深色用户先看到一闪白底。
(function(){
 if(typeof document==='undefined'||!document.documentElement||typeof document.documentElement.setAttribute!=='function')return;
 const KEY='radar-theme',root=document.documentElement,media=typeof window!=='undefined'&&window.matchMedia?window.matchMedia('(prefers-color-scheme: dark)'):null;
 const names={system:'跟随系统',light:'浅色',dark:'深色'},order=['system','light','dark'];
 function mode(){try{const v=localStorage.getItem(KEY);return v==='light'||v==='dark'?v:'system';}catch(e){return 'system';}}
 function label(){
  const button=document.getElementById('theme-toggle');if(!button)return;
  const current=mode(),text=button.querySelector('.nav-text');
  if(text)text.textContent='外观 · '+names[current];
  button.setAttribute('aria-label','切换外观，当前：'+names[current]);
  button.title='点击切换：跟随系统 → 浅色 → 深色';
 }
 function apply(){
  const current=mode(),dark=current==='dark'||(current==='system'&&!!media&&media.matches);
  root.setAttribute('data-theme',dark?'dark':'light');root.setAttribute('data-theme-mode',current);
  const meta=document.querySelector('meta[name=theme-color]');
  if(meta&&document.body){const bg=getComputedStyle(document.body).backgroundColor;if(bg)meta.setAttribute('content',bg);}
  label();
 }
 apply();
 if(media){const listener=()=>{if(mode()==='system')apply();};media.addEventListener?media.addEventListener('change',listener):media.addListener(listener);}
 document.addEventListener('DOMContentLoaded',()=>{
  apply();
  document.getElementById('theme-toggle')?.addEventListener('click',()=>{
   const next=order[(order.indexOf(mode())+1)%order.length];
   try{localStorage.setItem(KEY,next);}catch(e){}
   apply();
  });
 });
})();
