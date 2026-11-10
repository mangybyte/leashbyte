# LeashByte Python SDK

A Python client for the stable LeashByte REST API. The models are generated from `openapi.json`, and a hand-written client on top adds authentication, binary downloads and run polling. It needs Python 3.13.

The PyPI package isn't published yet, so install it from a GitHub checkout:

```bash
uv pip install ./sdk/python
```

```python
import os

from leashbyte_sdk import FetchRequest, LeashByteClient, RunCreateRequest

with LeashByteClient(api_key=os.environ["LEASHBYTE_API_KEY"]) as client:
    page = client.fetch(FetchRequest(url="https://example.com"))
    results = client.search("self-hosted web automation", limit=3)
    run = client.create_run(
        RunCreateRequest(
            goal="Read the page title. Return it as the result.",
            start_url="https://example.com",
        )
    )
    finished = client.wait_for_run(run.id)
    print(page.content)
    print(results.results)
    print(finished.state)
```

You can leave out `api_key` and set `LEASHBYTE_API_KEY` or `LEASHBYTE_API_KEY_FILE` instead, which keeps the key out of your code. The default base URL is `http://127.0.0.1:8080`.

The client never logs keys, and its `repr` and API errors redact the configured one. Run artifacts and screenshots can contain page data, so keep them in private storage. For browser logins, use credential placeholders. Passwords don't belong in goals, URLs or ordinary text actions.
