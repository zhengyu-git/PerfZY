// Long-report display keeps observed peaks without changing source samples.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../../assets/report_template_v10.html'),'utf8');
const helper=source.slice(source.indexOf('let metricGapLimit;'),source.indexOf('function reportTrendSvg('));
const rows=Array.from({length:100000},(_,i)=>({elapsed:i/1000,fps:30}));
rows[23761].fps=240;rows[61517].fps=0;
const original=JSON.stringify(rows);
const {decimateMetricSegment,metricSegments}=new Function('R',helper+';return {decimateMetricSegment,metricSegments};')({samples:rows});
for(const limit of [4,16,170,800,2000]){
  const reduced=decimateMetricSegment(rows,'fps',limit);
  assert.ok(reduced.length<=limit);assert.equal(reduced[0],rows[0]);assert.equal(reduced.at(-1),rows.at(-1));
  assert.ok(reduced.includes(rows[23761]));assert.ok(reduced.includes(rows[61517]));
  for(let i=1;i<reduced.length;i++)assert.ok(reduced[i].elapsed>reduced[i-1].elapsed);
}
assert.equal(JSON.stringify(rows),original);
assert.equal(decimateMetricSegment(rows.slice(0,2),'fps',20).length,2);
assert.deepEqual(decimateMetricSegment([],'fps',20),[]);
const separated=[...rows.slice(0,100),{elapsed:.1,fps:null},...rows.slice(100,200)];
const pieces=metricSegments(separated,'fps').map(s=>decimateMetricSegment(s,'fps',16));
assert.equal(pieces.length,2);assert.equal(pieces[0].at(-1).elapsed,.099);assert.equal(pieces[1][0].elapsed,.1);
assert.ok(source.includes('const rows=decimateMetricSegment(segment,key,800)'));
assert.equal((source.match(/for\(const row of decimateMetricSegment\(segment,key,/g)||[]).length,2);
const workbench=fs.readFileSync(path.join(__dirname,'../../assets/report_workbench.js'),'utf8');
new Function(workbench);
assert.ok(workbench.includes('decimateMetricSegment(segment,key,170)'));
assert.ok(!/initializePreview|previewFpsStats|previewScreenshotData|hasDemoPictures/.test(workbench));
console.log('Report peak/valley, endpoints, missing boundaries, nonmutation and workbench checks passed');
