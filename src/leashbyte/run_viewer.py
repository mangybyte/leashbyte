"""Local run viewer. The static shell holds no run data or credentials."""
# ruff: noqa: E501

from fastapi.responses import HTMLResponse

VIEWER_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>LeashByte runs</title>
<style>
body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;background:#10151b;color:#e8edf2}
input,button{font:inherit;padding:.55rem}input{width:24rem;max-width:70%}button{cursor:pointer}
.run,section{border:1px solid #405064;border-radius:.5rem;padding:1rem;margin:1rem 0}
button.link{background:none;border:0;color:#8fc8ff;text-decoration:underline;padding:0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#080c10;padding:1rem;max-height:32rem;overflow:auto}
img{max-width:100%;height:auto}.muted{color:#b2bec9}#status{min-height:1.5rem}
</style></head><body>
<h1>LeashByte runs</h1>
<p>This viewer is local. The API key stays in this page and is not stored.</p>
<form id="auth"><label for="key">API key</label> <input id="key" type="password" required autocomplete="off">
<button>Open runs</button></form><p id="status" role="status"></p>
<main id="runs"></main><section id="detail" hidden></section>
<script>
const auth=document.querySelector('#auth'),runs=document.querySelector('#runs'),detail=document.querySelector('#detail'),status=document.querySelector('#status');
let key='',selected=null;
function node(tag,text){const item=document.createElement(tag);item.textContent=text;return item}
async function api(path){const response=await fetch(path,{headers:{'X-API-Key':key}});if(!response.ok){let message='Request failed: HTTP '+response.status;try{message=(await response.json()).error.message}catch{}throw new Error(message)}return response}
async function load(){status.textContent='Loading runs';runs.replaceChildren();detail.hidden=true;try{const body=await (await api('/v1/runs?limit=50')).json();status.textContent=body.runs.length+' runs';for(const run of body.runs){const card=node('article');card.className='run';const button=node('button',run.goal);button.className='link';button.onclick=()=>openRun(run.id);card.append(button,node('p',run.state+' | '+run.model+' | '+run.step_count+' steps'));runs.append(card)}}catch(error){status.textContent=error.message}}
async function openRun(id){try{const run=await (await api('/v1/runs/'+id)).json();const steps=await (await api('/v1/runs/'+id+'/steps')).json();selected=run;detail.replaceChildren();detail.hidden=false;detail.append(node('h2','Run '+run.id),node('p','State: '+run.state),node('p','Goal: '+run.goal));if(run.result)detail.append(node('pre',JSON.stringify(run.result,null,2)));if(run.error)detail.append(node('p','Error: '+run.error));detail.append(node('p','Tokens: '+run.usage.total_tokens+' | Artifact bytes: '+run.artifact_bytes));for(const step of steps){const card=node('section');card.append(node('h3','Step '+step.number),node('pre',JSON.stringify(step,null,2)));const image=document.createElement('img');image.alt='Screenshot for step '+step.number;image.loading='lazy';card.append(image);api('/v1/runs/'+id+'/artifacts/step-'+String(step.number).padStart(4,'0')+'.png').then(async response=>{image.src=URL.createObjectURL(await response.blob())}).catch(()=>image.remove());detail.append(card)}for(const name of ['run-log.json','result.json']){const link=node('button','Open '+name);link.className='link';link.onclick=()=>openArtifact(id,name);detail.append(link)}}catch(error){status.textContent=error.message}}
async function openArtifact(id,name){try{const body=await (await api('/v1/runs/'+id+'/artifacts/'+name)).json();const output=node('pre',JSON.stringify(body,null,2));detail.append(output)}catch(error){status.textContent=error.message}}
auth.onsubmit=event=>{event.preventDefault();key=document.querySelector('#key').value;load()};
</script></body></html>"""


def viewer_response() -> HTMLResponse:
    return HTMLResponse(
        VIEWER_HTML,
        headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
