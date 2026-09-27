# PRISM LINK v0.3.2 OpenRouter import hotfix

Pipecat 1.7.0 exposes `OpenRouterLLMService` from `pipecat.services.openrouter.llm`.

The v0.3.1 code imported it from `pipecat.services.openrouter`, which can raise:

```text
ImportError: cannot import name 'OpenRouterLLMService' from 'pipecat.services.openrouter'
```

The corrected import is:

```python
from pipecat.services.openrouter.llm import OpenRouterLLMService
```

No environment-variable or API-key changes are required.
