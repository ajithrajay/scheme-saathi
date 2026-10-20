document.querySelector('#login').addEventListener('submit',async e=>{
 e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;
 try {const r=await fetch('/api/bootstrap',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Saathi-Request':'1'},body:JSON.stringify({token:document.querySelector('#key').value})});const d=await r.json();if(!r.ok)throw Error(d.detail||'Sign-in failed');document.querySelector('#key').value='';location.replace('/');}
 catch(e){document.querySelector('#status').textContent=e.message;}finally{button.disabled=false;}
});
