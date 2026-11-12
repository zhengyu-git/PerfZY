(() => {
  'use strict';
  const find=id=>document.getElementById(id);
  const make=(tag,cls,html='')=>{const node=document.createElement(tag);node.className=cls;node.innerHTML=html;return node};
  const paths={overview:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',fps:'M3 15h3l3-8 5 13 3-10h4',frame:'M4 18V9m5 9V5m5 13v-7m5 7V3',cpu:'M7 7h10v10H7zM9 1v4m6-4v4M9 19v4m6-4v4M1 9h4m-4 6h4m14-6h4m-4 6h4',memory:'M3 7h18v10H3zM7 10v4m4-4v4m4-4v4M6 17v3m4-3v3m4-3v3m4-3v3',gpu:'M3 6h18v12H3zM7 18v3M8 10h8v4H8zM1 9h2m-2 5h2',io:'M4 5h16v14H4zM7 9h10M7 13h6m3 2h1',scenes:'M4 4h16v16H4zM4 10h16M10 10v10',notes:'M5 3h14v18H5zM8 7h8M8 11h8m-8 4h5',scope:'M4 8V4h4m8 0h4v4M4 16v4h4m8 0h4v-4M8 12h8',menu:'M4 6h16M4 12h16M4 18h16'};
  Object.assign(paths,{up:'m6 15 6-6 6 6',down:'m6 9 6 6 6-6',close:'m6 6 12 12M18 6 6 18',info:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 7v.1M11 11h1v6h1',sidebar:'M7 4h10a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3ZM10 4v16'});
  paths.timeline='M3 12h18M6 9v6m6-8v10m6-8v6';
  paths.point='M12 21s5-5 5-10a5 5 0 1 0-10 0c0 5 5 10 5 10zM12 11h.01';
  const icon=key=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[key]||paths.frame}"/></svg>`;
  const labels={overview:'报告概览',fps:'应用帧率',jank_events:'卡顿事件',frame_latency:'帧耗时分位值',frame_quality:'慢帧占比',cpu:'应用 CPU',cpu_core_usage:'各核 CPU 使用率',cpu_core_clock:'CPU 核心频率',cpu_health:'在线 CPU 核心',memory:'应用内存',gpu_busy:'GPU 忙碌耗时（估算）',thermal:'设备温度',network:'设备网络（共享）',process:'应用线程',stutter:'慢帧统计',system_cpu:'系统 CPU',system_memory:'整机物理内存',system_memory_commit:'整机提交内存',system_gpu:'整机 GPU',system_gpu_memory:'整机显存',memory_detail:'内存分类 / 交换',battery_level:'电池电量',battery_voltage:'电池电压',memory_resident:'驻留 / 独占内存',memory_growth:'内存增长',android_graphics_memory:'图形内存（回退）',cpu_frequency_ratio:'CPU 降频',io:'磁盘读写',scenes:'场景对比'};
  const groupLabel=group=>{
    if(!group)return '';
    if(group.key==='frame')return /sampled/i.test(group.title)?'帧耗时（采样）':'帧耗时';
    if(group.key==='gpu')return /device|shared/i.test(group.title)?'设备 GPU（共享）':'应用 GPU';
    if(group.key==='display_drops')return /budget|estimate/i.test(group.title)?'掉帧（估算）':'显示丢帧';
    return labels[group.key]||group.title||group.key;
  };
  document.body.classList.add('report-workbench');

  if(!find('studioSidebar')){
    const sidebar=make('aside','studio-sidebar');sidebar.id='studioSidebar';sidebar.setAttribute('aria-label','报告目录');
    const brand=make('div','studio-brand','<div><b>PerfZY</b><small>采集报告</small></div>');
    const nav=make('nav','studio-nav','<div class="studio-nav-label">报告内容</div>');nav.append(find('metricTabs'));
    const extra=make('div','studio-nav-extra');extra.id='studioExtraNav';
    sidebar.append(brand,extra,nav,make('div','studio-side-foot','<span class="studio-dot"></span><b>单文件 · 本地离线</b><br>性能采集与记录'));
    document.body.prepend(sidebar);

    const top=document.querySelector('.topbar');
    const navToggle=make('button','studio-nav-toggle',icon('sidebar'));navToggle.id='studioNavToggle';navToggle.title='收起目录';navToggle.setAttribute('aria-label','收起目录');
    top.prepend(navToggle,make('div','studio-caption','<span class="studio-crumb">报告</span><span class="path-divider">/</span><span>采集报告</span>'));
    const actions=document.querySelector('.top-actions'),more=make('details','studio-more','<summary class="top-pill" aria-label="更多报告操作">更多 ···</summary><div class="studio-more-menu"></div>');more.id='studioMore';
    const menu=more.querySelector('.studio-more-menu');
    for(const node of [...actions.querySelectorAll('[data-rename-report],#saveEditedButton,#csvButton,#printButton')])menu.append(node);
    const extraRename=document.querySelector('.case-title>[data-rename-report]');if(extraRename)extraRename.remove();
    const themeToggle=make('button','top-pill');themeToggle.type='button';themeToggle.setAttribute('data-report-theme-toggle','');
    actions.append(themeToggle,more);window.PerfZYReportTheme?.refreshButtons();
    const exportButton=find('generateTestReportButton');if(exportButton)exportButton.textContent='导出报告';
    const shots=find('topShotButton');if(shots){shots.classList.remove('hidden');shots.textContent='截图回放';if(!R.screenshots.length){shots.disabled=true;shots.title='这份源报告未包含截图'}}
    const scope=document.querySelector('.tabs');const bar=make('div','studio-scope-inner',icon('scope')+'<span class="studio-scope-label">统计范围</span>');
    bar.append(find('summaryScopeText'),find('rangeCurrent'),find('statisticsCurrent'),find('clearStatistics'),find('overviewReset'));scope.replaceChildren(bar);
    find('overviewReset').textContent='恢复全程';

    const sceneSection=make('section','paper chart-section','<div class="section-head"><span class="section-accent"></span><span class="section-title">场景对比</span><span class="section-sub">勾选场景同步筛选曲线与统计</span></div>');sceneSection.id='section-scenes';sceneSection.append(find('sceneEditor'));document.querySelector('main.page').prepend(sceneSection);
  }

  const overview=document.querySelector('#section-overview .overview-content');
  const secondary=overview.querySelector('.studio-secondary-controls')||make('div','studio-secondary-controls');
  const timeline=secondary.querySelector('.studio-timeline')||make('div','studio-timeline');
  const extrema=secondary.querySelector('.studio-extremes')||make('details','studio-extremes');
  const timelineHeading=make('div','studio-review-heading',`${icon('timeline')}<b>显示时间轴</b><span>拖动两端调整图表显示范围</span>`);
  timelineHeading.title='调整显示范围时会退出场景筛选并清除统计框选';
  timeline.replaceChildren(timelineHeading,find('globalRange'));
  const extremaHeading=make('summary','studio-review-heading',`${icon('point')}<b>关键时间点</b><span>整次采集</span>${icon('down')}`);
  extrema.replaceChildren(extremaHeading,find('extremePoints'),find('extremeNotice'));
  secondary.replaceChildren(timeline,extrema);
  overview.prepend(find('overviewKpis'));
  overview.append(secondary);
  document.querySelector('.chart-view-toolbar b').textContent='指标趋势';

  const sourceScopePreview=updateReportScopePreview;
  updateReportScopePreview=function(){sourceScopePreview();if(statStart!==null&&statEnd!==null)find('reportScopePreview').textContent+='。注意：框选仅用于页面统计，导出仍使用上述场景或显示区间。'};
  const menuSub=document.querySelector('.report-menu-sub');if(menuSub)menuSub.textContent='导出所选场景；未选场景时导出显示时间轴范围。曲线框选仅影响页面统计，不改变导出范围。';
  const reportHealth=find('reportHealth');
  const healthFacts=make('div','studio-health-facts');
  for(const number of reportHealth.querySelectorAll('.health-number'))healthFacts.append(number.parentElement);
  const quality=R.quality||{};
  const qualityRows=Object.values(quality.metrics||{});
  const missingCount=(R.missing_metrics||[]).length;
  const hasQuality=Boolean(quality.samples&&qualityRows.length);
  const needsReview=hasQuality&&qualityRows.some(row=>
    (row.requested>0&&Number.isFinite(row.valid_percent)&&row.valid_percent<90)||row.suspicious_long>0);
  let qualityState=hasQuality?'已记录':'未记录';
  if(needsReview)qualityState='待复核';
  if(missingCount)qualityState=`${missingCount} 项未采到`;

  const captureDetails=make('div','studio-capture-quality',qualityTableForExport());
  captureDetails.id='captureQualityDetails';
  captureDetails.hidden=true;
  const healthMissing=reportHealth.querySelector('.health-missing');
  const requests=R.metric_requests||{};
  const requestNotes=[
    ['未进入采样请求',requests.not_requested],
    ['曾中途停采',requests.stopped],
    ['请求情况未记录',requests.unverified],
  ].filter(([,keys])=>Array.isArray(keys)&&keys.length);
  for(const [label,keys] of requestNotes.reverse()){
    const note=make('div','health-missing');
    const title=make('strong','');
    title.textContent=label+' · '+keys.length+' 项';
    note.append(title,document.createTextNode('　'+keys.map(key=>requests.titles?.[key]||R.metric_provenance?.[key]?.title||R.metrics[key]?.title||key).join('、')));
    captureDetails.prepend(note);
  }
  if(healthMissing)captureDetails.prepend(healthMissing);
  find('captureQualityDetails')?.remove();

  const captureToggle=make('button','action studio-quality-toggle',
    `${icon('info')}<span>数据完整度</span><span class="studio-quality-state">${qualityState}</span>${icon('down')}`);
  captureToggle.type='button';
  captureToggle.classList.toggle('needs-review',needsReview||missingCount>0);
  captureToggle.setAttribute('aria-controls',captureDetails.id);
  captureToggle.setAttribute('aria-expanded','false');
  captureToggle.addEventListener('click',()=>{
    captureDetails.hidden=!captureDetails.hidden;
    captureToggle.setAttribute('aria-expanded',String(!captureDetails.hidden));
  });
  reportHealth.append(healthFacts,captureToggle,captureDetails);
  const sceneHint=find('sceneEditorHint');
  if(sceneHint&&!sceneHint.closest('.studio-scene-help')){
    const help=make('details','studio-scene-help',`<summary>${icon('info')}<span>场景选择与统计说明</span></summary>`);
    sceneHint.before(help);help.append(sceneHint);
  }
  // Change labels only; keep the existing scene populations and statistics.
  const sceneMetricLabels={fps:'平均 FPS',fps_1_low:'1% Low FPS',fps_0_1_low:'0.1% Low FPS',frame_time:'平均帧耗时',frame_time_p95:'P95 帧耗时',jank_rate:'慢帧占比',dropped_frame_rate:'掉帧占比',app_cpu:'应用 CPU',total_cpu:'系统 CPU',app_gpu_usage:'应用 GPU',private_memory:'私有内存',virtual_memory:'虚拟内存',available_memory:'可用内存',thread_count:'线程数',handle_count:'句柄数',app_rss:'应用 RSS',app_uss:'应用 USS',app_threads:'应用线程',app_gpu_memory:'图形内存（回退）',swap_memory:'交换 PSS'};
  const sourceBuildSceneTable=buildSegmentTable;
  buildSegmentTable=function(){
    sourceBuildSceneTable();
    const cells=find('segmentHead').querySelectorAll('th');
    segmentMetricKeys.forEach((key,index)=>{
      const cell=cells[index+1],meta=R.metrics[key];if(!cell||!meta)return;
      const label=sceneMetricLabels[key]||(key==='memory'?(/PSS/i.test(meta.title)?'应用 PSS':'应用内存'):meta.title);
      const heading=make('span','studio-metric-heading');heading.textContent=label;cell.title=meta.title+(meta.unit?' · '+meta.unit:'');cell.replaceChildren(heading);
    });
  };
  buildSegmentTable();
  const sceneHead=find('section-scenes').querySelector('.section-head'),sceneToolbar=find('sceneEditor').querySelector('.segment-toolbar');
  sceneToolbar.classList.add('studio-scene-toolbar');sceneHead.append(sceneToolbar);
  // Keep the actual scene table near the top as well as its pinned shortcut.
  document.querySelector('main.page').prepend(find('section-scenes'));
  const sessionCard=document.querySelector('.case-hero');sessionCard.classList.add('studio-session-card');sessionCard.append(find('reportHealth'));
  const facts=[...sessionCard.querySelectorAll(':scope>.info-cell')];facts.forEach((cell,index)=>cell.classList.toggle('studio-last-fact',index===facts.length-1));
  const platformTag=find('platformTag');if(platformTag){platformTag.textContent='';platformTag.hidden=true}
  const process=find('caseProcess'),identity=process.textContent||'';process.title=identity;process.setAttribute('aria-label','应用标识：'+identity);process.hidden=!identity.trim();process.replaceChildren();
  identity.split(':').forEach((part,index)=>{if(index)process.append(document.createTextNode(':'),document.createElement('wbr'));process.append(document.createTextNode(part))});
  const navIcon=key=>{
    const family=key.replace(/^system_/,'');
    if(family.startsWith('cpu'))return 'cpu';
    if(family.startsWith('frame'))return 'frame';
    if(family.startsWith('gpu'))return 'gpu';
    if(family.startsWith('memory'))return 'memory';
    return key==='overview'||key==='fps'?key:'io';
  };
  document.querySelectorAll('#metricTabs .tab').forEach(button=>{
    const key=button.dataset.section,group=R.groups.find(group=>group.key===key),label=groupLabel(group)||labels[key]||button.textContent;button.title=group?.title||label;button.innerHTML=icon(navIcon(key))+`<span>${esc(label)}</span>`;
  });
  const extra=find('studioExtraNav');extra.replaceChildren();
  // Keep the scene shortcut outside the scrolling metric directory, including
  // when loading a saved copy of the already-rendered workbench.
  find('studioSidebar').insertBefore(extra,document.querySelector('.studio-nav'));
  for(const [key,label] of [['scenes','场景对比']]){
    const target=find(key==='scenes'?'section-scenes':key);if(!target)continue;
    const button=make('button','tab',icon(key==='scenes'?'scenes':'notes')+`<span>${label}</span>`);button.dataset.section=key;button.title=label;
    button.onclick=()=>{setActiveTab(key);target.scrollIntoView({behavior:'smooth',block:'start'})};extra.append(button);
  }
  const navToggle=find('studioNavToggle');navToggle.innerHTML=icon('sidebar');navToggle.type='button';navToggle.setAttribute('aria-controls','studioSidebar');
  const syncNavToggle=()=>{
    const expanded=!document.body.classList.contains('studio-rail-collapsed'),label=expanded?'收起目录':'展开目录';
    navToggle.title=label;navToggle.setAttribute('aria-label',label);navToggle.setAttribute('aria-expanded',String(expanded));
  };
  const navResize=find('studioSidebarResizeHandle')||make('div','studio-sidebar-resize');navResize.id='studioSidebarResizeHandle';navResize.tabIndex=0;
  navResize.setAttribute('role','separator');navResize.setAttribute('aria-orientation','vertical');navResize.setAttribute('aria-label','调整目录宽度');navResize.title='左右拖动调整目录宽度 · 双击恢复默认';document.body.append(navResize);
  const sidebarWidthKey='perfzy-report-sidebar-width-v1';let sidebarPreferredWidth=208,sidebarWidth=208,sidebarDrag=null;
  try{const value=Number(localStorage.getItem(sidebarWidthKey));if(Number.isFinite(value)&&value>0)sidebarPreferredWidth=value}catch(error){}
  const sidebarBounds=()=>({min:176,max:Math.max(176,Math.min(440,innerWidth-550))});
  const setSidebarWidth=(value,persist=false)=>{
    const {min,max}=sidebarBounds();sidebarWidth=Math.round(Math.max(min,Math.min(max,value)));
    document.body.style.setProperty('--sidebar-expanded-width',sidebarWidth+'px');
    navResize.setAttribute('aria-valuemin',String(min));navResize.setAttribute('aria-valuemax',String(max));navResize.setAttribute('aria-valuenow',String(sidebarWidth));
    if(persist){sidebarPreferredWidth=sidebarWidth;try{localStorage.setItem(sidebarWidthKey,String(sidebarWidth))}catch(error){}}
    schedule(false);
  };
  const endSidebarResize=()=>{
    if(!sidebarDrag)return;const pointerId=sidebarDrag.pointerId;sidebarDrag=null;document.body.classList.remove('studio-sidebar-resizing');
    if(navResize.hasPointerCapture(pointerId))navResize.releasePointerCapture(pointerId);setSidebarWidth(sidebarWidth,true);
  };
  navResize.addEventListener('pointerdown',event=>{
    if(event.button!==0||event.isPrimary===false)return;event.preventDefault();
    sidebarDrag={pointerId:event.pointerId,x:event.clientX,width:sidebarWidth};navResize.setPointerCapture(event.pointerId);document.body.classList.add('studio-sidebar-resizing');
  });
  navResize.addEventListener('pointermove',event=>{if(sidebarDrag&&event.pointerId===sidebarDrag.pointerId)setSidebarWidth(sidebarDrag.width+event.clientX-sidebarDrag.x)});
  for(const event of ['pointerup','pointercancel','lostpointercapture'])navResize.addEventListener(event,endSidebarResize);
  navResize.addEventListener('dblclick',()=>setSidebarWidth(208,true));
  navResize.addEventListener('keydown',event=>{
    const {min,max}=sidebarBounds(),values={ArrowLeft:sidebarWidth-24,ArrowRight:sidebarWidth+24,Home:min,End:max};
    if(Object.hasOwn(values,event.key)){event.preventDefault();setSidebarWidth(values[event.key],true)}
  });
  window.addEventListener('blur',endSidebarResize);window.addEventListener('resize',()=>setSidebarWidth(sidebarPreferredWidth));
  navToggle.onclick=()=>{endSidebarResize();document.body.classList.toggle('studio-rail-collapsed');syncNavToggle();schedule(false)};
  setSidebarWidth(sidebarPreferredWidth);
  const pageContent=document.querySelector('main.page');
  const syncContentFit=()=>{
    const width=pageContent.getBoundingClientRect().width;
    document.body.classList.toggle('studio-content-wide',width>=980);document.body.classList.toggle('studio-content-narrow',width<760);document.body.classList.toggle('studio-content-small',width<600);
  };
  if(typeof ResizeObserver!=='undefined')new ResizeObserver(syncContentFit).observe(pageContent);
  window.addEventListener('resize',syncContentFit);syncContentFit();
  document.addEventListener('click',event=>{const more=find('studioMore');if(more&&!more.contains(event.target))more.open=false});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){find('studioMore').open=false;document.querySelectorAll('.studio-help[open]').forEach(el=>el.open=false)}});
  const refreshChartToggle=section=>{
    const button=section?.querySelector('.collapse-toggle');if(!button)return;
    const expanded=!section.classList.contains('collapse'),title=section.querySelector('.section-title')?.textContent||'图表';
    button.type='button';button.innerHTML=icon(expanded?'up':'down');button.title=expanded?'收起图表':'展开图表';
    button.setAttribute('aria-label',(expanded?'收起 ':'展开 ')+title);button.setAttribute('aria-expanded',String(expanded));
    const body=section.querySelector('.chart-body');if(body){body.id=section.id+'-body';button.setAttribute('aria-controls',body.id)}
  };
  const sourceSetChartCollapsed=setChartCollapsed;
  setChartCollapsed=function(section,collapsed){sourceSetChartCollapsed(section,collapsed);refreshChartToggle(section)};
  document.querySelectorAll('#chartSections>.chart-section').forEach((section,index)=>{
    const group=R.groups[index],head=section.querySelector('.section-head'),sub=head.querySelector('.section-sub');
    const title=head.querySelector('.section-title');title.textContent=groupLabel(group);title.title=group.title||title.textContent;
    sub.textContent=group.keys.length+' 项指标';
    const help=head.querySelector('.studio-help')||make('details','studio-help','<summary aria-label="图表操作说明"></summary><div>单击：锁定时间点<br>再次单击 / Esc：解除锁定<br>拖动框选：计算选区统计<br>底部滑块：调整显示范围<br>图例：隐藏或显示单项指标</div>');
    help.querySelector('summary').innerHTML=icon('info');help.querySelector('summary').title='图表操作说明';
    const legend=find('legend-'+index);if(legend){legend.classList.add('studio-chart-legend');head.after(legend)}
    const toggle=head.querySelector('.collapse-toggle'),heading=head.querySelector('.studio-chart-title')||make('div','studio-chart-title'),actions=head.querySelector('.studio-chart-actions')||make('div','studio-chart-actions');
    heading.replaceChildren(title,sub);actions.replaceChildren(help,toggle);head.replaceChildren(heading,actions);refreshChartToggle(section);
    const body=section.querySelector('.chart-body');if(body){const summary=body.querySelector('.metric-summary');summary.setAttribute('aria-label','当前统计范围的指标摘要')}
  });

  // Resize the replay strip without changing screenshots, selected scenes or
  // recorded values. The splitter and thumbnails share the same measured size.
  const drawer=find('shotDrawer'),shotList=find('shotList'),heightKey='perfzy-report-replay-height-v1';
  const resizeHandle=find('studioShotResizeHandle')||make('div','studio-shot-resize');resizeHandle.id='studioShotResizeHandle';
  resizeHandle.tabIndex=0;resizeHandle.setAttribute('role','separator');resizeHandle.setAttribute('aria-orientation','horizontal');resizeHandle.setAttribute('aria-label','调整截图回放高度');
  resizeHandle.title='上下拖动调整高度 · 双击恢复默认';drawer.prepend(resizeHandle);
  for(const id of ['shotClose','shotLightboxClose']){const button=find(id);if(button){button.innerHTML=icon('close');button.type='button';button.setAttribute('aria-label',id==='shotClose'?'关闭截图回放':'关闭放大截图')}}
  let replayHeight=254,thumbFrame=0,resizeDrag=null;
  try{const saved=localStorage.getItem(heightKey),value=Number(saved);if(saved!==null&&Number.isFinite(value)&&value>0)replayHeight=value}catch(error){}
  const replayBounds=()=>{
    const toolbarBottom=document.querySelector('.tabs').getBoundingClientRect().bottom,max=Math.max(100,innerHeight-toolbarBottom-28);
    return {min:Math.min(180,max),max};
  };
  const syncReplayThumbnails=()=>{
    thumbFrame=0;
    const style=getComputedStyle(shotList),first=shotList.querySelector('.shot');
    if(!first||!shotList.clientHeight)return;
    const paddingY=parseFloat(style.paddingTop)+parseFloat(style.paddingBottom),paddingX=parseFloat(style.paddingLeft)+parseFloat(style.paddingRight),gap=parseFloat(style.columnGap)||10;
    const previousWidth=first.getBoundingClientRect().width,anchor=shotList.scrollLeft/Math.max(1,previousWidth+gap);
    const cardHeight=Math.max(54,Math.floor(shotList.clientHeight-paddingY)),imageHeight=Math.max(18,cardHeight-36),cardWidth=Math.max(96,Math.min(Math.round(imageHeight*16/9+12),shotList.clientWidth-paddingX));
    drawer.style.setProperty('--shot-card-height',cardHeight+'px');drawer.style.setProperty('--shot-image-height',imageHeight+'px');drawer.style.setProperty('--shot-card-width',cardWidth+'px');
    if(Math.abs(previousWidth-cardWidth)>1)shotList.scrollLeft=Math.max(0,anchor*(cardWidth+gap));
  };
  const queueReplayThumbnails=()=>{if(!thumbFrame)thumbFrame=requestAnimationFrame(syncReplayThumbnails)};
  const setReplayHeight=(value,persist=false)=>{
    const {min,max}=replayBounds();replayHeight=Math.round(Math.max(min,Math.min(max,value)));
    document.body.style.setProperty('--shot-panel-height',replayHeight+'px');
    resizeHandle.setAttribute('aria-valuemin',String(Math.round(min)));resizeHandle.setAttribute('aria-valuemax',String(Math.round(max)));resizeHandle.setAttribute('aria-valuenow',String(replayHeight));
    if(persist)try{localStorage.setItem(heightKey,String(replayHeight))}catch(error){}
    queueReplayThumbnails();
  };
  const endReplayResize=()=>{
    if(!resizeDrag)return;const pointerId=resizeDrag.pointerId;resizeDrag=null;
    document.body.classList.remove('studio-shot-resizing');if(resizeHandle.hasPointerCapture(pointerId))resizeHandle.releasePointerCapture(pointerId);
    setReplayHeight(replayHeight,true);loadVisibleShots();
  };
  resizeHandle.addEventListener('pointerdown',event=>{
    if(event.button!==0||event.isPrimary===false)return;
    event.preventDefault();resizeDrag={pointerId:event.pointerId,y:event.clientY,height:replayHeight};resizeHandle.setPointerCapture(event.pointerId);document.body.classList.add('studio-shot-resizing');
  });
  resizeHandle.addEventListener('pointermove',event=>{if(resizeDrag&&event.pointerId===resizeDrag.pointerId)setReplayHeight(resizeDrag.height+resizeDrag.y-event.clientY)});
  for(const event of ['pointerup','pointercancel','lostpointercapture'])resizeHandle.addEventListener(event,endReplayResize);
  resizeHandle.addEventListener('dblclick',()=>setReplayHeight(254,true));
  resizeHandle.addEventListener('keydown',event=>{
    const {min,max}=replayBounds(),values={ArrowUp:replayHeight+24,ArrowDown:replayHeight-24,Home:min,End:max};
    if(Object.hasOwn(values,event.key)){event.preventDefault();setReplayHeight(values[event.key],true)}
  });
  window.addEventListener('blur',endReplayResize);
  window.addEventListener('resize',()=>setReplayHeight(replayHeight));
  if(typeof ResizeObserver!=='undefined')new ResizeObserver(queueReplayThumbnails).observe(drawer);
  const sourceToggleDrawer=toggleDrawer;
  toggleDrawer=function(open){sourceToggleDrawer(open);if(open){setReplayHeight(replayHeight);loadVisibleShots()}};
  setReplayHeight(replayHeight);

  // Keep the original statistical population and original per-metric functions.
  const sourceUpdateSummaries=updateSummaries;
  const missingLabel=key=>R.metrics[key]?'当前范围未采到':'源报告未提供';
  function sparkline(rows,key,color){
    const s=statsFor(key,rows);if(!s)return '';
    const start=rows[0]?.elapsed??0,end=rows.at(-1)?.elapsed??1,span=Math.max(1e-9,end-start),height=Math.max(1e-9,s.max-s.min);
    const parts=selectedSegmentRanges.length?selectedSegmentRanges.map(range=>rows.filter(row=>row.elapsed>=range.start&&row.elapsed<=range.end)):[rows];
    const segments=parts.flatMap(part=>metricSegments(part,key).map(segment=>decimateMetricSegment(segment,key,170)));
    const paths=segments.map(segment=>{
      const point=row=>[(2+(row.elapsed-start)/span*82).toFixed(1),(25-(row[key]-s.min)/height*21).toFixed(1)];
      if(segment.length===1){const [cx,cy]=point(segment[0]);return `<circle cx="${cx}" cy="${cy}" r="2" fill="${color}"/>`}
      const path=segment.map((row,i)=>`${i?'L':'M'}${point(row).join(',')}`).join(' ');
      return `<path d="${path}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"/>`;
    }).join('');
    return `<svg viewBox="0 0 86 29" role="img" aria-label="当前统计范围走势">${paths}</svg>`;
  }
  function renderWorkbenchSummary(rows){
    const cpuKey=R.metrics.app_cpu?'app_cpu':null,memoryKey=['ios_physical_footprint','memory','app_rss'].find(key=>R.metrics[key]),gpuKey=R.metrics.app_gpu_usage?'app_gpu_usage':R.metrics.gpu?'gpu':null;
    const definitions=[['fps','平均帧率','fps','avg'],[cpuKey,'应用 CPU','cpu','avg'],[memoryKey,'峰值内存','memory','max'],[gpuKey,gpuKey==='gpu'?'设备 GPU（共享）':'应用 GPU','gpu','avg']];
    find('overviewKpis').innerHTML=definitions.map(([key,title,type,operation])=>{
      const meta=key?R.metrics[key]:null,s=key?statsFor(key,rows):null,color=reportMetricColor(key,meta?.color),value=s?s[operation]:null;
      const number=value===null?'未采到':value.toLocaleString('en-US',{maximumFractionDigits:type==='memory'?0:1,minimumFractionDigits:type==='memory'?0:1});
      const detail=s?(operation==='max'?`平均 ${fmt(s.avg,meta.unit)}`:`峰值 ${fmt(s.max,meta.unit)}`):(key?missingLabel(key):'源报告未提供该项');
      return `<article class="studio-kpi" style="--metric:${color}"><div class="studio-kpi-top"><span>${title}</span>${icon(type)}</div><div class="studio-kpi-value${s?'':' no-data'}">${esc(number)}${s?`<small>${esc(meta.unit||'')}</small>`:''}</div><div class="studio-kpi-bottom"><span>${esc(detail)}<small>${s?s.count.toLocaleString()+' 次有效观测':'不以 0 替代缺失'}</small></span>${s?sparkline(rows,key,color):''}</div></article>`;
    }).join('');
    R.groups.forEach((group,index)=>{
      const host=find('summary-'+index);if(!host)return;
      host.querySelectorAll('.metric-mini-name').forEach(node=>{node.textContent=node.textContent.replace(/^Avg\((.*)\)$/,'平均 · $1')});
      const method=host.querySelector('.fps-stat-method');
      if(method){const detail=make('details','studio-fps-method','<summary>统计口径</summary>');const copy=make('p','');copy.textContent=method.textContent;detail.append(copy);method.replaceWith(detail);}
    });
    const scenes=selectedSegments(),framed=statStart!==null&&statEnd!==null;
    find('summaryScopeText').textContent=framed?`框选统计 ${fmtTime(statStart)}—${fmtTime(statEnd)}${scenes.length?' · 与所选场景交集':''}`:scenes.length?`已选 ${scenes.length} 个场景 · ${fmtTime(filteredTimelineDuration())}`:`显示范围 ${fmtTime(left)}—${fmtTime(right)}`;
  }
  updateSummaries=function(rows){sourceUpdateSummaries(rows);renderWorkbenchSummary(rows)};
  updateSummaries(statisticsRows());
  syncRanges();
  const footer=document.querySelector('.footer');if(footer)footer.textContent='PerfZY · 单文件离线性能报告 · 数据保存在本机';
  // Keep window scrolling and correct offsets; also include the relocated detail sections.
  let navFrame=0;
  const updateStudioNavigation=()=>{
    navFrame=0;const top=document.querySelector('.tabs').getBoundingClientRect().bottom;
    const sections=[...document.querySelectorAll('main>.chart-section,#chartSections>.chart-section')];
    let current=document.querySelector('main>.chart-section')||find('section-overview'),largest=-1;
    if(window.scrollY>=100)for(const section of sections){const b=section.getBoundingClientRect(),shown=Math.max(0,Math.min(b.bottom,innerHeight)-Math.max(b.top,top));if(shown>largest){largest=shown;current=section}}
    if(current)setActiveTab(current.id.replace('section-',''));
  };
  const requestNavUpdate=()=>{if(!navFrame)navFrame=requestAnimationFrame(updateStudioNavigation)};
  window.addEventListener('scroll',requestNavUpdate,{passive:true});window.addEventListener('resize',requestNavUpdate);requestNavUpdate();
  const redrawAppearance=()=>{buildLegends();updateSummaries(statisticsRows());schedule(true)};
  document.addEventListener('perfzy:theme-change',redrawAppearance);
  window.addEventListener('beforeprint',()=>schedule(true));
  window.addEventListener('afterprint',()=>schedule(true));
  if(innerWidth<1100)document.body.classList.add('studio-rail-collapsed');
  syncNavToggle();
  schedule(false);
})();
