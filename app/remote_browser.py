"""Transient, owner-authenticated browser handoff. No OTP/screenshot persistence."""
import asyncio
from urllib.parse import urlparse
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from typing import Literal

PAUSED = {'LOGIN_REQUIRED','VERIFICATION_REQUIRED','NEEDS_INFORMATION','READY_FOR_SUBMISSION','SUBMISSION_BLOCKED','SUBMISSION_UNVERIFIED','INTERRUPTED','PORTAL_UNAVAILABLE','DEMO_REVIEW','DEMO_BLOCKED'}
class BrowserAction(BaseModel):
    action: Literal['click','text','key','scroll']
    x: float = Field(default=0,ge=0,le=1200)
    y: float = Field(default=0,ge=0,le=850)
    text: str = Field(default='',max_length=512)
    key: Literal['Tab','Shift+Tab','Enter','Backspace','Delete','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Escape','ControlOrMeta+A'] = 'Tab'
    delta: int = Field(default=0,ge=-800,le=800)

def install_remote_browser(app, runner, store):
    locks = {}

    def page_for(id):
        job = store.get('application',id)
        if not job:
            raise HTTPException(404,'Application not found')
        if job['status'] not in PAUSED:
            raise HTTPException(409,'Automation is running or this application is finished. Wait for a pause.')
        if id in runner.tasks and not runner.tasks[id].done():
            raise HTTPException(409,'Wait for the current automation step to finish.')
        context = runner.contexts.get(id)
        if not context or not context.pages:
            raise HTTPException(409,'Browser is closed. Use Resume in Applications to reopen it.')
        page = context.pages[-1]
        scheme = runner.scheme(job)
        url = urlparse(page.url)
        if job.get('demo') and not job.get('official_demo'):
            allowed = url.netloc == urlparse(runner.config.base_url).netloc and url.path.startswith('/demo/')
        else:
            allowed = url.scheme == 'https' and url.hostname in scheme['allowed_hosts']
        if not allowed:
            raise HTTPException(403,'Browser is outside the approved application portal.')
        return job,page,url.hostname

    @app.get('/api/browser/{id}/state')
    async def state(id:str):
        job,page,host = page_for(id)
        return {'host':host,'status':job['status'],'width':1200,'height':850,'auto_resume':bool(job.get('auto_resume_waiting')),'official_demo':bool(job.get('official_demo'))}

    @app.get('/api/browser/{id}/frame')
    async def frame(id:str):
        async with locks.setdefault(id,asyncio.Lock()):
            _,page,_ = page_for(id)
            data = await page.screenshot(type='jpeg',quality=65,full_page=False,timeout=5000)
            return Response(data,media_type='image/jpeg',headers={'Cache-Control':'no-store, private'})

    @app.post('/api/browser/{id}/action')
    async def action(id:str,body:BrowserAction):
        async with locks.setdefault(id,asyncio.Lock()):
            job,page,_ = page_for(id)
            # The verification watcher uses this same lock, preventing an action
            # from racing a subsequent automatic step.
            async with runner.locks.setdefault(id,asyncio.Lock()):
                job,page,_ = page_for(id)
                if job.get('official_demo'):
                    if body.action!='scroll':
                        raise HTTPException(409,'Demo browser is view-only remotely; submission and verification are disabled.')
                    await page.mouse.wheel(0,body.delta)
                    return {'ok':True}
                if job['status'] not in {'LOGIN_REQUIRED','VERIFICATION_REQUIRED'}:
                    raise HTTPException(409,'Controls are available only during login or verification.')
                if body.action == 'click':
                    await page.mouse.click(body.x,body.y)
                elif body.action == 'text':
                    await page.keyboard.insert_text(body.text)
                elif body.action == 'key':
                    await page.keyboard.press(body.key)
                elif body.action == 'scroll':
                    await page.mouse.wheel(0,body.delta)
        return {'ok':True}
