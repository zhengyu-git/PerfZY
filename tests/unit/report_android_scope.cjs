const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../../assets/report_template_v10.html'),'utf8');
for(const match of source.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)){
  if(!match[1].includes('application/json'))new Function(match[2]);
}
function functionText(name){
  const start=source.indexOf('function '+name+'(');assert.ok(start>=0,name);
  const end=source.indexOf('\nfunction ',start+9);return source.slice(start,end<0?source.length:end);
}
const R={measurement:{platform:'android',android_frame_sampling:true},metrics:{
  fps:{unit:'FPS',title:'应用图层 FPS'},frame_time:{unit:'ms',title:'帧时间采样'},jank_rate:{unit:'%',title:'固定阈值慢帧比例'}}};
const stats={avg:60,min:10,max:100,p5:60,p95:100,cv:.01,first:60,last:60};
const reportStats=key=>R.metrics[key]?stats:null;
const evaluate=new Function('R','reportStats',functionText('professionalReferenceEvaluation')+';return professionalReferenceEvaluation;')(R,reportStats);
const keys=['fps','frame_time','jank_rate'],scope={rows:[]},result=evaluate(scope,keys,60);
const frame=result.gates.find(g=>g.key==='frame_time');
assert.equal(frame.actual,null);assert.equal(frame.result,null);assert.equal(frame.status,'missing');
assert.ok(result.gates.find(g=>g.key==='jank_rate').title.includes('非 PerfDog'));
const alertFn=new Function('R','reportStats','fmt',functionText('professionalAlerts')+';return professionalAlerts;')(R,reportStats,String);
const alert=alertFn(scope,keys).find(a=>a.code==='frame-time');
assert.ok(alert.title.includes('采样偏高'));assert.ok(alert.detail.includes('直方图'));
const findingFn=new Function('R','fmt',functionText('reportFinding')+';return reportFinding;')(R,String);
assert.ok(findingFn('frame_time',stats).includes('不是整场逐帧'));
const warningFn=new Function('R',functionText('iosMeasurementWarning')+';return iosMeasurementWarning;')(R);
assert.ok(warningFn().includes('预算掉帧不是实测显示丢帧'));
R.measurement={platform:'windows'};
assert.equal(evaluate(scope,keys,60).gates.find(g=>g.key==='frame_time').actual,100);
assert.equal(warningFn(),'');
console.log('Android report scope passed: sampled/fallback frame statistics never substitute full-session frame gates.');
