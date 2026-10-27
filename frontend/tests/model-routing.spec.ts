import {test,expect} from '@playwright/test';

test('routing evidence shows routine, thinking and rejection escalation without invoking models',async({page})=>{
  // Read-only API snapshots supply the surrounding UI; route fixtures are local
  // to this browser and never written to backend state or LangSmith.
  const state=await (await page.request.get('/api/state')).json();
  const report=await (await page.request.get('/api/evaluation')).json();
  const routing={policy:'cost-aware',execution:'live',routine_model:'gpt-4.1-mini',thinking_model:'gpt-5.4',thinking_effort:'medium',single_model:'gpt-5.5',routine_roles:['commander','sourcing-analyst'],thinking_roles:['mitigation-planner','risk-critic'],escalation:'Sourcing switches to the thinking model after critic rejection.',active:true};
  const calls=[
    {id:'fixture-routine',role:'sourcing-analyst',tier:'routine',model:'gpt-4.1-mini',reason:'Structured evidence lookup and tool-result reporting.',reasoning_effort:null,after_rejection:false,status:'success',duration_ms:1000,usage:{input_tokens:10,output_tokens:5,total_tokens:15}},
    {id:'fixture-thinking',role:'risk-critic',tier:'thinking',model:'gpt-5.4',reason:'Independent risk review and constraint interpretation.',reasoning_effort:'medium',after_rejection:false,status:'success',duration_ms:2000,usage:{input_tokens:20,output_tokens:10,total_tokens:30}},
    {id:'fixture-escalated',role:'sourcing-analyst',tier:'thinking',model:'gpt-5.4',reason:'Escalated sourcing after the critic rejected a capacity-infeasible plan.',reasoning_effort:'medium',after_rejection:true,status:'started',duration_ms:null,usage:null},
  ];
  Object.assign(state,{mode:'live',model_routing:routing,model_calls:calls});
  Object.assign(report,{model_routing:routing,model_calls:calls});
  await page.route('**/api/events',route=>route.fulfill({contentType:'text/event-stream',body:`data: ${JSON.stringify(state)}\n\n`}));
  await page.route('**/api/evaluation',route=>route.fulfill({json:report}));
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await page.getByRole('button',{name:'Models (3)',exact:true}).click();
  const evidence=page.getByRole('region',{name:'Model routing evidence'});
  await expect(evidence.getByText('gpt-4.1-mini',{exact:true}).first()).toBeVisible();
  await expect(evidence.getByText('gpt-5.4',{exact:true}).first()).toBeVisible();
  await expect(evidence.getByText('45 reported tokens',{exact:true})).toBeVisible();
  const escalated=evidence.locator('details').last();
  await escalated.locator('summary').click();
  await expect(escalated.getByText('Escalated sourcing after the critic rejected a capacity-infeasible plan.',{exact:true})).toBeVisible();
  await expect(escalated.getByText('CALLING',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Run evidence',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Run evidence',exact:true});
  await expect(dialog.getByRole('region',{name:'Model routing evidence'})).toBeVisible();
  expect(errors).toEqual([]);
});
