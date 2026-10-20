"""Local desktop entry point. Hosted deployments use python serve.py."""
import os
import threading
import time
import webbrowser
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env',encoding='utf-8')
os.environ['LANGSMITH_TRACING']='false'
os.environ['LANGCHAIN_TRACING_V2']='false'
from app.config import Config
from app.main import create_app

def main():
    import uvicorn
    config=Config()
    if config.mode != 'local':
        raise SystemExit('Use python serve.py for cloud mode.')
    app=create_app(config)
    print('\nSchemeSaathi: '+config.base_url+'/login')
    print('Owner key for this session: '+config.token)
    print('Keep this terminal open. Ctrl+C stops the app.')
    def open_browser():
        import urllib.request
        for _ in range(30):
            try:
                with urllib.request.urlopen(config.base_url+'/healthz',timeout=1):
                    webbrowser.open(config.base_url+'/login')
                return
            except Exception:
                time.sleep(.5)
    if os.getenv('SCHEMESAATHI_NO_OPEN') != '1':
        threading.Thread(target=open_browser,daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=config.port,access_log=False,log_level='warning')

if __name__=='__main__':
    main()
