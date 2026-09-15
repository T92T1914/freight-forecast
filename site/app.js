const $ = id => document.getElementById(id);
const project = document.body.dataset.project;
const number = (value, digits=0) => value.toLocaleString('en-US', {maximumFractionDigits:digits, minimumFractionDigits:digits});
function metrics(items) {
  $('metrics').replaceChildren(...items.map(([value,label]) => {
    const box=document.createElement('div');box.className='metric';
    const strong=document.createElement('strong');strong.textContent=value;
    const span=document.createElement('span');span.textContent=label;
    box.append(strong,span);return box;
  }));
}
function table(headers, rows, caption) {
  const element=document.createElement('table');
  const cap=document.createElement('caption');cap.textContent=caption;element.append(cap);
  const head=document.createElement('thead'),hr=document.createElement('tr');
  headers.forEach(text=>{const cell=document.createElement('th');cell.scope='col';cell.textContent=text;hr.append(cell);});head.append(hr);element.append(head);
  const body=document.createElement('tbody');
  rows.forEach(row=>{const tr=document.createElement('tr');row.forEach(text=>{const cell=document.createElement('td');cell.textContent=text;tr.append(cell);});body.append(tr);});element.append(body);
  $('table').replaceChildren(element);
}
function options(labels) {
  $('choice').replaceChildren(...labels.map((label,i)=>{const o=document.createElement('option');o.value=String(i);o.textContent=label;return o;}));
}
try {
  const response=await fetch('data.json');if(!response.ok)throw new Error('Saved evidence could not be loaded.');
  const data=await response.json();
  $('revision').textContent='Recorded source revision: '+data.source_commit;
  $('revision').href='https://github.com/T92T1914/'+project+'/tree/'+data.source_commit;
  let update;
  if(project==='freight-forecast') {
    $('choice-label').textContent='Inspect a test month';options(data.rows.map(row=>row.month));
    update=()=>{const row=data.rows[Number($('choice').value)];
      metrics([[number(row.actual),'actual moves'],[number(row.model,1),'model forecast'],[number(row.baseline,1),'seasonal baseline']]);
      const modelError=Math.abs(row.model-row.actual),baseError=Math.abs(row.baseline-row.actual);
      $('finding').textContent=modelError<baseError?'The model has the smaller absolute error in this month.':modelError>baseError?'The seasonal baseline has the smaller absolute error in this month.':'Both forecasts have the same absolute error in this month.';
      table(['Forecast','Absolute error'],[['Model',number(modelError,1)+' moves'],['Seasonal baseline',number(baseError,1)+' moves']],'Absolute error is the distance from the recorded actual value.');
    };
    $('context').textContent='Across all 24 test months, mean absolute error is '+number(data.metrics.model_mae,1)+' moves for the model and '+number(data.metrics.naive_mae,1)+' for the seasonal baseline. A better average does not mean a better forecast in every month.';
  } else if(project==='mcts-combat-engine') {
    $('choice-label').textContent='Inspect an action';options(data.actions.map(a=>a.name));
    update=()=>{const a=data.actions[Number($('choice').value)],total=data.actions.reduce((n,x)=>n+x.visits,0);
      metrics([[number(a.reward,3),'mean shaped reward'],[number(a.visits),'visits'],[number(100*a.visits/total,1)+'%','share of recorded visits']]);
      $('finding').textContent=a.name==='Spark'?'Spark has the highest recorded mean reward in this decision.':'A lower ranked action still received simulations so the search could compare it with the alternatives.';
      table(['Action','Mean reward','Visits'],data.actions.map(a=>[a.name,number(a.reward,3),number(a.visits)]),'All available actions in the recorded initial decision.');
    };
    $('context').textContent=data.conditions+'. '+data.precision+'.';
  } else if(project==='exact-blackjack-solver') {
    $('choice-label').textContent='Inspect a hand';options(data.hands.map(h=>h.cards.join(' + ')+' against dealer '+h.dealer));
    const labels={H:'Hit',S:'Stand',D:'Double'};
    update=()=>{const h=data.hands[Number($('choice').value)];
      metrics([[labels[h.action],'preferred action'],[number(h.values[h.action],6),'expected net return'],[number(h.margin,6),'margin over the next action']]);
      $('finding').textContent='The preferred move is '+labels[h.action].toLowerCase()+'. The negative value still represents an expected loss under these rules.';
      table(['Action','Expected net return'],Object.entries(h.values).sort((a,b)=>b[1]-a[1]).map(([k,v])=>[labels[k],number(v,6)]),'The margin compares expected returns. It is not a confidence score or win probability.');
    };
    $('context').textContent=data.rules+'. No new hands are calculated by this static page.';
  }
  if(update){update();$('choice').addEventListener('change',update);$('interactive').hidden=false;}
} catch(error) {
  $('load-error').hidden=false;$('load-error').textContent='The interactive evidence did not load. You can still inspect the source data using the link below.';
}
