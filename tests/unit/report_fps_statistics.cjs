// Run with node tests/unit/report_fps_statistics.cjs.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../../assets/report_template_v10.html'),'utf8');
for(const match of source.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)){
  if(!match[1].includes('application/json'))new Function(match[2]);
}
const quantile=(values,p)=>{const a=[...values].sort((x,y)=>x-y);if(!a.length)return null;const x=(a.length-1)*p/100,lo=Math.floor(x),hi=Math.ceil(x);return a[lo]+(a[hi]-a[lo])*(x-lo)};
const code=source.slice(source.indexOf('function fpsQualityStats('),source.indexOf('function fpsQualityMarkup('));
const calc=new Function('percentile','R',code+';return fpsQualityStats;')(quantile,{samples:[]});
const rows=values=>values.map((fps,elapsed)=>({elapsed,fps}));
const near=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-8,`${actual} != ${expected}`);
const analyze=(data,ranges=null,reference=data)=>calc(data,ranges,reference);

let result=analyze(rows([30,18,25,10,0]));
assert.equal(result.count,5);near(result.ratio18,60);near(result.ratio25,40);
near(result.variance,114.24);assert.equal(result.dropCount,3);
assert.equal(result.observedSeconds,4);near(result.dropsPerHour,2700);

result=analyze(rows([30,22,14,5]));
assert.equal(result.dropCount,1);near(result.dropsPerHour,1200); // strictly >8
result=analyze(rows([0,0]));
assert.equal(result.ratio18,0);assert.equal(result.variance,0);assert.equal(result.dropsPerHour,0);
result=analyze(rows([18,25]));near(result.ratio18,100);near(result.ratio25,50);

result=analyze([{elapsed:0,fps:60},{elapsed:1,fps:50},{elapsed:2,fps:50},{elapsed:10,fps:30},{elapsed:11,fps:20}]);
assert.equal(result.gapLimit,3);assert.equal(result.dropCount,2);
assert.equal(result.observedSeconds,3);near(result.dropsPerHour,2400);
result=analyze([{elapsed:0,fps:30},{elapsed:.2,memory:400},{elapsed:.7,app_cpu:30},{elapsed:1,fps:20}]);
assert.equal(result.count,2);assert.equal(result.dropCount,1);assert.equal(result.observedSeconds,1);
result=analyze([{elapsed:0,fps:30},{elapsed:1,fps:null},{elapsed:2,fps:20}]);
assert.equal(result.count,2);assert.equal(result.observedSeconds,0);assert.equal(result.dropsPerHour,null);

result=analyze(rows([30,30,10,10]),[{start:0,end:1},{start:2,end:3}]);
assert.equal(result.dropCount,0);assert.equal(result.observedSeconds,2);
result=analyze(rows([30,30,10,10]),[{start:2,end:3}]);
assert.equal(result.count,2);assert.equal(result.ratio18,0);
result=analyze([{elapsed:0,fps:10},{elapsed:0,fps:60},{elapsed:1,fps:50}]);
assert.equal(result.count,2);assert.equal(result.dropCount,1);

result=analyze(rows([30]));
assert.equal(result.ratio18,100);assert.equal(result.variance,0);assert.equal(result.dropsPerHour,null);
for(const data of [[],rows([null,NaN,Infinity,-1,1001]),[{elapsed:NaN,fps:30}]]){
  result=analyze(data);assert.equal(result.count,0);
  for(const key of ['ratio18','ratio25','variance','dropsPerHour'])assert.equal(result[key],null);
}
const original=rows([30,18,25,10,0]),snapshot=JSON.stringify(original);analyze(original);
assert.equal(JSON.stringify(original),snapshot);
const rangeCode=source.slice(source.indexOf('function statisticsRanges('),source.indexOf('\n}',source.indexOf('function statisticsRanges('))+2);
const ranges=new Function('selectedSegmentRanges','statStart','statEnd','left','right',rangeCode+';return statisticsRanges();');
assert.deepEqual(ranges([{start:0,end:2},{start:5,end:8}],1,6,0,8),[{start:1,end:2},{start:5,end:6}]);
assert.deepEqual(ranges([],null,null,1,4),[{start:1,end:4}]);
assert.deepEqual(ranges([],2,3,0,8),[{start:2,end:3}]);
console.log('FPS report statistics passed: inclusive rates, population variance, >8 drops/hour, zero/missing, gaps, asynchronous rows, scene boundaries and selection.');
