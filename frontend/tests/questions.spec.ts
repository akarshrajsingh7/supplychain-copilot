import {test,expect} from '@playwright/test';

const answer={
  answer:'Formosa Silicon has 2,000 units of available capacity.',
  sources:[{id:'N:B',label:'Formosa Silicon',kind:'entity',entity_id:'B',data:{available_capacity:2000}}],
  warnings:[],provider:'openai',model:'gpt-4.1-mini',view:'current',run_id:'test-snapshot',
  incident_status:'HEALTHY',as_of:'2026-10-25T09:00:00+00:00',
};

test('graph question model controls, ephemeral keys and source inspection',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  let submitted:Record<string,unknown>|null=null;
  await page.route('**/api/qa/ask',async route=>{
    submitted=route.request().postDataJSON();
    expect(route.request().headers()['x-llm-api-key']).toBe('test-browser-key');
    expect(route.request().postData()).not.toContain('test-browser-key');
    await route.fulfill({json:answer});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Ask the knowledge graph',exact:true}).click();
  const panel=page.getByRole('dialog',{name:'Ask the knowledge graph'});
  await expect(panel.getByLabel('Model provider')).toBeEnabled();
  await panel.getByLabel('Model provider').selectOption('openai');
  await panel.getByLabel('Model',{exact:true}).selectOption('custom');
  await panel.getByLabel('Custom model ID').fill('gpt-4.1-mini');
  await panel.getByLabel('Your question').fill('What is Supplier B capacity?');
  await expect(panel.getByRole('button',{name:'Ask graph',exact:true})).toBeDisabled();
  await panel.getByLabel('OpenAI API key',{exact:true}).fill('test-browser-key');
  await expect(panel.getByLabel('OpenAI API key',{exact:true})).toHaveAttribute('type','password');
  await panel.getByRole('button',{name:'Ask graph',exact:true}).click();
  await expect(panel.getByText(answer.answer,{exact:true})).toBeVisible();
  expect(submitted).toMatchObject({provider:'openai',model:'gpt-4.1-mini',view:'current'});
  await panel.getByText('Formosa Silicon',{exact:true}).click();
  await panel.getByRole('button',{name:'Highlight entity',exact:true}).click();
  await expect(page.getByText('Entity inspector',{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).not.toContain('test-browser-key');
  await panel.getByRole('button',{name:'Close graph questions'}).click();
  await page.getByRole('button',{name:'Ask the knowledge graph',exact:true}).click();
  await panel.getByLabel('Model provider').selectOption('openai');
  await expect(panel.getByLabel('OpenAI API key',{exact:true})).toHaveValue('');
  await panel.getByLabel('Model provider').selectOption('anthropic');
  await expect(panel.getByLabel('Anthropic API key',{exact:true})).toBeVisible();
  await panel.getByLabel('Model provider').selectOption('google');
  await expect(panel.getByLabel('Gemini API key',{exact:true})).toBeVisible();
  await panel.getByLabel('Model provider').selectOption('ollama');
  await expect(panel.getByText('No API key required.',{exact:false})).toBeVisible();
  await expect(panel.locator('input[type="password"]')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('question panel respects graph view and works in mobile fullscreen',async({page})=>{
  await page.route('**/api/qa/ask',route=>route.fulfill({json:answer}));
  await page.goto('/');
  await page.getByRole('button',{name:'Ask the knowledge graph',exact:true}).click();
  const panel=page.getByRole('dialog',{name:'Ask the knowledge graph'});
  await expect(panel.getByLabel('Model provider')).toBeEnabled();
  await panel.getByLabel('Your question').fill('Show Supplier B capacity.');
  await panel.getByRole('button',{name:'Ask graph',exact:true}).click();
  await expect(panel.getByText(answer.answer,{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Before disruption',exact:true}).click();
  await expect(panel.getByText('BEFORE DISRUPTION',{exact:true})).toBeVisible();
  await expect(panel.getByText(answer.answer,{exact:true})).toHaveCount(0);
  await panel.getByRole('button',{name:'Close graph questions'}).click();
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>Object.defineProperty(document,'fullscreenEnabled',{configurable:true,get:()=>false}));
  await page.getByRole('button',{name:'Enter graph fullscreen',exact:true}).click();
  await page.getByRole('button',{name:'Ask the knowledge graph',exact:true}).click();
  await expect(panel).toBeVisible();
  const bounds=await panel.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x+bounds!.width).toBeLessThanOrEqual(390);
  await panel.getByLabel('Your question').press('Escape');
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Exit graph fullscreen',exact:true})).toBeVisible();
});
