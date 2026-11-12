const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../../assets/report_template_v10.html'),'utf8');
for(const match of source.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)){
  if(!match[1].includes('application/json'))new Function(match[2]);
}
function functionText(name){
  const start=source.indexOf('function '+name+'(');
  assert.ok(start>=0,name);
  const end=source.indexOf('\nfunction ',start+9);
  return source.slice(start,end<0?source.length:end);
}
const R={measurement:{platform:'ios',frame_time_estimated:true},metrics:{
  fps:{unit:'FPS',title:'设备 FPS'},frame_time:{unit:'ms',title:'估算帧间隔'}}};
const stats={avg:60,min:10,max:100,p5:60,p95:100,cv:0.01,first:60,last:60};
const reportStats=key=>R.metrics[key]?stats:null;
const evaluate=new Function('R','reportStats',functionText('professionalReferenceEvaluation')+';return professionalReferenceEvaluation;')(R,reportStats);
const scope={rows:[]},keys=['fps','frame_time'];
let result=evaluate(scope,keys,60);
let frame=result.gates.find(gate=>gate.key==='frame_time');
assert.equal(frame.actual,null);assert.equal(frame.result,null);assert.equal(frame.status,'missing');
assert.ok(frame.title.includes('未采集'));assert.equal(result.status,'incomplete');
const alertFn=new Function('R','reportStats','fmt',functionText('professionalAlerts')+';return professionalAlerts;')(R,reportStats,String);
const alerts=alertFn(scope,keys);
assert.ok(alerts.some(alert=>alert.code==='estimated-frame-interval'));
assert.ok(!alerts.some(alert=>alert.code==='frame-time'));
const findingFn=new Function('R','fmt',functionText('reportFinding')+';return reportFinding;')(R,String);
assert.ok(findingFn('frame_time',stats).includes('不是逐帧耗时分布'));
const warningFn=new Function('R',functionText('iosMeasurementWarning')+';return iosMeasurementWarning;')(R);
assert.ok(warningFn().includes('不能归因'));
R.measurement={platform:'windows',frame_time_estimated:false};
result=evaluate(scope,keys,60);frame=result.gates.find(gate=>gate.key==='frame_time');
assert.equal(frame.actual,100);assert.equal(frame.result,false);assert.equal(result.status,'fail');
assert.ok(alertFn(scope,keys).some(alert=>alert.code==='frame-time'));
assert.equal(warningFn(),'');
console.log('iOS report scope passed: estimated intervals never substitute measured-frame gates; Windows unchanged.');
