/* Local appearance only. Recorded samples, metadata and exports remain untouched. */
(() => {
  'use strict';
  const root=document.documentElement,key='perfzy-report-appearance-v1';
  const sun='<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>';
  const moon='<path d="M20 14a8 8 0 0 1-10-10 8 8 0 1 0 10 10z"/>';
  const icon=shape=>'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+shape+'</svg>';
  function current(){return root.dataset.theme==='dark'?'dark':'light'}
  function refreshButtons(){
    const dark=current()==='dark',title=dark?'切换为浅色主题':'切换为深色主题';
    document.querySelectorAll('[data-report-theme-toggle]').forEach(button=>{
      button.innerHTML=icon(dark?sun:moon)+'<span>'+(dark?'浅色':'深色')+'</span>';
      button.title=title;button.setAttribute('aria-label',title);button.setAttribute('aria-pressed',String(dark));
    });
  }
  function set(mode,persist=true){
    if(mode!=='light'&&mode!=='dark')return;
    root.dataset.theme=mode;root.style.colorScheme=mode;
    if(persist)try{localStorage.setItem(key,mode)}catch(error){}
    refreshButtons();document.dispatchEvent(new CustomEvent('perfzy:theme-change',{detail:{mode}}));
  }
  let initial=root.dataset.theme==='dark'?'dark':'light';
  try{const stored=localStorage.getItem(key);if(stored==='dark'||stored==='light')initial=stored}catch(error){}
  root.dataset.theme=initial;root.style.colorScheme=initial;
  window.PerfZYReportTheme={current,set,refreshButtons};
  document.addEventListener('click',event=>{
    if(event.target.closest('[data-report-theme-toggle]'))set(current()==='dark'?'light':'dark');
  });
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',refreshButtons,{once:true});else refreshButtons();
})();
