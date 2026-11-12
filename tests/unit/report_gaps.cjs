// Run with node tests/unit/report_gaps.cjs.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../../assets/report_template_v10.html'), 'utf8');
for (const match of source.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)) {
  if (!match[1].includes('application/json')) new Function(match[2]);
}
const helper = source.slice(source.indexOf('let metricGapLimit;'), source.indexOf('function reportTrendSvg('));
const rows = [0,1,2,3,4,10,11].map(elapsed => ({elapsed, fps: 30}));
const split = new Function('R', helper + ';return metricSegments;')({samples: rows});
assert.deepEqual(split(rows, 'fps').map(s => s.length), [5,2]);
const missing = rows.map(row => ({...row}));
delete missing[2].fps;
assert.deepEqual(split(missing, 'fps').map(s => s.length), [4,2]);
missing[2].fps = null;
assert.deepEqual(split(missing, 'fps').map(s => s.length), [2,2,2]);
assert.equal(split([{elapsed: 1, fps: 0}], 'fps')[0][0].fps, 0);
assert.deepEqual(split([{elapsed: 1}], 'fps'), []);
const boundsCode = source.slice(source.indexOf('function yBounds('), source.indexOf('\n', source.indexOf('function yBounds(')));
const bounds = new Function('R', boundsCode + ';return yBounds;')({metrics: {memory_growth: {unit:'MB'}}});
assert.ok(bounds({}, [{memory_growth:-100}, {memory_growth:20}], ['memory_growth'])[0] < -100);
console.log('Report JS syntax, missing data, long gaps, zero values and negative bounds passed');
const lazyCode = source.slice(source.indexOf('let shotObserver;'), source.indexOf('function buildShots('));
let open = false, callback, watched = 0;
const img = {dataset:{shotIndex:'0'},removeAttribute(){}};
const doc = {getElementById:()=>({classList:{contains:()=>open}}),querySelectorAll:()=>[img]};
class Observer {constructor(cb){callback=cb} observe(){watched++} unobserve(){}}
const load = new Function('document','IntersectionObserver','R',lazyCode+';return loadVisibleShots;')(doc,Observer,{screenshots:[{image:'data:test'}]});
load();assert.equal(watched,0);assert.equal(img.src,undefined);
open=true;load();assert.equal(watched,1);assert.equal(img.src,undefined);
callback([{isIntersecting:true,target:img}]);assert.equal(img.src,'data:test');
console.log('Screenshots remain unloaded until visible in the open drawer');
const drawCode=source.slice(source.indexOf('function drawAll('),source.indexOf('\n',source.indexOf('function drawAll(')));
let drawn=[];
const boxes=[{top:10,bottom:350,width:800,height:340},{top:1200,bottom:1540,width:800,height:340},{top:-500,bottom:-160,width:800,height:340}];
const draw=new Function('document','innerHeight','curveRowsInRange','selectedSegmentRanges','R','drawChart','drawFilteredChart','updateSummaries','statisticsRows',drawCode+';return drawAll;')(
 {querySelectorAll:()=>boxes.map(box=>({getBoundingClientRect:()=>box}))},800,()=>[],[],{groups:[{}, {}, {}]},(_c,_g,i)=>drawn.push(i),()=>{},()=>{},()=>[]);
draw();assert.deepEqual(drawn,[0]);
boxes[1].top=400;boxes[1].bottom=740;drawn=[];draw();assert.deepEqual(drawn,[0,1]);
console.log('Only visible charts render; newly visible charts render on next update');
const layoutCode=source.slice(source.indexOf('const compactGroupKeys='),source.indexOf('function buildCharts('));
const model={groups:[{key:'memory'},{key:'network'},{key:'gpu'},{key:'cpu'},{key:'fps'}]};
const layout=new Function('R',layoutCode+';return {prepareChartLayout,setChartCollapsed};')(model);
layout.prepareChartLayout();assert.deepEqual(model.groups.map(g=>g.key),['fps','cpu','gpu','memory','network']);
const attrs={},button={setAttribute:(k,v)=>attrs[k]=v};let collapsed;
const section={classList:{toggle:(_k,v)=>collapsed=v},querySelector:()=>button};
layout.setChartCollapsed(section,true);assert.equal(collapsed,true);assert.equal(button.textContent,'展开');assert.equal(attrs['aria-expanded'],'false');
layout.setChartCollapsed(section,false);assert.equal(collapsed,false);assert.equal(button.textContent,'收起');
console.log('Chart ordering and accessible collapse controls passed');
const extremeCode=source.slice(source.indexOf('function extremePoint('),source.indexOf('function buildExtremePoints('));
const extreme=new Function(extremeCode+';return extremePoint;')();
const values=[{elapsed:0,fps:30},{elapsed:1,fps:0},{elapsed:2,fps:null},{elapsed:3,fps:Infinity},{elapsed:4,fps:0}];
assert.equal(extreme(values,'fps','min').elapsed,1);
assert.equal(extreme(values,'fps','max').elapsed,0);
assert.equal(extreme(values,'memory','max'),null);
assert.equal(extreme([],'fps','min'),null);
console.log('Extreme selection preserves zero, chooses first tie and ignores missing/nonfinite data');
