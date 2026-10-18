const {chromium}=require('playwright');
const fs=require('fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let detailCalls=0,revision=0;
 const student={id:'s1',filename:'示例学生.docx',student_id:'123',candidates:[{id:'c1',problem_id:1,state:'approved',code:Array.from({length:150},(_,i)=>'// source line '+i).join('\n')}]};
 const state={workspace:'demo',summary:{},settings:{},students:[student],jobs:[]};
 await page.route('http://workbench.test/**',async route=>{
  const url=new URL(route.request().url());let data;
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('homework_import/workbench.html','utf8')});
  if(url.pathname==='/api/ui/state')data=state;
  else if(url.pathname==='/api/ui/job')data={jobs:[]};
  else if(url.pathname==='/api/ui/student'){detailCalls++;data={document:student,assignment:{problems:[{id:1,statement:'求两个整数之和'}]},report:{dir:'demo',result:[],cases:[{'题号':'1','用例':'case1','结果':'答案错误',diff_text:'difference '+revision,expected_text:'2',actual_text:'3'}],compilation:{'1':{status:'编译失败',log:'error: missing semicolon'}}}}}
  else data={};
  await route.fulfill({json:data});
 });
 await page.goto('http://workbench.test/');
 await page.getByRole('button',{name:'学生与判题',exact:true}).first().click();
 await page.locator('[data-read-key="case-1-case1"] summary').click();
 assert.equal(await page.locator('[data-read-key="batch-compile"]').getAttribute('open'),'');
 await page.locator('.code').evaluate(n=>n.scrollTop=600);
 const before=await page.locator('.code').evaluate(n=>n.scrollTop);
 await page.evaluate(()=>loadState());
 assert.equal(await page.locator('.code').evaluate(n=>n.scrollTop),before);
 revision++;
 await page.evaluate(()=>loadState());
 assert.equal(await page.locator('.code').evaluate(n=>n.scrollTop),before);
 assert.equal(await page.locator('[data-read-key="case-1-case1"]').getAttribute('open'),'');
 await page.locator('#auto-refresh').uncheck();
 const calls=detailCalls;
 await page.evaluate(async()=>{lastStateAt=0;await tick()});
 assert.equal(detailCalls,calls);
 await page.getByRole('button',{name:'批量判题',exact:true}).first().click();
 await page.locator('#f-out').fill('my-reports');
 await page.getByRole('button',{name:'AI 输出判定',exact:true}).click();
 await page.getByRole('button',{name:'批量判题',exact:true}).first().click();
 assert.equal(await page.locator('#f-out').inputValue(),'my-reports');
 assert.deepEqual(errors,[]);
 console.log('UI regression checks passed: refresh, expansion, scroll, pause and form drafts');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
