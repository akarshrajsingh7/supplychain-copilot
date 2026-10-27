import {readFile} from 'node:fs/promises';
import {test,expect} from '@playwright/test';

test('read failure recovers and exports inspectable checkpoint evidence',async({page},testInfo)=>{
  await page.request.post('/api/reset');
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Inject Tariff Event',exact:true})).toBeEnabled();
  await page.getByLabel('Test recovery',{exact:true}).check();
  await page.getByRole('button',{name:'Inject Tariff Event',exact:true}).click();
  await expect(page.getByRole('button',{name:'Approve',exact:true})).toBeVisible({timeout:45000});
  await expect(page.getByText('Read recovered; continuing the same investigation thread',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:/^Tools \(/}).click();
  await expect(page.getByText('TOOL_RESULT',{exact:true}).first()).toBeVisible();
  await page.getByRole('button',{name:'Run evidence',exact:true}).click();
  const panel=page.getByRole('dialog',{name:'Run evidence',exact:true});
  await expect(panel.getByText('10 passed · 0 pending · 0 failed',{exact:true})).toBeVisible({timeout:15000});
  await expect(panel.getByText('Scripted BaseChatModel; no incident LLM inference',{exact:true})).toBeVisible();
  const downloadPromise=page.waitForEvent('download');
  await panel.getByRole('link',{name:'Download JSON',exact:true}).click();
  const download=await downloadPromise;
  const path=testInfo.outputPath('run-evidence.json');
  await download.saveAs(path);
  const report=JSON.parse(await readFile(path,'utf8'));
  expect(report.metrics.retries).toBe(1);
  expect(report.metrics.failed_reads).toBe(1);
  expect(report.checkpoint.pending_interrupts).toBe(1);
  expect(report.run.policy.simulate_tool_failure).toBe(true);
  expect(report.events.some((e:{stage:string})=>e.stage==='TOOL_ERROR')).toBe(true);
  await panel.getByRole('button',{name:'Close run evidence',exact:true}).click();
  await page.getByRole('button',{name:'Approve',exact:true}).click();
  await expect(page.getByText('✓ APPROVED · written to temporal memory')).toBeVisible();
  const approved=await (await page.request.get('/api/evaluation')).json();
  expect(approved.checkpoint.approval_interrupt_observed).toBe(true);
  expect(approved.summary.fail).toBe(0);
});

test('extra-cost policy blocks approval when the limit is too low',async({page})=>{
  await page.request.post('/api/reset');
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Inject Tariff Event',exact:true})).toBeEnabled();
  await page.getByLabel('Extra cost limit in euros',{exact:true}).fill('3000');
  await page.getByRole('button',{name:'Inject Tariff Event',exact:true}).click();
  await expect(page.getByText('POLICY BLOCKED · extra cost exceeds the limit',{exact:true})).toBeVisible({timeout:45000});
  await expect(page.getByRole('button',{name:'Approve',exact:true})).toHaveCount(0);
  const report=await (await page.request.get('/api/evaluation')).json();
  expect(report.spending_check).toMatchObject({allowed:false,incremental_cost_eur:4990,limit_eur:3000});
  expect(report.provenance.some((f:{kind:string})=>f.kind==='DECISION')).toBe(false);
  await expect.poll(async()=> (await page.request.post('/api/reset')).status(),{timeout:15000}).toBe(200);
});
