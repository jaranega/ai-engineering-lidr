import os

# Offline import: LiteLLM would otherwise download its price map at import time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
