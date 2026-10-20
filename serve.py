"""Exactly one ASGI process: browser contexts and job state are process-owned."""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent/'.env',encoding='utf-8')
os.environ['LANGSMITH_TRACING']='false'
os.environ['LANGCHAIN_TRACING_V2']='false'
from app.config import Config
from app.main import create_app

def main():
    import uvicorn
    config=Config()
    if config.mode != 'cloud':
        raise SystemExit('Set SCHEMESAATHI_MODE=cloud, or use python run.py locally.')
    app=create_app(config)
    uvicorn.run(app,host='0.0.0.0',port=config.port,workers=1,proxy_headers=False,
                access_log=False,log_level='warning',limit_concurrency=20,timeout_keep_alive=5)

if __name__=='__main__':
    main()
