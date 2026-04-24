import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('../.env', override=True)
load_dotenv('../.env.local', override=True)
from app.core.config import settings
print('REDIS_HOST:', settings.REDIS_HOST)
print('REDIS_PORT:', settings.REDIS_PORT)
print('MILVUS_HOST:', settings.MILVUS_HOST)
print('CHAT_LLM_API_KEY set:', bool(settings.CHAT_LLM_API_KEY))
