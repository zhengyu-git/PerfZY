// Run with node tests/unit/report_async_metrics.cjs. Synthetic observations only.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const template=fs.readFileSync(path.join(__dirname,'../../assets/report_template_v10.html'),'utf8');
const start=template.indexOf('let metricGapLimit;'),end=template.indexOf('function reportTrendSvg(');
assert.ok(start>=0&&end>start,'Report metric helper extraction boundaries must remain available');
const helpers=template.slice(start,end);
const percentile=(values,p)=>{
  if(!values.length)return null;
  const ordered=[...values].sort((a,b)=>a-b),position=(ordered.length-1)*p/100;
  const lower=Math.floor(position),upper=Math.ceil(position);
  return ordered[lower]+(ordered[upper]-ordered[lower])*(position-lower);
};
function model(samples){
  return new Function('R','percentile',helpers+';return {metricObservations,metricTimeGapLimit,metricSegments,metricObservationAt};')({samples},percentile);
}
const close=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-9,`${actual} != ${expected}`);
const stamps=parts=>parts.map(part=>part.map(row=>row.elapsed));

// GPU has four observations per second; FPS one, CPU one every two seconds.
// The extra channel rows are not failed observations of the other channels.
const interleaved=Array.from({length:33},(_,index)=>{
  const row={elapsed:index/4,gpu:40+index%5};
  if(index%4===0)row.fps=index===8?0:30-index%3;
  if(index%8===0)row.app_cpu=10+index/8;
  return row;
});
const before=structuredClone(interleaved),mixed=model(interleaved);
assert.deepEqual(mixed.metricObservations('fps').map(row=>row.elapsed),[0,1,2,3,4,5,6,7,8]);
assert.deepEqual(mixed.metricObservations('app_cpu').map(row=>row.elapsed),[0,2,4,6,8]);
close(mixed.metricTimeGapLimit('fps'),3);
close(mixed.metricTimeGapLimit('app_cpu'),6);
close(mixed.metricTimeGapLimit('gpu'),.75);
assert.deepEqual(stamps(mixed.metricSegments(interleaved,'fps')),[[0,1,2,3,4,5,6,7,8]]);
assert.deepEqual(stamps(mixed.metricSegments(interleaved,'app_cpu')),[[0,2,4,6,8]]);
assert.equal(mixed.metricSegments(interleaved,'gpu')[0].length,33);
// A CPU/GPU row may be nearer to the pointer than the FPS point; use FPS itself.
assert.equal(mixed.metricObservationAt('fps',1.25).elapsed,1);
assert.equal(mixed.metricObservationAt('fps',2).fps,0);
assert.equal(mixed.metricObservationAt('app_cpu',1.9).elapsed,2);
assert.deepEqual(interleaved,before,'Charting and probing must not mutate source rows');

// Return time-ordered actual rows, without inventing values on other timestamps.
const disorder=[{elapsed:2,fps:0},{elapsed:.5,app_cpu:15},{elapsed:0,fps:30},{elapsed:1,fps:null}];
const disorderBefore=structuredClone(disorder),ordered=model(disorder);
assert.deepEqual(ordered.metricObservations('fps').map(row=>row.elapsed),[0,1,2]);
assert.strictEqual(ordered.metricObservations('fps')[1],disorder[3]);
assert.deepEqual(disorder,disorderBefore);

const explicit=[{elapsed:0,fps:30},{elapsed:.25,app_cpu:20},{elapsed:1,fps:null},
  {elapsed:1.5,app_cpu:30},{elapsed:2,fps:0},{elapsed:3,fps:28},{elapsed:4,fps:NaN},
  {elapsed:5,fps:30},{elapsed:6,fps:Infinity},{elapsed:7,fps:20},{elapsed:8,fps:19}];
const missing=model(explicit);
assert.equal(missing.metricObservations('fps').length,9,'Keep explicit invalid observations as break evidence');
assert.deepEqual(stamps(missing.metricSegments(explicit,'fps')),[[0],[2,3],[5],[7,8]]);
for(const time of [.5,1,1.5,3.5,4,4.5,5.5,6,6.5])
  assert.equal(missing.metricObservationAt('fps',time),null,`Explicit missing FPS must not become a hover value at ${time}`);
assert.strictEqual(missing.metricObservationAt('fps',2),explicit[4]);

const longRows=[0,1,2,10,11].map(elapsed=>({elapsed,fps:30-elapsed}));
const long=model(longRows);
close(long.metricTimeGapLimit('fps'),3);
assert.deepEqual(stamps(long.metricSegments(longRows,'fps')),[[0,1,2],[10,11]]);
for(const time of [2.1,6,9.9])assert.equal(long.metricObservationAt('fps',time),null);
assert.strictEqual(long.metricObservationAt('fps',2),longRows[2]);
assert.strictEqual(long.metricObservationAt('fps',10),longRows[3]);
assert.strictEqual(long.metricObservationAt('fps',-.5),longRows[0]);
assert.strictEqual(long.metricObservationAt('fps',11.9),longRows[4]);
assert.equal(long.metricObservationAt('fps',-1.6),null);
assert.equal(long.metricObservationAt('fps',12.6),null);

// A forced scene boundary is different from an absent metric on an async row.
const timeline=[0,1,2,3,4,5].map(elapsed=>({elapsed,fps:30-elapsed}));
const scenes=model(timeline);
const selected=[timeline[0],timeline[1],{elapsed:4,__metricBreak:true},timeline[4],timeline[5]];
assert.deepEqual(stamps(scenes.metricSegments(selected,'fps')),[[0,1],[4,5]]);
const ranges=[{start:0,end:1},{start:4,end:5}];
for(const time of [1.01,2.5,3.99])assert.equal(scenes.metricObservationAt('fps',time,ranges),null);
assert.strictEqual(scenes.metricObservationAt('fps',1,ranges),timeline[1]);
assert.strictEqual(scenes.metricObservationAt('fps',4,ranges),timeline[4]);
assert.equal(scenes.metricObservationAt('fps',4.4,ranges).elapsed,4);
// Do not snap to a sample outside a selected range, even if it is close.
assert.equal(scenes.metricObservationAt('fps',2.5,[{start:2.2,end:2.8}]),null);
assert.equal(scenes.metricObservationAt('fps',1,[]),null);

const singleRow={elapsed:5,fps:0},single=model([singleRow]);
assert.strictEqual(single.metricObservationAt('fps',5),singleRow);
assert.equal(single.metricObservationAt('fps',5.001),null);
assert.equal(single.metricObservationAt('fps',4.999),null);
assert.deepEqual(single.metricSegments([singleRow],'fps'),[[singleRow]]);
assert.deepEqual(single.metricObservations('memory'),[]);
assert.equal(single.metricObservationAt('memory',5),null);
assert.equal(single.metricObservationAt('fps',NaN),null);
assert.equal(single.metricObservationAt('fps',Infinity),null);

console.log('Async report metrics passed: own observation cadence, explicit missing, zero, long gaps, scene boundaries, hover and source nonmutation.');
