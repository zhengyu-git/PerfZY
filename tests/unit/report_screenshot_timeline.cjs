const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../../assets/report_template_v10.html'),'utf8');
const start=source.indexOf('function screenshotMatchAt('),end=source.indexOf('\nfunction showScreenshotGap(',start);
assert.ok(start>0&&end>start);
const match=new Function(source.slice(start,end)+';return screenshotMatchAt;')();
const cases=JSON.parse(fs.readFileSync(path.join(__dirname,'screenshot_timeline_cases.json'),'utf8'));
for(const test of cases){
 const result=match({screenshots:test.shots.map(elapsed=>({elapsed})),capture_settings:test.settings,capture_settings_changes:test.changes||[]},test.time);
 assert.equal(result.index,test.expected,test.name);
 if(test.reason)assert.equal(result.reason,test.reason,test.name);
}
console.log(`Screenshot matching passed ${cases.length} shared cases.`);
